"""Read the factor catalog and run a selected factor without a research job."""

import inspect
import json
import os
from dataclasses import asdict
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path

import pandas as pd
import streamlit as st

from hetu_quant.data import AccessDenied, SnapshotTamperedError
from hetu_quant.factors import FactorRequest, TeachingSource, close_cutoff, compute_factor, list_factors
from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.core import CN, parameter_identity, resolve_request
from hetu_quant.factors.cross_section import component_requests
from hetu_quant.factors.selftest import read_selftest, replay_request, replay_selftest
from hetu_quant.factors.sources import teaching_document
from hetu_quant.ui.after_close_page import _source_choices

_RESEARCH_TOPICS = (
    "未来收益标签、分组收益、IC、RankIC 和成本后收益归入后续效果验证",
    "实际成交参与率、组合换手、交易费用、组合优化和实盘委托归入执行或策略研究",
    "线性模型、排序模型、树模型及监督训练归入后续建模研究",
)
_STATES = {"passed": "通过", "failed": "失败", "untested": "未测"}
_DATA_STATES = {"available": "可算", "partial": "部分可算", "unavailable": "缺证"}


def _name(factor_id):
    definition = get_factor(factor_id)
    return f"{definition.name} · {factor_id}"


def _catalog():
    definitions = list_factors()
    search = st.text_input("名称、别名、标识或调研章节", key="factor_search").strip().casefold()
    category = st.selectbox("类别", ["全部", *sorted({d.category for d in definitions})], key="factor_category")
    kinds = sorted({kind for d in definitions for kind in d.subject_kind})
    kind = st.selectbox("适用对象", ["全部", *kinds], key="factor_subject_kind")
    matches = []
    for item in definitions:
        searchable = " ".join((item.name, item.factor_id, item.chapters, *item.aliases, *item.subject_kind)).casefold()
        if (
            all(token in searchable for token in search.split())
            and (category == "全部" or item.category == category)
            and (kind == "全部" or kind in item.subject_kind)
        ):
            matches.append(item.factor_id)
    with st.expander("调研条目去向"):
        st.markdown("\n".join(f"- {topic}" for topic in _RESEARCH_TOPICS))
    if not matches:
        st.info("没有匹配的计算项。")
        return None
    return get_factor(st.selectbox("因子", matches, format_func=_name, key="factor_id"))


def _result(result):
    st.subheader(f"选定输入状态 · {_DATA_STATES[result['status']]}")
    st.caption(
        f"请求日 {result['trade_date']}　信息截止 {result['cutoff']}　"
        f"{result['source']['nature']} · {result['source']['id']}"
    )
    st.json(dict(subjects=result["subjects"], parameters=result["parameters"], source=result["source"]), expanded=False)
    if result["rows"]:
        rows = []
        for row in result["rows"]:
            value = row["value"]
            if isinstance(value, bool):
                value = "是" if value else "否"
            elif isinstance(value, (int, float)) and row["unit"] in ("比例", "年化比例", "月度比例"):
                value = f"{value * 100:.8g}%"
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            else:
                value = str(value)
            rows.append(
                {
                    "对象": " / ".join(row["subject"]) if isinstance(row["subject"], tuple) else row["subject"],
                    "读数": value,
                    "单位": row["unit"],
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        with st.expander("复算中间量、原始输入与出处"):
            st.json(result["rows"], expanded=False)
    if result["gaps"]:
        st.dataframe(
            pd.DataFrame(
                [{field: value for field, value in gap.items() if field != "evidence"} for gap in result["gaps"]]
            ),
            hide_index=True,
        )
        if any(gap.get("evidence") for gap in result["gaps"]):
            with st.expander("缺口与冲突原件"):
                st.json(result["gaps"], expanded=False)


def _form(store, definition):
    sample = teaching_document(TeachingSource(definition.example_id))
    choices = {"教学样例": TeachingSource(definition.example_id), **_source_choices(store)}
    factor_id = definition.factor_id
    if len(choices) == 1:
        st.caption("当前数据根无本地已发布输入，可使用随包教学样例。")
    source_id = st.selectbox("计算来源", choices.keys(), key=f"factor_source_{factor_id}")
    day = st.date_input("请求交易日", value=date.fromisoformat(sample["trade_date"]), key=f"factor_date_{factor_id}")
    cutoff_time = st.time_input(
        "信息截止（北京时间）", value=close_cutoff(day).time(), key=f"factor_cutoff_{factor_id}"
    )
    subjects = st.text_input(
        "观察对象代码（空格分隔，配对按顺序）", value=" ".join(sample["subjects"]), key=f"factor_subjects_{factor_id}"
    )
    preset = st.selectbox("参数预设", definition.presets.keys(), key=f"factor_preset_{factor_id}")
    raw = st.text_area(
        "完整计算参数",
        json.dumps(definition.presets[preset], ensure_ascii=False, indent=2),
        key=f"factor_parameters_{factor_id}_{preset}",
    )
    try:
        updates = json.loads(raw)
        if not isinstance(updates, dict):
            raise ValueError("计算参数须为 JSON 对象")
        query = FactorRequest(factor_id, tuple(subjects.split()), day, datetime.combine(day, cutoff_time, CN), updates)
        query = resolve_request(definition, query)
        component_requests(query)
        key = parameter_identity(query.parameters)
    except ValueError as error:
        st.error(f"参数错误 · {getattr(error, 'field', '')} {error}")
        return None
    parameters = query.parameters
    coordinate = (factor_id, source_id, query.subjects, day, query.cutoff, key)
    if st.button("计算所选输入", key="factor_compute"):
        st.session_state.pop("factor_result", None)
        try:
            result = compute_factor(store, choices[source_id], query)
        except (AccessDenied, SnapshotTamperedError, OSError, ValueError) as error:
            st.error(f"无法读取所选输入。{error}")
        else:
            st.session_state["factor_result"] = coordinate, asdict(result)
    saved = st.session_state.get("factor_result")
    if saved and saved[0] == coordinate:
        _result(saved[1])
    return parameters


def render_factor_page(store, root):
    st.header("因子库")
    if (definition := _catalog()) is None:
        return
    factor_id = definition.factor_id
    st.markdown(files("hetu_quant.factors").joinpath("cards", f"{factor_id}.md").read_text(encoding="utf-8"))
    if definition.direction:
        st.caption(f"比较方向 · {definition.direction}")
    with st.expander("实际 Python 源码"):
        st.code(inspect.getsource(definition.compute), language="python")
    with st.expander("公式所属模块（含共用计算）"):
        module = inspect.getmodule(definition.compute)
        if module is not None:
            st.code(inspect.getsource(module), language="python")
    example = files("hetu_quant.factors").joinpath("examples", f"{definition.example_id}.json")
    st.download_button(
        "下载随包教学原始事实",
        example.read_bytes(),
        file_name=f"{definition.example_id}.json",
        mime="application/json",
        key=f"factor_sample_{factor_id}",
    )
    parameters = _form(store, definition)
    evidence_root = Path(os.environ.get("HETU_FACTOR_EVIDENCE_ROOT") or root)
    selftest = (
        read_selftest(evidence_root, factor_id, definition.version, parameters)
        if parameters is not None
        else {"status": "untested", "cases": []}
    )
    st.subheader(f"逻辑自测 · {_STATES[selftest['status']]}")
    st.caption(f"收据目录 {evidence_root.resolve()}")
    if selftest["status"] == "passed":
        count = selftest.get("formula_cases", 0)
        st.caption(f"基础用例通过，已保存 {count} 项公式专项断言。展开查看各用例的实际证明范围。")
    with st.expander("测试用例、独立预期与实际值"):
        st.caption("历史缺证检查只证明对应资料不足。公式专项列出输入变化、独立期望及实际输出。")
        st.json(
            [{key: value for key, value in row.items() if key != "replay"} for row in selftest["cases"]], expanded=False
        )
        replayable = {}
        for row in selftest["cases"]:
            if "replay" not in row and "execution_parameters" not in row:
                continue
            try:
                replay_request(row)
            except ValueError as error:
                st.warning(f"{row['case_id']} 收据缺少可复算资料。{error}")
            else:
                replayable[row["receipt"]] = row
        if replayable:
            selected = st.selectbox(
                "选择要复算的合成收据",
                replayable,
                format_func=lambda key: (
                    replayable[key]["case_id"] + " · " + str(replayable[key]["execution_parameters"])
                ),
                key=f"factor_replay_{factor_id}",
            )
            row = replayable[selected]
            package = {key: row[key] for key in ("factor_id", "execution_parameters", "replay")}
            package["tested_parameters"] = row.get("tested_parameters", row["parameters"])
            st.download_button(
                "下载此收据的精确输入",
                json.dumps(package, ensure_ascii=False, indent=2),
                file_name=f"{factor_id}-receipt-input.json",
                mime="application/json",
            )
            if st.button("复算所选收据", key="factor_replay_compute"):
                try:
                    _result(asdict(replay_selftest(row)))
                except (KeyError, OSError, ValueError) as error:
                    st.error(f"无法复算收据。{error}")
    st.subheader("效果未验证")
    st.json(
        dict(
            definition.effect_validation,
            关联=dict(factor_id=factor_id, version=definition.version, parameters=parameters),
        ),
        expanded=False,
    )
