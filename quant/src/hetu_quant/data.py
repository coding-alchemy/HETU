from hetu_data.store import (
    CN,
    CODELESS_KINDS,
    FREE_PERMISSION_STATUSES,
    SUPPORTED_ACTIONS,
    TABLE_SCHEMAS,
    AccessDenied,
    DataQualityError,
    DataRequest,
    Mapping,
    Path,
    QualityReport,
    SnapshotRef,
    SnapshotTamperedError,
    TypedDict,
    UnsupportedActionError,
    ZoneInfo,
    _action_query_records,
    _build_continuous,
    _date_gaps,
    _event_factors,
    _month_bounds,
    _month_keys,
    _new_snapshot_id,
    _ProductPlan,
    _safe_name,
    _sha256,
    _write_json,
    action_query_covers,
    assert_single_return_basis,
    build_price_series,
    build_total_return_series,
    check_product_access,
    data_request_from_dict,
    data_request_to_dict,
    date,
    datetime,
    duckdb,
    hashlib,
    json,
    pd,
    query_column,
    shutil,
    timedelta,
    uuid,
)
from hetu_data.store import DataStore as KernelDataStore

from hetu_quant.data_qualification import PURPOSE_TABLES, _persist_qualification, qualify_data


class DataStore(KernelDataStore):
    def __init__(self, root, config_path=None, providers=None):
        legacy = Path(__file__).resolve().parents[2] / "config/data-products.json"
        if config_path is None and not (Path(root) / "data-products.json").exists() and legacy.exists():
            config_path = legacy
        super().__init__(root, config_path, providers)

    def qualify(self, request, ref):
        return qualify_data(self, request, ref)

    def _persist_qualification(self, request, ref, report):
        return _persist_qualification(self, request, ref, report)


__all__ = [
    "AccessDenied",
    "CN",
    "CODELESS_KINDS",
    "DataQualityError",
    "DataRequest",
    "DataStore",
    "FREE_PERMISSION_STATUSES",
    "Mapping",
    "PURPOSE_TABLES",
    "Path",
    "QualityReport",
    "SUPPORTED_ACTIONS",
    "SnapshotRef",
    "SnapshotTamperedError",
    "TABLE_SCHEMAS",
    "TypedDict",
    "UnsupportedActionError",
    "ZoneInfo",
    "_ProductPlan",
    "_action_query_records",
    "_build_continuous",
    "_date_gaps",
    "_event_factors",
    "_month_bounds",
    "_month_keys",
    "_new_snapshot_id",
    "_persist_qualification",
    "_safe_name",
    "_sha256",
    "_write_json",
    "action_query_covers",
    "assert_single_return_basis",
    "build_price_series",
    "build_total_return_series",
    "check_product_access",
    "data_request_from_dict",
    "data_request_to_dict",
    "date",
    "datetime",
    "duckdb",
    "hashlib",
    "json",
    "pd",
    "qualify_data",
    "query_column",
    "shutil",
    "timedelta",
    "uuid",
]
