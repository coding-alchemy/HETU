"""二期回测定义与引用：递归冻结、完整协议键与 JSON 编解码。

定义在构造时逐层深拷贝并冻结（映射为只读代理、序列为元组），
导出为独立的纯 JSON 数据；修改原输入或导出值均不改变已冻结定义，
后续修改必须另存新实验。旧类型与序列化形状不受影响。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from hetu_quant.models import (
    MODES,
    RESULT_STATUSES,
    _datetime_from_json,
    _datetime_to_json,
    _fail,
    require_cn_aware,
    validate_json_value,
)

BACKTEST_PROTOCOL_KEYS: tuple[str, ...] = (
    "schema_version",
    "stage",
    "universe",
    "warmup_months",
    "initial_cash_cny",
    "currency",
    "account_type",
    "leverage",
    "cash_interest_rate",
    "investor_tax_profile",
    "rebalance",
    "benchmark_id",
    "execution",
    "costs",
    "rules_ref",
    "time_split",
    "parameter_family",
    "objectives",
    "robustness",
    "trial_budget",
    "viewing_record",
)

BACKTEST_STAGES: tuple[str, ...] = ("mechanism", "formal")


def _freeze_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _freeze_mapping_deep(value: Mapping[str, object], path: str) -> Mapping[str, object]:
    """校验为 JSON 值并逐层冻结；内部序列规范化为元组。"""
    validate_json_value(value, path)
    copied = json.loads(json.dumps(value, ensure_ascii=False))
    frozen = _freeze_json_value(copied)
    assert isinstance(frozen, Mapping)
    return frozen


def _thaw_json_value(value: object) -> object:
    """还原为独立的纯 JSON 数据结构（dict／list），不与定义共享引用。"""
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class BacktestDefinition:
    as_of: datetime
    snapshot_id: str
    start: date
    end: date
    strategy_id: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    protocol: Mapping[str, object] = field(default_factory=dict)
    market: str = "CN_A"
    mode: Literal["free", "paid"] = "free"

    def __post_init__(self) -> None:
        require_cn_aware(self.as_of)
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            _fail("snapshot_id must be a non-empty string")
        if not isinstance(self.start, date) or not isinstance(self.end, date):
            _fail("start/end must be dates")
        if self.start > self.end:
            _fail(f"start {self.start} must not be after end {self.end}")
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            _fail("strategy_id must be a non-empty string")
        object.__setattr__(
            self, "parameters", _freeze_mapping_deep(self.parameters, "$.parameters")
        )
        if not isinstance(self.protocol, Mapping):
            _fail("protocol must be a mapping")
        frozen_protocol = _freeze_mapping_deep(self.protocol, "$.protocol")
        missing = [key for key in BACKTEST_PROTOCOL_KEYS if key not in frozen_protocol]
        if missing:
            _fail(f"protocol is missing required keys: {', '.join(missing)}")
        stage = frozen_protocol["stage"]
        if stage not in BACKTEST_STAGES:
            _fail(f"unknown protocol stage: {stage!r}")
        object.__setattr__(self, "protocol", frozen_protocol)
        if self.mode not in MODES:
            _fail(f"unknown mode: {self.mode!r}")
        if not isinstance(self.market, str) or not self.market:
            _fail("market must be a non-empty string")
        if self.market != "CN_A":
            _fail(f"market {self.market!r} not supported: only CN_A backtest is supported")


@dataclass(frozen=True)
class BacktestRef:
    run_id: str
    root: Path
    status: Literal["complete", "limited"]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            _fail("run_id must be a non-empty string")
        object.__setattr__(self, "root", Path(self.root))
        if self.status not in RESULT_STATUSES:
            _fail(f"unknown backtest status: {self.status!r}")


def backtest_definition_to_dict(definition: BacktestDefinition) -> dict[str, object]:
    return {
        "as_of": _datetime_to_json(definition.as_of),
        "snapshot_id": definition.snapshot_id,
        "start": definition.start.isoformat(),
        "end": definition.end.isoformat(),
        "strategy_id": definition.strategy_id,
        "parameters": _thaw_json_value(definition.parameters),
        "protocol": _thaw_json_value(definition.protocol),
        "market": definition.market,
        "mode": definition.mode,
    }


def backtest_definition_from_dict(payload: Mapping[str, object]) -> BacktestDefinition:
    return BacktestDefinition(
        as_of=_datetime_from_json(payload["as_of"]),
        snapshot_id=payload["snapshot_id"],
        start=date.fromisoformat(payload["start"]),
        end=date.fromisoformat(payload["end"]),
        strategy_id=payload["strategy_id"],
        parameters=payload.get("parameters", {}),
        protocol=payload.get("protocol", {}),
        market=payload.get("market", "CN_A"),
        mode=payload.get("mode", "free"),
    )
