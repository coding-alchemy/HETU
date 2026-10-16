"""一期公共类型与冻结合同。

所有字段使用不可变 dataclass；映射字段在构造时深拷贝并校验为 JSON 值，
后续编辑必须生成新定义，不依赖浅冻结保护旧数据。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

CN_TZ_NAME = "Asia/Shanghai"
CN_UTC_OFFSET = timedelta(hours=8)

PURPOSES: tuple[str, ...] = (
    "ranking_current",
    "ranking_historical",
    "low_volatility_preparation",
    "ep_value_preparation",
)

MODES: tuple[str, ...] = ("free", "paid")
JOB_STATES: tuple[str, ...] = (
    "queued",
    "running",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
    "interrupted",
)
RESULT_STATUSES: tuple[str, ...] = ("complete", "limited")


def _fail(message: str) -> None:
    raise ValueError(message)


def require_cn_aware(value: datetime, field_name: str = "as_of") -> None:
    """as_of 必须带 Asia/Shanghai 时区。"""
    if not isinstance(value, datetime):
        _fail(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        _fail(f"{field_name} must be timezone-aware ({CN_TZ_NAME})")
    if value.utcoffset() != CN_UTC_OFFSET or value.tzname() != "CST":
        _fail(f"{field_name} must carry {CN_TZ_NAME} timezone")


def validate_json_value(value: object, path: str = "$") -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{path} has non-string key {key!r}")
            validate_json_value(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_json_value(item, f"{path}[{index}]")
        return
    _fail(f"{path} is not a JSON value: {type(value).__name__}")


def freeze_mapping(value: Mapping[str, object], path: str = "$") -> Mapping[str, object]:
    """校验为 JSON 值并深拷贝为只读映射；内部 tuple 规范化为 list。"""
    validate_json_value(value, path)
    copied = json.loads(json.dumps(value, ensure_ascii=False))
    return MappingProxyType(copied)


def _str_tuple(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    items = tuple(value)
    if not all(isinstance(item, str) and item for item in items):
        _fail(f"{field_name} must be a tuple of non-empty strings")
    return items


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            _fail("snapshot_id must be a non-empty string")
        object.__setattr__(self, "root", Path(self.root))


@dataclass(frozen=True)
class DataRequest:
    product_ids: tuple[str, ...]
    codes: tuple[str, ...]
    start: date
    end: date
    as_of: datetime
    purpose: str
    mode: Literal["free", "paid"] = "free"
    # 可选：按产品覆盖 codes 的取数域（如成员产品用板块代码、行情用股票代码）；
    # 未列出的产品沿用 codes。缺省为空，行为与单一 codes 域完全一致。
    codes_by_product: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        product_ids = _str_tuple(self.product_ids, "product_ids")
        if not product_ids:
            _fail("product_ids must not be empty")
        object.__setattr__(self, "product_ids", product_ids)
        object.__setattr__(self, "codes", _str_tuple(self.codes, "codes"))
        if self.codes_by_product is None:
            object.__setattr__(self, "codes_by_product", MappingProxyType({}))
        else:
            if not isinstance(self.codes_by_product, Mapping):
                _fail("codes_by_product must be a mapping")
            domains: dict[str, tuple[str, ...]] = {}
            for key, value in self.codes_by_product.items():
                if not isinstance(key, str) or not key:
                    _fail("codes_by_product keys must be non-empty strings")
                domains[key] = _str_tuple(tuple(value), f"codes_by_product[{key!r}]")
            object.__setattr__(self, "codes_by_product", MappingProxyType(domains))
        if not isinstance(self.start, date) or not isinstance(self.end, date):
            _fail("start/end must be dates")
        if self.start > self.end:
            _fail(f"start {self.start} must not be after end {self.end}")
        require_cn_aware(self.as_of)
        if self.purpose not in PURPOSES:
            _fail(f"unknown purpose: {self.purpose!r}")
        if self.mode not in MODES:
            _fail(f"unknown mode: {self.mode!r}")

    def codes_for(self, product_id: str) -> tuple[str, ...]:
        """该产品的取数代码域；未覆盖的产品沿用公共 codes。"""
        if self.codes_by_product and product_id in self.codes_by_product:
            return self.codes_by_product[product_id]
        return self.codes


@dataclass(frozen=True)
class QualityReport:
    can_run: bool
    allowed_uses: tuple[str, ...]
    eligible_ids: tuple[str, ...]
    excluded: Mapping[str, tuple[str, ...]]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_uses", tuple(self.allowed_uses))
        object.__setattr__(self, "eligible_ids", tuple(self.eligible_ids))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        excluded = {
            str(key): _str_tuple(tuple(reasons), f"excluded[{key!r}]")
            for key, reasons in self.excluded.items()
        }
        object.__setattr__(self, "excluded", MappingProxyType(excluded))


@dataclass(frozen=True)
class RankingDefinition:
    as_of: datetime
    cutoff: date
    snapshot_id: str
    benchmark_id: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    mode: Literal["free", "paid"] = "free"
    market: str = "CN_A"
    # 保存定义时固化的当时完整有效协议（默认配置 merge 覆盖参数后的全量）；
    # 存在时 run_ranking 只消费冻结值，不再读取当前默认配置。
    frozen_protocol: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_cn_aware(self.as_of)
        if not isinstance(self.cutoff, date):
            _fail("cutoff must be a date")
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            _fail("snapshot_id must be a non-empty string")
        if not isinstance(self.benchmark_id, str) or not self.benchmark_id:
            _fail("benchmark_id must be a non-empty string")
        object.__setattr__(self, "parameters", freeze_mapping(self.parameters, "$.parameters"))
        if self.frozen_protocol is not None:
            if not isinstance(self.frozen_protocol, Mapping):
                _fail("frozen_protocol must be a mapping")
            object.__setattr__(
                self,
                "frozen_protocol",
                freeze_mapping(self.frozen_protocol, "$.frozen_protocol"),
            )
        if self.mode not in MODES:
            _fail(f"unknown mode: {self.mode!r}")
        if not isinstance(self.market, str) or not self.market:
            _fail("market must be a non-empty string")
        if self.market != "CN_A":
            _fail(f"market {self.market!r} not supported: only CN_A ranking is supported")


@dataclass(frozen=True)
class ResultRef:
    result_id: str
    root: Path
    status: Literal["complete", "limited"]

    def __post_init__(self) -> None:
        if not isinstance(self.result_id, str) or not self.result_id:
            _fail("result_id must be a non-empty string")
        object.__setattr__(self, "root", Path(self.root))
        if self.status not in RESULT_STATUSES:
            _fail(f"unknown result status: {self.status!r}")


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    definition_id: str
    submission_id: str
    attempt: int
    state: Literal[
        "queued",
        "running",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
        "interrupted",
    ]
    progress: float
    reason: str | None
    output: Path | None

    def __post_init__(self) -> None:
        for name in ("job_id", "definition_id", "submission_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                _fail(f"{name} must be a non-empty string")
        if not isinstance(self.attempt, int) or self.attempt < 1:
            _fail("attempt must be a positive integer")
        if self.state not in JOB_STATES:
            _fail(f"unknown job state: {self.state!r}")
        if not 0.0 <= float(self.progress) <= 1.0:
            _fail("progress must be within [0, 1]")
        object.__setattr__(self, "progress", float(self.progress))
        if self.output is not None:
            object.__setattr__(self, "output", Path(self.output))


def _datetime_to_json(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_json(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None and parsed.utcoffset() == CN_UTC_OFFSET:
        # JSON 往返只保留偏移；+08:00 一律还原为 Asia/Shanghai 时区，
        # 使重建对象仍满足 require_cn_aware。
        return parsed.astimezone(ZoneInfo(CN_TZ_NAME))
    return parsed


def data_request_to_dict(request: DataRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "product_ids": list(request.product_ids),
        "codes": list(request.codes),
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "as_of": _datetime_to_json(request.as_of),
        "purpose": request.purpose,
        "mode": request.mode,
    }
    if request.codes_by_product:
        payload["codes_by_product"] = {
            key: list(value) for key, value in request.codes_by_product.items()
        }
    return payload


def data_request_from_dict(payload: Mapping[str, object]) -> DataRequest:
    override = payload.get("codes_by_product")
    return DataRequest(
        product_ids=tuple(payload["product_ids"]),
        codes=tuple(payload["codes"]),
        start=date.fromisoformat(payload["start"]),
        end=date.fromisoformat(payload["end"]),
        as_of=_datetime_from_json(payload["as_of"]),
        purpose=payload["purpose"],
        mode=payload.get("mode", "free"),
        codes_by_product=dict(override) if isinstance(override, Mapping) else None,
    )


def ranking_definition_to_dict(definition: RankingDefinition) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of": _datetime_to_json(definition.as_of),
        "cutoff": definition.cutoff.isoformat(),
        "snapshot_id": definition.snapshot_id,
        "benchmark_id": definition.benchmark_id,
        "parameters": dict(definition.parameters),
        "mode": definition.mode,
        "market": definition.market,
    }
    if definition.frozen_protocol is not None:
        payload["frozen_protocol"] = dict(definition.frozen_protocol)
    return payload


def ranking_definition_from_dict(payload: Mapping[str, object]) -> RankingDefinition:
    frozen = payload.get("frozen_protocol")
    return RankingDefinition(
        as_of=_datetime_from_json(payload["as_of"]),
        cutoff=date.fromisoformat(payload["cutoff"]),
        snapshot_id=payload["snapshot_id"],
        benchmark_id=payload["benchmark_id"],
        parameters=payload.get("parameters", {}),
        mode=payload.get("mode", "free"),
        market=payload.get("market", "CN_A"),
        frozen_protocol=frozen if isinstance(frozen, Mapping) else None,
    )
