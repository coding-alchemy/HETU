"""查找策略与只读纳入外部研究证据。"""

import tempfile
from pathlib import Path

import streamlit as st

from hetu_quant.strategy_catalog import (
    import_strategy_evidence,
    list_strategy_assets,
    read_strategy_asset,
    strategy_text_attachments,
)


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
        st.json({key: value for key, value in item.items() if key != "trials"})
        st.subheader("全部试验及状态")
        st.write(item["trials"] or "暂无已登记试验")
        attachments = strategy_text_attachments(root, item)
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
