"""二期 01.1 回测定义合同：递归冻结、完整协议键与 JSON 编解码往返。

输入只含事实；冻结语义、协议键集合等期望留在本测试侧。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from hetu_quant.backtest_models import (
    BACKTEST_PROTOCOL_KEYS,
    BacktestDefinition,
    BacktestRef,
    backtest_definition_from_dict,
    backtest_definition_to_dict,
)

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 10, 17, 9, 0, tzinfo=CN)
START = date(2024, 1, 1)
END = date(2026, 10, 13)


def make_protocol(stage: str = "mechanism") -> dict[str, object]:
    """构造包含全部固定顶层键的最小协议事实，不含期望结论。"""
    return {
        "schema_version": 1,
        "stage": stage,
        "universe": {"rule": "synthetic_history"},
        "warmup_months": 36,
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
        "time_split": {"final": ["2024-01-01", "2026-10-13"]},
        "parameter_family": {"lookback_months": [36]},
        "objectives": {"income": "net_cagr_gt_0"},
        "robustness": {"scenarios": ["double_costs"]},
        "trial_budget": {"max_runs": 8},
        "viewing_record": {"final_seen": False},
    }


def make_definition(**overrides: object) -> BacktestDefinition:
    facts: dict[str, object] = {
        "as_of": AS_OF,
        "snapshot_id": "prep-synthetic-01",
        "start": START,
        "end": END,
        "strategy_id": "low_volatility",
        "parameters": {"lookback_months": 36, "windows": {"short": [20, 60]}},
        "protocol": make_protocol(),
    }
    facts.update(overrides)
    return BacktestDefinition(**facts)  # type: ignore[arg-type]


def test_backtest_definition_frozen_roundtrip() -> None:
    parameters: dict[str, object] = {"lookback_months": 36, "notes": ["a"]}
    protocol = make_protocol()
    definition = make_definition(parameters=parameters, protocol=protocol)

    # 保存定义后修改原映射（含嵌套结构），不得改变已冻结定义。
    frozen_parameters = {"lookback_months": 36, "notes": ("a",)}
    parameters["lookback_months"] = 30
    parameters["notes"].append("b")
    protocol["warmup_months"] = 42
    protocol["execution"]["fill_time"] = "same_close"

    payload = json.loads(json.dumps(backtest_definition_to_dict(definition), ensure_ascii=False))
    restored = backtest_definition_from_dict(payload)

    assert restored.parameters == frozen_parameters
    assert restored.protocol["warmup_months"] == 36
    assert restored.protocol["execution"]["fill_time"] == "next_open"
    assert restored == definition
    assert restored.as_of.utcoffset().total_seconds() == 8 * 3600


def test_backtest_definition_rejects_nested_mutation() -> None:
    definition = make_definition()
    with pytest.raises(TypeError):
        definition.parameters["lookback_months"] = 12  # type: ignore[index]
    with pytest.raises(TypeError):
        definition.protocol["stage"] = "formal"  # type: ignore[index]
    # 嵌套字典同样只读。
    with pytest.raises(TypeError):
        definition.protocol["execution"]["fill_time"] = "same_close"  # type: ignore[index]
    # 嵌套列表冻结为不可变序列。
    with pytest.raises(AttributeError):
        definition.parameters["windows"]["short"].append(120)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        definition.protocol["robustness"]["scenarios"].append("half_capacity")  # type: ignore[attr-defined]


def test_backtest_definition_export_is_independent() -> None:
    definition = make_definition()
    exported = backtest_definition_to_dict(definition)
    # 导出的是独立 JSON 数据，修改导出值不得反改原定义。
    exported["protocol"]["costs"]["commission_rate"] = 0.9  # type: ignore[index]
    exported["parameters"]["windows"]["short"].append(999)  # type: ignore[union-attr]
    assert definition.protocol["costs"]["commission_rate"] == 0.0003
    assert definition.parameters["windows"]["short"] == (20, 60)
    # 导出值须为纯 JSON 结构，不残留只读代理。
    assert not isinstance(exported["parameters"], MappingProxyType)
    json.dumps(exported, ensure_ascii=False)


def test_backtest_definition_requires_complete_protocol() -> None:
    for missing_key in BACKTEST_PROTOCOL_KEYS:
        protocol = make_protocol()
        protocol.pop(missing_key)
        with pytest.raises(ValueError, match="protocol"):
            make_definition(protocol=protocol)


def test_backtest_definition_validates_stage_and_scope() -> None:
    protocol = make_protocol(stage="exploratory")
    with pytest.raises(ValueError, match="stage"):
        make_definition(protocol=protocol)
    with pytest.raises(ValueError, match="market"):
        make_definition(market="HK")
    with pytest.raises(ValueError, match="mode"):
        make_definition(mode="trial")
    with pytest.raises(ValueError, match="start"):
        make_definition(start=END, end=START)
    with pytest.raises(ValueError, match="strategy_id"):
        make_definition(strategy_id="")
    with pytest.raises(ValueError, match="as_of"):
        make_definition(as_of=datetime(2026, 10, 17, 9, 0))


def test_backtest_ref_contract() -> None:
    ref = BacktestRef(run_id="run-1", root="/tmp/x", status="complete")
    assert ref.run_id == "run-1"
    with pytest.raises(ValueError, match="status"):
        BacktestRef(run_id="run-1", root="/tmp/x", status="partial")
    with pytest.raises(ValueError, match="run_id"):
        BacktestRef(run_id="", root="/tmp/x", status="limited")
