"""研究用途资格；数据内核不判研究资格。"""

import uuid
from datetime import datetime

from hetu_data.models import DataRequest, QualityReport, SnapshotRef, data_request_to_dict
from hetu_data.store import CN, CODELESS_KINDS, _write_json

# 各用途必需的表；财务域缺失只阻断依赖它的用途
PURPOSE_TABLES: dict[str, tuple[str, ...]] = {
    "daily_archive": ("calendar", "securities", "daily_bars", "adjustment_factors"),
    "ranking_current": (
        "calendar",
        "daily_bars",
        "security_status",
        "boards",
        "memberships",
        "index_bars",
    ),
    "ranking_historical": (
        "calendar",
        "daily_bars",
        "security_status",
        "boards",
        "memberships",
        "index_bars",
    ),
    "low_volatility_preparation": (
        "calendar",
        "daily_bars",
        "security_status",
        "corporate_actions",
        "market_cap",
    ),
    "ep_value_preparation": (
        "calendar",
        "daily_bars",
        "security_status",
        "financial_reports",
        "market_cap",
    ),
    # 二期回测用途：另需历史证券（含退市）、历史交易制度与公开时点证据；
    # 财务链缺失只阻断 backtest_ep
    "backtest_low_volatility": (
        "calendar",
        "daily_bars",
        "security_status",
        "corporate_actions",
        "market_cap",
        "historical_securities",
        "trading_rules",
        "knowledge_evidence",
    ),
    "backtest_ep": (
        "calendar",
        "daily_bars",
        "security_status",
        "corporate_actions",
        "market_cap",
        "historical_securities",
        "trading_rules",
        "knowledge_evidence",
        "financial_reports",
    ),
}


def qualify_data(self, request: DataRequest, ref: SnapshotRef) -> QualityReport:
    """按对象、窗口、字段和用途检查资格；资格记录持久化，旧记录不被新请求改写。"""
    if request.purpose == "daily_archive":
        from hetu_quant.data_archive import archive_requirements, audit_archive

        for entry in self.load_manifest(ref).get("table_files", []):
            if entry["table"] in PURPOSE_TABLES["daily_archive"]:
                self.check_access(request.mode, self.product(entry["product_id"]), operation="read")
        history = self.read_table(ref, "securities")
        history = history[history.code.isin(request.codes)].copy()
        history["source_ref"] = ref.snapshot_id
        history.attrs["target_scope"] = {
            "complete": set(history.code) == set(request.codes),
            "source_ref": "explicit_request_codes",
        }
        requirements = archive_requirements(history, self.read_table(ref, "calendar"), request.end, start=request.start)
        audit = audit_archive(self, ref, requirements)
        report = QualityReport(
            audit["status"] == "complete",
            ("daily_archive",) if audit["status"] == "complete" else (),
            tuple(request.codes) if audit["status"] == "complete" else (),
            {},
            tuple(["daily_archive_limited"] if audit["status"] != "complete" else []),
        )
        self._persist_qualification(request, ref, report)
        return report
    for product_id in request.product_ids:
        product = self.product(product_id)
        # 资格评估只读已存在事实；各表行域在read_table检查，缺研究期间报缺口，
        # 不把未取齐改成新获取请求，也不用研究股票域误检查无关行业产品。
        self.check_access(request.mode, product, operation="read")
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

    # 信息时点：快照取得时间不得晚于研究 as_of；只核本用途实际所需表的
    # 取得时点，未用于本研究的晚取得域不触发全快照否决
    purpose_tables = set(PURPOSE_TABLES[request.purpose])
    for entry in manifest.get("table_files", []):
        if entry["table"] in purpose_tables:
            self.check_access(request.mode, self.product(entry["product_id"]), operation="read")
    obtained_latest = ""
    for item in manifest.get("items", []):
        if str(item.get("table", "")) not in purpose_tables:
            continue
        obtained = str((item.get("source") or {}).get("obtained_at", ""))
        obtained_latest = max(obtained_latest, obtained)
    obtained_ok = True
    if obtained_latest and obtained_latest > as_of.isoformat():
        obtained_ok = False
        limitations.append(f"obtained_after_as_of: snapshot obtained at {obtained_latest} later than as_of")

    # 快照内可用表与失败产品
    available_tables = {entry["table"] for entry in manifest.get("table_files", [])}
    failed_products = {entry["product_id"] for entry in manifest.get("failures", [])}
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
        if purpose == "daily_archive":
            continue  # 档案必须走实际行审计，表存在不能代替覆盖。
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
            if str(flag) in ("True", "1", "true") and request.start.isoformat() <= str(day) <= request.end.isoformat()
        )
        bars = self.read_table(ref, "daily_bars")
        status = self.read_table(ref, "security_status")
        members = self.read_table(ref, "memberships")
        financials = self.read_table(ref, "financial_reports")

        # 历史排名必须有当时可知成员；只有当前快照成员时拒绝该范围
        if request.purpose == "ranking_historical":
            kinds = set(members["membership_kind"].astype(str)) if len(members) else set()
            if not kinds - {"current_snapshot"}:
                historical_blocked = True
                limitations.append("ranking_historical blocked: no PIT membership (current_snapshot only)")

        for code in request.codes:
            market = code.rsplit(".", 1)[-1].upper()
            if covered_markets and market not in covered_markets:
                excluded[code].append("market_not_covered")
                limitations.append(f"{code}: market {market} not covered by snapshot products")

            if len(members):
                own = members[members["code"] == code]
                if not len(own):
                    excluded[code].append("no_membership")
                else:
                    late = own[own["available_at"].astype(str) > as_of.isoformat()]
                    if len(late):
                        excluded[code].append("member_not_available_at_as_of")
                    if request.purpose == "ranking_historical" and (own["membership_kind"] == "current_snapshot").any():
                        excluded[code].append("no_pit_membership")

            own_bars = bars[bars["code"] == code] if len(bars) else bars
            own_status = status[status["code"] == code] if len(status) else status
            bar_days = set(own_bars["date"].astype(str))
            suspended_days = set(
                own_status[own_status["is_suspended"].astype(str).isin(("True", "1", "true"))]["date"].astype(str)
            )
            missing = [day for day in trading_days if day not in bar_days and day not in suspended_days]
            if suspended_days & set(trading_days):
                limitations.append(
                    f"{code}: suspended sessions {sorted(suspended_days & set(trading_days))} recorded, not filled"
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

            if request.purpose in ("ep_value_preparation", "backtest_ep"):
                own_fin = financials[financials["code"] == code] if len(financials) else financials
                known = own_fin[own_fin["available_at"].astype(str) <= as_of.isoformat()]
                if not len(known):
                    excluded[code].append("no_pit_financials")
                elif len(own_fin) > len(known):
                    limitations.append(f"{code}: late_revision_ignored ({len(own_fin) - len(known)} rows after as_of)")

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


def _persist_qualification(self, request: DataRequest, ref: SnapshotRef, report: QualityReport) -> None:
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
