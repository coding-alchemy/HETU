"""已发布档案的离线查询界面。"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from hetu_data.archive import ARCHIVE_COLUMNS, filter_bars, query_archive
from hetu_data.models import SnapshotRef
from hetu_data.store import DataStore


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
    from hetu_data.updates import maintenance_plan

    st.subheader("日更与断更恢复")
    st.caption("独立维护目标，不滚动旧研究截止或档案初始化T；只提交持久任务，不自动启动worker。")
    mode = st.selectbox("维护来源模式", ("free", "paid"), key="maintenance_mode")
    path = st.text_input(
        "维护合同路径（本机JSON，不含密钥）",
        value=str(store.root / "maintenance-contracts.json"),
        key="maintenance_contract",
    )
    target = st.date_input("维护目标日", value=date.today(), key="maintenance_target")
    try:
        contract_text = Path(path).read_text()
    except OSError:
        contract_text = None
    inputs = (mode, path, target, contract_text)
    if st.button("预览维护计划", key="maintenance_preview"):
        st.session_state.pop("maintenance_plan", None)
        try:
            if contract_text is None:
                raise ValueError("维护合同不可读取")
            contracts = json.loads(contract_text)
            plan = maintenance_plan(store, target, tuple(contracts.get("products", {})), mode=mode, contracts=contracts)
            st.session_state["maintenance_plan"] = plan
            st.session_state["maintenance_inputs"] = inputs
        except (OSError, ValueError, KeyError, PermissionError) as error:
            st.error(f"维护预览受限：{error}；未请求外部数据。")
    plan = st.session_state.get("maintenance_plan")
    if plan is None:
        st.caption("配置产品合同、独立历史目标、日历适用市场和获批额度后可预览；当前未自动采集。")
        return
    st.caption(f"已预览模式 {plan['request']['mode']}，窗口 {plan['start']} 至 {plan['target']}；改变配置需重新预览。")
    st.json({k: plan[k] for k in ("domains", "counts", "limits", "target_scope", "unresolved")})
    if st.button(
        "提交维护任务",
        key="maintenance_submit",
        disabled=st.session_state.get("maintenance_inputs") != inputs
        or not plan["segments"]
        or bool(plan["unresolved"])
        or not plan["limits"].get("approved_scope_ref"),
    ):
        submit(plan)


def render_maintenance_job(job):
    if job.output is None:
        return
    for name in ("update.json", "progress.json"):
        path = job.output / name
        if path.exists():
            result = json.loads(path.read_text())
            st.caption("用量：本次新增 incremental_usage；累计 actual_usage（重开和重试保留账本）。")
            st.json(
                {
                    k: result.get(k)
                    for k in ("incremental_usage", "actual_usage", "pending", "failures", "snapshot_ids", "maintenance")
                }
            )
            break


def render_data_tasks(tasks):
    """共享队列元信息；只有维护任务可在独立页操作。"""
    import uuid

    from hetu_data.jobs import JobService

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
        if job.state in ("queued", "running", "cancel_requested"):
            confirmed = st.checkbox("确认取消此维护任务", key=f"confirm_cancel::{job.job_id}")
            if st.button("取消", key=f"cancel::{job.job_id}", disabled=not confirmed):
                service.cancel(job.job_id)
                st.rerun()
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
