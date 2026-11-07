from typing import Literal

from hetu_data.models import (
    CN_TZ_NAME,
    CN_UTC_OFFSET,
    JOB_STATES,
    MODES,
    PURPOSES,
    RESULT_STATUSES,
    DataRequest,
    JobRecord,
    Mapping,
    MappingProxyType,
    Path,
    QualityReport,
    SnapshotRef,
    ZoneInfo,
    _datetime_from_json,
    _datetime_to_json,
    _fail,
    _str_tuple,
    data_request_from_dict,
    data_request_to_dict,
    dataclass,
    date,
    datetime,
    field,
    freeze_mapping,
    json,
    require_cn_aware,
    timedelta,
    validate_json_value,
)


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


__all__ = [
    "CN_TZ_NAME",
    "CN_UTC_OFFSET",
    "DataRequest",
    "JOB_STATES",
    "JobRecord",
    "Literal",
    "MODES",
    "Mapping",
    "MappingProxyType",
    "PURPOSES",
    "Path",
    "QualityReport",
    "RESULT_STATUSES",
    "RankingDefinition",
    "ResultRef",
    "SnapshotRef",
    "ZoneInfo",
    "_datetime_from_json",
    "_datetime_to_json",
    "_fail",
    "_str_tuple",
    "data_request_from_dict",
    "data_request_to_dict",
    "dataclass",
    "date",
    "datetime",
    "field",
    "freeze_mapping",
    "json",
    "ranking_definition_from_dict",
    "ranking_definition_to_dict",
    "require_cn_aware",
    "timedelta",
    "validate_json_value",
]
