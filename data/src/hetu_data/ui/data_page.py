"""已发布档案的离线查询界面。"""

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from hetu_data.archive import (
    ARCHIVE_COLUMNS,
    filter_bars,
    list_archive_targets,
    load_archive_coverage,
    query_archive,
)
from hetu_data.jobs import JobService, submit_update_plan
from hetu_data.library import list_libraries, load_library, query_library
from hetu_data.models import LibraryRef, SnapshotRef
from hetu_data.store import DataStore


def submit_data_plan(tasks, plan):
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    key = f"data_submission:{tasks.root.resolve()}:{fingerprint}"
    current = st.session_state.get(key)
    if current and JobService(tasks).get(current.job_id).state not in ("queued", "running", "cancel_requested"):
        current = None
    job = submit_update_plan(tasks, plan, current.submission_id if current else uuid.uuid4().hex)
    st.session_state[key] = job
    st.success(f"已提交任务：{job.job_id}")


def _library_choices(store):
    return {row["library_id"]: row for row in list_libraries(store)}


def _library_label(libraries, key):
    item = libraries[key]
    created = str(item["created_at"]).split("T", 1)[0]
    return f"{created} · {'可读' if item['status'] == 'readable' else '受限'} · {key}"


def _render_coverage(store, ref, targets):
    if not targets:
        st.caption("尚无已登记的档案目标；库引用可读状态不表示覆盖完整。")
        return
    target = st.selectbox("固定档案目标", targets, key="library_target",
                          format_func=lambda key: f"{targets[key]['start']} 至 {targets[key]['end']} · {key}")
    report = load_archive_coverage(store, ref, target)
    if report is None:
        st.info("此库版本／目标未审计；已存且获准的原价仍可查询。")
        return
    st.caption(f"固定目标 {report['target_start']} 至 {report['target_end']}；"
               f"最晚合格原价 {report.get('latest_available_date') or '未知'}，不代表此前连续覆盖。")
    if acquired := report.get("last_daily_response_at"):
        st.caption(f"此目标最近一次取得日线响应 {acquired}。")
    updates = []
    try:
        records = store._last_success()
        for product_id, value in records.items():
            timestamp = datetime.fromisoformat(value)
            if timestamp.utcoffset() is not None:
                updates.append((timestamp, product_id))
    except (OSError, ValueError, TypeError, AttributeError):
        updates.clear()
    if updates:
        latest, product = max(updates)
        stamp = latest.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        st.caption(f"数据根最近成功更新时间 {stamp}（{product}）。"
                   "这是该产品的处理成功记录，所选库的实际数据日期见上方。")
    else:
        st.caption("数据根最近成功更新时间：暂无可用记录。")
    if not report["target_scope_complete"]:
        st.warning("历史对象全集未证，仅显示已知范围覆盖。")
    split = report.get("unknown_split")
    if split is not None:
        st.caption(
            f"已证应有但缺失 {split['confirmed_missing']:,} · "
            f"证据不足 {split['insufficient_evidence']:,} · 冲突 {split['conflicts']:,}"
        )
    if report.get("detail_ref"):
        st.caption(f"对象与期间明细 `{report['detail_ref']}`")
    source_scoped = report.get("coverage_scope") == "tushare_registered_scope"
    for limitation in report.get("scope_limitations", ()):
        st.caption(limitation)
    st.write({
        "原价覆盖完整": report["raw_price_complete"],
        "复权辅助完整": report["adjustment_complete"],
        "登记来源范围覆盖率" if source_scoped else "全市场覆盖率": report.get("coverage_ratio"),
        "已知范围覆盖率": report.get("known_scope_ratio"),
        "对象交易日": report.get("coverage_counts", {}),
    })
    if report.get("archive_status_rule"):
        st.caption(f"Tushare 原价合格且状态差异留档 {report['status_difference_count']:,} 日。")
        if count := report.get("accepted_exception_count"):
            st.caption(f"用户已接受例外 {count:,} 日，仍保留在事实覆盖分母中。")
    elif observations := report.get("tushare_observation_counts"):
        st.caption("Tushare 主源观察单列，不计入严格覆盖的合格原价。")
        st.write({
            "六字段可用、状态未决": observations["status_unresolved"],
            "原价及成交量可用、缺成交额": observations["amount_missing"],
            "原件 OHLC 异常": observations["invalid_ohlc"],
        })
    with st.expander("连续覆盖、读取限制及未闭合项"):
        for field in ("coverage", "gaps", "unknown_ranges", "read_failures"):
            st.write(field, report.get(field, []))
        if report.get("scope_exclusions"):
            st.write("目标范围排除及依据", report["scope_exclusions"])


def render_library(store: DataStore) -> None:
    libraries = _library_choices(store)
    if not libraries:
        return
    try:
        targets = {row["target_id"]: row for row in list_archive_targets(store)}
        earliest_target = min((date.fromisoformat(row["start"]) for row in targets.values()), default=None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        targets, earliest_target = {}, None
        st.warning(f"档案目标读取受限：{error}。仍可查询获准原价。")
    st.subheader("本地日线库")
    st.caption("原价；成交量为股，成交额为元。选择固定版本，查询不取数。")
    preferred = st.session_state.pop("library_suggested", None)
    if preferred in libraries:
        st.session_state["library_version"] = preferred
    selected = st.selectbox("库发布版本", libraries, index=None, key="library_version",
                            format_func=lambda key: _library_label(libraries, key))
    with st.form("library_filter"):
        markets = st.multiselect("市场", ("SH", "SZ", "BJ"), key="library_markets")
        codes = st.text_input("证券代码（逗号分隔）", key="library_codes")
        left, right = st.columns(2)
        start_min = min(earliest_target, date(2016, 1, 1)) if earliest_target else None
        end_min = min(earliest_target, date(2016, 10, 13)) if earliest_target else None
        start = left.date_input("查询开始", date(2026, 1, 1), min_value=start_min, key="library_start")
        end = right.date_input("查询结束", date(2026, 10, 13), min_value=end_min, key="library_end")
        columns = st.multiselect("字段", ARCHIVE_COLUMNS, default=list(ARCHIVE_COLUMNS), key="library_columns")
        field, op, value = st.columns(3)
        predicate_field = field.selectbox("数值筛选字段", ARCHIVE_COLUMNS[2:-2], index=None, key="library_field")
        predicate_op = op.selectbox("条件", ("eq", "ge", "le", "is_null"), key="library_op")
        predicate_value = value.number_input("数值", key="library_value")
        submitted = st.form_submit_button("查询日线库", key="library_query")
    selection = st.session_state.get("library_submitted_selection")
    result = None
    try:
        if submitted:
            if not selected or not codes.strip():
                st.warning("请明确选择库版本及证券代码。")
            else:
                result = query_library(
                    store, LibraryRef(selected, store.root),
                    codes=tuple(dict.fromkeys(code.strip() for code in codes.split(",") if code.strip())),
                    markets=tuple(markets), start=start, end=end, columns=tuple(columns),
                    predicates=(
                        (dict(field=predicate_field, op=predicate_op, value=predicate_value),)
                        if predicate_field else ()
                    ),
                )
                selection = result["selection"]
                st.session_state["library_submitted_selection"] = selection
        if selection is not None:
            ref = LibraryRef(selection["library_id"], Path(selection["root"]))
            if result is None:
                result = query_library(
                    store, ref, codes=tuple(selection["codes"]), markets=tuple(selection["markets"]),
                    start=date.fromisoformat(selection["start"]), end=date.fromisoformat(selection["end"]),
                    columns=tuple(selection["columns"]), predicates=tuple(selection["predicates"]),
                )
            st.caption(f"已提交：{ref.library_id} · {', '.join(selection['codes'])} · "
                       f"{selection['start']} 至 {selection['end']} · {result['status']}")
            st.dataframe(result["bars"], hide_index=True, key="library_bars")
            st.download_button("导出当前已提交筛选 CSV", result["bars"].to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"{ref.library_id}-daily.csv", mime="text/csv", key="library_csv")
            st.download_button("导出 Notebook 查询选择", json.dumps(selection, ensure_ascii=False).encode(),
                               file_name=f"{ref.library_id}-selection.json", mime="application/json",
                               key="library_notebook")
            with st.expander("来源与查询缺口"):
                st.write(result["sources"])
                if result.get("source_differences"):
                    st.write(result["source_differences"])
                if result.get("primary_observations"):
                    st.write("Tushare 主源记录及限制", result["primary_observations"])
                if result.get("status_differences"):
                    st.write("原价合格，状态差异留档", result["status_differences"])
                if result.get("accepted_exceptions"):
                    st.write("用户已接受例外", result["accepted_exceptions"])
                if result.get("prior_status_acceptances"):
                    st.write("原状态争议的接受决定", result["prior_status_acceptances"])
                st.dataframe(result["gaps"], hide_index=True)
            for limitation in result["auxiliary_limits"]:
                st.warning(f"辅助表 {limitation['table']} 读取受限：{limitation['reason']}")
            _render_coverage(store, ref, targets)
        elif selected:
            _render_coverage(store, LibraryRef(selected, store.root), targets)
    except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
        st.error(f"本地库读取受限：{error}。可重新选择其他获准的库或快照。")


def render_archive(store: DataStore, ref: SnapshotRef) -> None:
    st.subheader("本地日线档案")
    mode = store.load_manifest(ref).get("mode", "free")
    st.caption(f"快照：{ref.snapshot_id} · 来源模式：{mode} · 仅查询本地已发布数据")
    st.caption("默认原价，成交量单位为股、成交额为元。查询不取数，不自动启动研究。")
    with st.form("archive_filter"):
        text = st.text_input("证券代码（逗号分隔，如600519.SH）", key="archive_codes")
        left, right = st.columns(2)
        start = left.date_input("查询开始", value=date(2026, 1, 1), key="archive_start")
        end = right.date_input("查询结束", value=date(2026, 10, 13), key="archive_end")
        columns = st.multiselect("显示字段", ARCHIVE_COLUMNS, default=list(ARCHIVE_COLUMNS), key="archive_columns")
        predicates_text = st.text_area(
            "数值筛选（JSON 条件列表；field/op/value，支持 eq/ge/le/is_null）", value="[]", key="archive_predicates"
        )
        submitted = st.form_submit_button("查询本地数据", key="archive_query")
    key = f"archive_selection:{ref.snapshot_id}"
    if submitted:
        codes = tuple(dict.fromkeys(s.strip() for s in text.split(",") if s.strip()))
        st.session_state[key] = dict(
            codes=codes, start=start, end=end, columns=columns, predicates_text=predicates_text
        )
    selection = st.session_state.get(key)
    if selection is None:
        return
    if not selection["codes"]:
        st.warning("请填写要查询的证券代码。")
        return
    try:
        result = query_archive(store, ref, codes=selection["codes"], start=selection["start"], end=selection["end"])
        predicates = json.loads(selection["predicates_text"])
        if not isinstance(predicates, list) or not all(isinstance(p, dict) for p in predicates):
            raise ValueError("筛选条件须为对象列表")
        if predicates or selection["columns"] != list(ARCHIVE_COLUMNS):
            result["bars"] = filter_bars(
                result["bars"], columns=tuple(selection["columns"]), predicates=tuple(predicates)
            )
    except (ValueError, PermissionError, RuntimeError, OSError) as error:
        st.error(f"本地读取失败：{error}。未启动补取。")
        return
    bars, gaps = result["bars"], result["gaps"]
    st.caption(
        f"本次筛选：{','.join(selection['codes'])}，{selection['start']} 至 {selection['end']}；返回 {len(bars)} 行"
    )
    st.dataframe(bars, hide_index=True, key="archive_bars")
    st.download_button(
        "导出本次日线 CSV",
        bars.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{ref.snapshot_id}-daily.csv",
        mime="text/csv",
        key="archive_csv",
    )
    for limitation in result["auxiliary_limits"]:
        st.warning(f"辅助表 {limitation['table']} 读取受限：{limitation['reason']}")
    if not gaps.empty:
        st.warning("当前查询范围有下列空段或未核验项；详情与缺失数据均未补造。")
        st.dataframe(gaps, hide_index=True, key="archive_gaps")
    else:
        st.caption("本次范围未发现上述缺口；这不代表二十年全市场覆盖或回测资格已通过。")
    st.caption("未提供连续复权价：需另行证明复权依据及锚点；量额始终保留原始汇总。")
    if not bars.empty and set(ARCHIVE_COLUMNS) <= set(bars.columns):
        for code, frame in bars.groupby("code"):
            st.markdown(f"**{code} · 原价日 K 线**")
            if frame.duplicated("date").any():
                st.warning("同日有多条来源记录，保留明细，暂不绘制可能歧义的K线。")
                continue
            st.vega_lite_chart(
                frame,
                {
                    "encoding": {"x": {"field": "date", "type": "temporal", "title": "日期"}},
                    "layer": [
                        {
                            "mark": "rule",
                            "encoding": {
                                "y": {
                                    "field": "low_raw",
                                    "type": "quantitative",
                                    "scale": {"zero": False},
                                    "title": "元",
                                },
                                "y2": {"field": "high_raw"},
                            },
                        },
                        {
                            "mark": {"type": "bar", "size": 5},
                            "encoding": {
                                "y": {"field": "open_raw", "type": "quantitative", "scale": {"zero": False}},
                                "y2": {"field": "close_raw"},
                                "color": {
                                    "condition": {"test": "datum.close_raw >= datum.open_raw", "value": "#c73d46"},
                                    "value": "#23856a",
                                },
                            },
                        },
                    ],
                },
                width="stretch",
            )
            chart = frame.set_index(pd.to_datetime(frame.date))
            st.bar_chart(chart[["volume_shares"]], y_label="成交量（股）")
            st.bar_chart(chart[["amount_cny"]], y_label="成交额（元）")


def render_maintenance(store, submit):
    """只读计划预览和持久提交；此页面不执行provider。"""
    from hetu_data.models import data_request_from_dict
    from hetu_data.updates import maintenance_plan, validate_update_plan
    from hetu_data.worker import maintenance_data_store

    st.subheader("日更与断更恢复")
    st.caption("独立维护目标，不滚动旧研究截止或档案初始化T；只提交持久任务，不自动启动worker。")
    mode = st.selectbox("维护来源模式", ("free", "paid"), key="maintenance_mode")
    with st.expander("首次配置或更换维护合同"):
        path = st.text_input(
            "本机维护合同路径", value=str(store.root / "maintenance-contracts.json"),
            key="maintenance_contract",
        )
        st.markdown(
            "[首次配置见统一使用手册]"
            "(https://github.com/coding-alchemy/HETU/blob/main/docs/quant-research-platform-usage.md)"
        )
    target = st.date_input("维护目标日", value=date.today(), key="maintenance_target")
    libraries = _library_choices(store)
    library_id = (
        st.selectbox("维护接续版本", libraries, index=None, key="maintenance_library",
                     format_func=lambda key: _library_label(libraries, key))
        if libraries else None
    )
    try:
        contract_text = Path(path).read_text()
        configured = json.loads(contract_text)
        product_options = tuple(configured.get("products", {}))
    except (OSError, ValueError, AttributeError):
        contract_text = None
        configured = {}
        product_options = ()
    configured_window = configured.get("maintenance", {})
    begin = None
    if configured_window.get("window_floor"):
        begin = st.date_input(
            "本次补齐起点", value=date.fromisoformat(configured_window["start"]),
            min_value=date.fromisoformat(configured_window["window_floor"]), key="maintenance_begin",
        )
    products = tuple(st.multiselect("维护产品", product_options, default=product_options, key="maintenance_products"))
    st.caption("市场和证券代码只筛选按代码维护的产品。留空时使用独立历史目标中的登记对象。")
    markets = st.multiselect("维护市场", ("SH", "SZ", "BJ"), key="maintenance_markets")
    codes = st.text_input("维护证券代码（逗号分隔）", key="maintenance_codes")
    selected_codes = (code.strip().upper() for code in codes.replace("，", ",").split(","))
    object_filter = {"markets": markets, "codes": list(dict.fromkeys(code for code in selected_codes if code))}
    requested_budget = {}
    with st.expander("申请整批额度（可选）"):
        st.caption("留空时使用现行已批准额度。填写的申请须与相应批准额度一致后才能提交。")
        for meter, label in (
            ("provider_fetch", "提供方批次次数"),
            ("upstream_product_requests", "上游产品请求次数"),
            ("network_attempts", "网络尝试次数"),
        ):
            amount = st.number_input(label, min_value=0, value=None, step=1, key=f"maintenance_budget_{meter}")
            if amount is not None:
                requested_budget[meter] = amount
    inputs = (mode, path, begin, target, contract_text, products, library_id, object_filter, requested_budget)
    if st.button("预览维护计划", key="maintenance_preview"):
        st.session_state.pop("maintenance_plan", None)
        try:
            if contract_text is None:
                raise ValueError("维护合同不可读取")
            contracts = json.loads(contract_text)
            settings = contracts.setdefault("maintenance", {})
            if begin is not None:
                settings["start"] = begin.isoformat()
            settings["object_filter"] = object_filter
            acquisition = maintenance_data_store(store, contracts)
            if library_id:
                library = LibraryRef(library_id, store.root)
                manifest = load_library(store, library)
                assets = manifest["asset_snapshots"]
                if acquisition is not store:
                    from datetime import timedelta

                    from hetu_data.library import _relevant
                    from hetu_data.updates import _maintenance_lookback

                    horizon = max(
                        (_maintenance_lookback(acquisition.product(pid), contracts["products"][pid])
                         for pid in products), default=1,
                    )
                    left = min(date.fromisoformat(settings["start"]), target - timedelta(days=max(0, horizon - 1)))
                    tables = {"calendar", *(table for pid in products for table in acquisition.product(pid)["tables"])}
                    if any(acquisition.product(pid).get("cross_section") == "trade_day" for pid in products):
                        tables.add("securities")
                    relevant = {}
                    for entry in manifest["table_files"]:
                        if entry["table"] in tables and _relevant(entry, (), left, target):
                            relevant.setdefault((entry["root"], entry["snapshot_id"]), set()).add(entry["table"])
                    assets = [dict(asset, tables=sorted(relevant[key])) for asset in assets
                              if (key := (asset["root"], asset["snapshot_id"])) in relevant]
                settings["asset_snapshots"] = assets
                settings["coverage_snapshot_ids"] = []
                contracts["library_ref"] = {"library_id": library_id, "root": str(store.root.resolve())}
            plan = maintenance_plan(acquisition, target, products, mode=mode, contracts=contracts,
                                    quota_root=store.root)
            if acquisition is not store:
                plan["acquisition_owner"] = contracts["acquisition_owner"]
            plan["requested_budget"] = requested_budget
            if library_id:
                plan["library_ref"] = contracts["library_ref"]
            st.session_state["maintenance_plan"] = plan
            st.session_state["maintenance_inputs"] = inputs
        except (OSError, ValueError, KeyError) as error:
            st.error(f"维护预览受限：{error}；未请求外部数据。")
    plan = st.session_state.get("maintenance_plan")
    if plan is None:
        st.caption("配置产品合同、独立历史目标、日历适用市场和获批额度后可预览；当前未自动采集。")
        return
    st.caption(f"已预览模式 {plan['request']['mode']}，窗口 {plan['start']} 至 {plan['target']}；改变配置需重新预览。")
    fee = plan.get("new_data_fee_cny", plan["counts"].get("data_fee_cny"))
    st.caption(f"预计新增数据费：{'未知' if fee is None else f'{fee} 元'}")
    st.json({k: plan[k] for k in ("domains", "counts", "limits", "target_scope", "unresolved")})
    st.write("已预览的对象筛选及整批额度申请", {
        "对象筛选": plan.get("object_filter", {}),
        "申请额度": plan["requested_budget"],
    })
    issue = None
    if st.session_state.get("maintenance_inputs") != inputs:
        issue = "表单已变更，请重新预览。"
    else:
        try:
            validate_update_plan(plan)
            acquisition = maintenance_data_store(store, plan)
            request = data_request_from_dict(plan["request"])
            for domain in plan["domains"]:
                pid = domain["product_id"]
                if pid not in request.codes_by_product:
                    continue
                scoped = replace(request, product_ids=(pid,), start=date.fromisoformat(domain["start"]))
                acquisition._check_scope(scoped, pid, "read")
                if domain["missing"] or domain["refresh"]:
                    acquisition._check_scope(scoped, pid, "acquire")
        except (OSError, ValueError, KeyError) as error:
            issue = str(error)
        if not issue and not plan["limits"].get("approved_scope_ref"):
            issue = "缺少现行整批授权，当前计划仅供预览。"
    if issue:
        st.warning(issue)
    verified = plan.get("maintenance_contracts", {}).get("maintenance", {})
    can_verify_noop = verified.get("allow_verified_noop") and plan["target"] <= verified.get(
        "verified_market_day_through", "",
    )
    if st.button(
        "提交维护任务",
        key="maintenance_submit",
        disabled=bool(issue or not (plan["segments"] or can_verify_noop) or plan["unresolved"]),
    ):
        submit(plan)


def render_maintenance_job(job):
    if job.output is None:
        return
    for name in ("update.json", "progress.json"):
        path = job.output / name
        if path.exists():
            result = json.loads(path.read_text())
            st.caption("当日用量、整批累计及本次新增分别记录；任务成功不代表目标覆盖完整。")
            st.json(
                {
                    k: result.get(k)
                    for k in (
                        "incremental_usage", "actual_usage", "daily_usage", "stop_reason", "resume_after",
                        "last_success_at", "pending", "failures", "snapshot_ids", "maintenance",
                    )
                }
            )
            library = result.get("library_ref")
            if library and st.button("选择此次发布的库版本", key=f"open-library::{job.job_id}"):
                st.session_state["library_suggested"] = library["library_id"]
                if "page_nav" in st.session_state:
                    st.session_state["next_page"] = "数据"
                st.rerun()
            break


def render_data_tasks(tasks):
    """共享队列元信息；只有维护任务可在独立页操作。"""
    st.subheader("任务列表")
    service = JobService(tasks)
    jobs = service.list()
    if not jobs:
        st.caption("暂无任务。")
        return
    st.dataframe(
        [
            {
                "任务号": j.job_id,
                "类型": tasks.definition_kind(j.definition_id),
                "状态": j.state,
                "进度": j.progress,
                "失败或限制原因": j.reason or "",
            }
            for j in jobs
        ],
        hide_index=True,
    )
    for job in jobs:
        if tasks.definition_kind(job.definition_id) != "maintenance":
            st.caption(f"{job.job_id}：由 HETU 处理，仅显示元信息。")
            continue
        st.markdown(f"**{job.job_id} · {job.state}**")
        if job.reason:
            st.warning(job.reason)
        render_maintenance_job(job)
        if job.state in ("queued", "running"):
            confirmed = st.checkbox("确认取消此维护任务", key=f"confirm_cancel::{job.job_id}")
            if st.button("取消", key=f"cancel::{job.job_id}", disabled=not confirmed):
                service.cancel(job.job_id)
                st.rerun()
        elif job.state == "cancel_requested":
            st.caption("取消已请求，等待执行边界确认。")
        elif job.state in ("failed", "cancelled", "interrupted"):
            if st.button("显式重开维护任务", key=f"retry::{job.job_id}"):
                resumed = service.retry(job.job_id, uuid.uuid4().hex)
                st.session_state[f"reopened::{job.job_id}"] = resumed.job_id
                st.rerun()
            if st.session_state.get(f"reopened::{job.job_id}"):
                st.caption(f"已重开：{st.session_state[f'reopened::{job.job_id}']}")


def render_acquisition(store, submit):
    """只预览有界数据请求，冻结后交给共享维护队列。"""
    from hetu_data.models import data_request_from_dict
    from hetu_data.updates import plan_update

    st.subheader("有界数据拉取")
    request_path = st.text_input(
        "拉取请求 JSON 路径", value=str(store.root / "request.json"), key="acquisition_request"
    )
    contracts_path = st.text_input(
        "拉取合同 JSON 路径", value=str(store.root / "contracts.json"), key="acquisition_contract"
    )
    # 文件内容也是输入；同路径文件发生变化后不得提交旧预览。
    try:
        request_text = Path(request_path).read_text()
        contracts_text = Path(contracts_path).read_text()
        inputs = (request_path, contracts_path, request_text, contracts_text)
    except OSError:
        inputs = None
    if st.button("预览拉取计划", key="acquisition_preview"):
        st.session_state.pop("acquisition_plan", None)
        try:
            if inputs is None:
                raise ValueError("请求或合同文件不可读取")
            plan = plan_update(data_request_from_dict(json.loads(request_text)), store, json.loads(contracts_text))
            st.session_state["acquisition_plan"] = plan
            st.session_state["acquisition_inputs"] = inputs
        except (OSError, ValueError, KeyError, PermissionError) as error:
            st.error(f"拉取预览受限：{error}；未请求外部数据。")
    plan = st.session_state.get("acquisition_plan")
    if plan is None:
        st.caption("指定请求范围、来源模式和获批合同后预览；当前未自动采集。")
        return
    st.json({k: plan.get(k) for k in ("request", "reused_coverage", "counts", "limits", "unresolved")})
    if st.button(
        "提交拉取任务",
        key="acquisition_submit",
        disabled=(
            inputs is None
            or st.session_state.get("acquisition_inputs") != inputs
            or not plan["segments"]
            or bool(plan["unresolved"])
            or not plan["limits"].get("approved_scope_ref")
        ),
    ):
        submit(plan)


def render_input_browser(store, *, scopes_for, lookup_for=None, describe_amounts=None, registration_scope=None):
    """Browse a fixed publication under the caller's per-product scopes."""
    from hetu_data.browse import BrowseSelection, _axes, browse_page
    from hetu_data.store import TABLE_SCHEMAS

    state = st.session_state
    st.subheader("浏览因子输入")
    choices = {
        **{f"快照 · {path.parent.name}": SnapshotRef(path.parent.name, store.root)
           for path in sorted((store.root / "snapshots").glob("*/manifest.json"))
           if not path.parent.name.startswith(".")},
        **{f"库 · {row['library_id']}": LibraryRef(row["library_id"], store.root) for row in list_libraries(store)},
    }
    if not choices:
        st.caption("暂无已发布的本地数据。")
        return None
    label = st.selectbox("输入发布", choices, index=None, key="factor_browser_source")
    if label is None:
        st.caption("请选择要浏览的固定发布。")
        return None
    source = choices[label]
    manifest = store.load_manifest(source) if isinstance(source, SnapshotRef) else load_library(store, source)
    published = manifest.get("created_at") or manifest.get("published_at") or "未知"
    st.caption(f"发布时间 {published} · 用途 factor_calculation")
    tables = tuple(dict.fromkeys(entry["table"] for entry in manifest["table_files"]
                                 if entry["table"] in TABLE_SCHEMAS))
    if not tables:
        st.caption("此发布未登记可浏览的输入表。")
        return None
    table = st.selectbox("输入表", tables, key="factor_browser_table")
    available = ARCHIVE_COLUMNS if table == "daily_bars" else TABLE_SCHEMAS[table]["columns"]
    if table == "daily_bars":
        st.caption("原价 · 金额口径按成交额原件逐行核对")
    if lookup_for is not None:
        with st.expander("查找本地对象"):
            term = st.text_input("证券名称或代码", key="factor_browser_search")
            dates = state.get("factor_browser_dates", ())
            point = dates[-1] if dates else date.today()
            coordinate = (source, point, term)
            if st.button("查找获准对象", key="factor_browser_find"):
                state["factor_browser_matches"] = coordinate
            if state.get("factor_browser_matches") == coordinate:
                try:
                    matches = lookup_for(source, point, term)
                except (ValueError, OSError, PermissionError):
                    matches = []
                    st.warning("当前无法读取所选发布的对象登记。")
                if matches:
                    candidate = st.selectbox(
                        "选择匹配对象", matches, key="factor_browser_match",
                        format_func=lambda row: f"{row[1]} · {row[0]} · {row[2]}"
                    )

                    def apply_candidate():
                        try:
                            current = lookup_for(source, point, term)
                        except (ValueError, OSError, PermissionError):
                            return
                        if candidate in current:
                            state["factor_browser_codes"] = candidate[0]

                    st.button("填入查询", key="factor_browser_apply_candidate", on_click=apply_candidate)
                else:
                    st.caption("未找到获准的名称或代码。")
    with st.form("factor_browser_query"):
        products = sorted({entry["product_id"] for entry in manifest["table_files"] if entry["table"] == table})
        product = st.selectbox("产品", ["全部", *products], key="factor_browser_product")
        codes = st.text_input("对象代码（空格分隔）", key="factor_browser_codes")
        market = st.selectbox("市场", ["全部", "SH", "SZ", "BJ"], key="factor_browser_market")
        window = st.date_input("查询日期区间", value=(date.today(), date.today()), key="factor_browser_dates")
        axis = st.selectbox("日期口径", _axes(table) or ("静态",), key=f"factor_browser_axis_{table}")
        fields = st.multiselect("显示字段", available, default=available, key=f"factor_browser_fields_{table}")
        order = st.selectbox("排序字段", available, key=f"factor_browser_order_{table}")
        desc = st.checkbox("降序", key="factor_browser_descending")
        submitted = st.form_submit_button("查询所选发布", key="factor_browser_query_submit")
    if submitted:
        if len(window) != 2:
            st.warning("请补齐查询起止日期。")
        else:
            state["factor_browser_selection"] = BrowseSelection(
                source, "factor_calculation", table, tuple(fields), tuple(dict.fromkeys(codes.split())),
                window[0], window[1], "" if market == "全部" else market, axis if axis != "静态" else "",
                order, desc, "" if product == "全部" else product,
            )
            state["factor_browser_page"] = 1
    selection = state.get("factor_browser_selection")
    if selection is None:
        return None
    page = int(state.get("factor_browser_page", 1))
    size = 25
    try:
        result = browse_page(
            store, selection, scopes=tuple(scopes_for(selection)), page=page, page_size=size,
            describe_amounts=describe_amounts,
            registration_scope=registration_scope,
            range_cache=state.setdefault("factor_browser_range_cache", {}),
        )
    except (ValueError, OSError, PermissionError):
        st.warning("无法读取所选范围。请核对发布、对象、查询区间或使用许可。")
        return None
    st.caption(f"已提交 {selection.source} · 本次查询 {selection.start} 至 {selection.end} · "
               f"第 {page} 页 · {result.total} 条获准记录")
    coverage = result.coverage
    unavailable_date = "受限（日期字段未获准）" if coverage.get("date_field_restricted") else "未知"
    if "product_range" in coverage:
        first, last = coverage["product_range"]
        span = f"{first} 至 {last}" if first and last else unavailable_date
        st.caption(f"{selection.product_id} · 产品获准数据范围 {span}")
    if coverage["denominator"] is None:
        st.caption(f"实有 {coverage['actual']}，应有数量未知 · {coverage['reason']}")
    else:
        st.caption(f"实有 {coverage['actual']}，应有 {coverage['denominator']}")
    if selection.table == "daily_bars":
        latest = coverage["latest"]
        unavailable_subject_date = (
            "受限（证券代码字段未获准）" if coverage.get("code_field_restricted") else unavailable_date
        )
        for code in selection.subjects:
            if not selection.market or code.rsplit(".", 1)[-1] == selection.market:
                st.caption(f"{code} · 最近有据日期 {latest.get(code, unavailable_subject_date)}")
    if result.rows.empty:
        st.info("此已提交范围暂无可读行。")
    else:
        amount_details = ("成交额币种", "成交额单位", "成交额依据")
        visible = result.rows.loc[:, [*selection.columns, *(key for key in amount_details if key in result.rows)]]
        st.dataframe(visible, hide_index=True, key="factor_browser_results")
        if "date" in result.rows:
            dates = result.rows["date"].dropna()
            if not dates.empty:
                st.caption(f"本页实际日期 {dates.min()} 至 {dates.max()}")
            elif coverage.get("date_field_restricted"):
                st.caption(f"本页实际日期 {unavailable_date}")
        if "product_id" in result.rows:
            last_success = store._last_success()
            for pid in sorted(result.rows["product_id"].dropna().unique()):
                st.caption(f"{pid} · 最近成功维护 {last_success.get(pid) or '未知'}")
    for gap in result.gaps:
        if gap.get("fields"):
            owner = gap.get("product") or gap.get("code") or "日线"
            reason = "行情字段缺失" if gap["reason"] == "fields_missing" else gap["reason"]
            limitation = " · 日期字段受限" if gap.get("date_restricted") else ""
            st.caption(f"{owner} · {reason} · {', '.join(gap['fields'])}{limitation}")
    if result.gaps:
        st.json(result.gaps, expanded=False)
    first, second = st.columns(2)
    first.button("上一页", key="factor_browser_previous", disabled=page <= 1,
                 on_click=lambda: state.update(factor_browser_page=page - 1))
    second.button("下一页", key="factor_browser_next", disabled=page * size >= result.total,
                  on_click=lambda: state.update(factor_browser_page=page + 1))
    if result.rows.empty:
        return None
    records = result.rows.to_dict("records")
    identifying = tuple(dict.fromkeys(("code", selection.axis or "date", "record_key", "product_id")))
    index = st.selectbox(
        "选择要带入的行", range(len(records)),
        format_func=lambda i: " · ".join(str(records[i].get(key) or "未知") for key in identifying),
        key="factor_browser_row",
    )
    row = records[index]
    code = row.get("code")
    if not code or pd.isna(code):
        st.caption("该行缺少可验证的对象坐标，无法带入试算。")
        return None
    if selection.table == "daily_bars" and pd.isna(row["date"]):
        st.caption("该行日期字段未获准，无法带入试算。")
        return None
    if st.button("带入试算", key="factor_browser_to_trial"):
        context = dict(table=selection.table, axis=selection.axis, coordinate=row.get(selection.axis),
                       record_key=row.get("record_key"), start=selection.start, end=selection.end)
        if selection.table == "daily_bars":
            context.update({field: row[field] for field in ("source_root", "snapshot_id", "product_id")})
            if isinstance(origin := row.get("source_ref_origin"), dict):
                context["source_ref_origin"] = origin
        return dict(source=selection.source, subjects=(code,),
                    trade_date=date.fromisoformat(str(row["date"])) if selection.table == "daily_bars" else None,
                    context=context)
    return None
