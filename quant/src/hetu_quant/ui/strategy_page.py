"""查找策略与只读纳入外部研究证据。"""

import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from hetu_quant.backtest_reporting import (
    read_research_handoff,
    read_research_handoff_artifact,
    research_handoff_summary,
)
from hetu_quant.strategy_catalog import (
    import_strategy_evidence,
    list_strategy_assets,
    read_strategy_asset,
    strategy_text_attachments,
)


def _render_pressure_comparisons(payload) -> None:
    for trial in payload["trials"]:
        run = payload["runs"].get(trial.get("run_id"), {})
        metrics = run.get("evaluation", {}).get("metrics", {})
        comparison = metrics.get("comparison")
        if comparison and comparison.get("purpose") == "sensitivity_vs_main":
            st.markdown(
                f"**{trial['trial_id']}**：压力账户 {comparison['candidate_return']:+.2%}，"
                f"主策略 {comparison['reference_return']:+.2%}（{comparison['reference']['run_id']}）。"
            )
        elif trial.get("trial_id") in {"final_double_costs", "final_capacity"} and metrics.get("daily_nav_gaps"):
            st.markdown(f"**{trial['trial_id']}**：逐日净值缺口，压力比较待证。")
            st.dataframe(metrics["daily_nav_gaps"], hide_index=True)
            if known := metrics.get("daily_nav_evidence"):
                st.caption("缺口日期的已知净值（人民币元）")
                st.dataframe(known, hide_index=True)
        else:
            continue
        market = metrics["market_states"]
        states = {"up": "上涨", "down": "下跌", "flat": "持平"}
        for state, values in market["states"].items():
            st.markdown(
                f"同池账户 {market['reference']['run_id']} 在市场{states[state]}月段合计 "
                f"{values['benchmark_return']:+.2%}。"
            )
        for gap in market["gaps"]:
            start, end = gap["window"]
            st.caption(f"市场状态待证（{start} 至 {end}）：{gap['reason']}。")


def render_strategy_page(root: Path, store) -> None:
    st.header("策略目录")
    rows = list_strategy_assets(root)
    filters = {}
    for label, field in (("市场", "market"), ("假设", "hypothesis"), ("频率", "frequency"),
                         ("研究状态", "research_status")):
        if field == "market":
            values = {value for row in rows for value in (row["markets"] or ())}
        elif field == "research_status":
            values = {row["statuses"][field] for row in rows}
        else:
            values = {row[field] for row in rows if row[field] is not None}
        filters[field] = st.selectbox(label, [None, *sorted(values)],
                                      format_func=lambda value: value or "全部", key=f"strategy_filter_{field}")
    options = {row["strategy_id"]: row["name"] for row in list_strategy_assets(root, **filters)}
    if options:
        selected = st.selectbox("选择策略", options, key="strategy_asset",
                                format_func=lambda key: options[key] or key)
        item = read_strategy_asset(root, selected)
        releases = [
            path for study in item.get("study_ids") or []
            for path in (root / "research" / study / "releases").glob("*.zip")
            if path.resolve().is_relative_to(root.resolve())
        ]
        release = st.selectbox("研究发布物", sorted(releases), index=None,
                               format_func=lambda path: path.name) if releases else None
        payload = read_research_handoff(release) if release is not None else None
        header = {key: value for key, value in item.items() if key != "trials"}
        if payload is not None:
            header.update({key: item["statuses"][key] for key in ("evidence_import", "execution_adapter")})
            header.update(research_handoff_summary(release, payload, selected))
        st.json(header)
        st.subheader("全部试验及状态")
        trials = payload["trials"] if payload is not None else item["trials"]
        st.write(trials or "暂无已登记试验")
        if payload is not None:
            _render_pressure_comparisons(payload)
            st.json(payload)
            files = [name for name in payload["manifest"]["files"] if name.startswith(("attachments/", "runs/"))
                     and Path(name).suffix in (".json", ".md", ".py", ".ipynb", ".parquet")]
            name = st.selectbox("查看发布物中的附件或轨迹", files, index=None)
            if name:
                body = read_research_handoff_artifact(release, name)
                if name.endswith(".parquet"):
                    st.dataframe(pd.read_parquet(BytesIO(body)))
                else:
                    st.code(body.decode("utf-8"), language=None)
            st.download_button("下载同一研究 ZIP", release.read_bytes(), file_name=release.name)
        attachments = strategy_text_attachments(root, item) if release is None else {}
        if attachments:
            filename = st.selectbox("查看规则、适配与研究附件", attachments, index=None, key="strategy_attachment")
            if filename is not None:
                try:
                    st.code(attachments[filename].read_text(encoding="utf-8"), language=None)
                except (OSError, ValueError) as error:
                    st.warning(f"附件无法读取：{error}")
        if item["statuses"]["execution_adapter"] == "registered":
            if st.button("配置此策略", key="strategy_configure"):
                st.session_state["next_page"] = "回测"
                st.session_state["bt_stage"] = "mechanism" if selected == "universe_vw" else "formal"
                st.rerun()
        else:
            st.info("证据可读，尚无执行适配。")
    else:
        st.info("没有符合筛选的策略。")

    with st.expander("只读导入策略证据"):
        manifest = st.file_uploader("策略证据清单", type="json", key="strategy_manifest")
        files = st.file_uploader("清单中登记的附件", accept_multiple_files=True, key="strategy_attachments")
        if st.button("登记证据", disabled=manifest is None, key="strategy_import"):
            try:
                with tempfile.TemporaryDirectory(prefix="hetu-strategy-") as tmp:
                    source = Path(tmp)
                    path = source / "manifest.json"
                    path.write_bytes(manifest.getvalue())
                    for attachment in files:
                        name = attachment.name
                        if Path(name).name != name or name == path.name or (source / name).exists():
                            raise ValueError(f"附件名不合法或重复：{name}")
                        (source / name).write_bytes(attachment.getvalue())
                    imported = import_strategy_evidence(root, path)
            except (OSError, ValueError, TypeError) as error:
                st.error(f"证据导入受限：{error}")
            else:
                st.success(f"已登记证据：{imported['name']}。附件未执行。")
