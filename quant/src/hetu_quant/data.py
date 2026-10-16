"""数据资产：免费准入、原始/标准化分层、分区增量补缺与锁定读取。

快照在 `snapshots/.tmp-<id>/` 临时落盘，成功后原子发布为 `snapshots/<id>/`；
发布后不原位覆盖，修订生成新快照并以 `revises` 关联旧值。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from hetu_quant.models import (
    DataRequest,
    QualityReport,
    SnapshotRef,
    data_request_to_dict,
)

CN = ZoneInfo("Asia/Shanghai")

FREE_PERMISSION_STATUSES = ("verified_free", "declared_free")
CODELESS_KINDS = ("catalog", "calendar")
SUPPORTED_ACTIONS = ("bonus_split", "cash_dividend")

# 各用途必需的表；财务域缺失只阻断依赖它的用途
PURPOSE_TABLES: dict[str, tuple[str, ...]] = {
    "ranking_current": (
        "calendar", "daily_bars", "security_status", "boards", "memberships", "index_bars",
    ),
    "ranking_historical": (
        "calendar", "daily_bars", "security_status", "boards", "memberships", "index_bars",
    ),
    "low_volatility_preparation": (
        "calendar", "daily_bars", "security_status", "corporate_actions", "market_cap",
    ),
    "ep_value_preparation": (
        "calendar", "daily_bars", "security_status", "financial_reports", "market_cap",
    ),
}

def _action_query_records(entry: Mapping) -> list[dict]:
    records = entry.get("query_coverage")
    if isinstance(records, list):
        return [dict(record) for record in records]
    span = entry.get("coverage")
    if entry.get("query_basis") and isinstance(span, (list, tuple)) and len(span) == 2:
        return [{"basis": entry["query_basis"], "start": span[0], "end": span[1]}]
    return []


def action_query_covers(entry: Mapping, start: str, end: str) -> bool:
    """行动实施日期查询证明须连续覆盖所需期间；不得用未知段连接两段证据。"""
    cursor = date.fromisoformat(start)
    target = date.fromisoformat(end)
    spans = sorted((str(r["start"]), str(r["end"]))
                   for r in _action_query_records(entry) if r.get("basis") == "ex_date")
    for left, right in spans:
        left_day, right_day = date.fromisoformat(left), date.fromisoformat(right)
        if left_day > cursor:
            break
        if right_day >= target:
            return True
        cursor = max(cursor, right_day + timedelta(days=1))
    return False


class _ProductPlan(TypedDict):
    """单产品增量计划：整覆盖复制、部分覆盖合并补缺、缺口取数。"""

    copies: list[tuple[str, str, str, str]]  # (partition, base_id, relpath, table)
    copy_coverage: dict[str, list[str]]  # partition -> 原覆盖期间
    merges: dict[str, tuple[str, str, list[str]]]  # partition -> (relpath, base_id, 原覆盖)
    calls: list[tuple[tuple[str, ...], date, date]]
    fetched_partitions: list[str]  # 取数涉及的分区键（空响应时记 gaps)
    recovered_empty: dict[str, tuple[list[str], str]]  # partition -> (coverage, 证据快照)
    query_source: str | None  # 成员查询域的完整 raw 证据（可无股票分区）


TABLE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "securities": {
        "columns": [
            "code", "name", "market", "ipo_date", "out_date",
            "security_type", "status", "product_id", "source_ref",
        ],
        "keys": ["code"],
    },
    "calendar": {
        "columns": ["date", "is_trading_day", "product_id", "source_ref"],
        "keys": ["date"],
    },
    "daily_bars": {
        "columns": [
            "date", "code", "open_raw", "high_raw", "low_raw", "close_raw",
            "close_price_return", "close_total_return", "volume_shares", "amount_cny",
            "product_id", "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "security_status": {
        "columns": [
            "date", "code", "is_st", "is_suspended", "is_listed",
            "product_id", "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "corporate_actions": {
        "columns": [
            "code", "ex_date", "action_type", "m", "d", "terms_raw",
            "available_at", "product_id", "source_ref",
        ],
        "keys": ["code", "ex_date", "action_type"],
    },
    "index_bars": {
        "columns": [
            "code", "date", "close", "amount_cny", "return_basis",
            "product_id", "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "boards": {
        "columns": [
            "board_id", "board_kind", "name", "provider",
            "classification_version", "product_id", "source_ref",
        ],
        "keys": ["board_id"],
    },
    "theme_library": {
        "columns": [
            "theme_code", "theme_name", "parent_code", "parent_name",
            "product_id", "source_ref",
        ],
        "keys": ["theme_code"],
    },
    "memberships": {
        "columns": [
            "board_id", "code", "observed_at", "available_at",
            "effective_from", "effective_to", "membership_kind",
            "product_id", "source_ref",
        ],
        "keys": ["board_id", "code"],
    },
    "market_cap": {
        "columns": [
            "code", "date", "market_cap_cny", "available_at",
            "product_id", "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "financial_reports": {
        "columns": [
            "code", "report_period", "ann_date", "available_at",
            "product_id", "source_ref",
        ],
        "keys": ["code", "report_period", "available_at"],
    },
}


class AccessDenied(PermissionError):
    """来源准入拒绝。"""


class DataQualityError(ValueError):
    """标准化输入违反唯一性或单位约束。"""


class SnapshotTamperedError(RuntimeError):
    """锁定资产与清单哈希不一致。"""


class UnsupportedActionError(ValueError):
    """公司行动条款未核验，限制受影响对象。"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_snapshot_id(prefix: str) -> str:
    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _month_keys(start: date, end: date) -> list[str]:
    return [str(period) for period in pd.period_range(start, end, freq="M")]


def _month_bounds(month: str) -> tuple[date, date]:
    period = pd.Period(month, freq="M")
    return period.start_time.date(), period.end_time.date()


def _date_gaps(
    start: date, end: date, covered: list[tuple[date, date]]
) -> list[tuple[date, date]]:
    """请求窗口减去已覆盖区间后的缺口（闭区间、日期粒度）。"""
    gaps: list[tuple[date, date]] = []
    cursor = start
    for cov_start, cov_end in sorted(covered):
        day_after = cov_end + timedelta(days=1)
        if day_after < cursor:
            continue
        if cov_start > end:
            break
        if cov_start > cursor:
            gaps.append((cursor, cov_start - timedelta(days=1)))
        cursor = max(cursor, day_after)
        if cursor > end:
            return gaps
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def assert_single_return_basis(frames: list[pd.DataFrame]) -> str:
    """同一比较组只允许一种收益口径；混用直接拒绝。"""
    bases = set()
    for frame in frames:
        if "return_basis" in frame.columns:
            bases.update(str(item) for item in frame["return_basis"].dropna().unique())
    if len(bases) > 1:
        raise ValueError(f"return_basis mixed: {sorted(bases)}")
    if not bases:
        raise ValueError("return_basis missing")
    return bases.pop()


def _event_factors(actions: pd.DataFrame, code: str) -> dict[str, tuple[float, float]]:
    if not len(actions):
        return {}
    events = actions[actions["code"] == code]
    if not len(events):
        return {}
    bad = events[~events["action_type"].isin(SUPPORTED_ACTIONS)]
    if len(bad):
        raise UnsupportedActionError(
            f"{code}: unverified action types {sorted(bad['action_type'].unique())}"
        )
    factors: dict[str, tuple[float, float]] = {}
    for ex_date, group in events.groupby("ex_date"):
        m = float(pd.to_numeric(group["m"], errors="coerce").fillna(1.0).prod())
        d = float(pd.to_numeric(group["d"], errors="coerce").fillna(0.0).sum())
        factors[str(ex_date)] = (m, d)
    return factors


def _build_continuous(
    bars: pd.DataFrame, actions: pd.DataFrame, column: str, total: bool
) -> pd.DataFrame:
    """连续序列：除息/送转日因子价格口径 m*P_t/P_prev，总收益口径 (m*P_t+d)/P_prev。

    从最新原始收盘向前回推；平日 m=1、d=0。总收益为税前除息日收盘再投资的
    研究序列，不是账户现金或税后净收益。调用方须先证明所需期间公司行动完整。
    """
    if not {"date", "code", "close_raw"} <= set(bars.columns):
        raise DataQualityError("bars must carry date/code/close_raw")
    frames = []
    for code, group in bars.groupby("code"):
        group = group.sort_values("date").reset_index(drop=True)
        factors = _event_factors(actions, str(code))
        closes = [float(value) for value in group["close_raw"]]
        dates = [str(value) for value in group["date"]]
        continuous = [0.0] * len(group)
        continuous[-1] = closes[-1]
        for index in range(len(group) - 1, 0, -1):
            previous = closes[index - 1]
            if previous <= 0:
                raise DataQualityError(f"{code}: non-positive close before {dates[index]}")
            m, d = factors.get(dates[index], (1.0, 0.0))
            if total:
                factor = (m * closes[index] + d) / previous
            else:
                factor = m * closes[index] / previous
            if factor <= 0:
                raise DataQualityError(
                    f"{code}: non-positive adjustment factor on {dates[index]}"
                )
            continuous[index - 1] = continuous[index] / factor
        group[column] = continuous
        frames.append(group)
    if not frames:
        return bars.assign(**{column: pd.Series(dtype="float64")})
    return pd.concat(frames, ignore_index=True)


def build_price_series(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """消除送转/拆并股机械跳变、保留现金除息价格影响的连续价格列。"""
    return _build_continuous(bars, actions, "close_price_return", total=False)


def build_total_return_series(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """税前除息日收盘再投资的总收益研究序列。"""
    return _build_continuous(bars, actions, "close_total_return", total=True)


class DataStore:
    """数据合同入口：prepare / import_files / qualify / read_table。"""

    def __init__(
        self,
        root: Path,
        config_path: Path | None = None,
        providers: Mapping[str, object] | None = None,
    ) -> None:
        self.root = Path(root)
        (self.root / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(parents=True, exist_ok=True)
        if config_path is None:
            root_config = self.root / "data-products.json"
            config_path = (
                root_config
                if root_config.exists()
                else Path(__file__).resolve().parents[2] / "config" / "data-products.json"
            )
        self.config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        self._providers = dict(providers) if providers is not None else None
        self.invocations: list[dict[str, object]] = []

    # ---- 准入 ----

    def product(self, product_id: str) -> Mapping[str, object]:
        try:
            return self.config["products"][product_id]
        except KeyError:
            raise AccessDenied(f"unknown product: {product_id}") from None

    def check_access(self, mode: str, product: Mapping[str, object]) -> None:
        """网络、缓存、导入三入口统一调用；当前任何入口收到 paid 均明确拒绝。"""
        if mode not in ("free", "paid"):
            raise AccessDenied(f"unknown mode: {mode!r}")
        if mode == "paid":
            raise AccessDenied("paid mode is not enabled (paid)")
        fee_class = product.get("fee_class")
        if fee_class != "free":
            raise AccessDenied(f"fee_class is not free: {fee_class!r}")
        status = (product.get("permission") or {}).get("status")
        if status not in FREE_PERMISSION_STATUSES:
            raise AccessDenied(f"permission not established: {status!r}")

    def _log_invocation(self, entry: str, product_id: str, product: Mapping[str, object]) -> None:
        self.invocations.append(
            {
                "entry": entry,
                "product_id": product_id,
                "provider": product.get("provider", "local_file"),
                "fee_class": product.get("fee_class", "unknown"),
                "at": datetime.now(CN).isoformat(),
            }
        )

    # ---- 快照读写 ----

    def _snapshot_dir(self, snapshot_id: str) -> Path:
        return self.root / "snapshots" / snapshot_id

    def load_manifest(self, ref: SnapshotRef) -> dict[str, object]:
        path = self._snapshot_dir(ref.snapshot_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"snapshot manifest missing: {ref.snapshot_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _verify_hashes(self, ref: SnapshotRef, manifest: Mapping[str, object]) -> None:
        snapshot_dir = self._snapshot_dir(ref.snapshot_id)
        entries: list[dict[str, str]] = []
        for item in manifest.get("items", []):
            entries.extend(item.get("raw_files", []))
        entries.extend(manifest.get("table_files", []))
        for entry in entries:
            target = snapshot_dir / entry["path"]
            if not target.exists() or _sha256(target) != entry["sha256"]:
                raise SnapshotTamperedError(
                    f"locked asset mismatch: {ref.snapshot_id}/{entry['path']}"
                )

    def _publish(self, tmp_dir: Path, snapshot_id: str, doc: Mapping[str, object]) -> SnapshotRef:
        _write_json(tmp_dir / "manifest.json", doc)
        final_dir = self._snapshot_dir(snapshot_id)
        if final_dir.exists():
            raise RuntimeError(f"snapshot already published: {snapshot_id}")
        tmp_dir.rename(final_dir)
        return SnapshotRef(snapshot_id=snapshot_id, root=self.root)

    def _published_manifests(self) -> list[tuple[str, dict[str, object]]]:
        found = []
        for child in sorted((self.root / "snapshots").iterdir()):
            if child.name.startswith(".tmp-") or not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if manifest_path.exists():
                found.append(
                    (child.name, json.loads(manifest_path.read_text(encoding="utf-8")))
                )
        return found

    def _last_success(self) -> dict[str, str]:
        path = self.root / "last_success.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def _record_success(self, product_id: str) -> None:
        records = self._last_success()
        records[product_id] = datetime.now(CN).isoformat()
        _write_json(self.root / "last_success.json", records)

    # ---- 标准化 ----

    def _standardize(
        self,
        product_id: str,
        raw: pd.DataFrame,
        source_ref: str,
        provider: object | None,
    ) -> dict[str, pd.DataFrame]:
        product = self.product(product_id)
        if provider is not None and hasattr(provider, "standardize"):
            tables = provider.standardize(product_id, raw, product)  # type: ignore[attr-defined]
        else:
            tables = {product["tables"][0]: raw}
        out = {}
        for table_name, frame in tables.items():
            schema = TABLE_SCHEMAS[table_name]
            frame = frame.copy()
            frame["product_id"] = product_id
            frame["source_ref"] = source_ref
            for column in schema["columns"]:
                if column not in frame.columns:
                    frame[column] = None
            if len(frame):
                keys = schema["keys"]
                if frame.duplicated(subset=keys).any():
                    raise DataQualityError(
                        f"duplicate key {keys} in {table_name} for {product_id}"
                    )
            out[table_name] = frame
        return out

    # ---- 分区 ----

    def _product_has_date(self, product_id: str) -> bool:
        """首表是否按日期分区；memberships/corporate_actions 等无 date 列按 code 分区。"""
        product = self.product(product_id)
        return "date" in TABLE_SCHEMAS[product["tables"][0]]["columns"]

    @staticmethod
    def _entry_coverage(
        entry: Mapping[str, object], request_doc: object
    ) -> list[str] | None:
        """分区实际覆盖期间 [min, max]；新快照持久化 coverage，旧快照按请求窗口推导。"""
        stored = entry.get("coverage")
        if isinstance(stored, (list, tuple)) and len(stored) == 2:
            return [str(stored[0]), str(stored[1])]
        partition = str(entry.get("partition", ""))
        if (
            isinstance(request_doc, Mapping)
            and "|month=" in partition
            and request_doc.get("start")
            and request_doc.get("end")
        ):
            month_start, month_end = _month_bounds(partition.rsplit("|month=", 1)[1])
            req_start = date.fromisoformat(str(request_doc["start"]))
            req_end = date.fromisoformat(str(request_doc["end"]))
            return [max(req_start, month_start).isoformat(), min(req_end, month_end).isoformat()]
        return None

    def _coverage_index(
        self,
        published: list[tuple[str, dict[str, object]]] | None = None,
    ) -> dict[str, dict[str, list[tuple[str, str, str, str, list[str] | None]]]]:
        """product_id -> partition -> 候选列表（created_at 新→旧）。

        同一分区在多个已发布快照中存在时保留全部候选：组合快照只复制分区、
        条目可能缺 coverage 与 raw 证据而被最新候选遮蔽；计划层逐候选采用
        自带 coverage 或按该候选快照的 request+raw 证据恢复，第一个可证明者
        胜出，全部无法证明才安排取数。published 传入已加载的清单列表
        （prepare 全程只解析一次），缺省时自行加载。"""
        loaded = published if published is not None else self._published_manifests()
        collected: dict[
            str, dict[str, list[tuple[str, str, str, str, list[str] | None]]]
        ] = {}
        for snapshot_id, manifest in loaded:
            created = str(manifest.get("created_at", ""))
            request_doc = manifest.get("request")
            for entry in manifest.get("table_files", []):
                product_id = entry["product_id"]
                partition = entry["partition"]
                coverage = self._entry_coverage(entry, request_doc)
                collected.setdefault(product_id, {}).setdefault(partition, []).append(
                    (snapshot_id, entry["path"], entry["table"], created, coverage)
                )
        return {
            product_id: {
                partition: sorted(hits, key=lambda hit: (hit[3], hit[0]), reverse=True)
                for partition, hits in parts.items()
            }
            for product_id, parts in collected.items()
        }

    def _verified_source(
        self, base_id: str, relpath: str, base_manifests: dict[str, dict[str, object]]
    ) -> Path:
        """复用旧分区前按旧快照清单 sha256 校验来源文件，篡改一律拒绝。"""
        if base_id not in base_manifests:
            base_manifests[base_id] = self.load_manifest(
                SnapshotRef(snapshot_id=base_id, root=self.root)
            )
        for entry in base_manifests[base_id].get("table_files", []):
            if entry["path"] == relpath:
                source = self._snapshot_dir(base_id) / relpath
                if not source.exists() or _sha256(source) != entry["sha256"]:
                    raise SnapshotTamperedError(
                        f"locked asset mismatch: {base_id}/{relpath}"
                    )
                return source
        raise SnapshotTamperedError(f"partition not in source manifest: {base_id}/{relpath}")

    def _recovered_coverage(
        self,
        product_id: str,
        code: str,
        base_id: str,
        base_manifests: dict[str, dict[str, object]],
        derived_cache: dict[tuple[str, str], list[str] | None] | None = None,
    ) -> list[str] | None:
        """旧格式快照（无 coverage 元数据）按证据链恢复覆盖期间。两种取数形状：

        代码在旧请求代码域内（逐码取数，如公司行动）：存在该代码的 raw
        响应文件且 sha256 核验通过 → 覆盖 = 旧请求 [start, end]；raw 缺失
        无法证明，由调用方如实重取。

        代码不在旧请求代码域内（分区空间由批次查询派生，如成员按板块取，
        请求域是板块代码、分区是股票代码）：分区存在 + 该产品无取数失败
        + 该产品全部 raw 文件存在且 sha256 核验通过 → 覆盖 = 旧请求
        [start, end]；任何 raw 缺失即整批证据不完整，返回 None。

        无法证明返回 None；raw 哈希不一致按 A2 语义拒绝
        （SnapshotTamperedError），不静默降级为重取。"""
        if base_id not in base_manifests:
            base_manifests[base_id] = self.load_manifest(
                SnapshotRef(snapshot_id=base_id, root=self.root)
            )
        manifest = base_manifests[base_id]
        request_doc = manifest.get("request")
        if not isinstance(request_doc, Mapping):
            return None
        start, end = request_doc.get("start"), request_doc.get("end")
        if not start or not end:
            return None
        domains = request_doc.get("codes_by_product")
        product_codes = (
            domains.get(product_id)
            if isinstance(domains, Mapping)
            else None
        )
        queried = (
            product_codes if product_codes is not None else request_doc.get("codes") or ()
        )
        raw_entries = [
            entry
            for item in manifest.get("items", [])
            if item.get("product_id") == product_id
            for entry in item.get("raw_files", [])
        ]
        if code in {str(item) for item in queried}:
            needle = f"response-{_safe_name(code)}-"
            for entry in raw_entries:
                if Path(str(entry["path"])).name.startswith(needle):
                    target = self._snapshot_dir(base_id) / str(entry["path"])
                    if not target.exists():
                        return None
                    if _sha256(target) != entry["sha256"]:
                        raise SnapshotTamperedError(
                            f"locked asset mismatch: {base_id}/{entry['path']}"
                        )
                    return [str(start), str(end)]
            return None
        # 派生分区空间（批次按非股票标识取数）：全部 raw 完整核验才可证明。
        # 核验结论与具体代码无关，按 (快照, 产品) 缓存，prepare 全程核验一次。
        if not raw_entries:
            return None
        cache_key = (base_id, product_id)
        if derived_cache is not None and cache_key in derived_cache:
            return derived_cache[cache_key]
        failed = {
            str(failure["product_id"]) for failure in manifest.get("failures", [])
        }
        verdict: list[str] | None
        if product_id in failed:
            verdict = None
        else:
            verdict = [str(start), str(end)]
            for entry in raw_entries:
                target = self._snapshot_dir(base_id) / str(entry["path"])
                if not target.exists():
                    verdict = None
                    break
                if _sha256(target) != entry["sha256"]:
                    raise SnapshotTamperedError(
                        f"locked asset mismatch: {base_id}/{entry['path']}"
                    )
        if derived_cache is not None:
            derived_cache[cache_key] = verdict
        return verdict

    def _recovered_empty_coverage(
        self,
        product_id: str,
        code: str,
        base_manifests: dict[str, dict[str, object]],
        published: list[tuple[str, dict[str, object]]] | None = None,
    ) -> tuple[list[str], str] | None:
        """成功空响应恢复：代码在任何快照都没有分区时，扫描已发布快照
        （新→旧）：该产品请求代码域含此代码、存在该代码的 raw 响应文件且
        sha256 核验通过 → 视为"已核验无记录"，返回 (覆盖, 证据快照 id)，
        计划层写出空分区并登记 coverage，不安排取数。failures 中的取数失败
        永远不算覆盖证据；raw 哈希不一致按 A2 语义拒绝（SnapshotTamperedError），
        不静默降级为重取。仅用于无 date 的 code 分区产品。published 传入
        已加载的清单列表避免逐代码重复解析。"""
        needle = f"response-{_safe_name(code)}-"
        loaded = published if published is not None else self._published_manifests()
        for snapshot_id, manifest in loaded:
            failed = {
                str(failure["product_id"])
                for failure in manifest.get("failures", [])
            }
            if product_id in failed:
                continue
            request_doc = manifest.get("request")
            if not isinstance(request_doc, Mapping):
                continue
            start, end = request_doc.get("start"), request_doc.get("end")
            if not start or not end:
                continue
            domains = request_doc.get("codes_by_product")
            product_codes = (
                domains.get(product_id) if isinstance(domains, Mapping) else None
            )
            queried = (
                product_codes
                if product_codes is not None
                else request_doc.get("codes") or ()
            )
            if code not in {str(item) for item in queried}:
                continue
            if snapshot_id not in base_manifests:
                base_manifests[snapshot_id] = manifest
            hit_entry: dict[str, object] | None = None
            for item in manifest.get("items", []):
                if item.get("product_id") != product_id:
                    continue
                for entry in item.get("raw_files", []):
                    if Path(str(entry["path"])).name.startswith(needle):
                        hit_entry = entry
                        break
                if hit_entry is not None:
                    break
            if hit_entry is None:
                continue
            target = self._snapshot_dir(snapshot_id) / str(hit_entry["path"])
            if not target.exists():
                continue
            if _sha256(target) != hit_entry["sha256"]:
                raise SnapshotTamperedError(
                    f"locked asset mismatch: {snapshot_id}/{hit_entry['path']}"
                )
            return [str(start), str(end)], snapshot_id
        return None

    def _plan_product(
        self,
        product_id: str,
        request: DataRequest,
        coverage: Mapping[
            str, Mapping[str, list[tuple[str, str, str, str, list[str] | None]]]
        ],
        base_manifests: dict[str, dict[str, object]],
        published: list[tuple[str, dict[str, object]]] | None = None,
        derived_cache: dict[tuple[str, str], list[str] | None] | None = None,
    ) -> _ProductPlan:
        """按覆盖区间制定单产品计划：整覆盖复制、部分覆盖合并补缺、缺口取数。"""
        product = self.product(product_id)
        copies: list[tuple[str, str, str, str]] = []
        copy_coverage: dict[str, list[str]] = {}
        merges: dict[str, tuple[str, str, list[str]]] = {}
        calls: list[tuple[tuple[str, ...], date, date]] = []
        fetched_partitions: list[str] = []
        recovered_empty: dict[str, tuple[list[str], str]] = {}
        plan = _ProductPlan(
            copies=copies,
            copy_coverage=copy_coverage,
            merges=merges,
            calls=calls,
            fetched_partitions=fetched_partitions,
            recovered_empty=recovered_empty,
            query_source=None,
        )

        def add_copy(key: str, hit: tuple[str, str, str, str, list[str] | None]) -> None:
            self._verified_source(hit[0], hit[1], base_manifests)
            copies.append((key, hit[0], hit[1], hit[2]))
            if hit[4] is not None:
                copy_coverage[key] = hit[4]

        if product.get("kind") in CODELESS_KINDS:
            key = f"range={request.start.isoformat()}_{request.end.isoformat()}"
            hits = coverage.get(product_id, {}).get(key)
            if hits:
                add_copy(key, hits[0])
            else:
                calls.append(((), request.start, request.end))
                fetched_partitions.append(key)
            return plan
        codes = request.codes_for(product_id)
        if not codes:
            raise ValueError(
                f"codes required for {product_id}; empty codes only for catalog/calendar"
            )
        if not self._product_has_date(product_id):
            if product.get("kind") == "membership":
                # 查询域是板块，输出域是股票。相同板块集合的成功查询按 raw
                # 证明覆盖（包括全空）；不能靠 code=<板块> 股票空分区复用。
                for base_id, manifest in published or ():
                    doc = manifest.get("request") or {}
                    domains = doc.get("codes_by_product") or {}
                    queried = domains.get(product_id, doc.get("codes") or ())
                    if set(queried) != set(codes):
                        continue
                    if not (doc.get("start", "~") <= request.start.isoformat()
                            and doc.get("end", "") >= request.end.isoformat()):
                        continue
                    if any(f["product_id"] == product_id for f in manifest.get("failures", [])):
                        continue
                    if (manifest.get("gaps") or {}).get(product_id):
                        continue
                    raw_entries = [
                        entry for item in manifest.get("items", [])
                        if item.get("product_id") == product_id
                        for entry in item.get("raw_files", [])
                    ]
                    expected = {
                        f"response-{_safe_name(code)}-{doc['start']}_{doc['end']}.parquet"
                        for code in codes
                    }
                    if not expected.issubset({Path(e["path"]).name for e in raw_entries}):
                        continue
                    if any(not (self._snapshot_dir(base_id) / e["path"]).exists() for e in raw_entries):
                        continue
                    for entry in raw_entries:
                        target = self._snapshot_dir(base_id) / entry["path"]
                        if _sha256(target) != entry["sha256"]:
                            raise SnapshotTamperedError(f"locked asset mismatch: {base_id}/{entry['path']}")
                    for entry in manifest.get("table_files", []):
                        if entry["product_id"] != product_id:
                            continue
                        source = self._verified_source(base_id, entry["path"], base_manifests)
                        # 兼容旧写侧伪造的空股票分区，不将其复制为对象证据。
                        if not len(pd.read_parquet(source)):
                            continue
                        add_copy(entry["partition"], (
                            base_id, entry["path"], entry["table"], "",
                            self._entry_coverage(entry, doc) or [doc["start"], doc["end"]],
                        ))
                    plan["query_source"] = base_id
                    return plan
            # code 分区事件产品同样按查询期间补缺：整覆盖复制、部分覆盖合并补缺
            for code in codes:
                key = f"code={code}"
                hits = coverage.get(product_id, {}).get(key)
                chosen: tuple[str, str, str, str, list[str]] | None = None
                for hit in hits or ():
                    if product.get("kind") == "membership":
                        source = self._verified_source(hit[0], hit[1], base_manifests)
                        if not len(pd.read_parquet(source)):
                            continue
                    cov = hit[4]
                    if cov is None:
                        # 旧格式候选：按该候选快照的 request+raw 证据链恢复
                        # 实际覆盖；最新候选无法证明时依次回退较旧候选
                        cov = self._recovered_coverage(
                            product_id, code, hit[0], base_manifests, derived_cache
                        )
                    if cov is not None:
                        chosen = (hit[0], hit[1], hit[2], hit[3], cov)
                        break
                if chosen is None and not hits and product.get("kind") != "membership":
                    # 任何快照都没有该代码的分区：原始批次的成功空响应 raw
                    # （核验通过）证明"已核验无记录"，写空分区且不安排取数
                    recovered = self._recovered_empty_coverage(
                        product_id, code, base_manifests, published
                    )
                    if recovered is not None:
                        span, base_id = recovered
                        if (
                            span[0] <= request.start.isoformat()
                            and span[1] >= request.end.isoformat()
                        ):
                            recovered_empty[key] = (span, base_id)
                            continue
                if chosen is None:
                    calls.append(((code,), request.start, request.end))
                    fetched_partitions.append(key)
                    continue
                base_id, relpath, _table, _created, cov = chosen
                if cov[0] <= request.start.isoformat() and cov[1] >= request.end.isoformat():
                    # 复制条目落恢复/原覆盖，使新快照回到新格式、后续请求零恢复
                    add_copy(key, (base_id, relpath, _table, _created, cov))
                else:
                    merges[key] = (relpath, base_id, cov)
                    covered = [
                        (date.fromisoformat(cov[0]), date.fromisoformat(cov[1]))
                    ]
                    fetched_partitions.append(key)
                    for gap_start, gap_end in _date_gaps(request.start, request.end, covered):
                        calls.append(((code,), gap_start, gap_end))
            return plan
        # 按日期分区：以区间覆盖判断，同月部分覆盖识别为缺口
        for code in codes:
            covered: list[tuple[date, date]] = []
            prefix = f"code={code}|month="
            for key, hits in coverage.get(product_id, {}).items():
                if not key.startswith(prefix):
                    continue
                month_start, month_end = _month_bounds(key[len(prefix):])
                need_start = max(month_start, request.start)
                need_end = min(month_end, request.end)
                if need_start > need_end:
                    continue
                hit = next((item for item in hits if item[4] is not None), None)
                cov: list[str]
                if hit is None:
                    # 旧快照无覆盖元数据：以实际数据 min/max 期间为覆盖
                    hit = hits[0]
                    source = self._verified_source(hit[0], hit[1], base_manifests)
                    frame = pd.read_parquet(source, columns=["date"])
                    cov = [
                        str(frame["date"].astype(str).min()),
                        str(frame["date"].astype(str).max()),
                    ]
                else:
                    cov = list(hit[4])  # type: ignore[arg-type]
                base_id, relpath, _table, _created, _cov = hit
                if cov[0] <= need_start.isoformat() and cov[1] >= need_end.isoformat():
                    add_copy(key, (base_id, relpath, _table, _created, cov))
                else:
                    merges[key] = (relpath, base_id, cov)
                covered.append((date.fromisoformat(cov[0]), date.fromisoformat(cov[1])))
            for gap_start, gap_end in _date_gaps(request.start, request.end, covered):
                calls.append(((code,), gap_start, gap_end))
                for month in _month_keys(gap_start, gap_end):
                    fetched_partitions.append(f"{prefix}{month}")
        return plan

    def _requested_month_coverage(self, partition: str, request: DataRequest) -> list[str]:
        """该分区本次承诺的覆盖期间：请求窗口 ∩ 月分区。"""
        month_start, month_end = _month_bounds(partition.rsplit("|month=", 1)[1])
        return [
            max(request.start, month_start).isoformat(),
            min(request.end, month_end).isoformat(),
        ]

    def _write_partitions(
        self,
        tmp_dir: Path,
        product_id: str,
        tables: Mapping[str, pd.DataFrame],
        request: DataRequest,
        table_files: list[dict[str, object]],
        gaps: dict[str, list[str]],
        fetched_partitions: list[str],
        merge_sources: Mapping[str, tuple[Path, list[str]]] | None = None,
        queried_segments: Mapping[str, list[list[str]]] | None = None,
        query_evidence: Mapping[str, list[dict]] | None = None,
        prior_query_evidence: Mapping[str, list[dict]] | None = None,
    ) -> set[str]:
        """落盘分区并登记清单；部分覆盖分区与旧段合并去重。返回写出的分区键。

        code 分区产品中，成功取数但 0 行的代码写出空分区并记 coverage
        （对象级"已核验无记录"证据），不计入 gaps；gaps 只留真正未取到的缺口。
        """
        product = self.product(product_id)
        has_date = self._product_has_date(product_id)
        merge_sources = merge_sources or {}
        written: set[str] = set()
        for table_name, frame in tables.items():
            schema = TABLE_SCHEMAS[table_name]
            table_dir = tmp_dir / "tables" / table_name
            table_dir.mkdir(parents=True, exist_ok=True)
            if product.get("kind") in CODELESS_KINDS:
                partitions = [(f"range={request.start.isoformat()}_{request.end.isoformat()}", frame)]
            else:
                partitions = []
                empty_cov: dict[str, list[str]] = {}
                if len(frame):
                    frame = frame.copy()
                    if "date" in frame.columns:
                        frame["_month"] = frame["date"].astype(str).str[:7]
                        for (code, month), part in frame.groupby(["code", "_month"]):
                            partitions.append((f"code={code}|month={month}", part))
                    else:
                        for code, part in frame.groupby("code"):
                            partitions.append((f"code={code}", part))
                elif has_date or product.get("kind") in CODELESS_KINDS:
                    gaps.setdefault(product_id, []).extend(fetched_partitions)
                if not has_date and product.get("kind") not in (*CODELESS_KINDS, "membership"):
                    # code 分区产品：成功取数但 0 行的代码写出空分区
                    present = {partition for partition, _part in partitions}
                    for code, segments in (queried_segments or {}).items():
                        key = f"code={code}"
                        if key in present:
                            continue
                        starts = [segment[0] for segment in segments]
                        ends = [segment[1] for segment in segments]
                        empty_cov[key] = [min(starts), max(ends)]
                        partitions.append((key, frame.iloc[0:0]))
            for partition, part in partitions:
                name = f"part-{_safe_name(product_id)}-{_safe_name(partition)}.parquet"
                target = table_dir / name
                part = part.drop(columns=["_month"], errors="ignore")
                coverage: list[str] | None = None
                merge = merge_sources.get(partition)
                if merge is not None and len(part):
                    # 部分覆盖合并：旧段在前，重叠键保留旧值
                    old_frame = pd.read_parquet(merge[0])
                    part = pd.concat([old_frame, part], ignore_index=True)
                    part = part.drop_duplicates(subset=schema["keys"])
                    if "|month=" in partition:
                        new_cov = self._requested_month_coverage(partition, request)
                    else:
                        new_cov = empty_cov.get(partition) or [
                            request.start.isoformat(),
                            request.end.isoformat(),
                        ]
                    coverage = [min(merge[1][0], new_cov[0]), max(merge[1][1], new_cov[1])]
                elif merge is not None:
                    # code 分区新段成功空响应：保留旧段行，coverage 仍取并集
                    part = pd.read_parquet(merge[0])
                    new_cov = empty_cov.get(partition) or [
                        request.start.isoformat(),
                        request.end.isoformat(),
                    ]
                    coverage = [min(merge[1][0], new_cov[0]), max(merge[1][1], new_cov[1])]
                elif "|month=" in partition:
                    coverage = self._requested_month_coverage(partition, request)
                elif partition.startswith("code="):
                    # code 分区事件产品：承诺覆盖即本次实际查询期间
                    coverage = empty_cov.get(partition) or [
                        request.start.isoformat(),
                        request.end.isoformat(),
                    ]
                elif partition.startswith("range=") and "date" in part.columns:
                    coverage = [request.start.isoformat(), request.end.isoformat()]
                part.to_parquet(target, index=False)
                entry: dict[str, object] = {
                    "path": str(target.relative_to(tmp_dir)),
                    "sha256": _sha256(target),
                    "product_id": product_id,
                    "table": table_name,
                    "partition": partition,
                }
                if coverage is not None:
                    entry["coverage"] = coverage
                if table_name == "corporate_actions":
                    records = list((prior_query_evidence or {}).get(partition, []))
                    code = partition.removeprefix("code=")
                    records.extend((query_evidence or {}).get(code, []))
                    if records:
                        entry["query_coverage"] = records
                        if coverage and action_query_covers(entry, *coverage):
                            entry["query_basis"] = "ex_date"
                table_files.append(entry)
                written.add(partition)
        return written

    # ---- 入口：导入 ----

    def import_files(self, manifest: Path, files: tuple[Path, ...]) -> SnapshotRef:
        manifest_path = Path(manifest)
        manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        mode = manifest_doc.get("mode", "free")
        if mode == "paid":
            raise AccessDenied("paid mode is not enabled (paid)")
        items = manifest_doc["items"]
        available = {Path(file).name: Path(file) for file in files}
        # 先完成全部准入与校验，再落盘
        for item in items:
            product = self.product(item["product_id"])
            source = item.get("source") or {}
            basis = source.get("permission_basis")
            if not isinstance(basis, str) or not basis.strip():
                raise AccessDenied("permission_basis missing for imported file")
            if not source.get("name") or not source.get("obtained_at"):
                raise AccessDenied("imported file source must carry name and obtained_at")
            self.check_access(
                mode,
                {
                    "fee_class": source.get("fee_class"),
                    "permission": {"status": "verified_free"},
                    "provider": product.get("provider", "local_file"),
                },
            )
            for name in item["files"]:
                if name not in available:
                    raise FileNotFoundError(f"manifest file not provided: {name}")

        snapshot_id = _new_snapshot_id("imp")
        tmp_dir = self.root / "snapshots" / f".tmp-{snapshot_id}"
        written_items: list[dict[str, object]] = []
        table_files: list[dict[str, object]] = []
        for item in items:
            product = self.product(item["product_id"])
            source = item["source"]
            self._log_invocation(
                "import",
                item["product_id"],
                {"provider": product.get("provider", "local_file"), "fee_class": "free"},
            )
            raw_dir = tmp_dir / "raw" / item["product_id"]
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_files = []
            for name in item["files"]:
                target = raw_dir / name
                shutil.copyfile(available[name], target)
                raw_files.append(
                    {"path": str(target.relative_to(tmp_dir)), "sha256": _sha256(target)}
                )
            raw_frame = pd.read_csv(raw_dir / item["files"][0], dtype=str)
            tables = self._standardize(
                item["product_id"], raw_frame, raw_files[0]["path"], provider=None
            )
            for table_name, frame in tables.items():
                table_dir = tmp_dir / "tables" / table_name
                table_dir.mkdir(parents=True, exist_ok=True)
                name = f"part-{_safe_name(item['product_id'])}-import-{_safe_name(source['name'])}.parquet"
                target = table_dir / name
                frame.to_parquet(target, index=False)
                table_files.append(
                    {
                        "path": str(target.relative_to(tmp_dir)),
                        "sha256": _sha256(target),
                        "product_id": item["product_id"],
                        "table": table_name,
                        "partition": f"import={source['name']}",
                    }
                )
            written_items.append(
                {
                    "product_id": item["product_id"],
                    "table": item["table"],
                    "source": source,
                    "raw_files": raw_files,
                }
            )
            self._record_success(item["product_id"])
        doc = {
            "snapshot_id": snapshot_id,
            "kind": "import",
            "created_at": datetime.now(CN).isoformat(),
            "mode": mode,
            "request": None,
            "items": written_items,
            "table_files": table_files,
            "gaps": {},
            "failures": [],
            "revises": None,
        }
        return self._publish(tmp_dir, snapshot_id, doc)

    # ---- 入口：准备（网络获取） ----

    def _provider(self, name: str) -> object:
        if self._providers is None:
            from hetu_quant.providers import default_providers

            self._providers = default_providers()
        try:
            return self._providers[name]
        except KeyError:
            raise AccessDenied(f"no provider adapter registered: {name}") from None

    def prepare(self, request: DataRequest) -> SnapshotRef:
        for product_id in request.product_ids:
            product = self.product(product_id)
            self.check_access(request.mode, product)
            if not request.codes and product.get("kind") not in CODELESS_KINDS:
                raise ValueError(
                    f"codes required for {product_id}; empty codes only for catalog/calendar"
                )

        # 已发布清单全程只解析一次（恢复路径逐代码消费，避免重复解析放大）
        published = self._published_manifests()
        base_manifests: dict[str, dict[str, object]] = {
            snapshot_id: manifest for snapshot_id, manifest in published
        }
        coverage = self._coverage_index(published)
        derived_cache: dict[tuple[str, str], list[str] | None] = {}
        plans = {
            product_id: self._plan_product(
                product_id, request, coverage, base_manifests, published,
                derived_cache,
            )
            for product_id in request.product_ids
        }
        needed_sources = {
            (base_id, relpath, key)
            for plan in plans.values()
            for key, base_id, relpath, _table in plan["copies"]
        }
        needed_sources.update(
            (base_id, relpath, key) for plan in plans.values()
            for key, (relpath, base_id, _cov) in plan["merges"].items()
        )
        source_entries = {
            (base_id, entry["path"], entry["partition"]): entry
            for base_id, manifest in published for entry in manifest.get("table_files", [])
            if (base_id, entry["path"], entry["partition"]) in needed_sources
        }
        reused_snapshots = {
            base_id
            for plan in plans.values()
            for base_id in [copy[1] for copy in plan["copies"]]
            + [merge[1] for merge in plan["merges"].values()]
        }
        reused_snapshots.update(plan["query_source"] for plan in plans.values() if plan["query_source"])
        if not any(
            plan["calls"] or plan["merges"] or plan["recovered_empty"]
            for plan in plans.values()
        ) and len(reused_snapshots) == 1:
            # 全部覆盖且同一快照满足：不重取、不新建
            return SnapshotRef(snapshot_id=reused_snapshots.pop(), root=self.root)

        snapshot_id = _new_snapshot_id("prep")
        tmp_dir = self.root / "snapshots" / f".tmp-{snapshot_id}"
        items: list[dict[str, object]] = []
        table_files: list[dict[str, object]] = []
        gaps: dict[str, list[str]] = {}
        failures: list[dict[str, object]] = []
        inherited: dict[str, str] = {
            product_id: plan["query_source"]
            for product_id, plan in plans.items() if plan["query_source"]
        }
        first_error: Exception | None = None
        last_success = self._last_success()
        try:
            for product_id in request.product_ids:
                product = self.product(product_id)
                plan = plans[product_id]
                # 复用整覆盖分区：复制前已按来源清单校验 sha256
                for key, base_id, relpath, table_name in plan["copies"]:
                    inherited.setdefault(product_id, base_id)
                    source_file = self._verified_source(base_id, relpath, base_manifests)
                    target = tmp_dir / relpath
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_file, target)
                    entry: dict[str, object] = {
                        "path": relpath,
                        "sha256": _sha256(target),
                        "product_id": product_id,
                        "table": table_name,
                        "partition": key,
                    }
                    copy_cov = plan["copy_coverage"].get(key)
                    if copy_cov is not None:
                        entry["coverage"] = copy_cov
                    original = source_entries[(base_id, relpath, key)]
                    for field in ("query_basis", "query_coverage"):
                        if field in original:
                            entry[field] = original[field]
                    table_files.append(entry)
                # 恢复的成功空响应（任何快照无分区、原始批次 raw 核验通过）：
                # 写空分区并登记恢复出的 coverage，零取数；新快照回到新格式，
                # 后续请求直接复制、不再恢复。证据快照登记谱系可追溯。
                for key, (span, base_id) in plan["recovered_empty"].items():
                    inherited.setdefault(product_id, base_id)
                    table_name = product["tables"][0]
                    table_dir = tmp_dir / "tables" / table_name
                    table_dir.mkdir(parents=True, exist_ok=True)
                    empty_name = (
                        f"part-{_safe_name(product_id)}-{_safe_name(key)}.parquet"
                    )
                    empty_target = table_dir / empty_name
                    pd.DataFrame(
                        columns=TABLE_SCHEMAS[table_name]["columns"]
                    ).to_parquet(empty_target, index=False)
                    table_files.append(
                        {
                            "path": str(empty_target.relative_to(tmp_dir)),
                            "sha256": _sha256(empty_target),
                            "product_id": product_id,
                            "table": table_name,
                            "partition": key,
                            "coverage": span,
                        }
                    )
                if not plan["calls"]:
                    continue
                try:
                    self._log_invocation("fetch", product_id, product)
                    provider = self._provider(product["provider"])
                    fetched_tables: dict[str, list[pd.DataFrame]] = {}
                    raw_files: list[dict] = []
                    query_evidence: dict[str, list[dict]] = {}
                    for codes, seg_start, seg_end in plan["calls"]:
                        raw = provider.fetch(product_id, codes, seg_start, seg_end)
                        raw_dir = tmp_dir / "raw" / product_id
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        raw_name = (
                            f"response-{_safe_name('-'.join(codes) or 'all')}-"
                            f"{seg_start.isoformat()}_{seg_end.isoformat()}.parquet"
                        )
                        raw_path = raw_dir / raw_name
                        raw.to_parquet(raw_path, index=False)
                        raw_rel = str(raw_path.relative_to(tmp_dir))
                        raw_entry = {"path": raw_rel, "sha256": _sha256(raw_path)}
                        evidence = raw.attrs.get("query_evidence", [])
                        if evidence:
                            raw_entry["query_evidence"] = evidence
                        raw_files.append(raw_entry)
                        for record in evidence:
                            record = dict(record, source_ref=raw_rel, snapshot_id=snapshot_id)
                            query_evidence.setdefault(record["code"], []).append(record)
                        tables = self._standardize(product_id, raw, raw_rel, provider)
                        for table_name, frame in tables.items():
                            fetched_tables.setdefault(table_name, []).append(frame)
                    merged = {}
                    for name, parts in fetched_tables.items():
                        nonempty = [part for part in parts if len(part)]
                        merged[name] = (
                            pd.concat(nonempty, ignore_index=True)
                            if nonempty
                            else parts[0].iloc[0:0]
                        )
                    # code 分区产品：本次实际查询的代码期间段，供空分区记 coverage
                    queried_segments: dict[str, list[list[str]]] = {}
                    if not self._product_has_date(product_id):
                        for codes, seg_start, seg_end in plan["calls"]:
                            if len(codes) == 1:
                                queried_segments.setdefault(codes[0], []).append(
                                    [seg_start.isoformat(), seg_end.isoformat()]
                                )
                    # 部分覆盖分区：先校验来源，再与新取数段合并
                    resolved_merges = {
                        key: (
                            self._verified_source(base_id, relpath, base_manifests),
                            old_cov,
                        )
                        for key, (relpath, base_id, old_cov) in plan["merges"].items()
                    }
                    written = self._write_partitions(
                        tmp_dir, product_id, merged, request, table_files, gaps,
                        plan["fetched_partitions"], resolved_merges, queried_segments,
                        query_evidence,
                        {key: _action_query_records(source_entries[(base_id, relpath, key)])
                         for key, (relpath, base_id, _cov) in plan["merges"].items()},
                    )
                    # 缺口未取到新行的部分覆盖分区：原样复制已核验旧段
                    for key, (relpath, base_id, old_cov) in plan["merges"].items():
                        if key in written:
                            continue
                        inherited.setdefault(product_id, base_id)
                        source_file = self._verified_source(base_id, relpath, base_manifests)
                        target = tmp_dir / relpath
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source_file, target)
                        table_files.append(
                            {
                                "path": relpath,
                                "sha256": _sha256(target),
                                "product_id": product_id,
                                "table": self.product(product_id)["tables"][0],
                                "partition": key,
                                "coverage": old_cov,
                                **{field: source_entries[(base_id, relpath, key)][field]
                                   for field in ("query_basis", "query_coverage")
                                   if field in source_entries[(base_id, relpath, key)]},
                            }
                        )
                    items.append(
                        {
                            "product_id": product_id,
                            "table": product["tables"][0],
                            "source": {
                                "name": product["provider"],
                                "fee_class": product["fee_class"],
                                "source_url": product.get("permission", {}).get(
                                    "source_url", ""
                                ),
                                "obtained_at": datetime.now(CN).isoformat(),
                            },
                            "raw_files": raw_files,
                        }
                    )
                    self._record_success(product_id)
                except Exception as error:  # 单域失败只限制依赖它的工作
                    if first_error is None:
                        first_error = error
                    failures.append(
                        {
                            "product_id": product_id,
                            "error": f"{type(error).__name__}: {error}",
                            "last_success": last_success.get(product_id),
                            "at": datetime.now(CN).isoformat(),
                        }
                    )
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        # 仅复制（未本快照取数）的产品也登记来源与谱系，保证清单可追溯
        fetched_products = {str(item["product_id"]) for item in items}
        for product_id, base_id in sorted(inherited.items()):
            if product_id in fetched_products:
                continue
            if base_id not in base_manifests:
                base_manifests[base_id] = self.load_manifest(
                    SnapshotRef(snapshot_id=base_id, root=self.root)
                )
            source_doc: dict[str, object] = {}
            for base_item in base_manifests[base_id].get("items", []):
                if str(base_item.get("product_id")) == product_id:
                    source_doc = dict(base_item.get("source") or {})
                    break
            items.append(
                {
                    "product_id": product_id,
                    "table": self.product(product_id)["tables"][0],
                    "source": source_doc,
                    "raw_files": [],
                    "inherited_from": base_id,
                }
            )
        if not table_files and failures:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            assert first_error is not None
            raise first_error
        doc = {
            "snapshot_id": snapshot_id,
            "kind": "prepare",
            "created_at": datetime.now(CN).isoformat(),
            "mode": request.mode,
            "request": data_request_to_dict(request),
            "items": items,
            "table_files": table_files,
            "gaps": gaps,
            "failures": failures,
            "revises": sorted(reused_snapshots)[-1] if reused_snapshots else None,
        }
        return self._publish(tmp_dir, snapshot_id, doc)

    # ---- 读取 ----

    def read_table(self, ref: SnapshotRef, name: str) -> pd.DataFrame:
        manifest = self.load_manifest(ref)
        self._verify_hashes(ref, manifest)
        table_dir = self._snapshot_dir(ref.snapshot_id) / "tables" / name
        pattern = str(table_dir / "*.parquet")
        files = sorted(table_dir.glob("*.parquet")) if table_dir.exists() else []
        if not files:
            schema = TABLE_SCHEMAS.get(name)
            if schema is None:
                raise KeyError(f"unknown table: {name}")
            return pd.DataFrame(columns=schema["columns"])
        # union_by_name：空分区（0 行，列为 arrow null 类型）与非空分区
        # （字符串/数值列）混读时按列名统一 schema，避免首文件 NULL 类型
        # 导致整表读取失败（P1-2 复评：恢复出的空分区排在首位时必现）
        return duckdb.query(
            f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
        ).to_df()

    # ---- 用途资格 ----

    def qualify(self, request: DataRequest, ref: SnapshotRef) -> QualityReport:
        """按对象、窗口、字段和用途检查资格；资格记录持久化，旧记录不被新请求改写。"""
        for product_id in request.product_ids:
            product = self.product(product_id)
            self.check_access(request.mode, product)
        if not request.codes:
            kinds = {self.product(pid).get("kind") for pid in request.product_ids}
            if not kinds <= set(CODELESS_KINDS):
                raise ValueError("codes required for non-catalog/calendar products")

        manifest = self.load_manifest(ref)
        self._verify_hashes(ref, manifest)
        as_of = request.as_of

        limitations: list[str] = []
        excluded: dict[str, list[str]] = {code: [] for code in request.codes}
        eligible: list[str] = []

        # 信息时点：快照取得时间不得晚于研究 as_of
        obtained_latest = ""
        for item in manifest.get("items", []):
            obtained = str((item.get("source") or {}).get("obtained_at", ""))
            obtained_latest = max(obtained_latest, obtained)
        obtained_ok = True
        if obtained_latest and obtained_latest > as_of.isoformat():
            obtained_ok = False
            limitations.append(
                f"obtained_after_as_of: snapshot obtained at {obtained_latest} later than as_of"
            )

        # 快照内可用表与失败产品
        available_tables = {
            entry["table"] for entry in manifest.get("table_files", [])
        }
        failed_products = {
            entry["product_id"] for entry in manifest.get("failures", [])
        }
        gapped_products = set(manifest.get("gaps", {}))

        # 市场覆盖：快照实际参与产品声明的市场并集
        covered_markets: set[str] = set()
        for item in manifest.get("items", []):
            covered_markets.update(self.product(item["product_id"]).get("markets", []))

        def table_ok(table: str) -> bool:
            if table in available_tables:
                return True
            for product_id in request.product_ids:
                product = self.product(product_id)
                if table in product.get("tables", []):
                    if product_id in failed_products or product_id in gapped_products:
                        return False
            return False

        # 请求产品的表未进入快照：记 limitation，是否阻断取决于用途所需表
        snapshot_products = {item["product_id"] for item in manifest.get("items", [])}
        for product_id in request.product_ids:
            product = self.product(product_id)
            for table in product.get("tables", []):
                if table not in available_tables:
                    reason = (
                        f"product {product_id} failed or gapped"
                        if product_id in snapshot_products
                        else f"product {product_id} not in snapshot"
                    )
                    limitations.append(f"snapshot_missing_table: {table} ({reason})")

        # 用途级资格
        allowed_uses = []
        for purpose, tables in PURPOSE_TABLES.items():
            if obtained_ok and all(table_ok(table) for table in tables):
                allowed_uses.append(purpose)
        for table in PURPOSE_TABLES[request.purpose]:
            if not table_ok(table):
                limitations.append(f"missing_table_for_{request.purpose}: {table}")

        # 对象级检查
        historical_blocked = False
        if request.codes:
            calendar = self.read_table(ref, "calendar")
            trading_days = sorted(
                str(day)
                for day, flag in zip(calendar["date"], calendar["is_trading_day"], strict=False)
                if str(flag) in ("True", "1", "true")
                and request.start.isoformat() <= str(day) <= request.end.isoformat()
            )
            bars = self.read_table(ref, "daily_bars")
            status = self.read_table(ref, "security_status")
            members = self.read_table(ref, "memberships")
            financials = self.read_table(ref, "financial_reports")

            # 历史排名必须有当时可知成员；只有当前快照成员时拒绝该范围
            if request.purpose == "ranking_historical":
                kinds = (
                    set(members["membership_kind"].astype(str)) if len(members) else set()
                )
                if not kinds - {"current_snapshot"}:
                    historical_blocked = True
                    limitations.append(
                        "ranking_historical blocked: no PIT membership"
                        " (current_snapshot only)"
                    )

            for code in request.codes:
                market = code.rsplit(".", 1)[-1].upper()
                if covered_markets and market not in covered_markets:
                    excluded[code].append("market_not_covered")
                    limitations.append(
                        f"{code}: market {market} not covered by snapshot products"
                    )

                if len(members):
                    own = members[members["code"] == code]
                    if not len(own):
                        excluded[code].append("no_membership")
                    else:
                        late = own[
                            own["available_at"].astype(str) > as_of.isoformat()
                        ]
                        if len(late):
                            excluded[code].append("member_not_available_at_as_of")
                        if request.purpose == "ranking_historical" and (
                            own["membership_kind"] == "current_snapshot"
                        ).any():
                            excluded[code].append("no_pit_membership")

                own_bars = bars[bars["code"] == code] if len(bars) else bars
                own_status = status[status["code"] == code] if len(status) else status
                bar_days = set(own_bars["date"].astype(str))
                suspended_days = set(
                    own_status[own_status["is_suspended"].astype(str).isin(("True", "1", "true"))][
                        "date"
                    ].astype(str)
                )
                missing = [
                    day
                    for day in trading_days
                    if day not in bar_days and day not in suspended_days
                ]
                if suspended_days & set(trading_days):
                    limitations.append(
                        f"{code}: suspended sessions {sorted(suspended_days & set(trading_days))}"
                        " recorded, not filled"
                    )
                if missing:
                    excluded[code].append(f"missing_sessions:{len(missing)}")
                    if trading_days and trading_days[-1] in missing:
                        excluded[code].append("missing_latest_session")
                if len(own_status):
                    last_day = trading_days[-1] if trading_days else request.end.isoformat()
                    last = own_status[own_status["date"].astype(str) == last_day]
                    if len(last):
                        row = last.iloc[-1]
                        if str(row["is_st"]) in ("True", "1", "true"):
                            excluded[code].append("risk_alert")
                        if str(row["is_listed"]) in ("False", "0", "false"):
                            excluded[code].append("delisted")

                if request.purpose == "ep_value_preparation":
                    own_fin = financials[financials["code"] == code] if len(financials) else financials
                    known = own_fin[
                        own_fin["available_at"].astype(str) <= as_of.isoformat()
                    ]
                    if not len(known):
                        excluded[code].append("no_pit_financials")
                    elif len(own_fin) > len(known):
                        limitations.append(
                            f"{code}: late_revision_ignored "
                            f"({len(own_fin) - len(known)} rows after as_of)"
                        )

                if not excluded[code]:
                    eligible.append(code)

        can_run = request.purpose in allowed_uses and not historical_blocked
        report = QualityReport(
            can_run=can_run,
            allowed_uses=tuple(allowed_uses),
            eligible_ids=tuple(eligible),
            excluded={key: tuple(value) for key, value in excluded.items() if value},
            limitations=tuple(limitations),
        )
        self._persist_qualification(request, ref, report)
        return report

    def _persist_qualification(
        self, request: DataRequest, ref: SnapshotRef, report: QualityReport
    ) -> None:
        qual_dir = self.root / "qualification"
        qual_dir.mkdir(parents=True, exist_ok=True)
        record_id = f"qual-{datetime.now(CN).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        _write_json(
            qual_dir / f"{record_id}.json",
            {
                "record_id": record_id,
                "snapshot_id": ref.snapshot_id,
                "request": data_request_to_dict(request),
                "report": {
                    "can_run": report.can_run,
                    "allowed_uses": list(report.allowed_uses),
                    "eligible_ids": list(report.eligible_ids),
                    "excluded": {k: list(v) for k, v in report.excluded.items()},
                    "limitations": list(report.limitations),
                },
            },
        )
