"""二期 04.1 回测页面：配置→资格预览→提交→结果阅读与只读导出。

Streamlit 只组织交互：预览与 worker 共用 01 的 prepare_backtest 资格，
不自行降低门槛；提交走 JobService 持久化提交标识（同次重复幂等、终态后
新点击产生新任务）；结果区只读已发布产物——先研究/经济结论与限制，再
净值/成本/持仓/成交/信号。页面只构造 mode='free' 定义，任何入口不收
到 paid；未接入候选（E/P 等）只显示规则与缺口，不显示可运行。
"""

from __future__ import annotations

import json
import runpy
import tempfile
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from hetu_quant.backtest_data import prepare_backtest
from hetu_quant.backtest_models import (
    BacktestDefinition,
    backtest_definition_from_dict,
    backtest_definition_to_dict,
)
from hetu_quant.backtest_reporting import compare_backtests, export_backtest, read_backtest
from hetu_quant.experiments import ExperimentStore
from hetu_quant.strategies import require_formal_protocol, strategy_method_label
from hetu_quant.ui.app import _parse_cn, _submit

CN_TZ_NAME = "Asia/Shanghai"

# 未接入候选：只读显示规则与缺口，不提供可运行选项（03 登记后接入）
UNREGISTERED_STRATEGIES = ("ep_value",)


def _default_protocol(
    *,
    initial_cash: float,
    benchmark_id: str,
    commission_rate: float,
    min_commission_cny: float,
    slippage_rate: float,
    start: date,
    end: date,
) -> dict:
    """机制阶段默认协议（stage=mechanism；formal 由 03 接通后另存新定义）。"""
    return {
        "schema_version": 1,
        "stage": "mechanism",
        "universe": {"rule": "phase2_historical_universe"},
        "warmup_months": 0,
        "initial_cash_cny": float(initial_cash),
        "currency": "CNY",
        "account_type": "cash",
        "leverage": 1.0,
        "cash_interest_rate": 0.0,
        "investor_tax_profile": "cn_individual_research",
        "rebalance": {"frequency": "monthly"},
        "benchmark_id": benchmark_id,
        "execution": {
            "signal_time": "close",
            "fill_time": "next_open",
            "order_expiry": "same_day",
            "volume_lookback_days": 20,
            "participation_rate": 0.01,
            "terminal_policy": "hold",
        },
        "costs": {
            "commission_rate": float(commission_rate),
            "min_commission_cny": float(min_commission_cny),
            "slippage_rate": float(slippage_rate),
            "statutory_rules_ref": "phase2-trading-rules",
        },
        "rules_ref": "phase2-trading-rules",
        "time_split": {"final": [start.isoformat(), end.isoformat()]},
        "parameter_family": {"lookback_months": [36]},
        "objectives": {"income": "net_cagr_gt_0"},
        "robustness": {"scenarios": ["double_costs"]},
        "trial_budget": {"max_runs": 8},
        "viewing_record": {"final_seen": False},
    }


def _validation_definitions(payload: dict) -> list[dict]:
    # 复用独立研究调度的同一八项展开，不在网页另建预算或试验定义。
    entry = Path(__file__).resolve().parents[3] / "research" / "run_validation.py"
    return runpy.run_path(str(entry))["validation_definitions"](payload)


def _build_formal_definition(payload: dict, phase: str, trial_id: str) -> BacktestDefinition:
    selected = next((row for row in _validation_definitions(payload)
                     if row["trial_id"] == trial_id), None)
    if selected is None or selected["phase"] != phase:
        raise ValueError("trial/phase mismatch")
    facts = deepcopy(selected["definition"])
    require_formal_protocol(backtest_definition_from_dict(facts))
    facts["protocol"]["research_execution"] = dict(
        study_id=payload["study_id"], trial_id=trial_id, phase=phase, payload=deepcopy(payload))
    return backtest_definition_from_dict(facts)


def _build_definition(root_inputs: dict) -> BacktestDefinition:
    if root_inputs.get("stage") == "formal":
        payload = deepcopy(root_inputs["research_payload"])
        facts = payload["definition"]
        facts.update(as_of=root_inputs["as_of"].isoformat(),
                     snapshot_id=root_inputs["snapshot_id"], mode="free",
                     start=root_inputs["start"].isoformat(), end=root_inputs["end"].isoformat(),
                     strategy_id=root_inputs["strategy_id"],
                     parameters={"lookback_months": int(root_inputs["lookback_months"])})
        protocol = facts["protocol"]
        protocol.update(stage="formal", initial_cash_cny=float(root_inputs["initial_cash"]),
                        benchmark_id=root_inputs["benchmark_id"],
                        warmup_months=int(root_inputs["lookback_months"]))
        protocol["costs"].update(commission_rate=float(root_inputs["commission_rate"]),
                                 min_commission_cny=float(root_inputs["min_commission_cny"]),
                                 slippage_rate=float(root_inputs["slippage_rate"]))
        protocol["time_split"]["development"][0] = facts["start"]
        protocol["time_split"]["final"][1] = facts["end"]
        return _build_formal_definition(payload, root_inputs["phase"], root_inputs["trial_id"])
    strategy_id = root_inputs["strategy_id"]
    if strategy_id != "universe_vw":
        raise ValueError("网页机制验证仅支持透明市值基准 universe_vw；低波动请使用正式月频协议")
    protocol = _default_protocol(
        initial_cash=root_inputs["initial_cash"], benchmark_id=root_inputs["benchmark_id"],
        commission_rate=root_inputs["commission_rate"], min_commission_cny=root_inputs["min_commission_cny"],
        slippage_rate=root_inputs["slippage_rate"], start=root_inputs["start"], end=root_inputs["end"])
    protocol["rebalance"]["frequency"] = root_inputs.get("frequency", "monthly")
    return BacktestDefinition(
        as_of=root_inputs["as_of"],
        snapshot_id=root_inputs["snapshot_id"],
        start=root_inputs["start"],
        end=root_inputs["end"],
        strategy_id=strategy_id,
        parameters={},
        protocol=protocol,
        mode="free",
    )


def _render_configuration(root: Path, store, experiments: ExperimentStore) -> None:
    st.caption("免费模式 free；正式研究与机制演示分别冻结。执行由本地 worker 完成，关闭网页后可找回。")
    definitions = experiments.list_definitions("backtest")
    if definitions:
        with st.expander("复制已保存的回测定义"):
            _render_copy(store, experiments, definitions)
    stage = st.radio("运行用途", ("formal", "mechanism"), horizontal=True, key="bt_stage")
    mode = st.selectbox("来源模式", ("free", "paid"), key="bt_mode")
    if mode == "paid":
        st.warning("现有研究 worker 不接收 paid 定义。可在数据页使用已获准的付费数据操作。")
        return
    protocol_path = Path(__file__).resolve().parents[3] / "research" / "phase2-protocol.json"
    payload = _formal_payload(protocol_path)
    phase, trial_id = "development_validation", "dev_main"
    frequency = "monthly"
    if stage == "formal":
        st.caption("低波动：36个月月收益、月调仓、最低十分位及市值权重。八项试验共用冻结协议与预算。")
        st.selectbox("股票池规则", (payload["definition"]["protocol"]["universe"]["rule"],), key="bt_pool")
        try:
            rows = _validation_definitions(payload)
        except (ValueError, KeyError, TypeError) as error:
            st.error(f"研究协议无效：{error}")
            return
        phase = st.selectbox("研究阶段", ("development_validation", "final"), key="bt_phase")
        trials = [row["trial_id"] for row in rows if row["phase"] == phase]
        trial_id = st.selectbox("预算内试验", trials, index=1, key="bt_trial")
        for difference in payload.get("method_differences", []):
            st.caption(difference)
    else:
        st.info("透明市值基准 universe_vw 的周/月机制验证：从合格历史池生成市值权重，"
                "只证明信号与账户行为，经济结论为 not_evaluated；低波动研究使用正式月频协议。")
        frequency = st.selectbox("调仓规则", ("monthly", "weekly"), key="bt_frequency")
    snapshots = [snapshot_id for snapshot_id, _ in store._published_manifests()]
    if not snapshots:
        st.warning("尚无已发布快照，请先在数据页更新或导入。")
        return

    col_left, col_right = st.columns(2)
    strategy_id = col_left.selectbox(
        "已登记策略", ["low_volatility"] if stage == "formal" else ["universe_vw"], key="bt_strategy"
    )
    snapshot_id = col_right.selectbox("冻结快照", snapshots, key="bt_snapshot")
    col_start, col_end = st.columns(2)
    start = col_start.date_input(
        "开始", value=date.fromisoformat(payload["definition"]["start"]) if stage == "formal" else date(2021, 6, 1),
        key="bt_start")
    end = col_end.date_input(
        "结束", value=date.fromisoformat(payload["definition"]["end"]) if stage == "formal" else date(2024, 6, 30),
        key="bt_end")
    col_cash, col_bench = st.columns(2)
    initial_cash = col_cash.number_input(
        "初始资本（CNY）", min_value=0.0, value=1_000_000.0, key="bt_capital"
    )
    benchmark_id = col_bench.selectbox("共同基准", ("universe_vw",), key="bt_benchmark")
    col_comm, col_min, col_slip = st.columns(3)
    commission_rate = col_comm.number_input(
        "佣金率", min_value=0.0, value=0.0003, format="%.6f", key="bt_commission"
    )
    min_commission_cny = col_min.number_input(
        "最低佣金（CNY）", min_value=0.0, value=5.0, key="bt_min_commission"
    )
    slippage_rate = col_slip.number_input(
        "滑点率", min_value=0.0, value=0.0005, format="%.6f", key="bt_slippage"
    )
    lookback_months = 36
    if strategy_id == "low_volatility":
        lookback_months = st.number_input(
            "波动率回看月数", min_value=1, value=36, key="bt_lookback"
        )
    as_of_text = st.text_input(
        "信息截止 as_of（Asia/Shanghai）",
        value=datetime.now(ZoneInfo(CN_TZ_NAME)).isoformat(),
        key="bt_as_of",
    )
    st.info(
        "未接入候选：" + "、".join(UNREGISTERED_STRATEGIES)
        + "（规则与数据合格后以独立定义接入，当前只读显示规则/缺口，不可运行）"
    )

    with st.expander("候选规则与缺口（只读）"):
        candidates = protocol_path.with_name("phase2-candidates.md")
        st.markdown(candidates.read_text(encoding="utf-8"))

    try:
        definition = _build_definition(
            {
                "strategy_id": strategy_id,
                "snapshot_id": snapshot_id,
                "start": start,
                "end": end,
                "as_of": _parse_cn(as_of_text),
                "initial_cash": initial_cash,
                "benchmark_id": benchmark_id,
                "commission_rate": commission_rate,
                "min_commission_cny": min_commission_cny,
                "slippage_rate": slippage_rate,
                "lookback_months": lookback_months,
                "stage": stage, "research_payload": payload,
                "phase": phase, "trial_id": trial_id, "frequency": frequency,
            }
        )
    except (ValueError, KeyError, TypeError) as error:
        st.error(f"定义无效：{error}")
        return

    st.subheader("冻结定义（提交后不可修改；修改请另存新定义）")
    st.json(backtest_definition_to_dict(definition))
    st.caption(f"实际策略：{definition.strategy_id}；方法标签：{strategy_method_label(definition)}")
    st.caption("信号：收盘；成交：下一交易日开盘。")
    if st.button("检查资格与限制", key="bt_check_qualification"):
        st.session_state.pop("bt_qualification", None)
        # 与 worker 共用 01 的 prepare_backtest；不自行降低门槛
        # prepare_backtest 要求 bundle 目录尚不存在：用临时父目录下的新路径
        try:
            with tempfile.TemporaryDirectory(prefix="hetu-bt-qual-") as workdir:
                report = prepare_backtest(definition, store, Path(workdir) / "bundle")
        except Exception as error:  # noqa: BLE001 - 缺口如实展示
            st.error(f"资格检查失败：{type(error).__name__}: {error}")
        else:
            st.session_state["bt_qualification"] = (backtest_definition_to_dict(definition), {
                    "can_run": report.can_run,
                    "allowed_uses": list(report.allowed_uses),
                    "eligible": len(report.eligible_ids),
                    "excluded": {k: list(v) for k, v in report.excluded.items()},
                    "limitations": list(report.limitations),
                })
    qualified = st.session_state.get("bt_qualification")
    preview_current = bool(qualified and qualified[0] == backtest_definition_to_dict(definition))
    if preview_current:
        st.json(qualified[1])
    can_submit = stage == "mechanism" or (preview_current and qualified[1]["can_run"])
    if st.button("提交任务", key="bt_submit_job", disabled=not can_submit):
        _submit(experiments, "backtest", definition)


def _formal_payload(protocol_path: Path) -> dict:
    return json.loads(protocol_path.read_text(encoding="utf-8"))


def render_backtest_page(root: Path, store) -> None:
    st.header("回测")
    experiments = ExperimentStore(root)
    try:
        _render_configuration(root, store, experiments)
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(f"配置不可用：{error}")
    _render_results(experiments)


def _render_results(experiments: ExperimentStore) -> None:
    st.subheader("回测结果")
    run_ids = experiments.list_backtests()
    if not run_ids:
        st.info("尚无已发布回测。")
        return
    suggested = st.session_state.pop("suggested_result", None)
    if suggested in run_ids:
        st.session_state["bt_result_select"] = suggested
    run_id = st.selectbox("回测", run_ids, key="bt_result_select")
    ref = experiments.get_backtest(run_id)
    payload = read_backtest(ref)
    manifest = payload["manifest"]
    evaluation = payload["evaluation"]
    if ref.status == "limited":
        st.warning("回测受限（limited）：资格缺口如实列出，不冒充 complete")
    else:
        st.success("回测完整（complete）")

    st.markdown("**研究/经济结论与限制**")
    st.markdown(f"- execution_status: {evaluation.get('execution_status', '')}")
    st.markdown(f"- research_status: {evaluation.get('research_status', '')}")
    st.markdown(f"- economic_status: {evaluation.get('economic_status', '')}")
    st.markdown(f"- research_conclusion: {evaluation.get('research_conclusion', '')}")
    for reason in evaluation.get("reasons", []):
        st.markdown(f"- {reason}")
    for limitation in manifest.get("limitations", []):
        st.markdown(f"- 限制：{limitation}")
    for limitation in evaluation.get("limitations", []):
        st.markdown(f"- 限制：{limitation}")
    st.json({"metrics": evaluation.get("metrics", {}), "statistics": evaluation.get("statistics", {}),
             "exposure": evaluation.get("exposure", "not_available"),
             "capacity": evaluation.get("capacity", "not_available")})

    tables = payload["tables"]
    sections = (
        ("nav", "净值（毛/净/基准）"),
        ("costs", "成本"),
        ("positions", "持仓"),
        ("cash", "现金"),
        ("trades", "成交"),
        ("orders", "订单（含未成交原因）"),
        ("signals", "原始信号"),
        ("targets", "目标权重"),
        ("exclusions", "排除与缺口"),
    )
    for name, title in sections:
        st.markdown(f"**{title}**")
        frame = tables[name]
        if len(frame):
            st.dataframe(frame, hide_index=True, key=f"bt_table::{name}")
        else:
            st.markdown("- （空表）")
    st.markdown("**定义与来源**")
    st.json(
        {
            "definition": manifest.get("definition", {}),
            "sources": manifest.get("sources", {}),
            "engine": manifest.get("engine", {}),
        }
    )
    other_id = st.selectbox("对比回测", ["（不对比）"] + [item for item in run_ids if item != run_id],
                            key="bt_compare")
    if other_id != "（不对比）":
        st.json(compare_backtests(ref, experiments.get_backtest(other_id)))
    if st.button("导出 ZIP（只读已发布产物）", key="bt_export_zip"):
        try:
            zip_path = export_backtest(ref, Path(tempfile.mkdtemp(prefix="hetu-bt-export-")))
            payload_bytes = zip_path.read_bytes()
        except Exception as error:  # noqa: BLE001
            st.error(f"导出失败：{type(error).__name__}: {error}")
        else:
            st.session_state["bt_export_payload"] = (run_id, payload_bytes, zip_path.name)
            st.success(f"已生成 {zip_path.name}")
    cached_export = st.session_state.get("bt_export_payload")
    if cached_export and (len(cached_export) != 3 or cached_export[0] != run_id):
        del st.session_state["bt_export_payload"]
    if "bt_export_payload" in st.session_state:
        _, payload_bytes, name = st.session_state["bt_export_payload"]
        st.download_button(
            "下载 ZIP",
            data=payload_bytes,
            file_name=name,
            mime="application/zip",
            key="bt_download_zip",
        )


_EDITABLE_FIELDS = (
    "protocol.costs.commission_rate", "protocol.costs.min_commission_cny",
    "protocol.costs.slippage_rate", "parameters.lookback_months",
)


def copy_backtest_definition(
    store: ExperimentStore, definition_id: str, changes: dict,
) -> tuple[BacktestDefinition, list[dict]]:
    original = store.read_definition(definition_id)
    if not isinstance(original, BacktestDefinition):
        raise ValueError("definition is not a backtest")
    payload = backtest_definition_to_dict(original)
    differences = []
    for path, after in changes.items():
        if path not in _EDITABLE_FIELDS:
            raise ValueError(f"unsupported copy field: {path}")
        *parents, key = path.split(".")
        mapping = payload
        for name in parents:
            mapping = mapping[name]
        if key not in mapping or isinstance(after, bool) or not isinstance(after, (int, float)) or after < 0:
            raise ValueError(f"unsupported value for {path}")
        if path == "parameters.lookback_months" and (type(after) is not int or after < 1):
            raise ValueError("lookback_months must be a positive integer")
        before = mapping[key]
        if before != after:
            mapping[key] = after
            differences.append(dict(path=path, before=before, after=after))
    definition = backtest_definition_from_dict(payload)
    require_formal_protocol(definition)
    return definition, differences


def _render_copy(store, experiments, definitions) -> None:
    choices = {row["definition_id"]: row["created_at"] for row in definitions}
    selected = st.selectbox("来源定义", choices, format_func=lambda key: f"{choices[key]} · {key}",
                            key="bt_copy_source")
    original = experiments.read_definition(selected)
    changed = {}
    for field in _EDITABLE_FIELDS:
        parts = field.split(".")
        if field.startswith("parameters"):
            value = original.parameters.get(parts[-1])
        else:
            value = original.protocol["costs"].get(parts[-1])
        if value is not None:
            changed[field] = st.number_input(field, value=value, min_value=type(value)(0),
                                             key=f"bt_copy::{selected}::{field}")
    try:
        candidate, differences = copy_backtest_definition(experiments, selected, changed)
    except ValueError as error:
        st.warning(str(error))
        return
    st.json(differences)
    payload = backtest_definition_to_dict(candidate)
    frozen = candidate.protocol.get("research_execution")
    formal = candidate.protocol["stage"] == "formal"
    conforms = not formal or (
        frozen and payload == backtest_definition_to_dict(
            _build_formal_definition(payload["protocol"]["research_execution"]["payload"], frozen["phase"],
                                     frozen["trial_id"])
        )
    )
    if candidate.mode != "free" or not conforms:
        st.warning("草稿超出当前冻结试验定义或研究来源范围。正式研究账本和原定义保持。")
        if st.button("保存草稿", key="bt_copy_save"):
            st.success(experiments.save_definition("backtest", candidate))
        return
    if st.button("检查复制定义资格", key="bt_copy_qualification"):
        st.session_state.pop("bt_copy_qualified", None)
        with tempfile.TemporaryDirectory(prefix="hetu-bt-copy-") as workdir:
            report = prepare_backtest(candidate, store, Path(workdir) / "bundle")
        st.session_state["bt_copy_qualified"] = (payload, report.can_run, list(report.limitations))
    checked = st.session_state.get("bt_copy_qualified")
    current = checked is not None and checked[0] == payload
    if current:
        st.write({"can_run": checked[1], "limitations": checked[2]})
    if st.button("提交复制定义", key="bt_copy_submit", disabled=not (current and checked[1])):
        _submit(experiments, "backtest", candidate)
