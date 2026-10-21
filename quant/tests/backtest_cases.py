"""二期回测合成事实场景：只提供输入事实、定义与文件内容，不提供期望值。

case 限 `history`（01）、`event_ledger`（02）、`execution_clocks`（02）、
`pipeline`（02）；未知 case／variant 报错。事实取值是合成输入，
资格、账户与净值等判定一律留在各测试侧。
`build_case_store`/`case_definition` 是各测试共用的快照构造 helper，
只把事实落成快照，不携带期望结论。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CN = ZoneInfo("Asia/Shanghai")

_CASES = ("history", "event_ledger", "execution_clocks", "pipeline", "formal")


def _evidence_content(ref: str) -> bytes:
    """证据原件的合成字节：内容只依赖 ref，哈希可稳定手算复现。"""
    return f"合成证据原件:{ref}\n".encode()


def _evidence_sha(ref: str) -> str:
    return hashlib.sha256(_evidence_content(ref)).hexdigest()

# history case 的常量事实
HISTORY_MONTH_ENDS = [
    str(p.end_time.date()) for p in pd.period_range("2020-01", "2024-06", freq="M")
]
HISTORY_AS_OF = datetime(2024, 6, 30, 21, 0, tzinfo=CN)
HISTORY_OBTAINED = "2024-06-30T18:00:00+08:00"

HISTORY_TABLE_PRODUCTS = {
    "calendar": ("test_bt_calendar", "calendar"),
    "daily_bars": ("test_bt_daily", "market"),
    "security_status": ("test_bt_status", "market"),
    "corporate_actions": ("test_bt_actions", "event"),
    "market_cap": ("test_bt_mcap", "fundamental"),
    "historical_securities": ("test_bt_hist_securities", "catalog"),
    "trading_rules": ("test_bt_trading_rules", "catalog"),
    "knowledge_evidence": ("test_bt_knowledge", "catalog"),
}

# 历史证券事实：KEEP_A 全程上市；DELISTED_LATER 2022-06-30 退市（晚修订登记）；
# NOT_YET_LISTED 2024-01-02 才上市。evidence_ref 指向 knowledge_evidence 的 evidence_ref。
_HIST_SECURITIES_ROWS = [
    ("KEEP_A", "2019-01-02", "", "True", "SH", "main",
     "2019-01-02T18:00:00+08:00", "ev-hist-keepa-orig"),
    ("DELISTED_LATER", "2019-06-03", "", "True", "SZ", "main",
     "2019-06-03T18:00:00+08:00", "ev-hist-del-orig"),
    ("DELISTED_LATER", "2019-06-03", "2022-06-30", "False", "SZ", "main",
     "2022-07-05T18:00:00+08:00", "ev-hist-del-rev2"),
    ("NOT_YET_LISTED", "2024-01-02", "", "True", "SZ", "chinext",
     "2024-01-02T18:00:00+08:00", "ev-hist-nyl-orig"),
]

_KNOWLEDGE_ROWS = [
    ("historical_securities", "KEEP_A", "orig", "", "2019-01-02T18:00:00+08:00",
     "ev-hist-keepa-orig", _evidence_sha("ev-hist-keepa-orig"), "True"),
    ("historical_securities", "DELISTED_LATER", "orig", "", "2019-06-03T18:00:00+08:00",
     "ev-hist-del-orig", _evidence_sha("ev-hist-del-orig"), "True"),
    ("historical_securities", "DELISTED_LATER", "rev2", "orig", "2022-07-05T18:00:00+08:00",
     "ev-hist-del-rev2", _evidence_sha("ev-hist-del-rev2"), "True"),
    ("historical_securities", "NOT_YET_LISTED", "orig", "", "2024-01-02T18:00:00+08:00",
     "ev-hist-nyl-orig", _evidence_sha("ev-hist-nyl-orig"), "True"),
]

_HISTORY_EVIDENCE_REFS = tuple(row[5] for row in _KNOWLEDGE_ROWS)

_TRADING_RULES_ROWS = [
    ("cn-a-main-sh-2019", "SH", "main", "stock", "2019-01-01", "",
     "100", "100", "0.01", "T+1", "pct10", "stamp=sell:0.001;transfer=both:0.00001;dividend=zero", "ev-rules-2019"),
    ("cn-a-main-sz-2019", "SZ", "main", "stock", "2019-01-01", "",
     "100", "100", "0.01", "T+1", "pct10", "stamp=sell:0.001;transfer=both:0.00001;dividend=zero", "ev-rules-2019"),
    ("cn-a-chinext-2020", "SZ", "chinext", "stock", "2020-08-24", "",
     "100", "100", "0.01", "T+1", "pct20", "stamp=sell:0.001;transfer=both:0.00001;dividend=zero", "ev-rules-2020"),
]


def _history_csvs() -> dict[str, str]:
    bars: list[str] = []
    status: list[str] = []
    mcap: list[str] = []
    spans = {"KEEP_A": ("", None), "DELISTED_LATER": ("", "2022-06-30"), "NOT_YET_LISTED": ("2024-01-31", None)}
    for code, (first, last) in spans.items():
        for day in HISTORY_MONTH_ENDS:
            if day < first or (last is not None and day > last):
                continue
            bars.append(f"{day},{code},10.0,10.0,10.0,10.0,10.0,10.0,1000,20000")
            status.append(f"{day},{code},False,False,True")
            mcap.append(f"{code},{day},1000000,{day}T18:00:00+08:00")
    hist_rows = "\n".join(",".join(row) for row in _HIST_SECURITIES_ROWS)
    knowledge_rows = "\n".join(",".join(row) for row in _KNOWLEDGE_ROWS)
    rules_rows = "\n".join(",".join(row) for row in _TRADING_RULES_ROWS)
    return {
        "calendar": "date,is_trading_day\n"
        + "\n".join(f"{d},True" for d in HISTORY_MONTH_ENDS)
        + "\n",
        "daily_bars": (
            "date,code,open_raw,high_raw,low_raw,close_raw,"
            "close_price_return,close_total_return,volume_shares,amount_cny\n"
            + "\n".join(bars)
            + "\n"
        ),
        "security_status": "date,code,is_st,is_suspended,is_listed\n" + "\n".join(status) + "\n",
        "corporate_actions": "code,ex_date,action_type,m,d,terms_raw,available_at\n",
        "market_cap": "code,date,market_cap_cny,available_at\n" + "\n".join(mcap) + "\n",
        "historical_securities": (
            "code,valid_from,valid_to,listed,market,board,available_at,evidence_ref\n"
            + hist_rows
            + "\n"
        ),
        "trading_rules": (
            "rule_id,market,board,security_type,valid_from,valid_to,"
            "min_buy_qty,qty_step,tick_size,settlement,limit_rule,fee_rule,source_ref\n"
            + rules_rows
            + "\n"
        ),
        "knowledge_evidence": (
            "table_name,record_key,revision_id,supersedes,published_at,"
            "evidence_ref,sha256,verified\n" + knowledge_rows + "\n"
        ),
    }


def _history_definition() -> dict[str, object]:
    return {
        "as_of": HISTORY_AS_OF,
        "start": date(2021, 6, 1),
        "end": date(2024, 6, 30),
        "strategy_id": "low_volatility",
        "parameters": {"lookback_months": 36},
        "protocol": {
            "schema_version": 1,
            "stage": "mechanism",
            "universe": {"rule": "synthetic_history"},
            "warmup_months": 12,
            "initial_cash_cny": 1000000,
            "currency": "CNY",
            "account_type": "cash",
            "leverage": 1.0,
            "cash_interest_rate": 0.0,
            "investor_tax_profile": "cn_individual_research",
            "rebalance": {"frequency": "monthly"},
            "benchmark_id": "universe_vw",
            "execution": {
                "signal_time": "close",
                "fill_time": "next_open",
                "order_expiry": "same_day",
                "volume_lookback_days": 20,
                "participation_rate": 0.01,
                "terminal_policy": "hold",
            },
            "costs": {
                "commission_rate": 0.0003,
                "min_commission_cny": 5,
                "slippage_rate": 0.0005,
                "statutory_rules_ref": "phase2-trading-rules",
            },
            "rules_ref": "phase2-trading-rules",
            "time_split": {"final": ["2024-01-01", "2024-06-30"]},
            "parameter_family": {"lookback_months": [36]},
            "objectives": {"income": "net_cagr_gt_0"},
            "robustness": {"scenarios": ["double_costs"]},
            "trial_budget": {"max_runs": 8},
            "viewing_record": {"final_seen": False},
        },
    }


def make_backtest_case(case: str, variant: str = "base") -> dict[str, object]:
    """返回指定场景的输入事实与定义；不含期望资格或账户答案。"""
    if case == "pipeline":
        return _pipeline_case(variant)
    if case == "event_ledger":
        return _event_ledger_case(variant)
    if case == "execution_clocks":
        return _execution_clocks_case(variant)
    if case == "formal":
        return _formal_case(variant)
    if case != "history":
        raise ValueError(f"unknown backtest case: {case!r} (known: {', '.join(_CASES)})")
    if variant == "base":
        return {
            "case": case,
            "variant": variant,
            "csvs": _history_csvs(),
            "table_products": dict(HISTORY_TABLE_PRODUCTS),
            "obtained_at": HISTORY_OBTAINED,
            "definition": _history_definition(),
            "actions_coverage": {"start": "2020-01-01", "end": "2024-06-30"},
            "codes": ("KEEP_A", "DELISTED_LATER", "NOT_YET_LISTED"),
            "evidence_refs": _HISTORY_EVIDENCE_REFS,
        }
    if variant == "signal_pool":
        return {
            "case": case,
            "variant": variant,
            "csvs": _signal_pool_csvs(),
            "table_products": dict(HISTORY_TABLE_PRODUCTS),
            "obtained_at": SIGNAL_POOL_OBTAINED,
            "definition": _signal_pool_definition(),
            "actions_coverage": {"start": "2020-12-01", "end": "2024-01-31"},
            "codes": SIGNAL_POOL_CODES,
            "evidence_refs": tuple(f"ev-sp-{code.lower()}" for code in SIGNAL_POOL_CODES),
        }
    raise ValueError(f"unknown variant for case 'history': {variant!r}")


# ---- history/signal_pool 变体（03）：可手算波动率信号池 ----
# 月末点 2020-12..2024-01；信号日 2023-12-31 与 2024-01-31 各取末 37 点（36 个收益）。
# VOL_01/VOL_02 同振幅（边界并列事实），VOL_03..VOL_12 振幅递增；FUTURE_MCAP
# 市值晚于 as_of 才可知；SHORT_35 少前两个月末点；GAP_MONTH 缺 2022-06-30。
SIGNAL_POOL_AS_OF = datetime(2024, 2, 1, 21, 0, tzinfo=CN)
SIGNAL_POOL_OBTAINED = "2024-02-01T18:00:00+08:00"
SIGNAL_POOL_SIGNAL_DATES = ("2023-12-31", "2024-01-31")
_SIGNAL_AMPLITUDE = {
    "VOL_01": 0.01,
    "VOL_02": 0.01,
    **{f"VOL_{k:02d}": 0.01 * (k - 1) for k in range(3, 13)},
    "FUTURE_MCAP": 0.05,
    "SHORT_35": 0.03,
    "GAP_MONTH": 0.04,
}
SIGNAL_POOL_CODES = tuple(_SIGNAL_AMPLITUDE)


def _signal_pool_csvs() -> dict[str, str]:
    month_ends = [str(p.end_time.date()) for p in pd.period_range("2020-12", "2024-01", freq="M")]
    bars: list[str] = []
    status: list[str] = []
    for code in SIGNAL_POOL_CODES:
        points = month_ends[2:] if code == "SHORT_35" else month_ends
        amplitude = _SIGNAL_AMPLITUDE[code]
        value = 100.0
        for index, day in enumerate(points):
            if index > 0:
                value *= 1 + (amplitude if index % 2 == 1 else -amplitude)
            status.append(f"{day},{code},False,False,True")
            if code == "GAP_MONTH" and day == "2022-06-30":
                continue
            bars.append(
                f"{day},{code},{value:.6f},{value:.6f},{value:.6f},{value:.6f},"
                f"{value:.6f},{value:.6f},1000,20000"
            )
    mcap: list[str] = []
    for index, code in enumerate(sorted(SIGNAL_POOL_CODES)):
        cap = (index + 1) * 1000000000
        for day in SIGNAL_POOL_SIGNAL_DATES:
            available = (
                "2025-01-01T00:00:00+08:00" if code == "FUTURE_MCAP" else f"{day}T18:00:00+08:00"
            )
            mcap.append(f"{code},{day},{cap},{available}")
    hist_rows = "\n".join(
        f"{code},2020-01-02,,True,SH,main,2020-01-02T18:00:00+08:00,ev-sp-{code.lower()}"
        for code in SIGNAL_POOL_CODES
    )
    knowledge_rows = "\n".join(
        f"historical_securities,{code},orig,,2020-01-02T18:00:00+08:00,"
        f"ev-sp-{code.lower()},{_evidence_sha(f'ev-sp-{code.lower()}')},True"
        for code in SIGNAL_POOL_CODES
    )
    rules_rows = "\n".join(",".join(row) for row in _TRADING_RULES_ROWS)
    calendar_days = pd.date_range("2020-12-01", "2024-02-02", freq="D")
    return {
        "calendar": "date,is_trading_day\n"
        + "\n".join(f"{d.date()},True" for d in calendar_days)
        + "\n",
        "daily_bars": (
            "date,code,open_raw,high_raw,low_raw,close_raw,"
            "close_price_return,close_total_return,volume_shares,amount_cny\n"
            + "\n".join(bars)
            + "\n"
        ),
        "security_status": "date,code,is_st,is_suspended,is_listed\n" + "\n".join(status) + "\n",
        "corporate_actions": "code,ex_date,action_type,m,d,terms_raw,available_at\n",
        "market_cap": "code,date,market_cap_cny,available_at\n" + "\n".join(mcap) + "\n",
        "historical_securities": (
            "code,valid_from,valid_to,listed,market,board,available_at,evidence_ref\n"
            + hist_rows
            + "\n"
        ),
        "trading_rules": (
            "rule_id,market,board,security_type,valid_from,valid_to,"
            "min_buy_qty,qty_step,tick_size,settlement,limit_rule,fee_rule,source_ref\n"
            + rules_rows
            + "\n"
        ),
        "knowledge_evidence": (
            "table_name,record_key,revision_id,supersedes,published_at,"
            "evidence_ref,sha256,verified\n" + knowledge_rows + "\n"
        ),
    }


def _signal_pool_definition() -> dict[str, object]:
    definition = _history_definition()
    definition["as_of"] = SIGNAL_POOL_AS_OF
    definition["start"] = date(2024, 1, 2)
    definition["end"] = date(2024, 1, 31)
    protocol = dict(definition["protocol"])  # type: ignore[arg-type]
    protocol["universe"] = {"rule": "synthetic_signal_pool"}
    protocol["warmup_months"] = 36
    definition["protocol"] = protocol
    return definition


# ---- pipeline case（02.2）：共同入口的输入事实，复用 history 材料 ----
# base：与 history base 同一批已核证券事实（合格输入）；
# unqualified：行情事实整体缺失（逐对象缺口，不含资格判定）。


def _pipeline_case(variant: str) -> dict[str, object]:
    if variant == "base":
        base = make_backtest_case("history")
        # mechanism 阶段合成信号（标明 synthetic 的输入事实）：月末信号、
        # 次月月末（合成日历的次交易日）开盘执行；03 正式信号链接通后不再使用。
        definition = dict(base["definition"])  # type: ignore[index]
        parameters = dict(definition["parameters"])  # type: ignore[index]
        parameters["synthetic_signals"] = [
            {
                "signal_at": "2021-05-31",
                "executable_from": "2021-06-30",
                "code": "KEEP_A",
                "target_weight": 0.5,
            }
        ]
        definition["parameters"] = parameters
        # 合成行情日量仅 1,000 股：机制事实把 participation 提到 1.0 使信号可
        # 执行（容量语义本身由 execution_clocks capacity 变体验证）
        protocol = dict(definition["protocol"])  # type: ignore[index]
        execution = dict(protocol["execution"])  # type: ignore[index]
        execution["participation_rate"] = 1.0
        protocol["execution"] = execution
        definition["protocol"] = protocol
        return {**base, "case": "pipeline", "variant": variant, "definition": definition}
    if variant == "unqualified":
        base = make_backtest_case("history")
        csvs = dict(base["csvs"])  # type: ignore[arg-type]
        header = csvs["daily_bars"].splitlines()[0]
        csvs["daily_bars"] = header + "\n"  # 行情事实整体缺失
        return {
            **base,
            "csvs": csvs,
            "case": "pipeline",
            "variant": variant,
        }
    raise ValueError(f"unknown variant for case 'pipeline': {variant!r}")


# ---- 共用快照构造 helper（只落事实，不含期望） ----


def case_definition(case: dict[str, object], snapshot_id: str, **overrides: object):
    """由 case 事实构造 BacktestDefinition；overrides 只改定义字段。"""
    from hetu_quant.backtest_models import BacktestDefinition

    facts = dict(case["definition"])  # type: ignore[arg-type]
    facts["snapshot_id"] = snapshot_id
    facts.update(overrides)
    return BacktestDefinition(**facts)  # type: ignore[arg-type]


def _case_product(kind: str, tables: list[str]) -> dict[str, object]:
    return {
        "provider": "local_file",
        "fee_class": "free",
        "permission": {
            "status": "verified_free",
            "basis": "合成测试材料，无第三方权利",
            "source_url": "",
        },
        "kind": kind,
        "requires_codes": kind not in ("catalog", "calendar"),
        "tables": tables,
        "provider_timezone": "Asia/Shanghai",
        "notes": "",
    }


def build_case_store(
    tmp_path: Path,
    case: dict[str, object],
    *,
    tables: tuple[str, ...] | None = None,
    obtained_overrides: dict[str, str] | None = None,
):
    """把 case 的 CSV 事实经 import_files 落成 DataStore 快照；返回 (store, ref)。"""
    from hetu_quant.data import DataStore

    csvs: dict[str, str] = case["csvs"]  # type: ignore[assignment]
    table_products: dict[str, tuple[str, str]] = case["table_products"]  # type: ignore[assignment]
    tmp_path.mkdir(parents=True, exist_ok=True)
    selected = tables if tables is not None else tuple(csvs)
    products = {}
    items = []
    files = []
    for table in selected:
        product_id, kind = table_products[table]
        products[product_id] = _case_product(kind, [table])
        items.append(
            {
                "product_id": product_id,
                "table": table,
                "files": [f"{table}.csv"],
                "source": {
                    "name": f"合成{table}",
                    "fee_class": "free",
                    "permission_basis": "合成测试材料，无第三方权利",
                    "source_url": "",
                    "obtained_at": (obtained_overrides or {}).get(table, case["obtained_at"]),
                },
            }
        )
        path = tmp_path / f"{table}.csv"
        path.write_text(csvs[table], encoding="utf-8")
        files.append(path)
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps({"schema_version": 1, "products": products}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = DataStore(tmp_path / "store", config_path=config_path)
    evidence_dir = store.root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for evidence_ref in case.get("evidence_refs", ()):  # type: ignore[union-attr]
        (evidence_dir / str(evidence_ref)).write_bytes(_evidence_content(str(evidence_ref)))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"mode": "free", "items": items}, ensure_ascii=False), encoding="utf-8"
    )
    ref = store.import_files(manifest_path, tuple(files))
    return store, ref


def build_prepared_case_store(tmp_path: Path, case: dict[str, object], monkeypatch):
    """原始合成事实经生产 prepare；行动仅替换 SDK，不补写覆盖或信号。

    使用同一 case 的 CSV 和证据原件，明确成功空行动查询这一输入事实。
    旧 import 构造器保留其历史形状测试用途。
    """
    import io
    from datetime import timedelta

    from hetu_quant.data import DataStore
    from hetu_quant.models import DataRequest
    from hetu_quant.providers.baostock import BaoStockProvider

    tmp_path.mkdir(parents=True, exist_ok=True)
    products = {}
    frames = {}
    for table, (product_id, kind) in case["table_products"].items():
        if table == "corporate_actions":
            continue
        products[product_id] = dict(_case_product(kind, [table]), provider="synthetic_facts")
        frames[product_id] = pd.read_csv(io.StringIO(case["csvs"][table]), dtype=str)
    products["cn_a_actions"] = dict(
        _case_product("event", ["corporate_actions"]), provider="baostock")

    class Facts:
        def fetch(self, product_id, codes, start, end):
            frame = frames[product_id].copy()
            if "code" in frame and codes:
                frame = frame[frame["code"].isin(codes)]
            if "date" in frame:
                frame = frame[frame["date"].between(str(start), str(end))]
            return frame

    class EmptyResponse:
        error_code = "0"
        error_msg = ""
        fields = []

        def next(self):
            return False

    class SDK:
        def query_dividend_data(self, code, year, yearType):
            return EmptyResponse()

    class Clock(datetime):
        tick = 0

        @classmethod
        def now(cls, tz=None):
            cls.tick += 1
            return datetime.fromisoformat(case["obtained_at"]) + timedelta(microseconds=cls.tick)

    monkeypatch.setattr("hetu_quant.data.datetime", Clock)
    provider = BaoStockProvider()
    provider._bs = SDK()
    root = tmp_path / "store"
    root.mkdir()
    config = root / "data-products.json"
    config.write_text(json.dumps({"products": products}, ensure_ascii=False))
    store = DataStore(root, providers={"synthetic_facts": Facts(), "baostock": provider})
    evidence_dir = root / "evidence"
    evidence_dir.mkdir()
    for ref in case.get("evidence_refs", ()):
        (evidence_dir / ref).write_bytes(_evidence_content(ref))
    coverage = case["actions_coverage"]
    request = DataRequest(product_ids=tuple(products), codes=tuple(case["codes"]),
                          start=date.fromisoformat(coverage["start"]),
                          end=date.fromisoformat(coverage["end"]),
                          as_of=case["definition"]["as_of"], purpose="backtest_low_volatility")
    return store, store.prepare(request)


# ---- event_ledger / execution_clocks case（02.1）：账户语义合成事实 ----
# 只提供输入事实（交易日历、行情、制度、状态、行动、信号、定义），
# 资格、账户与净值等期望一律留在 quant/tests/test_backtest_account.py。

EL_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _account_protocol(**overrides: object) -> dict[str, object]:
    protocol: dict[str, object] = {
        "schema_version": 1,
        "stage": "mechanism",
        "universe": {"rule": "synthetic_account"},
        "warmup_months": 0,
        "initial_cash_cny": 10000,
        "currency": "CNY",
        "account_type": "cash",
        "leverage": 1.0,
        "cash_interest_rate": 0.0,
        "investor_tax_profile": "cn_individual_research",
        "rebalance": {"frequency": "monthly"},
        "benchmark_id": "universe_vw",
        "execution": {
            "signal_time": "close",
            "fill_time": "next_open",
            "order_expiry": "same_day",
            "volume_lookback_days": 20,
            "participation_rate": 0.01,
            "terminal_policy": "hold",
        },
        "costs": {
            "commission_rate": 0.0003,
            "min_commission_cny": 5,
            "slippage_rate": 0.0,
            "statutory_rules_ref": "phase2-trading-rules",
        },
        "rules_ref": "phase2-trading-rules",
        "time_split": {"final": ["2024-01-01", "2024-01-31"]},
        "parameter_family": {},
        "objectives": {},
        "robustness": {},
        "trial_budget": {},
        "viewing_record": {"final_seen": False},
    }
    protocol.update(overrides)
    return protocol


def _account_definition(**overrides: object) -> dict[str, object]:
    definition: dict[str, object] = {
        "as_of": datetime(2024, 1, 31, 21, 0, tzinfo=CN),
        "start": date(2024, 1, 2),
        "end": date(2024, 1, 31),
        "strategy_id": "low_volatility",
        "parameters": {},
        "protocol": _account_protocol(),
        "market": "CN_A",
        "mode": "free",
    }
    definition.update(overrides)
    return definition


def _calendar_df(days: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"date": days, "is_trading_day": ["True"] * len(days)})


def _bars_df(rows: list[tuple]) -> pd.DataFrame:
    """(code, day, open, close, volume) → 生产日线表（高低以开收夹逼的合成输入）。"""
    frames = {
        "date": [], "code": [], "open_raw": [], "high_raw": [], "low_raw": [],
        "close_raw": [], "close_price_return": [], "close_total_return": [],
        "volume_shares": [], "amount_cny": [],
    }
    for code, day, open_raw, close_raw, volume in rows:
        high = max(open_raw, close_raw)
        low = min(open_raw, close_raw)
        for key, value in (
            ("date", day), ("code", code),
            ("open_raw", open_raw), ("high_raw", high), ("low_raw", low),
            ("close_raw", close_raw), ("close_price_return", ""),
            ("close_total_return", ""), ("volume_shares", volume),
            ("amount_cny", volume * close_raw),
        ):
            frames[key].append(value)
    return pd.DataFrame(frames)


def _status_df(days: list[str], codes: tuple[str, ...], st_days: dict[str, tuple[str, ...]] | None = None,
                suspended_days: dict[str, tuple[str, ...]] | None = None) -> pd.DataFrame:
    st_days = st_days or {}
    suspended_days = suspended_days or {}
    rows = {
        "date": [], "code": [], "is_st": [], "is_suspended": [], "is_listed": [],
    }
    for day in days:
        for code in codes:
            rows["date"].append(day)
            rows["code"].append(code)
            rows["is_st"].append("True" if day in st_days.get(code, ()) else "False")
            rows["is_suspended"].append("True" if day in suspended_days.get(code, ()) else "False")
            rows["is_listed"].append("True")
    return pd.DataFrame(rows)


def _hist_df(rows: list[tuple]) -> pd.DataFrame:
    """(code, valid_from, market, board) → 历史证券表（全程上市，单版本）。"""
    return pd.DataFrame(
        {
            "code": [r[0] for r in rows],
            "valid_from": [r[1] for r in rows],
            "valid_to": [""] * len(rows),
            "listed": ["True"] * len(rows),
            "market": [r[2] for r in rows],
            "board": [r[3] for r in rows],
            "available_at": [f"{r[1]}T18:00:00+08:00" for r in rows],
            "evidence_ref": [f"ev-acct-{r[0].lower()}" for r in rows],
        }
    )


def _rules_df(rows: list[tuple]) -> pd.DataFrame:
    """(market, board, security_type, valid_from, valid_to, min, step, limit, fee_rule)。"""
    return pd.DataFrame(
        {
            "rule_id": [f"acct-{i}" for i in range(len(rows))],
            "market": [r[0] for r in rows],
            "board": [r[1] for r in rows],
            "security_type": [r[2] for r in rows],
            "valid_from": [r[3] for r in rows],
            "valid_to": [r[4] for r in rows],
            "min_buy_qty": [r[5] for r in rows],
            "qty_step": [r[6] for r in rows],
            "tick_size": ["0.01"] * len(rows),
            "settlement": ["T+1"] * len(rows),
            "limit_rule": [r[7] for r in rows],
            "fee_rule": [r[8] for r in rows],
            "source_ref": ["ev-acct-rules"] * len(rows),
        }
    )


def _actions_df(rows: list[tuple]) -> pd.DataFrame:
    """(code, ex_date, action_type, m, d) → 公司行动表。"""
    return pd.DataFrame(
        {
            "code": [r[0] for r in rows],
            "ex_date": [r[1] for r in rows],
            "action_type": [r[2] for r in rows],
            "m": [r[3] for r in rows],
            "d": [r[4] for r in rows],
            "terms_raw": [""] * len(rows),
            "available_at": [f"{r[1]}T18:00:00+08:00" for r in rows],
            "record_date": [r[5] for r in rows],
            "pay_date": [r[6] for r in rows],
            "stock_listing_date": [r[7] for r in rows],
        }
    )


_ZERO_FEE = "stamp=sell:0.001;transfer=both:0;dividend=zero"
_REAL_FEE = "stamp=sell:0.001;transfer=both:0.00001;dividend=holding_diff(<=1m:20%,1m~1y:10%,>1y:0%)"
_REAL_FEE_2023 = "stamp=sell:0.0005;transfer=both:0.00001;dividend=holding_diff(<=1m:20%,1m~1y:10%,>1y:0%)"


def _event_ledger_case(variant: str) -> dict[str, object]:
    """d1 收盘信号买 0.5 权重；d3 每原股派 1 元并 1 拆 2；exit 变体 d3 收盘清仓。"""
    if variant not in ("base", "exit"):
        raise ValueError(f"unknown variant for case 'event_ledger': {variant!r}")
    days = list(EL_DAYS)
    signals = [
        {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.5}
    ]
    if variant == "exit":
        signals.append(
            {"signal_at": days[2], "executable_from": days[3], "code": "CODE_A", "target_weight": 0.0}
        )
    bars = []
    for index, day in enumerate(days):
        price = 10.0 if index < 2 else 4.5
        bars.append(("CODE_A", day, price, price, 1_000_000))
    return {
        "case": "event_ledger",
        "variant": variant,
        "definition": _account_definition(),
        "signals": signals,
        "tables": {
            "calendar": _calendar_df(days),
            "daily_bars": _bars_df(bars),
            "security_status": _status_df(days, ("CODE_A",)),
            "historical_securities": _hist_df([("CODE_A", "2020-01-02", "SZ", "main")]),
            "trading_rules": _rules_df(
                [("SZ", "main", "stock", "2016-01-01", "", "100", "100", "pct10", _ZERO_FEE)]
            ),
            "corporate_actions": _actions_df(
                [
                    ("CODE_A", days[2], "cash_dividend", 1.0, 1.0, days[1], days[3], None),
                    ("CODE_A", days[2], "bonus_split", 2.0, 0.0, days[1], None, days[2]),
                ]
            ),
        },
    }


def _execution_clocks_case(variant: str) -> dict[str, object]:  # noqa: C901
    """时钟／制度参数场景：各变体只改输入事实，期望留在测试侧。"""
    days = list(EL_DAYS)
    base_bars = [("CODE_A", day, 10.0, 10.0, 1_000_000) for day in days]
    base_rules = [("SZ", "main", "stock", "2016-01-01", "", "100", "100", "pct10", _ZERO_FEE)]
    base_securities = [("CODE_A", "2020-01-02", "SZ", "main")]

    def case(bars=None, signals=None, rules=None, securities=None, actions=None,
             status=None, definition_overrides=None, case_days=None) -> dict[str, object]:
        use_days = case_days or days
        return {
            "case": "execution_clocks",
            "variant": variant,
            "definition": _account_definition(**{
                "start": date.fromisoformat(use_days[0]), "end": date.fromisoformat(use_days[-1]),
                **(definition_overrides or {}),
            }),
            "signals": signals if signals is not None else [
                {"signal_at": use_days[0], "executable_from": use_days[1], "code": "CODE_A", "target_weight": 0.5}
            ],
            "tables": {
                "calendar": _calendar_df(use_days),
                "daily_bars": _bars_df(bars if bars is not None else base_bars),
                "security_status": status if status is not None else _status_df(use_days, ("CODE_A",)),
                "historical_securities": _hist_df(securities if securities is not None else base_securities),
                "trading_rules": _rules_df(rules if rules is not None else base_rules),
                "corporate_actions": actions if actions is not None else _actions_df([]),
            },
        }

    if variant == "base":
        return case()
    if variant == "d2_close_volume":
        # 只改 d2 收盘与全天量：不得改变 d1 信号、d2 预先订单数量与开盘成交决策
        bars = [("CODE_A", days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[1], 10.0, 8.0, 500),
                ("CODE_A", days[2], 8.0, 8.0, 500),
                ("CODE_A", days[3], 8.0, 8.0, 500)]
        return case(bars=bars)
    if variant == "t1_sell_block":
        # d2 盘前：先按权重 1.0 买入成交，再按权重 0.0 清仓——新买股当日不可
        # 卖（T+1），卖出单被引擎拒绝；若 d3 再给退出信号则可正常卖出。
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 1.0},
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.0},
            {"signal_at": days[2], "executable_from": days[3], "code": "CODE_A", "target_weight": 0.0},
        ]
        return case(signals=signals)
    if variant == "suspended":
        # d2 全天零成交：d1 信号单当日不成交；d2 收盘再给同向信号，d3 开盘成交
        bars = [("CODE_A", days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[1], 10.0, 10.0, 0),
                ("CODE_A", days[2], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[3], 10.0, 10.0, 1_000_000)]
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.5},
            {"signal_at": days[1], "executable_from": days[2], "code": "CODE_A", "target_weight": 0.5},
        ]
        return case(bars=bars, signals=signals)
    if variant == "limit_up_buy":
        # d1 收盘 10.0，d2 开盘 11.0 = 涨停价（pct10）→ 买入保守拒绝
        bars = [("CODE_A", days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[1], 11.0, 11.0, 1_000_000),
                ("CODE_A", days[2], 11.0, 11.0, 1_000_000),
                ("CODE_A", days[3], 11.0, 11.0, 1_000_000)]
        return case(bars=bars)
    if variant == "limit_down_sell":
        # d2 买入，d3 开盘 9.0 = 跌停价（前收 10.0，pct10）→ 卖出保守拒绝
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.5},
            {"signal_at": days[1], "executable_from": days[2], "code": "CODE_A", "target_weight": 0.0},
        ]
        bars = [("CODE_A", days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[1], 10.0, 10.0, 1_000_000),
                ("CODE_A", days[2], 9.0, 9.0, 1_000_000),
                ("CODE_A", days[3], 9.0, 9.0, 1_000_000)]
        return case(bars=bars, signals=signals)
    if variant == "odd_lot_sell":
        # d2 买 500 股；d3 十送五（m=1.5）→ 750 股零股；d4 清仓一次性卖出 750
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.5},
            {"signal_at": days[2], "executable_from": days[3], "code": "CODE_A", "target_weight": 0.0},
        ]
        bars = [("CODE_A", day, 10.0, 10.0, 1_000_000) for day in days]
        actions = _actions_df([("CODE_A", days[2], "bonus_split", 1.5, 0.0, days[1], None, days[2])])
        return case(bars=bars, signals=signals, actions=actions)
    if variant == "star_qty":
        # 科创板 200 股起、1 股递增：10,000 元预算（费用预留后 999.5 股）申报
        # 999 股；同事实主板块规则下只能整手 900 股。
        securities = [("CODE_A", "2020-01-02", "SH", "star")]
        rules = [("SH", "star", "stock", "2020-01-01", "", "200", "1", "pct20", _ZERO_FEE)]
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 1.0}
        ]
        return case(rules=rules, securities=securities, signals=signals)
    if variant in ("chinext_before", "chinext_after"):
        # 创业板涨跌幅 10%→20%（2020-08-24 生效）：开盘 +12%，生效前拒绝、生效后成交
        # 成交日（第二天）须落在规则正确一侧：生效前 pct10、生效后 pct20
        switch_days = ["2020-08-21", "2020-08-24", "2020-08-25", "2020-08-26"]
        if variant == "chinext_before":
            switch_days = ["2020-08-18", "2020-08-19", "2020-08-20", "2020-08-21"]
        bars = [("CODE_A", switch_days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", switch_days[1], 11.2, 11.2, 1_000_000),
                ("CODE_A", switch_days[2], 11.2, 11.2, 1_000_000),
                ("CODE_A", switch_days[3], 11.2, 11.2, 1_000_000)]
        securities = [("CODE_A", "2020-01-02", "SZ", "chinext")]
        rules = [("SZ", "chinext", "stock", "2016-01-01", "2020-08-23", "100", "100", "pct10", _ZERO_FEE),
                 ("SZ", "chinext", "stock", "2020-08-24", "", "100", "100", "pct20", _ZERO_FEE)]
        return case(bars=bars, rules=rules, securities=securities, case_days=switch_days)
    if variant in ("st_switch_before", "st_switch_after"):
        # 主板 ST 涨跌幅 5%→10%（2026-07-06 生效）：开盘 +7%；成交日（第二天）
        # 须落在规则正确一侧：生效前 pct5、生效后 pct10
        switch_days = ["2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08"]
        if variant == "st_switch_before":
            switch_days = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]
        bars = [("CODE_A", switch_days[0], 10.0, 10.0, 1_000_000),
                ("CODE_A", switch_days[1], 10.7, 10.7, 1_000_000),
                ("CODE_A", switch_days[2], 10.7, 10.7, 1_000_000),
                ("CODE_A", switch_days[3], 10.7, 10.7, 1_000_000)]
        status = _status_df(switch_days, ("CODE_A",), st_days={"CODE_A": tuple(switch_days)})
        rules = [("SZ", "main", "stock_st", "2016-01-01", "2026-07-05", "100", "100", "pct5", _ZERO_FEE),
                 ("SZ", "main", "stock_st", "2026-07-06", "", "100", "100", "pct10", _ZERO_FEE)]
        return case(bars=bars, rules=rules, status=status, case_days=switch_days)
    if variant in ("fee_switch_before", "fee_switch_after"):
        # 印花税 1‰→0.5‰（2023-08-28 生效，按成交日取值）：卖出分别落在
        # 2023-08-25（1‰）与 2023-08-28（0.5‰）
        if variant == "fee_switch_before":
            switch_days = ["2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]
        else:
            switch_days = ["2023-08-24", "2023-08-25", "2023-08-28", "2023-08-29"]
        signals = [
            {"signal_at": switch_days[0], "executable_from": switch_days[1], "code": "CODE_A", "target_weight": 0.5},
            {"signal_at": switch_days[2], "executable_from": switch_days[3], "code": "CODE_A", "target_weight": 0.0},
        ]
        rules = [("SZ", "main", "stock", "2016-01-01", "2023-08-27", "100", "100", "pct10", _REAL_FEE),
                 ("SZ", "main", "stock", "2023-08-28", "", "100", "100", "pct10", _REAL_FEE_2023)]
        bars = [("CODE_A", day, 10.0, 10.0, 1_000_000) for day in switch_days]
        return case(bars=bars, signals=signals, rules=rules, case_days=switch_days)
    if variant == "capacity":
        # 此前 20 日中位量 100,000 × 1% = 1,000 股上限（现金本可买约 1,900 股）
        bars = [("CODE_A", day, 5.0, 5.0, 100_000) for day in days]
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 1.0}
        ]
        return case(bars=bars, signals=signals)
    if variant == "dividend_tax":
        # 差别化股息税：d2 买 1,000 股；除息每股 0.5 元；一月内卖出扣缴 20%
        tax_days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
                    "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17"]
        entry_weight = 10000.0 / 12000.0  # 目标市值恰 10,000 元 → 买 1,000 股
        signals = [
            {"signal_at": tax_days[0], "executable_from": tax_days[1], "code": "CODE_A", "target_weight": entry_weight},
            {"signal_at": tax_days[7], "executable_from": tax_days[8], "code": "CODE_A", "target_weight": 0.0},
        ]
        bars = [("CODE_A", day, 10.0, 10.0, 1_000_000) for day in tax_days]
        rules = [("SZ", "main", "stock", "2016-01-01", "", "100", "100", "pct10", _REAL_FEE)]
        actions = _actions_df([("CODE_A", tax_days[7], "cash_dividend", 1.0, 0.5, tax_days[6], tax_days[8], None)])
        definition = _account_definition(
            protocol=_account_protocol(initial_cash_cny=12000),
        )
        return {
            "case": "execution_clocks",
            "variant": variant,
            "definition": definition,
            "signals": signals,
            "tables": {
                "calendar": _calendar_df(tax_days),
                "daily_bars": _bars_df(bars),
                "security_status": _status_df(tax_days, ("CODE_A",)),
                "historical_securities": _hist_df(base_securities),
                "trading_rules": _rules_df(rules),
                "corporate_actions": actions,
            },
        }
    if variant == "costs_double":
        # 佣金／滑点翻倍：同一买入事实下费用实际变大（定义侧覆盖，事实不变）
        protocol = _account_protocol(
            initial_cash_cny=100000,
            costs={"commission_rate": 0.0006, "min_commission_cny": 5,
                   "slippage_rate": 0.001, "statutory_rules_ref": "phase2-trading-rules"},
        )
        bars = [("CODE_A", day, 10.0, 10.0, 1_000_000) for day in days]
        signals = [
            {"signal_at": days[0], "executable_from": days[1], "code": "CODE_A", "target_weight": 0.5}
        ]
        rules = [("SZ", "main", "stock", "2016-01-01", "", "100", "100", "pct10",
                  "stamp=sell:0.001;transfer=both:0;dividend=zero")]
        return case(bars=bars, signals=signals, rules=rules,
                    definition_overrides={"protocol": protocol})
    raise ValueError(f"unknown variant for case 'execution_clocks': {variant!r}")


def _formal_case(variant: str) -> dict[str, object]:
    """formal 分支端到端输入：月末日历＋全月末点行情，使 executable_from
    落在有 bar 的月末；信号日 2023-12-31（2024-01-31 无后续交易日不产信号）。
    12 只合成代码振幅递增，VOL_01/VOL_02 同振幅（并列事实）。
    """
    if variant == 'weekly_benchmark':
        import io

        case = _formal_case('base')
        codes = ('VOL_01', 'VOL_02')
        days = [str(d.date()) for d in pd.bdate_range('2024-01-01', '2024-02-02')]
        bars = _bars_df([(code, day, price, price, 1_000_000)
                        for code, price in zip(codes, (10.0, 20.0), strict=True) for day in days])
        caps = pd.DataFrame([dict(code=code, date=day, market_cap_cny=cap,
                                 available_at=f'{day}T18:00:00+08:00')
                             for code, cap in zip(codes, (100_000_000, 300_000_000), strict=True)
                             for day in days])
        csvs = dict(case['csvs'])
        for table in ('historical_securities', 'knowledge_evidence'):
            frame = pd.read_csv(io.StringIO(csvs[table]), dtype=str)
            key = 'code' if table == 'historical_securities' else 'record_key'
            csvs[table] = frame[frame[key].isin(codes)].to_csv(index=False)
        calendar = pd.date_range('2024-01-01', '2024-02-04')
        csvs.update(calendar=pd.DataFrame(dict(date=[str(d.date()) for d in calendar],
                    is_trading_day=[d.weekday() < 5 for d in calendar])).to_csv(index=False),
                    daily_bars=bars.to_csv(index=False), market_cap=caps.to_csv(index=False),
                    security_status=_status_df(days, codes).to_csv(index=False))
        definition = _account_definition(as_of=datetime(2024, 2, 4, 21, 0, tzinfo=CN),
            start=date(2024, 1, 8), end=date(2024, 2, 2), strategy_id='universe_vw',
            protocol=_account_protocol(warmup_months=0, rebalance={'frequency': 'weekly'}))
        return {**case, 'variant': variant, 'csvs': csvs, 'codes': codes,
                'evidence_refs': tuple(f'ev-sp-{code.lower()}' for code in codes),
                'obtained_at': '2024-02-04T18:00:00+08:00', 'definition': definition,
                'actions_coverage': {'start': '2024-01-01', 'end': '2024-02-02'}}
    if variant not in ("base", "diagnostics", "workflow"):
        raise ValueError(f"unknown variant for case 'formal': {variant!r}")
    # 行情自 2020-10 月末起：2023-12-31 信号（warmup 起点 2020-12-01）不触发
    # bars_warmup_gap；37 个收益点为 2021-02..2023-12。
    last_month = "2024-12" if variant in ("diagnostics", "workflow") else "2024-01"
    end_day = str(pd.Period(last_month).end_time.date())
    first_month = "2020-01" if variant == "workflow" else "2020-10"
    month_ends = [str(p.end_time.date()) for p in pd.period_range(first_month, last_month, freq="M")]
    bars: list[str] = []
    status: list[str] = []
    for code in SIGNAL_POOL_CODES:
        amplitude = _SIGNAL_AMPLITUDE[code]
        value = 100.0
        for index, day in enumerate(month_ends):
            if index > 0:
                value *= 1 + (amplitude if index % 2 == 1 else -amplitude)
            status.append(f"{day},{code},False,False,True")
            bars.append(
                f"{day},{code},{value:.2f},{value:.2f},{value:.2f},{value:.2f},"
                f"{value:.2f},{value:.2f},1000000,20000"
            )
    mcap: list[str] = []
    for index, code in enumerate(sorted(SIGNAL_POOL_CODES)):
        cap = (index + 1) * 1000000000
        for day in [day for day in month_ends if day >= "2023-12-31"]:
            available = (
                "2025-01-01T00:00:00+08:00" if code == "FUTURE_MCAP" else f"{day}T18:00:00+08:00"
            )
            mcap.append(f"{code},{day},{cap},{available}")
    hist_rows = "\n".join(
        f"{code},2020-01-02,,True,SH,main,2020-01-02T18:00:00+08:00,ev-sp-{code.lower()}"
        for code in SIGNAL_POOL_CODES
    )
    knowledge_rows = "\n".join(
        f"historical_securities,{code},orig,,2020-01-02T18:00:00+08:00,"
        f"ev-sp-{code.lower()},{_evidence_sha(f'ev-sp-{code.lower()}')},True"
        for code in SIGNAL_POOL_CODES
    )
    rules_rows = "\n".join(",".join(row) for row in _TRADING_RULES_ROWS)
    calendar_rows = "\n".join(f"{day},True" for day in month_ends)
    protocol = _account_protocol(
        stage="formal",
        warmup_months=36,
        initial_cash_cny=1000000,
        time_split={
            "development": ["2021-01-29", "2021-12-31"],
            "validation": ["2022-01-04", "2022-12-30"],
            "final": ["2023-12-31", end_day],
        },
        objectives={
            "income": "final_net_cagr_gt_0_and_excess_vs_universe_vw_gt_0",
            "risk": "ann_vol_lte_90pct_benchmark_and_maxdd_not_worse",
            "tolerance": "zero",
        },
        trial_budget={"max_runs": 8},
    )
    definition = _account_definition(
        as_of=datetime(2025, 1, 1, 21, 0, tzinfo=CN) if variant in ("diagnostics", "workflow") else SIGNAL_POOL_AS_OF,
        start=date(2024, 1, 2),
        end=date.fromisoformat(end_day),
        protocol=protocol,
    )
    if variant == "workflow":
        definition["start"] = date(2023, 12, 31)
        protocol["time_split"] = {
            "development": ["2023-12-31", "2024-03-31"],
            "validation": ["2024-04-01", "2024-07-31"],
            "final": ["2024-08-01", "2024-12-31"],
        }
    return {
        "case": "formal",
        "variant": variant,
        "csvs": {
            "calendar": f"date,is_trading_day\n{calendar_rows}\n",
            "daily_bars": (
                "date,code,open_raw,high_raw,low_raw,close_raw,"
                "close_price_return,close_total_return,volume_shares,amount_cny\n"
                + "\n".join(bars) + "\n"
            ),
            "security_status": "date,code,is_st,is_suspended,is_listed\n" + "\n".join(status) + "\n",
            "corporate_actions": "code,ex_date,action_type,m,d,terms_raw,available_at\n",
            "market_cap": "code,date,market_cap_cny,available_at\n" + "\n".join(mcap) + "\n",
            "historical_securities": (
                "code,valid_from,valid_to,listed,market,board,available_at,evidence_ref\n"
                + hist_rows + "\n"
            ),
            "trading_rules": (
                "rule_id,market,board,security_type,valid_from,valid_to,"
                "min_buy_qty,qty_step,tick_size,settlement,limit_rule,fee_rule,source_ref\n"
                + rules_rows + "\n"
            ),
            "knowledge_evidence": (
                "table_name,record_key,revision_id,supersedes,published_at,"
                "evidence_ref,sha256,verified\n" + knowledge_rows + "\n"
            ),
        },
        "table_products": dict(HISTORY_TABLE_PRODUCTS),
        "obtained_at": "2025-01-01T18:00:00+08:00" if variant == "workflow" else SIGNAL_POOL_OBTAINED,
        "definition": definition,
        "actions_coverage": {"start": "2020-01-01" if variant == "workflow" else "2020-12-01", "end": end_day},
        "codes": SIGNAL_POOL_CODES,
        "evidence_refs": tuple(f"ev-sp-{code.lower()}" for code in SIGNAL_POOL_CODES),
    }


def write_engine_bundle(bundle_dir: Path, case: dict[str, object]) -> None:
    """把引擎 case 事实写为生产布局 bundle（只落输入，不带期望）。"""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "admission.json").write_text(
        json.dumps({"decision": "candidate_ready"}), encoding="utf-8"
    )
    (bundle_dir / "definition.json").write_text(
        json.dumps(case["definition"], ensure_ascii=False, default=str), encoding="utf-8"
    )
    pd.DataFrame(case["signals"]).to_parquet(bundle_dir / "signals.parquet", index=False)
    evidence_dir = bundle_dir / "evidence"
    for name, data in case.get("evidence_files", {}).items():
        evidence_dir.mkdir(exist_ok=True)
        (evidence_dir / name).write_bytes(data)
    tables_dir = bundle_dir / "tables"
    tables_dir.mkdir()
    for name, frame in case["tables"].items():  # type: ignore[union-attr]
        frame.to_parquet(tables_dir / f"{name}.parquet", index=False)
