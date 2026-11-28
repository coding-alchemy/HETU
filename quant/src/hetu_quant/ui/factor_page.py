"""Read the factor catalog and run a selected factor without a research job."""

import inspect
import json
import math
import os
from dataclasses import asdict
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path

import streamlit as st
from hetu_data.library import list_libraries
from hetu_data.models import LibraryRef, SnapshotRef

from hetu_quant.data import DataStore
from hetu_quant.factors import FactorRequest, TeachingSource, close_cutoff, compute_factor, list_factors
from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.core import CN, parameter_descriptions
from hetu_quant.factors.selftest import read_selftest, replay_request, replay_selftest
from hetu_quant.factors.sources import teaching_document
from hetu_quant.factors.workbench import build_trial
from hetu_quant.factors.workbench_inputs import preview_inputs
from hetu_quant.ui.factor_attempts import SessionRuns
from hetu_quant.ui.factor_navigation import object_candidates
from hetu_quant.ui.factor_result_view import render_attempt, render_result

_RESEARCH_TOPICS = (
    "未来收益标签、分组收益、IC、RankIC 和成本后收益归入后续效果验证",
    "实际成交参与率、组合换手、交易费用、组合优化和实盘委托归入执行或策略研究",
    "线性模型、排序模型、树模型及监督训练归入后续建模研究",
)
_STATES = {"passed": "通过", "failed": "失败", "untested": "未测"}


def _source_choices(store):
    sources = {
        entry.parent.name: SnapshotRef(entry.parent.name, store.root)
        for entry in sorted((store.root / "snapshots").glob("*/manifest.json"))
        if not entry.parent.name.startswith(".")
    }
    for row in list_libraries(store):
        key = row["library_id"]
        sources[key if key not in sources else f"库 · {key}"] = LibraryRef(key, store.root)
    return sources


def _name(factor_id):
    definition = get_factor(factor_id)
    return f"{definition.name} · {factor_id}"


def _catalog_description(item):
    card = files("hetu_quant.factors").joinpath("cards", f"{item.factor_id}.md").read_text(encoding="utf-8")
    introduction = card.partition("## 介绍、公式与口径")[2].strip()
    first = next((line.strip() for line in introduction.splitlines() if line.strip() and not line.startswith("#")), "")
    if first:
        return first.partition("。")[0].partition("；")[0] + "。"
    return f"按选定观察日计算{item.name}。"


def _clear_filters():
    st.session_state.update(
        factor_search="", factor_category="全部", factor_subject_kind="全部", factor_output_kind="全部"
    )


def _catalog():
    definitions = list_factors()
    search = st.text_input("名称、别名、标识或调研章节", key="factor_search").strip().casefold()
    category = st.selectbox("类别", ["全部", *sorted({d.category for d in definitions})], key="factor_category")
    kinds = sorted({kind for d in definitions for kind in d.subject_kind})
    kind = st.selectbox("适用对象", ["全部", *kinds], key="factor_subject_kind")
    shape = st.selectbox(
        "结果形态",
        ["全部", *sorted({item.output_shape for item in definitions}), "带日期序列"],
        key="factor_output_kind",
    )
    st.button("清除筛选", key="factor_clear_filters", on_click=_clear_filters)
    matches = []
    for item in definitions:
        searchable = " ".join((item.name, item.factor_id, item.chapters, *item.aliases, *item.subject_kind)).casefold()
        if (
            all(token in searchable for token in search.split())
            and (category == "全部" or item.category == category)
            and (kind == "全部" or kind in item.subject_kind)
            and (shape == "全部" or shape == item.output_shape or (shape == "带日期序列" and item.has_dated_series))
        ):
            matches.append(item.factor_id)
    with st.expander("调研条目去向"):
        st.markdown("\n".join(f"- {topic}" for topic in _RESEARCH_TOPICS))
    st.caption(f"找到 {len(matches)} 个因子")
    if not matches:
        st.info("没有匹配的计算项。")
        return None
    definition = get_factor(st.selectbox("因子", matches, format_func=_name, key="factor_id"))
    with st.expander("匹配因子一览", expanded=bool(search) or shape != "全部"):
        st.table(
            [
                {
                    "因子": _name(item.factor_id),
                    "用途": _catalog_description(item),
                    "适用对象": "、".join(item.subject_kind),
                    "主要输出": item.output_label,
                    "单位": item.unit,
                }
                for item in definitions
                if item.factor_id in matches
            ]
        )
    return definition


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _set_parameter(raw_key, path, widget_key, factor_id):
    parameters = {**get_factor(factor_id).defaults, **json.loads(st.session_state[raw_key])}
    parameters = json.loads(_dump(parameters))
    target = parameters
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = st.session_state[widget_key]
    if factor_id == "robust_cross_section_z" and path == ("mode",) and parameters["component"] == "traded_amount":
        nested = parameters.get("component_parameters", {})
        if isinstance(nested, dict):
            parameters["component_parameters"] = {
                **get_factor("traded_amount").defaults, **nested, "mode": parameters["mode"],
            }
    st.session_state[raw_key] = _dump(parameters)


def _parameter_control(raw_key, path, value, label, *, factor_id, choices=(), minimum=None):
    widget_key = f"{raw_key.replace('factor_parameters_', 'factor_param_').split('_默认')[0]}_{'.'.join(path)}"
    st.session_state[widget_key] = value
    options = dict(key=widget_key, on_change=_set_parameter, args=(raw_key, path, widget_key, factor_id))
    if choices and value in choices:
        st.selectbox(label, choices, **options)
    elif type(value) is bool:
        st.checkbox(label, **options)
    elif type(value) in (int, float):
        lower = type(value)(minimum) if minimum is not None else None
        if type(value) is float and path[-1] == "epsilon":
            options.update(format="%.12g", step=0.001)
        st.number_input(label, min_value=lower, **options)
    elif isinstance(value, str):
        st.text_input(label, **options)


def _load_sample(factor_id, sample):
    st.session_state.pop(f"factor_browse_context_{factor_id}", None)
    st.session_state.pop(f"factor_imported_benchmark_{factor_id}", None)
    st.session_state.update(
        {
            f"factor_source_{factor_id}": "教学样例",
            f"factor_date_{factor_id}": date.fromisoformat(sample["trade_date"]),
            f"factor_cutoff_{factor_id}": close_cutoff(date.fromisoformat(sample["trade_date"])).time(),
            f"factor_subjects_{factor_id}": " ".join(sample["subjects"]),
            f"factor_preset_{factor_id}": "默认",
            f"factor_parameters_{factor_id}_默认": _dump(get_factor(factor_id).defaults),
        }
    )
    for index, code in enumerate(sample["subjects"]):
        st.session_state[f"factor_role_{factor_id}_{index}"] = code


def _set_benchmark(definition, benchmark):
    factor_id = definition.factor_id
    preset = st.session_state.get(f"factor_preset_{factor_id}", "默认")
    key = f"factor_parameters_{factor_id}_{preset}"
    try:
        parameters = json.loads(st.session_state.get(key, _dump(definition.presets[preset])))
    except ValueError:
        return
    if isinstance(parameters, dict):
        st.session_state[key] = _dump(dict(parameters, benchmark=benchmark.strip()))


def _set_default(definition):
    factor_id = definition.factor_id
    st.session_state[f"factor_preset_{factor_id}"] = "默认"
    defaults = dict(definition.defaults)
    if "benchmark" in defaults and st.session_state.get(f"factor_browse_context_{factor_id}"):
        defaults["benchmark"] = st.session_state.get(f"factor_imported_benchmark_{factor_id}", "")
    st.session_state[f"factor_parameters_{factor_id}_默认"] = _dump(defaults)


def _compute_trial(trial, root):
    return compute_factor(DataStore(root), trial.source, trial.request)


def _run_controls(store, trial, can_submit):
    state = st.session_state
    if "factor_runs" not in state:
        state["factor_runs"] = SessionRuns(state.setdefault("factor_attempts", {}))
    runs = state["factor_runs"]
    runs.collect()

    def submit(query):
        try:
            permitted = preview_inputs(store, query).can_submit
        except (ValueError, OSError, PermissionError):
            permitted = False
        if not permitted:
            st.warning("当前许可或发布已变更，请检查所选输入。")
            return False
        root = store.root.resolve()
        runs.start(query, lambda frozen: _compute_trial(frozen, root))
        runs.collect()
        return True

    def failed_attempt(attempt, *, archived=False):
        query = attempt.trial
        st.error(attempt.error)
        if archived:
            st.json(dict(
                source=str(query.source), subjects=query.subjects, trade_date=str(query.trade_date),
                cutoff=query.cutoff.isoformat(), parameters=query.request.parameters,
            ), expanded=True)
        if st.button("按原配置重试", key=f"factor_retry_{attempt.id}" if archived else "factor_retry",
                     disabled=runs.active_id is not None or (not archived and not can_submit)):
            if submit(query):
                st.rerun()

    if trial is not None and st.button(
        "计算所选输入", key="factor_compute", disabled=not can_submit or runs.active_id is not None
    ):
        submit(trial)
    attempt = next((item for item in reversed(runs.attempts.values()) if item.trial == trial), None)
    if attempt is None:
        st.caption("当前条件 · 待计算")
    elif attempt.state == "completed":
        render_attempt(store, attempt)
    elif attempt.state == "failed":
        failed_attempt(attempt)

    if runs.active_id is not None:

        @st.fragment(run_every=0.3)
        def poll_running():
            runs.collect()
            if runs.active_id is None:
                st.rerun()
            else:
                running = runs.attempts[runs.active_id]
                st.info(f"进行中 · {running.trial.factor_id} · {running.trial.trade_date}")

        poll_running()

    other = [item for item in runs.attempts.values() if item != attempt and item.state != "running"]
    if other:
        with st.expander("本会话其他试算"):
            chosen = st.selectbox(
                "查看已提交的尝试",
                [None, *[item.id for item in other]],
                format_func=lambda key: (
                    "请选择"
                    if key is None
                    else f"{runs.attempts[key].trial.factor_id} · {runs.attempts[key].submitted_at}"
                ),
            )
            if chosen:
                archived = runs.attempts[chosen]
                if archived.result is not None:
                    render_attempt(store, archived, archive=True)
                else:
                    failed_attempt(archived, archived=True)


def _choose_known_object(store, source, day, definition):
    factor_id = definition.factor_id
    if isinstance(source, TeachingSource):
        return
    with st.expander("在所选发布查找名称或代码"):
        needle = st.text_input("名称或代码", key=f"factor_object_search_{factor_id}")
        coord = (source, day, needle)
        key = f"factor_known_objects_{factor_id}"
        if st.button("查询获准对象", key=f"factor_find_object_{factor_id}"):
            st.session_state[key] = coord
        if st.session_state.get(key) != coord:
            return
        try:
            candidates = object_candidates(store, source, day, definition.purpose, needle)
        except (ValueError, OSError, PermissionError):
            st.warning("无法读取所选发布的对象登记。")
            return
        if not candidates:
            st.info("所选范围无已获准的匹配登记。可以输入已知代码，运行前仍须检查用途。")
            return
        candidate = st.selectbox(
            "匹配对象",
            candidates,
            key=f"factor_pick_{factor_id}",
            format_func=lambda row: f"{row[1]} · {row[0]} · {row[2]}",
        )
        if "配对" in definition.subject_kind:
            index = st.selectbox(
                "填入角色",
                range(len(definition.object_roles)),
                format_func=lambda i: definition.object_roles[i],
                key=f"factor_pick_role_{factor_id}",
            )
            target = f"factor_role_{factor_id}_{index}"
        elif "benchmark" in definition.defaults and st.checkbox("填入基准", key=f"factor_use_benchmark_{factor_id}"):
            target = None
        else:
            target = f"factor_subjects_{factor_id}"

        def apply_candidate():
            try:
                current = object_candidates(DataStore(store.root), source, day, definition.purpose, needle)
            except (ValueError, OSError, PermissionError):
                return
            if candidate in current:
                if target is None:
                    _set_benchmark(definition, candidate[0])
                else:
                    st.session_state[target] = candidate[0]

        st.button(
            "填入所选代码",
            key=f"factor_apply_object_{factor_id}",
            on_click=apply_candidate,
        )


def _form(store, definition):
    sample = teaching_document(TeachingSource(definition.example_id))
    choices = {"教学样例": TeachingSource(definition.example_id), **_source_choices(store)}
    factor_id = definition.factor_id
    st.subheader("试算配置")
    incoming = st.session_state.pop("factor_browse_incoming", None)
    if incoming is not None:
        # An explicit import updates this factor and seeds factors without a draft.
        st.session_state["factor_browse_transfer"] = incoming
    elif st.session_state.get("factor_draft_id") != factor_id:
        if draft := st.session_state.get("factor_drafts", {}).get(factor_id):
            st.session_state.update(draft)
        else:
            incoming = st.session_state.get("factor_browse_transfer")
    st.session_state["factor_draft_id"] = factor_id
    if incoming:
        source = next((key for key, ref in choices.items() if ref == incoming["source"]), None)
        st.session_state[f"factor_source_{factor_id}"] = source
        st.session_state[f"factor_subjects_{factor_id}"] = " ".join(incoming["subjects"])
        for index, code in enumerate(incoming["subjects"]):
            st.session_state[f"factor_role_{factor_id}_{index}"] = code
        if len(incoming["subjects"]) < 2:
            st.session_state[f"factor_role_{factor_id}_1"] = ""
        st.session_state[f"factor_browse_context_{factor_id}"] = incoming["context"]
        if "benchmark" in definition.defaults:
            _set_benchmark(definition, "")
        if incoming["trade_date"]:
            st.session_state[f"factor_date_{factor_id}"] = incoming["trade_date"]
        else:
            st.info("已带入数据来源与对象，请确认观察日。")
    if context := st.session_state.get(f"factor_browse_context_{factor_id}"):
        st.caption(
            f"来自数据浏览 · {context['table']} · {context['axis'] or '静态'} "
            f"{context.get('coordinate') or '未知'} · {context['start']} 至 {context['end']}"
        )
        if context.get("record_key"):
            st.caption(f"凭据登记 {context['record_key']}")
    st.button("用教学样例试算", key="factor_load_teaching", on_click=_load_sample, args=(factor_id, sample))
    if len(choices) == 1:
        st.caption("当前数据根无本地已发布输入，可使用随包教学样例。")
    else:
        st.button(
            "用本地数据试算",
            key="factor_load_local",
            on_click=lambda: st.session_state.update({
                f"factor_source_{factor_id}": next(iter(list(choices)[1:])),
            }),
        )
    source_id = st.selectbox(
        "计算来源",
        choices,
        key=f"factor_source_{factor_id}",
        format_func=lambda key: (
            "教学样例" if key == "教学样例" else f"{'库' if isinstance(choices[key], LibraryRef) else '快照'} · {key}"
        ),
    )
    if source_id is None:
        st.error("带入的发布当前不可用，请选择计算来源。")
        _run_controls(store, None, False)
        return None
    day_key = f"factor_date_{factor_id}"
    cutoff_key = f"factor_cutoff_{factor_id}"
    st.session_state.setdefault(day_key, date.fromisoformat(sample["trade_date"]))
    st.session_state.setdefault(cutoff_key, close_cutoff(date.fromisoformat(sample["trade_date"])).time())
    day = st.date_input("请求交易日", value=None, key=day_key)
    cutoff_time = st.time_input("信息截止（北京时间）", value=None, key=cutoff_key)
    if day is None or cutoff_time is None:
        st.error("请补齐观察日与信息截止。")
        _run_controls(store, None, False)
        return None
    _choose_known_object(store, choices[source_id], day, definition)
    if "配对" in definition.subject_kind:
        selected = []
        for index, role in enumerate(definition.object_roles):
            role_key = f"factor_role_{factor_id}_{index}"
            st.session_state.setdefault(role_key, sample["subjects"][index] if index < len(sample["subjects"]) else "")
            selected.append((st.text_input(role, value=None, key=role_key) or "").strip())
        subjects = tuple(selected)
    else:
        st.caption("截面组与基准可在参数中配置。" if "截面" in definition.subject_kind else "输入代码或适用的组标识。")
        subjects_key = f"factor_subjects_{factor_id}"
        st.session_state.setdefault(subjects_key, " ".join(sample["subjects"]))
        subjects = tuple((st.text_input("观察对象代码（空格分隔）", value=None, key=subjects_key) or "").split())
    benchmark_key = f"factor_imported_benchmark_{factor_id}"
    show_benchmark = (
        source_id != "教学样例" and "benchmark" in definition.defaults
        and st.session_state.get(f"factor_browse_context_{factor_id}")
    )
    preset = st.selectbox("参数预设", definition.presets, key=f"factor_preset_{factor_id}")
    st.button("恢复默认", key=f"factor_reset_{factor_id}", on_click=_set_default, args=(definition,))
    changes = {key: value for key, value in definition.presets[preset].items() if definition.defaults.get(key) != value}
    if changes:
        st.caption(f"预设改动 {_dump(changes)}")
    raw_key = f"factor_parameters_{factor_id}_{preset}"
    initial = definition.presets[preset]
    st.session_state.setdefault(raw_key, _dump(dict(initial, benchmark="") if show_benchmark else initial))
    with st.expander("高级编辑 · 完整参数"):
        raw = st.text_area("完整计算参数", value=None, key=raw_key)
    draft_keys = (
        raw_key,
        benchmark_key,
        *(f"factor_{key}_{factor_id}" for key in ("source", "date", "cutoff", "subjects", "preset")),
        *(f"factor_role_{factor_id}_{i}" for i in range(2)),
    )
    draft = {key: st.session_state[key] for key in draft_keys if key in st.session_state}
    st.session_state.setdefault("factor_drafts", {})[factor_id] = draft
    st.session_state["factor_return_state"] = {
        **draft,
        **{key: st.session_state[key] for key in (
            "factor_search", "factor_category", "factor_subject_kind", "factor_output_kind", "factor_id",
        ) if key in st.session_state},
    }
    trial, updates = None, None
    try:
        updates = json.loads(raw or "")
        if not isinstance(updates, dict):
            raise ValueError("计算参数须为 JSON 对象")
        if show_benchmark:
            benchmark = updates.get("benchmark", "")
            st.session_state[benchmark_key] = benchmark if isinstance(benchmark, str) else ""
            st.text_input(
                "基准代码（请显式选择）", key=benchmark_key,
                on_change=lambda: _set_benchmark(definition, st.session_state[benchmark_key]),
            )
            updates.setdefault("benchmark", "")
        trial = build_trial(
            choices[source_id], FactorRequest(factor_id, subjects, day, datetime.combine(day, cutoff_time, CN), updates)
        )
    except ValueError as error:
        st.error(f"参数错误 · {getattr(error, 'field', '')} {error}")
    if not isinstance(updates, dict):
        _run_controls(store, None, False)
        return None
    parameters = trial.request.parameters if trial else {**initial, **updates}
    descriptions = parameter_descriptions(definition)
    for name, value in parameters.items():
        if name == "benchmark" and show_benchmark:
            continue
        spec = descriptions.get(name)
        if spec is None:
            continue
        if trial is None:
            kind = spec["kind"]
            numeric = kind in (int, float) and type(value) in (int, float) and math.isfinite(value)
            if not (numeric or kind in (bool, str) and type(value) is kind):
                continue
            if spec["minimum"] is not None and value < spec["minimum"]:
                continue
        label = f"{spec['label']} · {name}" + (f"（{spec['unit']}）" if spec["unit"] else "")
        _parameter_control(
            raw_key, (name,), value, label, factor_id=factor_id, choices=spec["choices"], minimum=spec["minimum"]
        )
        if name == "components":
            for component, settings in value.items():
                st.caption(get_factor(component).name)
                for field, setting in settings.items():
                    _parameter_control(raw_key, (name, component, field), setting, field, factor_id=factor_id)
                    if field == "parameters":
                        for key, parameter in setting.items():
                            _parameter_control(
                                raw_key, (name, component, field, key), parameter, key, factor_id=factor_id
                            )
    if trial is None:
        _run_controls(store, None, False)
        return None
    with st.expander("本次有效参数"):
        st.code(_dump(parameters), language="json")
    try:
        preview = preview_inputs(store, trial)
    except (ValueError, OSError, PermissionError):
        st.warning("无法核对固定发布的覆盖与当前读权。")
        _run_controls(store, None, False)
        return parameters
    states = {"covered": "已具备", "missing": "缺输入", "denied": "用途受限", "pending": "待计算核对"}
    with st.expander("输入覆盖与许可"):
        for check in preview.checks:
            period = " 至 ".join(str(check[key]) for key in ("start", "end") if check.get(key) is not None)
            source = " / ".join(str(check[key]) for key in ("source", "product") if check.get(key))
            st.write(
                f"{states[check['state']]} · {check['subject']} · {check['field']} · "
                f"{period} · {check['purpose']} · {source} · {check['reason']}"
            )
    if not preview.can_submit:
        st.warning("当前发布对本次因子用途无可读输入。")
    _run_controls(store, trial, preview.can_submit)
    return parameters


def render_factor_page(store, root):
    if st.session_state.pop("factor_restore_draft", False):
        st.session_state.update(st.session_state.get("factor_return_state", {}))
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
                    render_result(asdict(replay_selftest(row)))
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
