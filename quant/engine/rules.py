"""二期 02.1 交易制度解析：按对象／日期从 bundle 的 trading_rules 等表取值。

编码契约见 `.hetu/quant/qualification/phase2/trading-rules-20261017/sources.md` §0：
fee_rule 分号键值（stamp=sell:<rate>、transfer=both:<rate>、dividend=…）、
limit_rule（pct5/pct10/pct20）、min_buy_qty/qty_step 按 board/security_type
与生效区间逐日期取值；无 stock_st 行时回落 stock 行。

本模块只依赖 pandas，引擎进程（隔离 venv）与测试手算账本（quant venv）
共用同一份解析逻辑，保证两侧口径一致；未知规则显式报错，不默认为零。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

_LIMIT_PCT = {"pct5": 0.05, "pct10": 0.10, "pct20": 0.20}
_TICK = 0.01

# fee_rule 中股息税 token：holding_diff=差别化 20%/10%/0%（2015-09-08 起 statutory）；
# zero=合成零税制（仅机制账本用，不代表真实费率）
DIVIDEND_HOLDING_DIFF = "holding_diff"
DIVIDEND_ZERO = "zero"


class RuleResolutionError(RuntimeError):
    """制度缺失或编码无法解析：显式拒绝，不默认为零。"""


def parse_fee_rule(fee_rule: str) -> dict[str, str]:
    """解析 fee_rule 分号键值为字典；键重复或空值报错。"""
    result: dict[str, str] = {}
    for token in str(fee_rule).split(";"):
        token = token.strip()
        if not token:
            continue
        key, sep, value = token.partition("=")
        if not sep or not key.strip() or key.strip() in result:
            raise RuleResolutionError(f"fee_rule token 无法解析或键重复: {token!r}（整串 {fee_rule!r}）")
        result[key.strip()] = value.strip()
    return result


class BundleRules:
    """bundle 表（trading_rules／historical_securities／security_status）的按日解析。"""

    def __init__(
        self,
        trading_rules: pd.DataFrame,
        historical_securities: pd.DataFrame,
        security_status: pd.DataFrame | None = None,
    ) -> None:
        self._rules = trading_rules.copy()
        self._hist = historical_securities.copy()
        self._status = security_status.copy() if security_status is not None else pd.DataFrame(
            columns=["date", "code", "is_st", "is_suspended"]
        )

    # ---- 证券身份 ----

    def board_of(self, code: str) -> tuple[str, str]:
        """(market, board)；同代码多版本取上市窗口覆盖最全的一行（合成宇宙单版本）。"""
        rows = self._hist[self._hist["code"].astype(str) == str(code)]
        if not len(rows):
            raise RuleResolutionError(f"historical_securities 无证券 {code}")
        row = rows.sort_values("valid_from").iloc[-1]
        return str(row["market"]), str(row["board"])

    def is_st(self, code: str, day: str) -> bool:
        rows = self._status[
            (self._status["code"].astype(str) == str(code))
            & (self._status["date"].astype(str) == str(day))
        ]
        if not len(rows):
            return False
        return str(rows.iloc[0]["is_st"]).strip().lower() in ("true", "1")

    # ---- 规则行解析 ----

    def _rule_row(self, code: str, day: str) -> pd.Series:
        market, board = self.board_of(code)
        security_type = "stock_st" if self.is_st(code, day) else "stock"
        pool = self._rules[
            (self._rules["market"].astype(str) == market)
            & (self._rules["board"].astype(str) == board)
            & (self._rules["security_type"].astype(str) == security_type)
            & (self._rules["valid_from"].astype(str) <= str(day))
            & (
                (self._rules["valid_to"].astype(str) == "")
                | (self._rules["valid_to"].isna())
                | (self._rules["valid_to"].astype(str) >= str(day))
            )
        ]
        if not len(pool) and security_type == "stock_st":
            # 无 stock_st 行时回落 stock 行（契约 §0）
            pool = self._rules[
                (self._rules["market"].astype(str) == market)
                & (self._rules["board"].astype(str) == board)
                & (self._rules["security_type"].astype(str) == "stock")
                & (self._rules["valid_from"].astype(str) <= str(day))
                & (
                    (self._rules["valid_to"].astype(str) == "")
                    | (self._rules["valid_to"].isna())
                    | (self._rules["valid_to"].astype(str) >= str(day))
                )
            ]
        if not len(pool):
            raise RuleResolutionError(
                f"trading_rules 无适用规则: {code} ({market}/{board}/{security_type}) @ {day}"
            )
        return pool.sort_values("valid_from").iloc[-1]

    def fee_components(self, code: str, day: str) -> dict[str, object]:
        """费用制：stamp_rate（卖出）、transfer_rate（双边）、dividend_mode。"""
        parsed = parse_fee_rule(str(self._rule_row(code, day)["fee_rule"]))
        if "stamp" not in parsed or "transfer" not in parsed or "dividend" not in parsed:
            raise RuleResolutionError(f"fee_rule 缺键: {parsed}（{code} @ {day}）")
        stamp_spec = parsed["stamp"]
        transfer_spec = parsed["transfer"]
        if not stamp_spec.startswith("sell:") or not transfer_spec.startswith("both:"):
            raise RuleResolutionError(f"fee_rule 方向编码未识别: {parsed}（{code} @ {day}）")
        dividend_spec = parsed["dividend"]
        if dividend_spec == DIVIDEND_ZERO:
            dividend_mode = DIVIDEND_ZERO
        elif dividend_spec.startswith(DIVIDEND_HOLDING_DIFF):
            dividend_mode = DIVIDEND_HOLDING_DIFF
        else:
            raise RuleResolutionError(f"dividend 税制未识别: {dividend_spec!r}（{code} @ {day}）")
        return {
            "stamp_rate": float(stamp_spec[len("sell:"):]),
            "transfer_rate": float(transfer_spec[len("both:"):]),
            "dividend_mode": dividend_mode,
        }

    def limit_pct(self, code: str, day: str) -> float:
        rule = str(self._rule_row(code, day)["limit_rule"]).strip()
        if rule not in _LIMIT_PCT:
            raise RuleResolutionError(f"limit_rule 未识别: {rule!r}（{code} @ {day}）")
        return _LIMIT_PCT[rule]

    def tick_size(self, code: str, day: str) -> float:
        tick = float(self._rule_row(code, day)["tick_size"])
        if not 0 < tick < float("inf"):
            raise RuleResolutionError(f"invalid tick_size: {code} @ {day}")
        return tick

    def slippage_price(self, code: str, day: str, price: float, rate: float, *, buy: bool) -> float:
        """滑点按不利方向取整到当日步长；不以价格上限截断滑点。"""
        tick = Decimal(str(self.tick_size(code, day)))
        slipped = Decimal(str(price)) * (1 + Decimal(str(rate)) * (1 if buy else -1))
        return float((slipped / tick).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR) * tick)

    def limit_price(self, prev_close: float, pct: float, tick: float = _TICK) -> tuple[float, float]:
        """(limit_up, limit_down)，按适用价格步长四舍五入。"""
        previous, step = Decimal(str(prev_close)), Decimal(str(tick))
        return tuple(float((previous * (1 + Decimal(str(pct)) * sign) / step).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP) * step) for sign in (1, -1))

    def qty_rules(self, code: str, day: str) -> tuple[int, int]:
        """(min_buy_qty, qty_step)。"""
        row = self._rule_row(code, day)
        return int(row["min_buy_qty"]), int(row["qty_step"])


def load_bundle_rules(bundle_dir: Path) -> BundleRules:
    """从生产 bundle 的 tables/ 目录加载制度表；表缺失显式拒绝。"""
    tables_dir = Path(bundle_dir) / "tables"
    required = ("trading_rules", "historical_securities")
    missing = [name for name in required if not (tables_dir / f"{name}.parquet").exists()]
    if missing:
        raise RuleResolutionError(f"bundle 缺制度表: {', '.join(missing)}（{tables_dir}）")
    status_path = tables_dir / "security_status.parquet"
    return BundleRules(
        pd.read_parquet(tables_dir / "trading_rules.parquet"),
        pd.read_parquet(tables_dir / "historical_securities.parquet"),
        pd.read_parquet(status_path) if status_path.exists() else None,
    )
