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

from hetu_data.access import AccessDenied, check_product_access
from hetu_data.locking import worker_lock
from hetu_data.models import (
    DataRequest,
    SnapshotRef,
    data_request_from_dict,
    data_request_to_dict,
)
from hetu_data.models import (
    QualityReport as QualityReport,
)

CN = ZoneInfo("Asia/Shanghai")

FREE_PERMISSION_STATUSES = ("verified_free", "declared_free")
CODELESS_KINDS = ("catalog", "calendar")
SUPPORTED_ACTIONS = ("bonus_split", "cash_dividend")


def query_column(table, column):
    """旧CSV导入可保存数字字符串；查询统一日线数值类型，不重写旧文件。"""
    if table == "daily_bars" and column in {
        "open_raw",
        "high_raw",
        "low_raw",
        "close_raw",
        "close_price_return",
        "close_total_return",
        "volume_shares",
        "amount_cny",
    }:
        return f'TRY_CAST("{column}" AS DOUBLE) AS "{column}"'
    return f'"{column}"'


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
    spans = sorted(
        (str(r["start"]), str(r["end"])) for r in _action_query_records(entry) if r.get("basis") == "ex_date"
    )
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
    cross_section_replay: tuple[str, dict] | None


TABLE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "terminal_disposals": {
        "columns": [
            "code",
            "date",
            "cash_per_share",
            "available_at",
            "evidence_ref",
            "sha256",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "securities": {
        "columns": [
            "code",
            "name",
            "market",
            "ipo_date",
            "out_date",
            "security_type",
            "status",
            "product_id",
            "source_ref",
        ],
        "keys": ["code"],
    },
    "calendar": {
        "columns": ["date", "is_trading_day", "product_id", "source_ref"],
        "keys": ["date"],
    },
    "daily_bars": {
        "columns": [
            "date",
            "code",
            "open_raw",
            "high_raw",
            "low_raw",
            "close_raw",
            "close_price_return",
            "close_total_return",
            "volume_shares",
            "amount_cny",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "security_status": {
        "columns": [
            "date",
            "code",
            "is_st",
            "is_suspended",
            "is_listed",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "corporate_actions": {
        "columns": [
            "code",
            "ex_date",
            "action_type",
            "m",
            "d",
            "terms_raw",
            "available_at",
            "record_date",
            "pay_date",
            "stock_listing_date",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "ex_date", "action_type"],
    },
    "index_bars": {
        "columns": [
            "code",
            "date",
            "close",
            "amount_cny",
            "return_basis",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "boards": {
        "columns": [
            "board_id",
            "board_kind",
            "name",
            "provider",
            "classification_version",
            "product_id",
            "source_ref",
        ],
        "keys": ["board_id"],
    },
    "theme_library": {
        "columns": [
            "theme_code",
            "theme_name",
            "parent_code",
            "parent_name",
            "product_id",
            "source_ref",
        ],
        "keys": ["theme_code"],
    },
    "memberships": {
        "columns": [
            "board_id",
            "code",
            "observed_at",
            "available_at",
            "effective_from",
            "effective_to",
            "membership_kind",
            "product_id",
            "source_ref",
        ],
        "keys": ["board_id", "code"],
    },
    "market_cap": {
        "columns": [
            "code",
            "date",
            "market_cap_cny",
            "available_at",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "date"],
    },
    "financial_reports": {
        "columns": [
            "code",
            "report_period",
            "ann_date",
            "available_at",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "report_period", "available_at"],
    },
    # 二期回测事实表：历史证券（含退市）、历史交易制度、公开时点证据
    "historical_securities": {
        "columns": [
            "code",
            "valid_from",
            "valid_to",
            "listed",
            "market",
            "board",
            "available_at",
            "evidence_ref",
            "product_id",
            "source_ref",
        ],
        "keys": ["code", "valid_from", "available_at"],
    },
    "trading_rules": {
        "columns": [
            "rule_id",
            "market",
            "board",
            "security_type",
            "valid_from",
            "valid_to",
            "min_buy_qty",
            "qty_step",
            "tick_size",
            "settlement",
            "limit_rule",
            "fee_rule",
            "product_id",
            "source_ref",
        ],
        "keys": ["rule_id", "valid_from"],
    },
    "knowledge_evidence": {
        "columns": [
            "table_name",
            "record_key",
            "revision_id",
            "supersedes",
            "published_at",
            "evidence_ref",
            "sha256",
            "verified",
            "product_id",
            "source_ref",
        ],
        "keys": ["table_name", "record_key", "revision_id"],
    },
}

# 新源的观察/辅助事实独立保存，不冒充历史证券或完整交易状态。
TABLE_SCHEMAS.update(
    {
        "security_observations": {
            "columns": [
                "code",
                "date",
                "name",
                "total_shares",
                "observed_at",
                "revision_id",
                "product_id",
                "source_ref",
            ],
            "keys": ["code", "date"],
        },
        "adjustment_factors": {
            "columns": ["code", "date", "adj_factor", "product_id", "source_ref"],
            "keys": ["code", "date"],
        },
        "action_versions": {
            "columns": [
                "code",
                "revision_id",
                "div_proc",
                "ann_date",
                "published_at",
                "supersedes",
                "ex_date",
                "record_date",
                "pay_date",
                "stock_listing_date",
                "observed_at",
                "product_id",
                "source_ref",
            ],
            "keys": ["code", "revision_id"],
        },
        "security_events": {
            "columns": ["code", "date", "event_type", "suspend_timing", "product_id", "source_ref"],
            "keys": ["code", "date", "event_type", "suspend_timing"],
        },
        "price_limits": {
            "columns": ["code", "date", "reference_price", "up_limit", "down_limit", "product_id", "source_ref"],
            "keys": ["code", "date"],
        },
    }
)


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


def _date_gaps(start: date, end: date, covered: list[tuple[date, date]]) -> list[tuple[date, date]]:
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
        raise UnsupportedActionError(f"{code}: unverified action types {sorted(bad['action_type'].unique())}")
    factors: dict[str, tuple[float, float]] = {}
    for ex_date, group in events.groupby("ex_date"):
        m = float(pd.to_numeric(group["m"], errors="coerce").fillna(1.0).prod())
        d = float(pd.to_numeric(group["d"], errors="coerce").fillna(0.0).sum())
        factors[str(ex_date)] = (m, d)
    return factors


def _build_continuous(bars: pd.DataFrame, actions: pd.DataFrame, column: str, total: bool) -> pd.DataFrame:
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
                raise DataQualityError(f"{code}: non-positive adjustment factor on {dates[index]}")
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
            config_path = root_config if root_config.exists() else None
        if config_path is None:
            from importlib.resources import files

            self.config = json.loads(
                files("hetu_data").joinpath("resources/data-products.json").read_text(encoding="utf-8")
            )
        else:
            self.config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        self._providers = dict(providers) if providers is not None else None
        self.invocations: list[dict[str, object]] = []
        self._manifest_cache: dict[str, tuple[tuple[int, ...], dict[str, object]]] = {}

    # ---- 准入 ----

    def product(self, product_id: str) -> Mapping[str, object]:
        try:
            return {**self.config["products"][product_id], "product_id": product_id}
        except KeyError:
            raise AccessDenied(f"unknown product: {product_id}") from None

    def _grant(self, product_id: str) -> dict | None:
        path = self.root / "data-entitlements.json"
        grants = json.loads(path.read_text()).get("grants", []) if path.exists() else []
        return next((g for g in grants if g.get("product_id") == product_id), None)

    def check_access(self, mode: str, product: Mapping[str, object], *, operation: str = "acquire") -> None:
        grant = self._grant(str(product.get("product_id", "")))
        check_product_access(mode, product, grant, operation=operation)
        fixed_market = product.get("fixed_market")
        if fixed_market and fixed_market not in (grant or {}).get("markets", []):
            raise AccessDenied("product market outside current grant")

    def _check_scope(self, request: DataRequest, product_id: str, operation: str) -> None:
        product = self.product(product_id)
        self.check_access(request.mode, product, operation=operation)
        cross_section = product.get("cross_section")
        if cross_section:
            if request.codes_for(product_id) or (cross_section == "trade_day" and request.start != request.end):
                raise ValueError("market cross-section requires no code filter and a single day for trade_day")
        if product.get("fee_class") != "paid":
            return
        grant = self._grant(product_id) or {}
        if cross_section and operation == "acquire" and grant.get("acquire_cross_section") is not True:
            raise AccessDenied("whole-market raw response requires explicit cross-section acquisition permission")
        if not (
            grant.get("start", "~") <= request.start.isoformat() and grant.get("end", "") >= request.end.isoformat()
        ):
            raise AccessDenied("request outside granted period")
        codes = request.codes_for(product_id)
        if any(code not in grant.get("codes", []) for code in codes):
            raise AccessDenied("request object outside product grant")
        if any(code.rsplit(".", 1)[-1] not in grant.get("markets", []) for code in codes):
            raise AccessDenied("request market outside product grant")

    def _check_paid_rows(self, product_id, frame, table):
        if self.product(product_id).get("fee_class") != "paid":
            return
        grant = self._grant(product_id) or {}
        if "code" in frame:
            required = "code" in TABLE_SCHEMAS[table]["columns"]
            codes = frame.code.dropna()
            if (required and frame.code.isna().any()) or not codes.isin(grant.get("codes", [])).all():
                raise AccessDenied("stored/imported rows exceed current object grant")
            if not codes.astype(str).str.rsplit(".", n=1).str[-1].isin(grant.get("markets", [])).all():
                raise AccessDenied("stored/imported rows exceed current market grant")
        date_column = "date" if "date" in frame else "ex_date" if table == "corporate_actions" else None
        if date_column:
            dates = frame[date_column]
            if dates.isna().any() or not dates.astype(str).between(grant.get("start", "~"), grant.get("end", "")).all():
                raise AccessDenied("stored/imported rows exceed current period grant")

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
                raise SnapshotTamperedError(f"locked asset mismatch: {ref.snapshot_id}/{entry['path']}")

    def _publish(self, tmp_dir: Path, snapshot_id: str, doc: Mapping[str, object]) -> SnapshotRef:
        doc["source_grants"] = {
            i["product_id"]: self._grant(i["product_id"])
            for i in doc.get("items", [])
            if self.product(i["product_id"]).get("fee_class") == "paid"
        }
        self._year_layout(tmp_dir, doc)
        shutil.rmtree(tmp_dir / ".merge", ignore_errors=True)
        _write_json(tmp_dir / "manifest.json", doc)
        final_dir = self._snapshot_dir(snapshot_id)
        if final_dir.exists():
            raise RuntimeError(f"snapshot already published: {snapshot_id}")
        tmp_dir.rename(final_dir)
        return SnapshotRef(snapshot_id=snapshot_id, root=self.root)

    def _logical_entries(self, manifest):
        """年度文件按独立对象期间投影为旧规划器可消费的逻辑月分区。"""
        for entry in manifest.get("table_files", []):
            if "year" not in entry:
                yield entry
                continue
            for cov in manifest.get("object_coverage", []):
                if cov["product_id"] != entry["product_id"] or cov["table"] != entry["table"]:
                    continue
                for month in _month_keys(date.fromisoformat(cov["start"]), date.fromisoformat(cov["end"])):
                    if month[:4] != entry["year"]:
                        continue
                    left, right = _month_bounds(month)
                    span = [max(cov["start"], left.isoformat()), min(cov["end"], right.isoformat())]
                    yield {
                        **entry,
                        "partition": f"code={cov['code']}|month={month}",
                        "coverage": span,
                        "shared_year": True,
                    }

    def _year_layout(self, tmp_dir, doc):
        """只对选定日线启用；逐年处理，不把20年全库装进一个DataFrame。"""
        table_files = doc.get("table_files", [])
        object_coverage = doc.setdefault("object_coverage", [])
        products = {
            e["product_id"] for e in table_files if self.product(e["product_id"]).get("storage_layout") == "year"
        }
        for pid in products:
            # 已年度化的继承文件不重写，其逐对象覆盖由父清单一并继承。
            entries = [
                e for e in table_files if e["product_id"] == pid and e["table"] == "daily_bars" and "year" not in e
            ]
            by_year = {}
            for entry in entries:
                if "|month=" not in entry["partition"]:
                    raise DataQualityError("year layout requires per-object query coverage")
                year = entry["partition"].rsplit("month=", 1)[1][:4]
                by_year.setdefault(year, []).append(entry)
                span = entry.get("coverage")
                if span:
                    object_coverage.append(
                        dict(
                            product_id=pid,
                            table="daily_bars",
                            code=entry["partition"].split("|", 1)[0].removeprefix("code="),
                            start=span[0],
                            end=span[1],
                            query_basis="provider_observation",
                            evidence_refs=[
                                r["evidence_ref"]
                                for i in doc["items"]
                                if i["product_id"] == pid
                                for r in i.get("raw_files", [])
                                if r.get("evidence_ref")
                            ],
                        )
                    )
            for year, parts in sorted(by_year.items()):
                frames = [pd.read_parquet(tmp_dir / e["path"]) for e in parts]
                nonempty = [frame for frame in frames if len(frame)]
                combined = pd.concat(nonempty, ignore_index=True) if nonempty else frames[0].iloc[0:0]
                combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
                combined = combined.sort_values(["code", "date"]).reset_index(drop=True)
                target = tmp_dir / "tables/daily_bars" / f"part-{pid}-year-{year}.parquet"
                combined.to_parquet(target, index=False, compression="zstd")
                for entry in parts:
                    old = tmp_dir / entry["path"]
                    if old != target:
                        old.unlink(missing_ok=True)
                    table_files.remove(entry)
                table_files.append(
                    dict(
                        path=str(target.relative_to(tmp_dir)),
                        sha256=_sha256(target),
                        product_id=pid,
                        table="daily_bars",
                        partition=f"year={year}",
                        year=year,
                        compression="zstd",
                    )
                )

    def _materialize_shared(self, tmp_dir, source, entry, key, *, span=None):
        if not entry.get("shared_year"):
            return source
        code, month = key.removeprefix("code=").split("|month=", 1)
        frame = pd.read_parquet(source)
        frame = frame[frame.code.eq(code) & frame.date.astype(str).str.startswith(month)]
        if span:
            frame = frame[frame.date.astype(str).between(*span)]
        target = tmp_dir / ".merge" / f"{uuid.uuid4().hex}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        return target

    def _published_manifests(self) -> list[tuple[str, dict[str, object]]]:
        current = {}
        for child in sorted((self.root / "snapshots").iterdir()):
            if child.name.startswith(".tmp-") or not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            try:
                stat = manifest_path.stat()
            except FileNotFoundError:
                continue
            # 本进程只缓存 JSON 解析。复用分区时仍按原清单校验内容哈希。
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            saved = self._manifest_cache.get(child.name)
            current[child.name] = (
                saved if saved is not None and saved[0] == stamp
                else (stamp, json.loads(manifest_path.read_text(encoding="utf-8")))
            )
        self._manifest_cache = current
        return [(name, saved[1]) for name, saved in current.items()]

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
                keys = product.get("keys_by_table", {}).get(table_name, schema["keys"])
                if frame.duplicated(subset=keys).any():
                    raise DataQualityError(f"duplicate key {keys} in {table_name} for {product_id}")
            out[table_name] = frame
        return out

    # ---- 分区 ----

    def _cross_section_codes(self, product_id):
        grant = self._grant(product_id) or {}
        markets = grant.get("markets", [])
        return sorted({code for code in grant.get("codes", []) if code.rsplit(".", 1)[-1] in markets})

    def _bound_provider_tables(self, product_id, tables, request):
        """全目录/全史原件保持原形，标准表只落本次请求的对象和事实期间。"""
        product = self.product(product_id)
        if product.get("provider") != "tushare":
            return tables
        bounded = {}
        codes = request.codes_for(product_id)
        scoped_cross_section = bool(product.get("cross_section"))
        if scoped_cross_section:
            codes = self._cross_section_codes(product_id)
        for name, frame in tables.items():
            if (codes or scoped_cross_section) and "code" in TABLE_SCHEMAS[name]["columns"]:
                frame = frame[frame.code.isin(codes)]
            if "date" in frame:
                frame = frame[frame.date.between(request.start.isoformat(), request.end.isoformat())]
            elif name == "corporate_actions":
                frame = frame[frame.ex_date.between(request.start.isoformat(), request.end.isoformat())]
            bounded[name] = frame.copy()
        return bounded

    def _provider_originals(self, tmp_dir, product_id, raw, tables, raw_files, table_files):
        """保存供应商原字节并生成观察证据；哈希通过不等于历史公开/PIT已证明。"""
        responses = raw.attrs.get("provider_responses", [])
        if not responses:
            return
        evidence_dir = self.root / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        knowledge = []
        for response in responses:
            body = response["body"].encode("utf-8")
            digest = hashlib.sha256(body).hexdigest()
            if digest != response["sha256"]:
                raise SnapshotTamperedError("provider response bytes differ from recorded hash")
            source = self.product(product_id)["provider"]
            filename = f"{_safe_name(str(source))}-{digest}.json"
            evidence = evidence_dir / filename
            if evidence.exists() and evidence.read_bytes() != body:
                raise SnapshotTamperedError("existing source evidence changed")
            evidence.write_bytes(body)
            target = tmp_dir / "raw" / product_id / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            raw_files.append(
                {
                    "path": str(target.relative_to(tmp_dir)),
                    "sha256": digest,
                    "api": response["api"],
                    "params": response["params"],
                    "obtained_at": response["obtained_at"],
                    "evidence_ref": filename,
                }
            )
            for table, frame in tables.items():
                for row in frame.to_dict("records"):
                    revision = str(row.get("revision_id", ""))
                    if not revision.startswith(digest + ":"):
                        continue
                    key = str(row.get("code", ""))
                    if row.get("date"):
                        key += "|" + str(row["date"])
                    if row.get("ex_date"):
                        key += "|" + str(row["ex_date"]) + "|" + str(row.get("action_type", ""))
                    knowledge.append(
                        dict(
                            table_name=table,
                            record_key=key,
                            revision_id=revision,
                            supersedes="",
                            published_at=None,
                            observed_at=response["obtained_at"],
                            evidence_ref=filename,
                            sha256=digest,
                            verified=False,
                            product_id=product_id,
                            source_ref=str(target.relative_to(tmp_dir)),
                        )
                    )
        if knowledge:
            target = tmp_dir / "tables/knowledge_evidence" / f"observations-{uuid.uuid4().hex}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(knowledge).to_parquet(target, index=False)
            table_files.append(
                dict(
                    path=str(target.relative_to(tmp_dir)),
                    sha256=_sha256(target),
                    product_id=product_id,
                    table="knowledge_evidence",
                    partition=f"observation={target.stem}",
                )
            )

    def _product_has_date(self, product_id: str) -> bool:
        """首表是否按日期分区；memberships/corporate_actions 等无 date 列按 code 分区。"""
        product = self.product(product_id)
        return "date" in TABLE_SCHEMAS[product["tables"][0]]["columns"]

    @staticmethod
    def _entry_coverage(entry: Mapping[str, object], request_doc: object) -> list[str] | None:
        """分区实际覆盖期间 [min, max]；新快照持久化 coverage，旧快照按请求窗口推导。"""
        stored = entry.get("coverage")
        if entry.get("table") == "corporate_actions" and entry.get("query_coverage"):
            spans = sorted(
                (str(r["start"]), str(r["end"])) for r in _action_query_records(entry) if r.get("basis") == "ex_date"
            )
            if spans:
                # 历史组合的通用 coverage 可能宽于真实实施日查询证明。
                # 只复用第一段连续证明；不能用 min/max 跨过未知间隔。
                left, right = spans[0]
                for start, end in spans[1:]:
                    if date.fromisoformat(start) > date.fromisoformat(right) + timedelta(days=1):
                        break
                    right = max(right, end)
                if isinstance(stored, (list, tuple)) and left == str(stored[0]):
                    return [left, right]
                # 缺前段证明的旧公告年/未知资产仍保留原范围与受限资格；
                # 不因后段已证就隐式重查旧段，或升级旧段的查询口径。
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
        collected: dict[str, dict[str, list[tuple[str, str, str, str, list[str] | None]]]] = {}
        for snapshot_id, manifest in loaded:
            created = str(manifest.get("created_at", ""))
            request_doc = manifest.get("request")
            for entry in self._logical_entries(manifest):
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

    def _verified_source(self, base_id: str, relpath: str, base_manifests: dict[str, dict[str, object]]) -> Path:
        """复用旧分区前按旧快照清单 sha256 校验来源文件，篡改一律拒绝。"""
        if base_id not in base_manifests:
            base_manifests[base_id] = self.load_manifest(SnapshotRef(snapshot_id=base_id, root=self.root))
        for entry in base_manifests[base_id].get("table_files", []):
            if entry["path"] == relpath:
                source = self._snapshot_dir(base_id) / relpath
                if not source.exists() or _sha256(source) != entry["sha256"]:
                    raise SnapshotTamperedError(f"locked asset mismatch: {base_id}/{relpath}")
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
            base_manifests[base_id] = self.load_manifest(SnapshotRef(snapshot_id=base_id, root=self.root))
        manifest = base_manifests[base_id]
        request_doc = manifest.get("request")
        if not isinstance(request_doc, Mapping):
            return None
        start, end = request_doc.get("start"), request_doc.get("end")
        if not start or not end:
            return None
        domains = request_doc.get("codes_by_product")
        product_codes = domains.get(product_id) if isinstance(domains, Mapping) else None
        queried = product_codes if product_codes is not None else request_doc.get("codes") or ()
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
                        raise SnapshotTamperedError(f"locked asset mismatch: {base_id}/{entry['path']}")
                    return [str(start), str(end)]
            return None
        # 派生分区空间（批次按非股票标识取数）：全部 raw 完整核验才可证明。
        # 核验结论与具体代码无关，按 (快照, 产品) 缓存，prepare 全程核验一次。
        if not raw_entries:
            return None
        cache_key = (base_id, product_id)
        if derived_cache is not None and cache_key in derived_cache:
            return derived_cache[cache_key]
        failed = {str(failure["product_id"]) for failure in manifest.get("failures", [])}
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
                    raise SnapshotTamperedError(f"locked asset mismatch: {base_id}/{entry['path']}")
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
            failed = {str(failure["product_id"]) for failure in manifest.get("failures", [])}
            if product_id in failed:
                continue
            request_doc = manifest.get("request")
            if not isinstance(request_doc, Mapping):
                continue
            start, end = request_doc.get("start"), request_doc.get("end")
            if not start or not end:
                continue
            domains = request_doc.get("codes_by_product")
            product_codes = domains.get(product_id) if isinstance(domains, Mapping) else None
            queried = product_codes if product_codes is not None else request_doc.get("codes") or ()
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
                raise SnapshotTamperedError(f"locked asset mismatch: {snapshot_id}/{hit_entry['path']}")
            return [str(start), str(end)], snapshot_id
        return None

    def _plan_product(
        self,
        product_id: str,
        request: DataRequest,
        coverage: Mapping[str, Mapping[str, list[tuple[str, str, str, str, list[str] | None]]]],
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
            cross_section_replay=None,
        )

        def add_copy(key: str, hit: tuple[str, str, str, str, list[str] | None]) -> None:
            self._verified_source(hit[0], hit[1], base_manifests)
            copies.append((key, hit[0], hit[1], hit[2]))
            if hit[4] is not None:
                copy_coverage[key] = hit[4]

        if product.get("kind") in CODELESS_KINDS:
            key = f"range={request.start.isoformat()}_{request.end.isoformat()}"
            hits = coverage.get(product_id, {}).get(key, [])
            scoped_cross_section = product.get("provider") == "tushare" and product.get("cross_section")
            if hits and not scoped_cross_section:
                add_copy(key, hits[0])
                return plan
            if hits:
                from hetu_data.providers.tushare import calls_for

                (api, params), = calls_for(product_id, (), request.start, request.end)
                scope = self._cross_section_codes(product_id)
                for hit in hits:
                    base_id, path, *_ = hit
                    manifest = base_manifests[base_id]
                    entry = next(entry for entry in manifest["table_files"] if entry["path"] == path)
                    saved_scope = entry.get("cross_section_codes")
                    if saved_scope is None:
                        # 旧清单只能以实际已保存代码证明同一范围，不能把缺行当成已处理对象。
                        source = self._verified_source(base_id, path, base_manifests)
                        saved_scope = sorted(pd.read_parquet(source, columns=["code"]).code.dropna().unique())
                    if saved_scope == scope:
                        add_copy(key, hit)
                        return plan
                    for item in manifest["items"]:
                        if item["product_id"] != product_id:
                            continue
                        original = next((raw for raw in item["raw_files"]
                                         if raw.get("api") == api and raw.get("params") == params), None)
                        if original is not None:
                            # 保持当前快照选择的顺序，不让较早的宽范围遮蔽已登记修订。
                            plan["cross_section_replay"] = (base_id, original)
                            return plan
            calls.append(((), request.start, request.end))
            fetched_partitions.append(key)
            return plan
        codes = request.codes_for(product_id)
        if not codes:
            raise ValueError(f"codes required for {product_id}; empty codes only for catalog/calendar")
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
                    if not (
                        doc.get("start", "~") <= request.start.isoformat()
                        and doc.get("end", "") >= request.end.isoformat()
                    ):
                        continue
                    if any(f["product_id"] == product_id for f in manifest.get("failures", [])):
                        continue
                    if (manifest.get("gaps") or {}).get(product_id):
                        continue
                    raw_entries = [
                        entry
                        for item in manifest.get("items", [])
                        if item.get("product_id") == product_id
                        for entry in item.get("raw_files", [])
                    ]
                    expected = {f"response-{_safe_name(code)}-{doc['start']}_{doc['end']}.parquet" for code in codes}
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
                        add_copy(
                            entry["partition"],
                            (
                                base_id,
                                entry["path"],
                                entry["table"],
                                "",
                                self._entry_coverage(entry, doc) or [doc["start"], doc["end"]],
                            ),
                        )
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
                        cov = self._recovered_coverage(product_id, code, hit[0], base_manifests, derived_cache)
                    if cov is not None:
                        chosen = (hit[0], hit[1], hit[2], hit[3], cov)
                        break
                if chosen is None and not hits and product.get("kind") != "membership":
                    # 任何快照都没有该代码的分区：原始批次的成功空响应 raw
                    # （核验通过）证明"已核验无记录"，写空分区且不安排取数
                    recovered = self._recovered_empty_coverage(product_id, code, base_manifests, published)
                    if recovered is not None:
                        span, base_id = recovered
                        if span[0] <= request.start.isoformat() and span[1] >= request.end.isoformat():
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
                    covered = [(date.fromisoformat(cov[0]), date.fromisoformat(cov[1]))]
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
                month_start, month_end = _month_bounds(key[len(prefix) :])
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

    def _build_plans(self, request, published, base_manifests):
        coverage = self._coverage_index(published)
        plans = {
            pid: self._plan_product(pid, request, coverage, base_manifests, published, {})
            for pid in request.product_ids
        }
        if request.refresh_from is None:
            return plans
        for pid, plan in plans.items():
            if self.product(pid).get("kind") == "calendar":
                if request.refresh_from != request.start:
                    raise ValueError("calendar refresh requires a separately bounded request starting at refresh_from")
                # 明确有界的新查询产生新快照，不复用旧值充当刷新，也不覆盖旧日历。
                plan["copies"] = []
                plan["copy_coverage"] = {}
                plan["calls"] = [((), request.start, request.end)]
                continue
            if self.product(pid).get("kind") in CODELESS_KINDS:
                raise ValueError("catalog/calendar refresh requires a separately bounded request")
            refresh = request.refresh_from
            calls = [
                (codes, left, min(right, refresh - timedelta(days=1)))
                for codes, left, right in plan["calls"]
                if left < refresh
            ]
            calls.extend(((code,), refresh, request.end) for code in request.codes_for(pid))
            plan["calls"] = calls
            for copy in list(plan["copies"]):
                key, base, path, _ = copy
                affected = "|month=" not in key or key.rsplit("month=", 1)[1] >= refresh.isoformat()[:7]
                if affected:
                    plan["copies"].remove(copy)
                    plan["merges"][key] = (path, base, plan["copy_coverage"].pop(key))
        return plans

    def plan(self, request: DataRequest) -> dict:
        for pid in request.product_ids:
            self._check_scope(request, pid, "read")
        published = self._published_manifests()
        plans = self._build_plans(request, published, dict(published))
        segments = [
            dict(
                product_id=pid,
                codes=list(codes),
                start=left.isoformat(),
                end=right.isoformat(),
                refresh=bool(request.refresh_from and right >= request.refresh_from),
            )
            for pid, plan in plans.items()
            for codes, left, right in plan["calls"]
        ]
        segments.extend(
            dict(product_id=pid, codes=[], start=request.start.isoformat(), end=request.end.isoformat(),
                 refresh=False, local_cross_section_codes=self._cross_section_codes(pid))
            for pid, plan in plans.items() if plan["cross_section_replay"]
        )
        reused = [
            dict(product_id=pid, partition=key, snapshot_id=base, coverage=plan["copy_coverage"].get(key))
            for pid, plan in plans.items()
            for key, base, _, _ in plan["copies"]
        ]
        return dict(
            segments=segments,
            reused_coverage=reused,
            gaps=segments,
            refresh_segments=[s for s in segments if s["refresh"]],
        )

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
                elif has_date and product.get("provider") != "tushare":
                    gaps.setdefault(product_id, []).extend(fetched_partitions)
                if has_date and product.get("provider") == "tushare":
                    # 逐码成功查询的空月只保留“该口径返回空”的证据；不造bar、
                    # 不推断上市/停牌/PIT。旧免费产品的缺口行为保持不变。
                    present = {partition for partition, _part in partitions}
                    for code, segments in (queried_segments or {}).items():
                        for left, right in segments:
                            for month in _month_keys(date.fromisoformat(left), date.fromisoformat(right)):
                                key = f"code={code}|month={month}"
                                if key in present:
                                    continue
                                month_left, month_right = _month_bounds(month)
                                empty_cov[key] = [
                                    max(left, month_left.isoformat()),
                                    min(right, month_right.isoformat()),
                                ]
                                partitions.append((key, frame.iloc[0:0]))
                                present.add(key)
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
                    keys = product.get("keys_by_table", {}).get(table_name, schema["keys"])
                    part = part.drop_duplicates(subset=keys, keep="last" if request.refresh_from else "first")
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
                    coverage = empty_cov.get(partition) or self._requested_month_coverage(partition, request)
                elif partition.startswith("code="):
                    # code 分区事件产品：承诺覆盖即本次实际查询期间
                    coverage = empty_cov.get(partition) or [
                        request.start.isoformat(),
                        request.end.isoformat(),
                    ]
                elif partition.startswith("range=") and "date" in part.columns:
                    coverage = [request.start.isoformat(), request.end.isoformat()]
                same_batch = next((e for e in table_files if e["path"] == str(target.relative_to(tmp_dir))), None)
                same_batch_queries = []
                if same_batch is not None:
                    old_cov = same_batch.get("coverage")
                    disjoint = (
                        old_cov
                        and coverage
                        and (
                            date.fromisoformat(old_cov[1]) + timedelta(days=1) < date.fromisoformat(coverage[0])
                            or date.fromisoformat(coverage[1]) + timedelta(days=1) < date.fromisoformat(old_cov[0])
                        )
                    )
                    if disjoint:
                        target = target.with_stem(target.stem + "-" + uuid.uuid4().hex[:8])
                    else:
                        old_rows = pd.read_parquet(target)
                        combined = pd.concat([old_rows, part], ignore_index=True)
                        keys = product.get("keys_by_table", {}).get(table_name, schema["keys"])
                        if "revision_id" not in keys and combined.duplicated(subset=keys).any():
                            raise DataQualityError("overlapping same-batch facts require explicit revision identity")
                        part = combined.drop_duplicates(subset=keys, keep="first")
                        if old_cov and coverage:
                            coverage = [min(old_cov[0], coverage[0]), max(old_cov[1], coverage[1])]
                        same_batch_queries = _action_query_records(same_batch)
                        table_files.remove(same_batch)
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
                if product.get("provider") == "tushare" and product.get("cross_section"):
                    entry["cross_section_codes"] = self._cross_section_codes(product_id)
                if table_name == "corporate_actions":
                    records = same_batch_queries + list((prior_query_evidence or {}).get(partition, []))
                    code = partition.removeprefix("code=")
                    records.extend((query_evidence or {}).get(code, []))
                    if records:
                        entry["query_coverage"] = records
                        if coverage and action_query_covers(entry, *coverage):
                            entry["query_basis"] = "ex_date"
                table_files.append(entry)
                written.add(partition)
        return written

    def _import_provider_item(self, tmp_dir, item, raw_files, mode, table_files):
        """原始JSON导入复用Provider映射和标准写侧；不允许验收脚本补覆盖或资格。"""
        product_id = item["product_id"]
        product = self.product(product_id)
        request = data_request_from_dict(item["request"])
        if request.mode != mode or request.product_ids != (product_id,):
            raise AccessDenied("import request does not match product/mode")
        self._check_scope(request, product_id, "import")
        query = item["query"]
        if query["api"] != product.get("source_api"):
            raise DataQualityError("raw API does not match selected product")
        params = query["params"]
        if product.get("source_exchange"):
            if params.get("exchange") != product["source_exchange"]:
                raise DataQualityError("raw calendar exchange does not match selected product")
            if not {"start_date", "end_date"} <= params.keys():
                raise DataQualityError("raw calendar import requires both queried dates")
        if query["api"] == "stock_basic" and not params.get("ts_code"):
            observed = datetime.fromisoformat(item["source"]["obtained_at"]).astimezone(CN).date()
            if request.start != observed or request.end != observed:
                raise DataQualityError("directory observation cannot claim a historical coverage period")
        if params.get("ts_code") and params["ts_code"] not in request.codes_for(product_id):
            raise AccessDenied("raw query object outside import request")
        if "start_date" in params and (
            date.fromisoformat(params["start_date"]) > request.start
            or date.fromisoformat(params["end_date"]) < request.end
        ):
            raise DataQualityError("raw query cannot cover claimed import period")
        if product.get("cross_section") == "trade_day" and "trade_date" not in params:
            raise DataQualityError("market-date raw import requires trade_date")
        if "trade_date" in params and not request.start == request.end == date.fromisoformat(params["trade_date"]):
            raise DataQualityError("dated catalog is not a multi-day history")
        provider = self._provider(product["provider"])
        body = (tmp_dir / raw_files[0]["path"]).read_bytes()
        raw = provider.decode_response(body, api=query["api"], params=params, obtained_at=item["source"]["obtained_at"])
        tables = self._standardize(product_id, raw, raw_files[0]["path"], provider)
        tables = self._bound_provider_tables(product_id, tables, request)
        codes = request.codes_for(product_id)
        self._provider_originals(tmp_dir, product_id, raw, tables, raw_files, table_files)
        evidence = {code: [r for r in raw.attrs.get("query_evidence", []) if r["code"] == code] for code in codes}
        queried = {code: [[request.start.isoformat(), request.end.isoformat()]] for code in codes}
        self._write_partitions(
            tmp_dir, product_id, tables, request, table_files, {}, [], queried_segments=queried, query_evidence=evidence
        )

    # ---- 入口：导入 ----

    def import_files(self, manifest: Path, files: tuple[Path, ...]) -> SnapshotRef:
        with worker_lock(self.root, reentrant=True):
            manifest_path = Path(manifest)
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            mode = manifest_doc.get("mode", "free")
            items = manifest_doc["items"]
            available = {Path(file).name: Path(file) for file in files}
            # 先完成全部准入与校验，再落盘
            for item in items:
                product = self.product(item["product_id"])
                self.check_access(mode, product, operation="import")
                if item.get("format") == "provider_json":
                    imported_request = data_request_from_dict(item["request"])
                    self._check_scope(imported_request, item["product_id"], "import")
                source = item.get("source") or {}
                basis = source.get("permission_basis")
                if not isinstance(basis, str) or not basis.strip():
                    raise AccessDenied("permission_basis missing for imported file")
                if not source.get("name") or not source.get("obtained_at"):
                    raise AccessDenied("imported file source must carry name and obtained_at")
                self.check_access(
                    mode,
                    {
                        "product_id": item["product_id"],
                        "fee_class": source.get("fee_class"),
                        "permission": {"status": "verified_free"},
                        "provider": product.get("provider", "local_file"),
                    },
                    operation="import",
                )
                if product.get("fee_class") == "paid" and source.get("fee_class") != "paid":
                    raise AccessDenied("paid product cannot be disguised as a free local source")
                for name in item["files"]:
                    if name not in available:
                        raise FileNotFoundError(f"manifest file not provided: {name}")
                if product.get("fee_class") == "paid" and item.get("format") != "provider_json":
                    frame = pd.read_csv(available[item["files"][0]], dtype=str)
                    tables = self._standardize(item["product_id"], frame, "import-preflight", provider=None)
                    for table, standard in tables.items():
                        self._check_paid_rows(item["product_id"], standard, table)

            snapshot_id = _new_snapshot_id("imp")
            tmp_dir = self.root / "snapshots" / f".tmp-{snapshot_id}"
            written_items: list[dict[str, object]] = []
            table_files: list[dict[str, object]] = []
            base_ids = list(manifest_doc.get("base_snapshot_ids", []))
            for item in items:
                if item.get("supplement") and self.product(item["product_id"])["tables"] != ["corporate_actions"]:
                    raise ValueError("supplement only supports corporate_actions")
            replaced = {
                table
                for item in items
                if not item.get("supplement")
                for table in self.product(item["product_id"])["tables"]
            }
            inherited: dict[str, tuple[str, dict]] = {}
            bases = {}
            for base_id in base_ids:
                ref = SnapshotRef(base_id, self.root)
                base = self.load_manifest(ref)
                self._verify_hashes(ref, base)
                for entry in base["table_files"]:
                    self.check_access(mode, self.product(entry["product_id"]), operation="read")
                    if entry["table"] not in replaced:
                        inherited[entry["table"]] = (base_id, base)
                bases[base_id] = base
            inherited_products = set()
            inherited_coverage = []
            for table, (base_id, base) in inherited.items():
                self.read_table(SnapshotRef(base_id, self.root), table)
                inherited_coverage.extend(c for c in base.get("object_coverage", []) if c["table"] == table)
                prefix = Path("inherited") / base_id
                for file_index, entry in enumerate(base["table_files"]):
                    if entry["table"] != table:
                        continue
                    source = self.root / "snapshots" / base_id / entry["path"]
                    relative = Path("tables") / table / f"part-inherited-{file_index:012d}.parquet"
                    target = tmp_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    table_files.append({**entry, "path": str(relative), "inherited_from": base_id})
                    inherited_products.add(entry["product_id"])
                for item in base.get("items", []):
                    if item.get("table") != table:
                        continue
                    raw_files = []
                    for raw in item.get("raw_files", []):
                        relative = prefix / raw["path"]
                        target = tmp_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self.root / "snapshots" / base_id / raw["path"], target)
                        raw_files.append({**raw, "path": str(relative)})
                    written_items.append({**item, "raw_files": raw_files, "inherited_from": base_id})
            for item_index, item in enumerate(items):
                product = self.product(item["product_id"])
                source = item["source"]
                self._log_invocation(
                    "import",
                    item["product_id"],
                    {"provider": product.get("provider", "local_file"), "fee_class": source["fee_class"]},
                )
                raw_dir = tmp_dir / "raw" / item["product_id"]
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_files = []
                for name in item["files"]:
                    target = raw_dir / name
                    shutil.copyfile(available[name], target)
                    raw_files.append({"path": str(target.relative_to(tmp_dir)), "sha256": _sha256(target)})
                if item.get("format") == "provider_json":
                    self._import_provider_item(tmp_dir, item, raw_files, mode, table_files)
                    written_items.append(
                        dict(
                            product_id=item["product_id"],
                            table=item["table"],
                            source=source,
                            raw_files=raw_files,
                            query=item["query"],
                        )
                    )
                    continue
                raw_frame = pd.read_csv(raw_dir / item["files"][0], dtype=str)
                tables = self._standardize(item["product_id"], raw_frame, raw_files[0]["path"], provider=None)
                for table_name, frame in tables.items():
                    table_dir = tmp_dir / "tables" / table_name
                    table_dir.mkdir(parents=True, exist_ok=True)
                    if item.get("supplement"):
                        # 官方原件补充漏报事件；保留旧事实及实际查询证明，
                        # 不把新增事实本身升级为新的查询覆盖。重叠键须另作修订。
                        prior = [e for e in table_files if e["table"] == table_name]
                        if prior:
                            old = pd.concat([pd.read_parquet(tmp_dir / e["path"]) for e in prior])
                            keys = TABLE_SCHEMAS[table_name]["keys"]
                            if len(frame[keys].astype(str).merge(old[keys].astype(str), on=keys)):
                                raise DataQualityError("action supplement overlap; explicit revision required")
                        for code, group in frame.groupby("code"):
                            key = f"code={code}"
                            entry = next((e for e in prior if e["partition"] == key), None)
                            if entry is None:
                                raise DataQualityError("action supplement requires existing code partition")
                            combined = pd.concat([pd.read_parquet(tmp_dir / entry["path"]), group], ignore_index=True)
                            for column in ("m", "d"):
                                combined[column] = pd.to_numeric(combined[column], errors="raise")
                            name = (
                                f"part-{_safe_name(item['product_id'])}-supplement-"
                                f"{item_index}-{_safe_name(key)}.parquet"
                            )
                            target = table_dir / name
                            combined.to_parquet(target, index=False)
                            table_files.remove(entry)
                            # read_table按目录读取；移除本次暂存的旧副本，父快照不动。
                            (tmp_dir / entry["path"]).unlink()
                            parent_id, parent = inherited[table_name]
                            parent_entry = next(
                                e for e in parent["table_files"] if e["table"] == table_name and e["partition"] == key
                            )
                            updated = {k: v for k, v in entry.items() if k != "inherited_from"}
                            updated.update(
                                path=str(target.relative_to(tmp_dir)),
                                sha256=_sha256(target),
                                supplemented_from={
                                    "snapshot_id": parent_id,
                                    "path": parent_entry["path"],
                                    "sha256": parent_entry["sha256"],
                                },
                            )
                            table_files.append(updated)
                        continue
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
                "request": manifest_doc.get("request"),
                "items": written_items,
                "table_files": table_files,
                "object_coverage": inherited_coverage,
                "gaps": {
                    pid: value
                    for base in bases.values()
                    for pid, value in base.get("gaps", {}).items()
                    if pid in inherited_products
                },
                "failures": [
                    f
                    for base in bases.values()
                    for f in base.get("failures", [])
                    if f["product_id"] in inherited_products
                ],
                "revises": base_ids or None,
            }
            return self._publish(tmp_dir, snapshot_id, doc)

        # ---- 入口：准备（网络获取） ----

    def _provider(self, name: str) -> object:
        if self._providers is None:
            from hetu_data.providers import default_providers

            self._providers = default_providers()
        try:
            return self._providers[name]
        except KeyError:
            raise AccessDenied(f"no provider adapter registered: {name}") from None

    def prepare(self, request: DataRequest, *, allow_acquire: bool = True) -> SnapshotRef:
        with worker_lock(self.root, reentrant=True):
            for product_id in request.product_ids:
                product = self.product(product_id)
                self._check_scope(request, product_id, "read")
                if not request.codes and product.get("kind") not in CODELESS_KINDS:
                    raise ValueError(f"codes required for {product_id}; empty codes only for catalog/calendar")

            # 已发布清单全程只解析一次（恢复路径逐代码消费，避免重复解析放大）
            published = self._published_manifests()
            base_manifests: dict[str, dict[str, object]] = {
                snapshot_id: manifest for snapshot_id, manifest in published
            }
            plans = self._build_plans(request, published, base_manifests)
            if not allow_acquire and any(plan["calls"] for plan in plans.values()):
                raise AccessDenied("local cross-section plan cannot acquire missing data")
            needed_sources = {
                (base_id, relpath, key) for plan in plans.values() for key, base_id, relpath, _table in plan["copies"]
            }
            needed_sources.update(
                (base_id, relpath, key)
                for plan in plans.values()
                for key, (relpath, base_id, _cov) in plan["merges"].items()
            )
            source_entries = {
                (base_id, entry["path"], entry["partition"]): entry
                for base_id, manifest in published
                for entry in self._logical_entries(manifest)
                if (base_id, entry["path"], entry["partition"]) in needed_sources
            }
            reused_snapshots = {
                base_id
                for plan in plans.values()
                for base_id in [copy[1] for copy in plan["copies"]] + [merge[1] for merge in plan["merges"].values()]
            }
            reused_snapshots.update(plan["query_source"] for plan in plans.values() if plan["query_source"])
            reused_snapshots.update(plan["cross_section_replay"][0] for plan in plans.values()
                                    if plan["cross_section_replay"])
            if (
                not any(plan["calls"] or plan["merges"] or plan["recovered_empty"] or plan["cross_section_replay"]
                        for plan in plans.values())
                and len(reused_snapshots) == 1
            ):
                # 全部覆盖且同一快照满足：不重取、不新建
                return SnapshotRef(snapshot_id=reused_snapshots.pop(), root=self.root)

            snapshot_id = _new_snapshot_id("prep")
            tmp_dir = self.root / "snapshots" / f".tmp-{snapshot_id}"
            items: list[dict[str, object]] = []
            table_files: list[dict[str, object]] = []
            gaps: dict[str, list[str]] = {}
            failures: list[dict[str, object]] = []
            inherited: dict[str, str] = {
                product_id: plan["query_source"] for product_id, plan in plans.items() if plan["query_source"]
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
                        original = source_entries[(base_id, relpath, key)]
                        copy_cov = plan["copy_coverage"].get(key)
                        if original.get("shared_year"):
                            copy_cov = [
                                max(copy_cov[0], request.start.isoformat()),
                                min(copy_cov[1], request.end.isoformat()),
                            ]
                        source_file = self._materialize_shared(tmp_dir, source_file, original, key, span=copy_cov)
                        output_rel = (
                            f"tables/{table_name}/part-{product_id}-copy-{_safe_name(key)}.parquet"
                            if original.get("shared_year")
                            else relpath
                        )
                        target = tmp_dir / output_rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source_file, target)
                        entry: dict[str, object] = {
                            "path": output_rel,
                            "sha256": _sha256(target),
                            "product_id": product_id,
                            "table": table_name,
                            "partition": key,
                        }
                        if copy_cov is not None:
                            entry["coverage"] = copy_cov
                        for field in ("query_basis", "query_coverage", "cross_section_codes"):
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
                        empty_name = f"part-{_safe_name(product_id)}-{_safe_name(key)}.parquet"
                        empty_target = table_dir / empty_name
                        pd.DataFrame(columns=TABLE_SCHEMAS[table_name]["columns"]).to_parquet(empty_target, index=False)
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
                    replay = plan["cross_section_replay"]
                    if not plan["calls"] and not replay:
                        continue
                    self._check_scope(request, product_id, "import" if replay else "acquire")
                    try:
                        self._log_invocation("import" if replay else "fetch", product_id, product)
                        provider = self._provider(product["provider"])
                        fetched_tables: dict[str, list[pd.DataFrame]] = {}
                        raw_files: list[dict] = []
                        query_evidence: dict[str, list[dict]] = {}
                        inputs = [((), request.start, request.end)] if replay else plan["calls"]
                        for codes, seg_start, seg_end in inputs:
                            if replay:
                                base_id, original = replay
                                source = self._snapshot_dir(base_id) / original["path"]
                                body = source.read_bytes()
                                if hashlib.sha256(body).hexdigest() != original["sha256"]:
                                    raise SnapshotTamperedError(f"locked asset mismatch: {base_id}/{original['path']}")
                                raw = provider.decode_response(body, api=original["api"], params=original["params"],
                                                               obtained_at=original["obtained_at"])
                            else:
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
                            if observation := raw.attrs.get("query_observation"):
                                raw_entry["query_observation"] = observation
                            evidence = raw.attrs.get("query_evidence", [])
                            if evidence:
                                raw_entry["query_evidence"] = evidence
                            raw_files.append(raw_entry)
                            for record in evidence:
                                record = dict(record, source_ref=raw_rel, snapshot_id=snapshot_id)
                                query_evidence.setdefault(record["code"], []).append(record)
                            tables = self._standardize(product_id, raw, raw_rel, provider)
                            segment_request = data_request_from_dict(
                                {
                                    **data_request_to_dict(request),
                                    "codes": list(codes or request.codes_for(product_id)),
                                    "codes_by_product": {},
                                    "start": seg_start.isoformat(),
                                    "end": seg_end.isoformat(),
                                    "refresh_from": None,
                                }
                            )
                            tables = self._bound_provider_tables(product_id, tables, segment_request)
                            self._provider_originals(tmp_dir, product_id, raw, tables, raw_files, table_files)
                            for table_name, frame in tables.items():
                                fetched_tables.setdefault(table_name, []).append(frame)
                        merged = {}
                        for name, parts in fetched_tables.items():
                            nonempty = [part for part in parts if len(part)]
                            merged[name] = pd.concat(nonempty, ignore_index=True) if nonempty else parts[0].iloc[0:0]
                        # code 分区产品：本次实际查询的代码期间段，供空分区记 coverage
                        queried_segments: dict[str, list[list[str]]] = {}
                        if not self._product_has_date(product_id) or product.get("provider") == "tushare":
                            for codes, seg_start, seg_end in plan["calls"]:
                                if len(codes) == 1:
                                    queried_segments.setdefault(codes[0], []).append(
                                        [seg_start.isoformat(), seg_end.isoformat()]
                                    )
                        # 部分覆盖分区：先校验来源，再与新取数段合并
                        resolved_merges = {
                            key: (
                                self._materialize_shared(
                                    tmp_dir,
                                    self._verified_source(base_id, relpath, base_manifests),
                                    source_entries[(base_id, relpath, key)],
                                    key,
                                ),
                                old_cov,
                            )
                            for key, (relpath, base_id, old_cov) in plan["merges"].items()
                        }
                        written = self._write_partitions(
                            tmp_dir,
                            product_id,
                            merged,
                            request,
                            table_files,
                            gaps,
                            plan["fetched_partitions"],
                            resolved_merges,
                            queried_segments,
                            query_evidence,
                            {
                                key: _action_query_records(source_entries[(base_id, relpath, key)])
                                for key, (relpath, base_id, _cov) in plan["merges"].items()
                            },
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
                                    **{
                                        field: source_entries[(base_id, relpath, key)][field]
                                        for field in ("query_basis", "query_coverage")
                                        if field in source_entries[(base_id, relpath, key)]
                                    },
                                }
                            )
                        items.append(
                            {
                                "product_id": product_id,
                                "table": product["tables"][0],
                                "source": {
                                    "name": product["provider"],
                                    "fee_class": product["fee_class"],
                                    "source_url": product.get("permission", {}).get("source_url", ""),
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
            # 复用产品的原件与观察证据也进入新快照，不能只有标准分区而丢来源。
            for product_id, plan in plans.items():
                if self.product(product_id).get("provider") != "tushare":
                    continue
                parents = {c[1] for c in plan["copies"]} | {m[1] for m in plan["merges"].values()}
                if plan["query_source"]:
                    parents.add(plan["query_source"])
                for base_id in parents:
                    base = base_manifests[base_id]
                    self._verify_hashes(SnapshotRef(base_id, self.root), base)
                    copied_raw = []
                    for base_item in base.get("items", []):
                        if base_item["product_id"] != product_id:
                            continue
                        for original in base_item.get("raw_files", []):
                            relative = Path("retained") / base_id / original["path"]
                            target = tmp_dir / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(self._snapshot_dir(base_id) / original["path"], target)
                            copied_raw.append({**original, "path": str(relative)})
                    for original in base.get("table_files", []):
                        if original["product_id"] != product_id or original["table"] != "knowledge_evidence":
                            continue
                        relative = Path("tables/knowledge_evidence") / f"{base_id}-{Path(original['path']).name}"
                        target = tmp_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self._snapshot_dir(base_id) / original["path"], target)
                        table_files.append({**original, "path": str(relative)})
                    if copied_raw:
                        items.append(
                            dict(
                                product_id=product_id,
                                table=self.product(product_id)["tables"][0],
                                inherited_from=base_id,
                                source=next(
                                    (
                                        i.get("source", {})
                                        for i in base.get("items", [])
                                        if i["product_id"] == product_id
                                    ),
                                    {},
                                ),
                                raw_files=copied_raw,
                            )
                        )
            # 仅复制（未本快照取数）的产品也登记来源与谱系，保证清单可追溯
            fetched_products = {str(item["product_id"]) for item in items}
            for product_id, base_id in sorted(inherited.items()):
                if product_id in fetched_products:
                    continue
                if base_id not in base_manifests:
                    base_manifests[base_id] = self.load_manifest(SnapshotRef(snapshot_id=base_id, root=self.root))
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
            if request.refresh_from is not None and any(
                self.product(pid).get("kind") == "calendar" for pid in request.product_ids
            ):
                doc["calendar_refresh"] = {
                    pid: dict(
                        start=request.start.isoformat(),
                        end=request.end.isoformat(),
                        supersedes=[
                            sid
                            for sid, previous in published
                            if any(
                                e["product_id"] == pid and e["table"] == "calendar"
                                for e in previous.get("table_files", [])
                            )
                        ],
                    )
                    for pid in request.product_ids
                    if self.product(pid).get("kind") == "calendar" and not any(f["product_id"] == pid for f in failures)
                }
            return self._publish(tmp_dir, snapshot_id, doc)

        # ---- 读取 ----

    def _range_files(self, ref, name, *, codes=None, start=None, end=None, product_id=None):
        """仅选清单登记分区并核验字节；未知布局保守保留，由DuckDB裁剪行。"""
        if name not in TABLE_SCHEMAS:
            raise ValueError(f"unknown table: {name}")
        if Path(ref.root).resolve() != self.root.resolve():
            raise ValueError("snapshot belongs to another data root")
        base = self._snapshot_dir(ref.snapshot_id).resolve()
        if base.parent != (self.root / "snapshots").resolve():
            raise ValueError("invalid snapshot id")
        manifest = self.load_manifest(ref)
        selected = []
        for entry in manifest.get("table_files", []):
            if entry["table"] != name or (product_id is not None and entry["product_id"] != product_id):
                continue
            parts = dict(p.split("=", 1) for p in entry.get("partition", "").split("|") if "=" in p)
            if codes is not None and parts.get("code") is not None and parts["code"] not in codes:
                continue
            if start is not None and end is not None:
                span = parts.get("month") or parts.get("year")
                if span and not str(start)[: len(span)] <= span <= str(end)[: len(span)]:
                    continue
            self.check_access(manifest.get("mode", "free"), self.product(entry["product_id"]), operation="read")
            path = (base / entry["path"]).resolve()
            if not path.is_relative_to(base) or not path.is_file() or _sha256(path) != entry["sha256"]:
                raise SnapshotTamperedError(f"locked asset mismatch: {ref.snapshot_id}/{entry['path']}")
            selected.append(str(path))
        return selected

    def read_range(
        self, ref: SnapshotRef, name: str, *, codes: tuple[str, ...], start: date, end: date, columns: tuple[str, ...],
        product_id: str | None = None,
    ) -> pd.DataFrame:
        """离线范围读取；日期两端包含。无时间列的静态表只按代码筛选。"""
        schema = TABLE_SCHEMAS.get(name)
        if schema is None or not columns or not set(columns).issubset(schema["columns"]):
            raise ValueError("unknown table or column")
        if start > end:
            raise ValueError("start must not exceed end")
        has_code = "code" in schema["columns"]
        paths = self._range_files(ref, name, codes=codes if has_code else None, start=start, end=end,
                                 product_id=product_id)
        if not paths or (has_code and not codes):
            return pd.DataFrame(
                {
                    c: pd.Series(dtype="float64" if query_column(name, c).startswith("TRY_CAST") else "object")
                    for c in columns
                }
            )
        clock = "date" if "date" in schema["columns"] else "ex_date" if "ex_date" in schema["columns"] else None
        # 投影不能绕过付费行授权；内部核验字段不增加返回列。
        checked = list(
            dict.fromkeys([*columns, "product_id", *(["code"] if has_code else []), *([clock] if clock else [])])
        )
        projection = ",".join(query_column(name, col) for col in checked)
        predicates, args = [], [paths]
        if product_id is not None:
            predicates.append('"product_id" = ?')
            args.append(product_id)
        if has_code:
            predicates.append('"code" IN (SELECT unnest(?))')
            args.append(list(codes))
        if clock:
            predicates.append(f'"{clock}" BETWEEN ? AND ?')
            args.extend([start.isoformat(), end.isoformat()])
        order = [col for col in ("code", clock, "product_id", "source_ref") if col and col in checked]
        sql = f"SELECT {projection} FROM read_parquet(?, union_by_name=true)"
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += " ORDER BY " + ",".join(f'"{col}"' for col in order)
        with duckdb.connect() as con:
            frame = con.execute(sql, args).df()
        mode = self.load_manifest(ref).get("mode", "free")
        for pid, own in frame.groupby("product_id", dropna=False):
            self.check_access(mode, self.product(str(pid)), operation="read")
            self._check_paid_rows(str(pid), own, name)
        return frame.loc[:, list(columns)]

    def read_table(self, ref: SnapshotRef, name: str) -> pd.DataFrame:
        manifest = self.load_manifest(ref)
        for entry in manifest.get("table_files", []):
            if entry["table"] == name:
                self.check_access(manifest.get("mode", "free"), self.product(entry["product_id"]), operation="read")
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
        frame = duckdb.query(f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)").to_df()
        for product_id in frame.get("product_id", pd.Series(dtype=str)).dropna().unique():
            own = frame[frame.product_id.eq(product_id)]
            self._check_paid_rows(str(product_id), own, name)
        return frame

    # ---- 用途资格 ----
