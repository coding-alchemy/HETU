"""北交所三产品（bj_basic/bj_daily/bj_actions）采集与标准化映射测试。

fixtures 为 2026-10-16 真实原始响应落盘（.hetu/quant/qualification/
capability-gap-20261016/bj-provider-verify.json 留证），只隔离网络、
不隔离真实标准化分支：位置/字段映射、单位换算与事件语义以真实形状钉住。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from hetu_quant.providers.akshare import EM_KLINE, AKShareProvider

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


@pytest.fixture
def provider() -> AKShareProvider:
    return AKShareProvider()


def test_bj_basic_standardize_maps_bse_fields(provider: AKShareProvider) -> None:
    """bse.cn 字典行 → securities：代码/简称/上市日期/股本/行业。"""
    raw = pd.read_parquet(FIXTURES / "bj_basic.parquet")
    securities = provider.standardize("bj_basic", raw, {"tables": ["securities"]})["securities"]
    first = securities.iloc[0]
    assert first["code"] == "920000.BJ" and first["name"] == "安徽凤凰"
    assert first["ipo_date"] == "2020-12-23", "YYYYMMDD 必须转 ISO 日期"
    assert int(first["raw_total_shares"]) == 91_680_000
    assert (securities["market"] == "BJ").all()
    assert (securities["security_type"] == "stock").all()


def test_bj_daily_volume_converts_hand_to_shares(provider: AKShareProvider) -> None:
    """东财成交量单位手（2026-09-01 万达轴承 11641 手）必须 ×100 转股；
    成交额保持元；金额/股数隐含均价须落在当日高低价内（单位自洽）。"""
    raw = pd.read_parquet(FIXTURES / "bj_daily.parquet")
    bars = provider.standardize("bj_daily", raw, {"tables": ["daily_bars"]})["daily_bars"]
    first = bars.iloc[0]
    assert first["date"] == "2026-09-01" and first["code"] == "920002.BJ"
    assert first["volume_shares"] == pytest.approx(1_164_100.0)
    assert first["amount_cny"] == pytest.approx(61_830_127.92)
    vwap = first["amount_cny"] / first["volume_shares"]
    assert first["low_raw"] <= vwap <= first["high_raw"], "单位不自洽：隐含均价须在日内区间"


def test_bj_actions_only_implemented_rows_become_events(provider: AKShareProvider) -> None:
    """无除权除息日的预案行不成事件；每 10 股口径换算每股；可得时点取预案公告日。"""
    raw = pd.read_parquet(FIXTURES / "bj_actions.parquet")
    actions = provider.standardize("bj_actions", raw, {"tables": ["corporate_actions"]})[
        "corporate_actions"
    ]
    assert (actions["ex_date"].astype(str) != "").all()
    by_key = {
        (row["code"], str(row["ex_date"]), row["action_type"]): row
        for _, row in actions.iterrows()
    }
    # 920002 2024-09-30 报告期 10派2：ex 2025-01-10、预案公告 2024-10-28
    cash = by_key[("920002.BJ", "2025-01-10", "cash_dividend")]
    assert cash["d"] == pytest.approx(0.2) and cash["m"] == pytest.approx(1.0)
    assert cash["available_at"] == "2024-10-28"
    # 2025-04-24 预案的 10转4派1：同除息日两条事件（现金 + 送转）
    split = by_key[("920002.BJ", "2026-05-27", "bonus_split")]
    assert split["m"] == pytest.approx(1.4)
    assert by_key[("920002.BJ", "2026-05-27", "cash_dividend")]["d"] == pytest.approx(0.1)


def test_bj_actions_conversion_matches_baostock_values(provider: AKShareProvider) -> None:
    """301059.SZ 2022 年报 10转9派3（BaoStock 实测 m=1.9、d=0.3，除息
    2023-03-14）：东财原始行经同一换算必须得到一致事件要素。"""
    saved = json.loads(
        (FIXTURES / "em-fhps-raw-301059.json")
        .read_text(encoding="utf-8")
    )
    raw = pd.DataFrame((saved.get("result") or {}).get("data") or [])
    actions = provider.standardize("bj_actions", raw, {"tables": ["corporate_actions"]})[
        "corporate_actions"
    ]
    split = actions[
        (actions["ex_date"].astype(str) == "2023-03-14") & (actions["action_type"] == "bonus_split")
    ]
    cash = actions[
        (actions["ex_date"].astype(str) == "2023-03-14") & (actions["action_type"] == "cash_dividend")
    ]
    assert float(split.iloc[0]["m"]) == pytest.approx(1.9)
    assert float(cash.iloc[0]["d"]) == pytest.approx(0.3)


def test_bj_daily_fetch_uses_pure_numeric_secid(provider: AKShareProvider) -> None:
    """BJ 的 secid 实测为 0.<纯数字>；拼接 .BJ 后缀（akshare 1.19.1 行为）
    返回空。传输替身只拦截 HTTP，构造逻辑走生产分支。"""
    payload = json.loads(
        (FIXTURES / "bj-kline-920002-raw.json")
        .read_text(encoding="utf-8")
    )
    captured: dict = {}

    def _stub(url, params):
        captured.update({"url": url, **params})
        return payload

    provider._get_json = _stub  # noqa: SLF001  只隔离网络
    raw = provider.fetch("bj_daily", ("920002.BJ",), date(2026, 9, 1), date(2026, 10, 13))
    assert captured["url"] == EM_KLINE
    assert captured["secid"] == "0.920002"
    assert captured["beg"] == "20260901" and captured["end"] == "20261013"
    assert len(raw) == 25 and raw.iloc[0]["date"] == "2026-09-01"
