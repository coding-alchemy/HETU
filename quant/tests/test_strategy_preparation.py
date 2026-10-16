"""02 策略准备机制测试：适配继承边界、低波动预热与权重、E/P 时点资格。

同文件合成构造器只含规则事实（价格、状态、市值、财务公告时点）；
预期排序、权重与资格结论只留在断言侧。
"""

from __future__ import annotations

import json
import runpy
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import annotate_actions_coverage
from hetu_quant.data import DataStore
from hetu_quant.models import DataRequest

CN = ZoneInfo("Asia/Shanghai")
RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
AS_OF = datetime(2024, 6, 30, 21, 0, tzinfo=CN)
SIGNAL_DATE = date(2024, 6, 30)
MONTH_ENDS = [p.end_time.date() for p in pd.period_range("2021-06", "2024-06", freq="M")]
assert len(MONTH_ENDS) == 37
GAP_MISSING = date(2023, 1, 31)


# ---- 合成快照构造（只含事实） ----


def _product(kind: str, tables: list[str]) -> dict:
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


TABLE_PRODUCTS = {
    "calendar": ("test_calendar", "calendar"),
    "daily_bars": ("test_daily", "market"),
    "security_status": ("test_status", "market"),
    "corporate_actions": ("test_actions", "event"),
    "market_cap": ("test_mcap", "fundamental"),
    "financial_reports": ("test_fin", "fundamental"),
}


def _build_store(
    tmp_path: Path, csvs: dict[str, str], obtained_at: str = "2024-06-30T18:00:00+08:00"
) -> tuple[DataStore, object]:
    products = {}
    items = []
    files = []
    for table, csv_text in csvs.items():
        product_id, kind = TABLE_PRODUCTS[table]
        products[product_id] = _product(kind, [table])
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
                    "obtained_at": obtained_at,
                },
            }
        )
        path = tmp_path / f"{table}.csv"
        path.write_text(csv_text, encoding="utf-8")
        files.append(path)
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps({"schema_version": 1, "products": products}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = DataStore(tmp_path / "store", config_path=config_path)
    # CLI 消费同一冻结快照时从数据根目录读取同一配置
    (tmp_path / "store" / "data-products.json").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"mode": "free", "items": items}, ensure_ascii=False), encoding="utf-8"
    )
    ref = store.import_files(manifest_path, tuple(files))
    annotate_actions_coverage(store, ref, "2021-06-01", "2024-06-30",
                              query_basis="ex_date")
    return store, ref


def _alternating(amplitude: float, count: int) -> list[float]:
    return [amplitude if i % 2 == 0 else -amplitude for i in range(count)]


def _prices_from_returns(returns: list[float], base: float = 100.0) -> list[float]:
    prices = [base]
    for r in returns:
        prices.append(round(prices[-1] * (1 + r), 6))
    return prices


def _amplitude(code: str, overrides: dict[str, float]) -> float:
    if code in overrides:
        return overrides[code]
    if code.startswith("S") and code[1:].isdigit():
        return 0.03 + 0.001 * int(code[1:])
    return 0.01


def _pool_csvs(
    codes: list[str],
    caps: dict[str, float],
    amplitudes: dict[str, float] | None = None,
    short: str | None = None,
    gap: str | None = None,
) -> dict[str, str]:
    """月末价格、状态与市值事实；short 代码少一年预热，gap 代码缺一个月末且无状态。"""
    amplitudes = amplitudes or {}
    bar_rows: list[str] = []
    status_rows: list[str] = []
    mcap_rows: list[str] = []
    for code in codes:
        prices = _prices_from_returns(_alternating(_amplitude(code, amplitudes), 36))
        points = list(zip(MONTH_ENDS, prices, strict=False))
        if code == short:
            points = points[1:]
        for day, price in points:
            if code == gap and day == GAP_MISSING:
                continue
            bar_rows.append(f"{day},{code},{price},{price},1000,20000")
            status_rows.append(f"{day},{code},False,False,True")
        if code == short:
            # 首月末尚未上市（历史短的事实），与"无法区分停牌/缺采"不同
            status_rows.append(f"{MONTH_ENDS[0]},{code},False,False,False")
        mcap_rows.append(f"{code},{SIGNAL_DATE},{caps[code]},2024-06-28T18:00:00+08:00")
    return {
        "calendar": "date,is_trading_day\n"
        + "\n".join(f"{d},True" for d in MONTH_ENDS)
        + "\n",
        "daily_bars": "date,code,close_raw,close_total_return,volume_shares,amount_cny\n"
        + "\n".join(bar_rows)
        + "\n",
        "security_status": "date,code,is_st,is_suspended,is_listed\n"
        + "\n".join(status_rows)
        + "\n",
        "corporate_actions": "code,ex_date,action_type,m,d,terms_raw,available_at\n",
        "market_cap": "code,date,market_cap_cny,available_at\n"
        + "\n".join(mcap_rows)
        + "\n",
    }


def _load(script: str) -> dict:
    return runpy.run_path(str(RESEARCH_DIR / script))


# ---- 02.1 方法适配与经济协议 ----


@pytest.fixture
def method_case() -> tuple[dict, dict]:
    original = {
        "universe": "历史 MSCI 中国 A 股",
        "currency": "USD",
        "window": "36 个月月收益",
        "volatility": "月收益样本标准差",
        "weighting": "市值加权",
        "rebalance": "monthly",
        "direction": "long",
    }
    local = dict(original, rebalance="weekly")
    return original, local


@pytest.fixture
def incomplete_protocol() -> dict:
    return {
        "universe": "信号日仍上市 A 股普通股",
        "parameter_selection": "沿用原规则，未搜索",
        "training_window": "待定",
    }


def test_adaptation_cannot_inherit_external_validation(
    method_case: tuple[dict, dict], incomplete_protocol: dict
) -> None:
    namespace = _load("low_volatility.py")
    result = namespace["describe_method"](*method_case)
    assert result["method_status"] == "adapted"
    assert "rebalance" in result["adaptations"]
    assert result["local_validation"] == "not_tested"
    with pytest.raises(ValueError, match="incomplete_economic_protocol"):
        namespace["require_economic_protocol"](incomplete_protocol)


# ---- 02.2 低波动机制 ----

LOW_CODES = ["LOW_A", "LOW_B"] + [f"S{k:02d}" for k in range(3, 21)]
ALL_LOW_CODES = LOW_CODES + ["SHORT", "GAP"]
LOW_CAPS = {"LOW_A": 100, "LOW_B": 300}
LOW_PRODUCTS = ("test_calendar", "test_daily", "test_status", "test_actions", "test_mcap")


class _LowVolCase:
    def __init__(self, tmp_path: Path, variant: str = "base") -> None:
        self.tmp_path = tmp_path
        amplitudes = {"LOW_A": 0.05} if variant == "low_a_volatile" else {}
        csvs = _pool_csvs(
            list(ALL_LOW_CODES),
            {code: LOW_CAPS.get(code, 500) for code in ALL_LOW_CODES},
            amplitudes=amplitudes,
            short="SHORT",
            gap="GAP",
        )
        self.store, self.ref = _build_store(tmp_path, csvs)
        self._runs = 0

    def run(self, as_of: datetime = AS_OF):
        namespace = _load("low_volatility.py")
        self._runs += 1
        out = self.tmp_path / f"out{self._runs}"
        request = DataRequest(
            product_ids=LOW_PRODUCTS,
            codes=tuple(ALL_LOW_CODES),
            start=date(2021, 6, 1),
            end=SIGNAL_DATE,
            as_of=as_of,
            purpose="low_volatility_preparation",
        )
        report = namespace["prepare"](self.store, request, self.ref, out)
        signals_path = out / "signals.parquet"
        signals = (
            pd.read_parquet(signals_path) if signals_path.exists() else pd.DataFrame()
        )
        return report, signals


@pytest.fixture
def low_vol_case(tmp_path: Path) -> _LowVolCase:
    return _LowVolCase(tmp_path)


def test_low_volatility_requires_warmup_and_preserves_weights(
    low_vol_case: _LowVolCase,
) -> None:
    result, signals = low_vol_case.run()
    assert result["excluded"]["SHORT"] == ["insufficient_36_month_returns"]
    assert "unresolved_trading_status" in result["excluded"]["GAP"]
    assert signals.set_index("code")["target_weight"].to_dict() == {"LOW_A": 0.25, "LOW_B": 0.75}
    assert result["economic_status"] == "not_evaluated"


def test_month_end_dates_require_true_month_end() -> None:
    """反例（评审 D2）：日历含月中交易日时，月中点不得冒充月末；缺月与未完成月分别记录原因。"""
    namespace = _load("low_volatility.py")
    days = {d.isoformat() for d in MONTH_ENDS}
    days |= {d.date().isoformat() for d in pd.bdate_range("2024-06-03", "2024-06-14")}
    calendar = pd.DataFrame({"date": sorted(days), "is_trading_day": True})

    ends, reason = namespace["_month_end_dates"](calendar, date(2024, 6, 15))
    assert ends is None, "信号日落在月中：该月真实月末未到时点，整月拒绝"
    assert "2024-06" in reason and "2024-06-30" in reason

    ends, reason = namespace["_month_end_dates"](calendar, date(2024, 6, 30))
    assert reason is None
    assert ends == [d.isoformat() for d in MONTH_ENDS], "真实月末恰好等于信号日正常通过"

    incomplete = calendar[calendar["date"] != GAP_MISSING.isoformat()]
    ends, reason = namespace["_month_end_dates"](incomplete, date(2024, 6, 30))
    assert ends is None, "数据不足：缺月拒绝"
    assert "2023-01" in reason


def test_month_end_dates_reject_truncated_calendar() -> None:
    """反例（评审 R5）：日历截断在月内时，截断日不得冒充月末点；
    日历明确覆盖到该月之后时，该月真实月末（最后一个交易日）正常通过。"""
    namespace = _load("low_volatility.py")
    days = {d.isoformat() for d in MONTH_ENDS if d != date(2024, 6, 30)}
    days |= {d.date().isoformat() for d in pd.bdate_range("2024-06-03", "2024-06-14")}
    truncated = pd.DataFrame({"date": sorted(days), "is_trading_day": True})

    ends, reason = namespace["_month_end_dates"](truncated, date(2024, 6, 14))
    assert ends is None, "日历截断于 6-14：6 月完整性无法证明，不得产出月末点"
    assert "2024-06" in reason

    extended = pd.DataFrame(
        {"date": sorted(days | {"2024-06-28", "2024-07-01"}), "is_trading_day": True}
    )
    ends, reason = namespace["_month_end_dates"](extended, date(2024, 6, 28))
    assert reason is None
    assert ends[-1] == "2024-06-28", "日历覆盖到 6 月之后时，6 月真实月末通过"
    assert len(ends) == len(MONTH_ENDS)


def test_low_volatility_rejects_mid_month_signal_date(tmp_path: Path) -> None:
    """反例（评审 D2/R5）：日历与行情截断于 2024-06-14 时，6 月完整性无法证明，
    prepare 拒绝并记录原因，不产出信号。"""
    csvs = _pool_csvs(
        list(ALL_LOW_CODES),
        {code: LOW_CAPS.get(code, 500) for code in ALL_LOW_CODES},
        short="SHORT",
        gap="GAP",
    )
    # 日历与行情真实截断于 6-14：去掉 6-30 月末点、加 6-14 交易日，并补 6-14
    # 行情/状态/市值，使"截断日历"成为唯一缺陷（修复前旧实现会输出 signals_ready + 37 点）。
    cal_days = [d.isoformat() for d in MONTH_ENDS if d != date(2024, 6, 30)] + ["2024-06-14"]
    csvs["calendar"] = (
        "date,is_trading_day\n" + "\n".join(f"{d},True" for d in sorted(cal_days)) + "\n"
    )
    for code in ALL_LOW_CODES:
        price = _prices_from_returns(_alternating(_amplitude(code, {}), 36))[-1]
        csvs["daily_bars"] += f"2024-06-14,{code},{price},{price},1000,20000\n"
        csvs["security_status"] += f"2024-06-14,{code},False,False,True\n"
        csvs["market_cap"] += (
            f"{code},2024-06-14,{LOW_CAPS.get(code, 500)},2024-06-14T18:00:00+08:00\n"
        )
    store, ref = _build_store(tmp_path, csvs, obtained_at="2024-06-14T18:00:00+08:00")
    namespace = _load("low_volatility.py")
    request = DataRequest(
        product_ids=LOW_PRODUCTS,
        codes=tuple(ALL_LOW_CODES),
        start=date(2021, 6, 1),
        end=date(2024, 6, 14),
        as_of=datetime(2024, 6, 14, 21, 0, tzinfo=CN),
        purpose="low_volatility_preparation",
    )
    report = namespace["prepare"](store, request, ref, tmp_path / "out_mid_month")
    assert report["research_status"] == "blocked"
    assert report["data_window"]["reason"] == "calendar_incomplete"
    assert any("2024-06" in gap for gap in report["data_qualification"]["gaps"])
    assert not (tmp_path / "out_mid_month" / "signals.parquet").exists(), "月中截断不得产出信号"


def test_low_volatility_weekend_month_end_with_full_calendar(tmp_path: Path) -> None:
    """反例（复评 R5 回归）：完整日历含非交易日行（6-29/6-30 周末）时，
    覆盖证明看日历记录延伸而非最后交易日：6 月真实月末为最后交易日 6-28，
    prepare 正常产出信号。"""
    csvs = _pool_csvs(
        list(ALL_LOW_CODES),
        {code: LOW_CAPS.get(code, 500) for code in ALL_LOW_CODES},
        short="SHORT",
        gap="GAP",
    )
    # 真实周末月末：6 月交易日止于 6-28，日历记录（含非交易日）延伸到 6-30
    cal_days = [d.isoformat() for d in MONTH_ENDS if d != date(2024, 6, 30)] + [
        "2024-06-28",
    ]
    rows = [f"{d},True" for d in sorted(cal_days)] + ["2024-06-29,False", "2024-06-30,False"]
    csvs["calendar"] = "date,is_trading_day\n" + "\n".join(rows) + "\n"
    for code in ALL_LOW_CODES:
        price = _prices_from_returns(_alternating(_amplitude(code, {}), 36))[-1]
        csvs["daily_bars"] += f"2024-06-28,{code},{price},{price},1000,20000\n"
        csvs["security_status"] += f"2024-06-28,{code},False,False,True\n"
    store, ref = _build_store(tmp_path, csvs)
    namespace = _load("low_volatility.py")
    request = DataRequest(
        product_ids=LOW_PRODUCTS,
        codes=tuple(ALL_LOW_CODES),
        start=date(2021, 6, 1),
        end=SIGNAL_DATE,
        as_of=AS_OF,
        purpose="low_volatility_preparation",
    )
    report = namespace["prepare"](store, request, ref, tmp_path / "out_weekend_me")
    assert report["research_status"] == "signals_ready"
    assert report["data_window"]["month_end_points"] == len(MONTH_ENDS)
    signals = pd.read_parquet(tmp_path / "out_weekend_me" / "signals.parquet")
    assert signals.set_index("code")["target_weight"].to_dict() == {
        "LOW_A": 0.25,
        "LOW_B": 0.75,
    }


def test_low_volatility_passes_when_calendar_covers_past_signal_month(tmp_path: Path) -> None:
    """日历明确覆盖到信号月之后（7 月仍有记录）时，该月真实月末可作为月末点，
    prepare 正常产出信号（R5 正向口径不回退）。"""
    csvs = _pool_csvs(
        list(ALL_LOW_CODES),
        {code: LOW_CAPS.get(code, 500) for code in ALL_LOW_CODES},
        short="SHORT",
        gap="GAP",
    )
    csvs["calendar"] += "2024-07-01,True\n2024-07-02,True\n"
    store, ref = _build_store(tmp_path, csvs)
    namespace = _load("low_volatility.py")
    request = DataRequest(
        product_ids=LOW_PRODUCTS,
        codes=tuple(ALL_LOW_CODES),
        start=date(2021, 6, 1),
        end=SIGNAL_DATE,
        as_of=AS_OF,
        purpose="low_volatility_preparation",
    )
    report = namespace["prepare"](store, request, ref, tmp_path / "out_extended")
    assert report["research_status"] == "signals_ready"
    assert report["data_window"]["month_end_points"] == len(MONTH_ENDS)
    signals = pd.read_parquet(tmp_path / "out_extended" / "signals.parquet")
    assert signals.set_index("code")["target_weight"].to_dict() == {
        "LOW_A": 0.25,
        "LOW_B": 0.75,
    }


def test_low_volatility_signal_follows_input_change(tmp_path: Path) -> None:
    """原始指标变化必须引起相应信号变化：LOW_A 波动放大后跌出最低十分位。"""
    case = _LowVolCase(tmp_path, variant="low_a_volatile")
    _, signals = case.run()
    weights = signals.set_index("code")["target_weight"].to_dict()
    assert "LOW_A" not in weights
    assert "LOW_B" in weights
    assert sum(weights.values()) == pytest.approx(1.0)


def test_low_volatility_builds_total_return_from_raw_and_actions(tmp_path: Path) -> None:
    """消费侧总收益构建（2026-10-16 适配）：DIV 原始收盘在除息月含 -10% 机械
    跳变、现金行动重建后恢复 ±1% 交替，波动与无事件对照一致；配股等未核验
    行动类型只排除受影响对象，不中断运行、不硬套公式。"""
    codes = list(LOW_CODES) + ["DIV", "UNK"]
    caps = {code: LOW_CAPS.get(code, 500) for code in codes}
    csvs = _pool_csvs(codes, caps)
    baseline = _prices_from_returns(_alternating(0.01, 36))
    ex_index = 13
    header, *rows = csvs["daily_bars"].splitlines()
    rows = [row for row in rows if row.split(",")[1] != "DIV"]
    for day, price in zip(MONTH_ENDS, baseline, strict=True):
        raw = price if day <= MONTH_ENDS[ex_index - 1] else round(price * 0.9, 6)
        rows.append(f"{day.isoformat()},DIV,{raw},{raw},1000,20000")
    csvs["daily_bars"] = "\n".join([header, *rows]) + "\n"
    # 除息日总收益因子须还原基线收益 1.01：d = 0.101×前月末基线价
    div_cash = round(0.101 * baseline[ex_index - 1], 6)
    ex_day = MONTH_ENDS[ex_index].isoformat()
    csvs["corporate_actions"] = (
        "code,ex_date,action_type,m,d,terms_raw,available_at\n"
        f"DIV,{ex_day},cash_dividend,1.0,{div_cash},10派X,2024-05-20\n"
        f"UNK,{ex_day},rights_offer,1.5,2.0,配股,2024-05-20\n"
    )
    store, ref = _build_store(tmp_path, csvs)
    namespace = _load("low_volatility.py")
    request = DataRequest(
        product_ids=LOW_PRODUCTS,
        codes=tuple(codes),
        start=date(2021, 6, 1),
        end=SIGNAL_DATE,
        as_of=AS_OF,
        purpose="low_volatility_preparation",
    )
    out = tmp_path / "out"
    report = namespace["prepare"](store, request, ref, out)
    assert report["research_status"] == "signals_ready"
    signals = pd.read_parquet(out / "signals.parquet")
    vol_by_code = signals.set_index("code")["signal"].to_dict()
    assert "DIV" in vol_by_code, "现金行动经总收益重建后机械跳变不进入波动"
    assert vol_by_code["DIV"] == pytest.approx(vol_by_code["LOW_A"], rel=1e-6), (
        "重建后与无事件 ±1% 交替对象的波动一致"
    )
    assert set(vol_by_code) == {"DIV", "LOW_A"}, "同波动并列按代码截取"
    assert signals.set_index("code")["target_weight"]["DIV"] == pytest.approx(500 / 600)
    unhandled = [str(reason) for reasons in report["excluded"].values() for reason in reasons]
    assert any(reason.startswith("unverified_action") for reason in unhandled), (
        "未核验行动类型必须以对象级受限记录"
    )
    assert "UNK" not in vol_by_code


def test_later_as_of_does_not_revoke_saved_report(low_vol_case: _LowVolCase) -> None:
    report1, signals1 = low_vol_case.run(AS_OF)
    out1 = low_vol_case.tmp_path / "out1"
    before = (out1 / "preparation.json").read_bytes()
    _, signals2 = low_vol_case.run(datetime(2024, 6, 30, 23, 0, tzinfo=CN))
    assert (out1 / "preparation.json").read_bytes() == before, "旧实验报告不被改写"
    assert report1["research_status"] == "signals_ready"
    assert (
        signals2.set_index("code")["target_weight"].to_dict()
        == signals1.set_index("code")["target_weight"].to_dict()
    )


def test_low_volatility_cli_consumes_frozen_snapshot(
    low_vol_case: _LowVolCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "cli_run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "low_volatility.py",
            "--data-root",
            str(low_vol_case.store.root),
            "--snapshot-id",
            low_vol_case.ref.snapshot_id,
            "--as-of",
            "2024-06-30T21:00:00+08:00",
            "--signal-date",
            "2024-06-30",
            "--out",
            str(out),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(RESEARCH_DIR / "low_volatility.py"), run_name="__main__")
    assert exc_info.value.code == 0
    report = json.loads((out / "preparation.json").read_text(encoding="utf-8"))
    signals = pd.read_parquet(out / "signals.parquet")
    assert report["research_status"] == "signals_ready"
    assert report["data_window"]["signal_date"] == "2024-06-30"
    assert signals.set_index("code")["target_weight"].to_dict() == {
        "LOW_A": 0.25,
        "LOW_B": 0.75,
    }


# ---- 02.3 E/P 条件路径 ----

FIN_CODES = ["KNOWN", "MISSING_TIME"] + [f"F{k:02d}" for k in range(3, 11)]
FIN_CAPS = {
    "KNOWN": 100,
    "MISSING_TIME": 150,
    "F03": 50,
    "F04": 60,
    "F05": 70,
    "F06": 200,
    "F07": 300,
    "F08": 400,
    "F09": 500,
    "F10": 600,
}
EP_PRODUCTS = LOW_PRODUCTS + ("test_fin",)


def _financial_csv(variant: str) -> str:
    rows = []
    for code in FIN_CODES:
        profit = 8 if code == "KNOWN" else 10
        ann = "2024-04-20"
        available = "2024-04-20T00:00:00+08:00"
        if code == "MISSING_TIME":
            ann = ""
            available = ""
        if variant == "all_late":
            available = "2024-07-05T00:00:00+08:00"
        rows.append(f"{code},2024Q1,{ann},{available},{profit}")
    if variant == "base":
        # KNOWN 的晚修订：截止后公开，不得覆盖截止前已知版本
        rows.append("KNOWN,2024Q1,2024-04-20,2024-07-05T00:00:00+08:00,80")
    return (
        "code,report_period,ann_date,available_at,net_profit_ex\n" + "\n".join(rows) + "\n"
    )


class _FinancialCase:
    def __init__(self, tmp_path: Path, variant: str = "base") -> None:
        self.tmp_path = tmp_path
        csvs = _pool_csvs(list(FIN_CODES), FIN_CAPS)
        csvs["financial_reports"] = _financial_csv(variant)
        self.store, self.ref = _build_store(tmp_path, csvs)
        self._runs = 0

    def _request(self, purpose: str, products: tuple[str, ...]) -> DataRequest:
        return DataRequest(
            product_ids=products,
            codes=tuple(FIN_CODES),
            start=date(2021, 6, 1),
            end=SIGNAL_DATE,
            as_of=AS_OF,
            purpose=purpose,
        )

    def run_ep(self) -> dict:
        namespace = _load("ep_value.py")
        self._runs += 1
        out = self.tmp_path / f"ep_out{self._runs}"
        report = namespace["prepare"](
            self.store, self._request("ep_value_preparation", EP_PRODUCTS), self.ref, out
        )
        signals_path = out / "signals.parquet"
        if signals_path.exists():
            signals = pd.read_parquet(signals_path).set_index("code")
        else:
            signals = pd.DataFrame(columns=["signal", "target_weight"]).rename_axis("code")
        return dict(report, signals=signals)

    def run_low_vol(self) -> dict:
        namespace = _load("low_volatility.py")
        self._runs += 1
        out = self.tmp_path / f"lv_out{self._runs}"
        return namespace["prepare"](
            self.store, self._request("low_volatility_preparation", LOW_PRODUCTS), self.ref, out
        )


@pytest.fixture
def financial_case(tmp_path: Path) -> _FinancialCase:
    return _FinancialCase(tmp_path, "base")


def test_late_financials_block_only_ep(financial_case: _FinancialCase) -> None:
    ep = financial_case.run_ep()
    assert ep["signals"].loc["KNOWN", "signal"] == pytest.approx(0.08)
    assert "MISSING_TIME" not in ep["signals"].index
    assert ep["economic_status"] == "not_evaluated"
    assert financial_case.run_low_vol()["research_status"] == "signals_ready"


def test_ep_blocked_when_no_pit_financials(tmp_path: Path) -> None:
    """全部财务公告晚于截止：E/P 受阻且无信号；低波动资格独立继续。"""
    case = _FinancialCase(tmp_path, "all_late")
    ep = case.run_ep()
    assert ep["research_status"] == "blocked"
    assert len(ep["signals"]) == 0
    assert ep["untested"] or ep["data_qualification"]
    assert case.run_low_vol()["research_status"] == "signals_ready"


@pytest.mark.parametrize("variant", [
    "other_code", "missing_span", "short_span", "short_end",
    "announcement_year", "unknown_basis", "failed",
])
def test_low_volatility_rejects_unproven_action_coverage(tmp_path: Path, variant: str) -> None:
    case = _LowVolCase(tmp_path)
    path = case.store.root / "snapshots" / case.ref.snapshot_id / "manifest.json"
    doc = json.loads(path.read_text())
    for entry in doc["table_files"]:
        if entry.get("table") != "corporate_actions":
            continue
        if variant == "other_code":
            entry["partition"] = "code=OTHER"
        elif variant == "missing_span":
            entry.pop("coverage", None)
        elif variant == "short_span":
            entry["coverage"] = ["2022-01-01", "2024-06-30"]
        elif variant == "short_end":
            entry["coverage"] = ["2021-06-01", "2024-05-31"]
        elif variant == "announcement_year":
            entry["query_basis"] = "announcement_year"
        elif variant == "unknown_basis":
            entry.pop("query_basis", None)
    if variant == "failed":
        doc["failures"] = [{"product_id": "test_actions"}]
    path.write_text(json.dumps(doc))
    report, signals = case.run()
    assert report["research_status"] == "blocked"
    assert signals.empty
    assert all(any("corporate_actions_coverage" in reason for reason in report["excluded"][code])
               for code in ALL_LOW_CODES)


def test_low_volatility_action_coverage_exclusion_is_object_specific(tmp_path: Path) -> None:
    case = _LowVolCase(tmp_path)
    path = case.store.root / "snapshots" / case.ref.snapshot_id / "manifest.json"
    doc = json.loads(path.read_text())
    doc["table_files"] = [e for e in doc["table_files"]
                          if not (e.get("table") == "corporate_actions"
                                  and e.get("partition") == "code=LOW_A")]
    path.write_text(json.dumps(doc))
    report, signals = case.run()
    assert report["research_status"] == "signals_ready"
    assert "corporate_actions_coverage" in report["excluded"]["LOW_A"][0]
    assert set(signals["code"]) == {"LOW_B"}


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("legacy_basis", ["ex_date", "unknown", "announcement_year"])
def test_operate_query_reaches_low_volatility_through_real_prepare(
    tmp_path, monkeypatch, empty, legacy_basis
):
    """行动只替换 SDK 网络；真实 fetch/standardize/prepare/资格门，不补 manifest。"""
    import io

    from hetu_quant.providers.baostock import BaoStockProvider

    codes = [f"600{i:03d}.SH" for i in range(1, 22)]
    csvs = _pool_csvs(codes, {code: 100 for code in codes})
    frames = {TABLE_PRODUCTS[t][0]: pd.read_csv(io.StringIO(text), dtype=str)
              for t, text in csvs.items() if t != "corporate_actions"}

    class Inputs:
        def fetch(self, product_id, codes, start, end):
            frame = frames[product_id].copy()
            if "code" in frame:
                frame = frame[frame["code"].isin(codes)]
            if "date" in frame:
                frame = frame[frame["date"].between(str(start), str(end))]
            return frame

    class Result:
        error_code = "0"
        error_msg = ""
        fields = ["code", "dividOperateDate", "dividStocksPs", "dividReserveToStockPs",
                  "dividCashPsBeforeTax", "dividPreNoticeDate", "dividCashStock"]

        def __init__(self, code, year):
            num, market = code.split(".")
            self.rows = [] if empty or year != "2024" else [
                [f"{market}.{num}", "2024-05-31", "0", "0", "0.1", "2024-04-20", "10派1"]]

        def next(self):
            return bool(self.rows)

        def get_row_data(self):
            return self.rows.pop(0)

    class SDK:
        calls = []

        def query_dividend_data(self, code, year, yearType):
            self.calls.append((code, year, yearType))
            assert yearType == "operate"
            return Result(code, year)

    # 固定合成输入的取得时间，避免本机日期干扰2024信号的时点测试。
    class Clock(datetime):
        tick = 0

        @classmethod
        def now(cls, tz=None):
            cls.tick += 1
            return AS_OF - timedelta(hours=1) + timedelta(microseconds=cls.tick)

    monkeypatch.setattr("hetu_quant.data.datetime", Clock)
    provider = BaoStockProvider()
    provider._bs = SDK()
    products = {pid: dict(_product(kind, [table]), provider="inputs")
                for table, (pid, kind) in TABLE_PRODUCTS.items() if pid in frames}
    products["cn_a_actions"] = dict(_product("event", ["corporate_actions"]), provider="baostock")
    config = tmp_path / "products.json"
    config.write_text(json.dumps({"products": products}))
    store = DataStore(tmp_path / "store", config_path=config,
                      providers={"inputs": Inputs(), "baostock": provider})

    def request(end, selected=codes[:20]):
        return DataRequest(product_ids=tuple(products), codes=tuple(selected), start=date(2021, 6, 1),
                           end=end, as_of=AS_OF, purpose="low_volatility_preparation")

    partial = store.prepare(request(date(2023, 12, 31)))
    if legacy_basis != "ex_date":
        # 仅负例模拟旧资产形状；生产链路正例不修改清单。
        path = store.root / "snapshots" / partial.snapshot_id / "manifest.json"
        doc = json.loads(path.read_text())
        for entry in doc["table_files"]:
            if entry["table"] == "corporate_actions" and entry["partition"] == f"code={codes[0]}":
                entry.pop("query_coverage", None)
                entry.pop("query_basis", None)
                if legacy_basis == "announcement_year":
                    entry["query_basis"] = legacy_basis
        path.write_text(json.dumps(doc))
    ref = store.prepare(request(SIGNAL_DATE))  # 合并旧段与新查询段
    entries = [e for e in store.load_manifest(ref)["table_files"] if e["table"] == "corporate_actions"]
    assert len(entries) == 20
    for entry in entries:
        legacy = legacy_basis != "ex_date" and entry["partition"] == f"code={codes[0]}"
        assert (entry.get("query_basis") == "ex_date") == (not legacy)
    assert all(e["coverage"] == ["2021-06-01", "2024-06-30"] for e in entries)
    report = _load("low_volatility.py")["prepare"](store, request(SIGNAL_DATE), ref, tmp_path / "out")
    assert report["research_status"] == "signals_ready"
    if legacy_basis != "ex_date":
        assert "corporate_actions_coverage" in report["excluded"][codes[0]][0]
        gate = _load("low_volatility.py")["_action_coverage_reason"]
        assert gate(store.load_manifest(ref), codes[0], "2024-01-01", "2024-06-30") == ""
    copied = store.prepare(request(SIGNAL_DATE, codes))  # 新增一代码，强制复制既有20个分区
    copied_entries = [e for e in store.load_manifest(copied)["table_files"]
                      if e["table"] == "corporate_actions"]
    for entry in copied_entries:
        legacy = legacy_basis != "ex_date" and entry["partition"] == f"code={codes[0]}"
        assert (entry.get("query_basis") == "ex_date") == (not legacy)
    first = next(e for e in entries if e["partition"] == f"code={codes[0]}")
    copied_first = next(e for e in copied_entries if e["partition"] == f"code={codes[0]}")
    assert copied_first.get("query_coverage") == first.get("query_coverage")
    raw_items = [i for i in store.load_manifest(ref)["items"] if i["product_id"] == "cn_a_actions"]
    assert raw_items and all(r["query_evidence"][0]["yearType"] == "operate"
                             for i in raw_items for r in i["raw_files"])
    reopened = DataStore(store.root, config_path=config, providers={"inputs": Inputs(), "baostock": provider})
    before = len(provider._bs.calls)
    actions_only = DataRequest(product_ids=("cn_a_actions",), codes=tuple(codes),
                               start=date(2021, 6, 1), end=SIGNAL_DATE, as_of=AS_OF,
                               purpose="low_volatility_preparation")
    assert reopened.prepare(actions_only) == copied
    assert len(provider._bs.calls) == before
    assert all(c[2] == "operate" for c in provider._bs.calls)
    assert partial != ref
