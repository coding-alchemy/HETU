"""已发布档案的离线查询界面。"""

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

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


def _render_coverage(store, ref):
    targets = {row["target_id"]: row for row in list_archive_targets(store)}
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
    if not report["target_scope_complete"]:
        st.warning("历史对象全集未证，仅显示已知范围覆盖。")
    st.write({
        "原价覆盖完整": report["raw_price_complete"],
        "复权辅助完整": report["adjustment_complete"],
        "全市场覆盖率": report.get("coverage_ratio"),
        "已知范围覆盖率": report.get("known_scope_ratio"),
        "对象交易日": report.get("coverage_counts", {}),
    })
    with st.expander("连续覆盖、读取限制及未闭合项"):
        for field in ("coverage", "gaps", "unknown_ranges", "read_failures"):
            st.write(field, report.get(field, []))


def render_library(store: DataStore) -> None:
    libraries = _library_choices(store)
    if not libraries:
        return
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
        start = left.date_input("查询开始", date(2026, 1, 1), key="library_start")
        end = right.date_input("查询结束", date(2026, 10, 13), key="library_end")
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
                st.dataframe(result["gaps"], hide_index=True)
            for limitation in result["auxiliary_limits"]:
                st.warning(f"辅助表 {limitation['table']} 读取受限：{limitation['reason']}")
            _render_coverage(store, ref)
        elif selected:
            _render_coverage(store, LibraryRef(selected, store.root))
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
        product_options = ()
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
    inputs = (mode, path, target, contract_text, products, library_id, object_filter, requested_budget)
    if st.button("预览维护计划", key="maintenance_preview"):
        st.session_state.pop("maintenance_plan", None)
        try:
            if contract_text is None:
                raise ValueError("维护合同不可读取")
            contracts = json.loads(contract_text)
            settings = contracts.setdefault("maintenance", {})
            settings["object_filter"] = object_filter
            if library_id:
                library = LibraryRef(library_id, store.root)
                manifest = load_library(store, library)
                settings["asset_snapshots"] = manifest["asset_snapshots"]
                settings["coverage_snapshot_ids"] = []
                contracts["library_ref"] = {"library_id": library_id, "root": str(store.root.resolve())}
            plan = maintenance_plan(store, target, products, mode=mode, contracts=contracts)
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
            request = data_request_from_dict(plan["request"])
            for domain in plan["domains"]:
                pid = domain["product_id"]
                if pid not in request.codes_by_product:
                    continue
                scoped = replace(request, product_ids=(pid,), start=date.fromisoformat(domain["start"]))
                store._check_scope(scoped, pid, "read")
                if domain["missing"] or domain["refresh"]:
                    store._check_scope(scoped, pid, "acquire")
        except (OSError, ValueError, KeyError) as error:
            issue = str(error)
        if not issue and not plan["limits"].get("approved_scope_ref"):
            issue = "缺少现行整批授权，当前计划仅供预览。"
    if issue:
        st.warning(issue)
    if st.button(
        "提交维护任务",
        key="maintenance_submit",
        disabled=bool(issue or not plan["segments"] or plan["unresolved"]),
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
