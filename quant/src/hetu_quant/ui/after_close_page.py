"""Streamlit views for generation and read-only reopening of after-close research."""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from hetu_data.library import list_libraries
from hetu_data.models import LibraryRef

from hetu_quant.after_close import (
    _public_result_docs,
    generate_after_close,
    list_after_close_evidence,
    read_after_close,
    read_after_close_evidence,
)
from hetu_quant.data import DataQualityError, DataStore, SnapshotTamperedError
from hetu_quant.experiments import ExperimentStore
from hetu_quant.models import ResultRef, SnapshotRef


def _source_choices(store):
    return {
        **{sid: SnapshotRef(sid, store.root) for sid, _ in store._published_manifests()},
        **{row["library_id"]: LibraryRef(row["library_id"], store.root) for row in list_libraries(store)},
    }


def _open_result(result_id):
    st.session_state["suggested_result"] = result_id
    st.session_state["next_page"] = "结果与导出"
    st.rerun()


def render_after_close_form(store: DataStore, output_root: Path) -> None:
    choices = _source_choices(store)
    if not choices:
        st.info("尚无可选择的本地已发布输入。")
        return
    st.subheader("盘后研究")
    with st.form("after_close_form"):
        source = st.selectbox("已发布本地输入", list(choices), key="after_close_source")
        day = st.date_input("请求交易日", value=date.today(), key="after_close_trade_date")
        submitted = st.form_submit_button("生成并保存盘后结果", key="after_close_generate")
    if submitted:
        try:
            ref = generate_after_close(store, choices[source], trade_date=day, output_root=output_root)
        except (OSError, DataQualityError, SnapshotTamperedError, ValueError) as error:
            st.error(f"盘后结果未发布。{error}")
        else:
            st.session_state["after_close_result"] = ref.result_id
            st.success(f"盘后结果已保存 {ref.result_id}")
    if current := st.session_state.get("after_close_result"):
        render_after_close_result(ExperimentStore(output_root).get_result(current), show_follow_up=False)


def _table(rows, *, key, columns=None):
    if rows:
        frame = pd.DataFrame(rows)
        if columns:
            frame = frame.reindex(columns=columns)
        percentages = {
            name: st.column_config.NumberColumn(label, format="percent")
            for name, label in {"target_weight": "目标权重", "prior_weight": "上期权重",
                                "weight": "本期权重", "label_return": "到期总收益"}.items()
            if name in frame
        }
        st.dataframe(frame, key=key, hide_index=True, column_config=percentages)


def _render_rankings(rankings):
    for kind, title in (("industry", "行业榜"), ("theme", "主题榜")):
        st.subheader(title)
        st.markdown(f"排行榜日期 {rankings['trade_date']} · 信息截止 {rankings['as_of']} · {rankings['purpose']}")
        if rankings.get("benchmark"):
            benchmark = rankings["benchmark"]
            st.markdown(f"共同基准 {benchmark['code']} · {benchmark['return_basis']} 口径")
        boards = [row for row in rankings["boards"] if row["board_kind"] == kind]
        _table(boards, key=f"close_{kind}_boards",
               columns=["board_id", "name", "rank", "score", "eligible_members", "total_members", "selected"])
        ids = {row["board_id"] for row in boards}
        _table([row for row in rankings["cards"] if row["entity_id"] in ids or row.get("group_id") in ids],
               key=f"close_{kind}_evidence", columns=["entity_id", "group_id", "rationale", "limitations"])
        if not boards:
            st.markdown("没有具备证据的板块名次。")
        for reason in rankings["limitations"]:
            st.markdown(f"榜单缺口 · {reason}")
    if rankings["exclusions"]:
        _table(rankings["exclusions"], key="close_ranking_exclusions")


def render_after_close_result(ref: ResultRef, *, show_follow_up=True) -> None:
    value = read_after_close(ref)
    target = value["target"]
    coverage = value["coverage"]
    st.subheader("盘后日线研究")
    st.markdown(f"发布 {ref.result_id} · {value['data_nature']}")
    st.markdown(f"请求交易日 {value['trade_date']} · 行情实际截止 {value['actual_cutoff'] or '未知'}")
    st.markdown(f"低波信息截止 {value['cutoff']} · 生成时间 {value['generated_at']}")
    sources = value["source"]
    st.markdown(f"输入 {sources['kind']} {sources['id']} · {sources['root']}")
    if products := sorted({row["product_id"] for row in sources["assets"]}):
        st.markdown(f"来源产品 {'、'.join(products)}")
    pool = coverage["pool_size"] if coverage["pool_size"] is not None else "未知"
    st.markdown(f"请求日实到 {coverage['count']} 只 · 独立原池分母 {pool}")
    if coverage["codes"]:
        st.markdown(f"实到名单 {'、'.join(coverage['codes'])}")
    if coverage["suspended"]:
        st.markdown(f"有证暂停 {'、'.join(coverage['suspended'])}")
    if coverage["unresolved"]:
        st.markdown(f"行情或状态待证 {'、'.join(coverage['unresolved'])}")

    st.subheader("原价与成交观察")
    st.markdown("原价口径 · 连续日变化 · 20 日成交额中位数 · 60 日原价波动率（年化）")
    _table(value["observations"], key="after_close_observations", columns=[
        "code", "date", "open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny",
        "raw_change", "amount_median_20d", "raw_volatility_60d", "revised_published_at", "version_note",
    ])
    _table(value["observation_gaps"], key="after_close_observation_gaps")
    if value["actual_cutoff"] != value["trade_date"]:
        st.markdown(f"请求日行情未到，当前观察保留原截止日 {value['actual_cutoff'] or '未知'}。")
        earlier = sorted((doc for doc in _public_result_docs(ref.root) if doc["trade_date"] < value["trade_date"]),
                         key=lambda doc: (doc["trade_date"], doc["generated_at"]), reverse=True)
        if earlier and st.button(f"打开早期结果 {earlier[0]['trade_date']}", key="after_close_open_earlier"):
            _open_result(earlier[0]["result_id"])

    _render_rankings(value["rankings"])
    st.subheader("低波信号")
    st.markdown(value["rule"])
    state = {
        "ready": f"新调仓信号 {value['signal_date']}",
        "ordinary": "今日无新调仓信号",
        "calendar_unknown": "交易日或月末待证，保留已有观察",
        "blocked": "正式目标待证，保留有证排名与观察",
    }[target["status"]]
    st.markdown(state)
    if target["status"] == "ready":
        st.markdown(f"最早适用日 {target['executable_from']}" if target["executable_from"] else "最早适用日待定")
        _table(target["targets"], key="after_close_targets")
        _table(value["changes"], key="after_close_changes")
    elif target["candidates"]:
        _table(target["candidates"], key="after_close_candidates")
    _table(target["gaps"], key="after_close_signal_gaps")
    if previous := value["previous"]:
        origin = "已保存的原发布" if previous["origin"] == "saved" else "本次按当时事实回算"
        result_id = previous["result_id"] or ""
        source_id = previous.get("source_id")
        st.markdown(f"上次有效信号 {previous['signal_date']} · {origin} {result_id}"
                    f" · 形成时间 {previous['computed_at']}")
        if source_id:
            st.markdown(f"历史回算输入 {source_id}")
        _table(previous["targets"], key="after_close_previous")
    else:
        prefix = "首次可证信号" if target["status"] == "ready" else "尚无合格历史信号"
        st.markdown(f"{prefix} · {value['previous_gap']}")

    if show_follow_up:
        _render_follow_up(ref, value)


def _render_follow_up(ref, value):
    st.subheader("修订与后续证据")
    if original := value["revision_of"]:
        st.markdown(f"本发布为修订 · 原发布 {original}")
        _table([{**row, "before": str(row["before"]) if row["before"] is not None else None,
                 "after": str(row["after"]) if row["after"] is not None else None}
                for row in value.get("revision_changes", [])], key="after_close_revision_fields")
        if st.button("打开原发布", key="after_close_open_original"):
            _open_result(original)
    else:
        revisions = [doc["result_id"] for doc in _public_result_docs(ref.root) if doc["revision_of"] == ref.result_id]
        if revisions:
            rev = st.selectbox("已发布修订", revisions, key="after_close_revisions")
            if st.button("查看所选修订", key="after_close_open_revision"):
                _open_result(rev)
    signal_id = ref.result_id if value["signal_date"] else (value["previous"] or {}).get("result_id")
    links = list_after_close_evidence(ref.root, signal_id) if signal_id else []
    if not links:
        st.markdown("该信号版本暂无已发布的后续证据。")
    for link in links:
        name = "到期评价" if link["kind"] == "evaluation" else "历史模拟"
        source = link["source_id"]
        st.markdown(f"{name} {source} · 信号 {link['signal_date']} · 发布 {link['publication_id']}")
        try:
            data = read_after_close_evidence(ref.root, link)
        except (OSError, ValueError) as error:
            st.markdown(f"后续证据待核对 · {error}")
            continue
        if link["kind"] == "evaluation":
            st.markdown(f"评价截止 {data['through']}")
            _table(data["horizons"], key=f"evaluation_{source}")
        else:
            for table, label in (("orders", "订单"), ("trades", "成交"), ("costs", "费用"),
                                 ("unassigned_costs", "未归属费用"), ("labels", "模拟的因子评价"),
                                 ("excluded", "模拟因子评价缺口")):
                if data[table]:
                    st.markdown(label)
                    _table(data[table], key=f"followup_{source}_{table}")
    store = DataStore(Path(os.environ.get("HETU_DATA_ROOT") or ref.root).expanduser())
    choices = _source_choices(store)
    if not choices:
        return
    choice = st.selectbox("修订或到期评价的本地输入", list(choices), key="after_close_followup_source")
    if not value["revision_of"] and st.button("另存修订", key="after_close_revise"):
        try:
            new = generate_after_close(store, choices[choice], trade_date=date.fromisoformat(value["trade_date"]),
                                       output_root=ref.root, revision_of=ref.result_id)
        except (OSError, DataQualityError, SnapshotTamperedError, ValueError) as error:
            st.error(f"修订未发布。{error}")
        else:
            _open_result(new.result_id)

    if value["signal_date"]:
        through = st.date_input("评价截止交易日", value=date.today(), key="after_close_evaluation_through")
        if st.button("发布到期评价", key="after_close_evaluate"):
            from hetu_quant.backtest_evaluation import publish_after_close_evaluation

            try:
                identifier = publish_after_close_evaluation(store, choices[choice], ref, through=through)
            except (OSError, DataQualityError, SnapshotTamperedError, ValueError) as error:
                st.error(f"评价未发布。{error}")
            else:
                st.session_state["last_after_close_evaluation"] = identifier
                st.rerun()
