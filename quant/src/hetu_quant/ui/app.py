"""04.3 网页研究闭环：数据、研究提交、任务找回、两榜阅读与导出。

Streamlit 只负责交互与读取：刷新只查询，提交使用持久化提交标识，
图表只读 03 保存的序列，导出不重新计算、不提交新任务。
root 默认仓库下 .hetu/quant；测试以 HETU_QUANT_ROOT 指向临时目录。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from hetu_quant.backtest_models import backtest_definition_to_dict
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import (
    PURPOSES,
    DataRequest,
    RankingDefinition,
    SnapshotRef,
    data_request_to_dict,
    ranking_definition_to_dict,
)
from hetu_quant.reporting import export_result

CN = ZoneInfo("Asia/Shanghai")

_PAGES = ("任务列表", "数据", "研究提交", "回测", "结果与导出")

_DEFINITION_ENCODERS = {
    "data": data_request_to_dict,
    "ranking": ranking_definition_to_dict,
    "backtest": backtest_definition_to_dict,
}


# ---- 接缝：测试以替身隔离网络与长计算，生产走真实实现 ----


def _data_root() -> Path:
    env = os.environ.get("HETU_QUANT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / ".hetu" / "quant"


def _make_store(root: Path) -> DataStore:
    return DataStore(root)


def _parse_cn(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.strip())
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("as_of 必须带 Asia/Shanghai 时区偏移（如 +08:00）")
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError("as_of 必须处于 Asia/Shanghai 时区")
    return parsed.astimezone(CN)


# ---- 数据页 ----


def _page_data(root: Path, store: DataStore) -> None:
    st.header("数据")
    st.info("当前模式：free（paid 未启用；任何入口收到 paid 均明确拒绝）")
    st.subheader("产品与来源")
    rows = []
    for product_id, product in sorted(store.config.get("products", {}).items()):
        permission = product.get("permission") or {}
        rows.append(
            {
                "产品": product_id,
                "提供方": product.get("provider", ""),
                "费用": product.get("fee_class", ""),
                "权限": permission.get("status", ""),
                "市场": ",".join(product.get("markets", []) or []),
                "来源说明": permission.get("basis", ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, key="product_table")
    st.subheader("最后成功截止")
    last_success = store._last_success()
    if last_success:
        st.markdown(
            "\n".join(
                f"- {product_id}：{at}"
                for product_id, at in sorted(last_success.items())
            )
        )
    else:
        st.markdown("- 尚无成功更新记录")
    st.subheader("已发布快照（原始来源 / 公开时点 / 本次更新失败）")
    snap_rows = []
    for snapshot_id, manifest in store._published_manifests():
        failures = {
            item.get("product_id"): item.get("error", "")
            for item in manifest.get("failures", [])
        }
        for item in manifest.get("items", []):
            source = item.get("source") or {}
            snap_rows.append(
                {
                    "快照": snapshot_id,
                    "产品": item.get("product_id", ""),
                    "原始来源": source.get("name", ""),
                    "费用": source.get("fee_class", ""),
                    "公开时点": source.get("obtained_at", ""),
                    "本次更新失败": failures.get(item.get("product_id"), ""),
                }
            )
    if snap_rows:
        st.dataframe(pd.DataFrame(snap_rows), hide_index=True, key="snapshot_table")
    st.caption(
        "原始来源以快照 manifest 声明为准；“本地导入”不算原始来源，"
        "付费文件不得改标免费。"
    )
    with st.expander("文件导入（manifest + 文件路径）"):
        manifest_text = st.text_input("manifest 路径", key="import_manifest_path")
        files_text = st.text_input(
            "文件路径（逗号分隔）", key="import_files_paths"
        )
        if st.button("执行导入", key="import_files_btn"):
            try:
                files = tuple(
                    Path(item.strip())
                    for item in files_text.split(",")
                    if item.strip()
                )
                ref = store.import_files(Path(manifest_text), files)
            except Exception as error:  # noqa: BLE001 - 缺口如实展示
                st.error(f"导入失败：{type(error).__name__}: {error}")
            else:
                st.success(f"导入完成：快照 {ref.snapshot_id}")
    st.subheader("缺口更新（仅免费源）")
    product_ids = sorted(store.config.get("products", {}).keys())
    chosen = st.multiselect(
        "产品", product_ids, default=product_ids, key="gap_products"
    )
    codes_text = st.text_input("代码（逗号分隔；目录/日历产品可留空）", key="gap_codes")
    col_start, col_end = st.columns(2)
    start = col_start.date_input("开始", value=date(2024, 1, 1), key="gap_start")
    end = col_end.date_input("结束", value=date(2024, 6, 30), key="gap_end")
    as_of_text = st.text_input(
        "信息截止 as_of（Asia/Shanghai）",
        value=datetime.now(CN).isoformat(),
        key="gap_as_of",
    )
    purpose = st.selectbox("用途", list(PURPOSES), key="gap_purpose")
    if st.button("执行缺口更新", key="update_data"):
        try:
            codes = tuple(
                item.strip() for item in codes_text.split(",") if item.strip()
            )
            request = DataRequest(
                product_ids=tuple(chosen),
                codes=codes,
                start=start,
                end=end,
                as_of=_parse_cn(as_of_text),
                purpose=purpose,
                mode="free",
            )
        except ValueError as error:
            st.error(f"请求无效：{error}")
        else:
            _submit(ExperimentStore(root), "data", request)


# ---- 研究提交 ----


def _new_submission_id() -> str:
    return f"ui-{uuid.uuid4().hex[:12]}"


# 绑定生命周期：session_state 中的 (definition_id, submission_id, job_id) 绑定
# 只在绑定任务尚未终结（queued/running/cancel_requested）时复用，用于识别
# 同一次提交操作在 Streamlit rerun 中的传输重发；任务进入终态
# （succeeded/failed/cancelled/interrupted）后绑定清除，用户再次点击提交视为
# 新的明确提交操作，生成新定义与新提交标识。
_ACTIVE_JOB_STATES = ("queued", "running", "cancel_requested")


def _binding_expired(service: JobService, bound: tuple) -> bool:
    """绑定不可检查（旧格式/任务缺失）或绑定任务已终结时视为过期。"""
    if len(bound) < 3:
        return True
    try:
        return service.get(bound[2]).state not in _ACTIVE_JOB_STATES
    except KeyError:
        return True


def _submit(store: ExperimentStore, kind: str, definition) -> None:
    to_dict = _DEFINITION_ENCODERS.get(kind)
    if to_dict is None:
        raise ValueError(f"unknown definition kind: {kind!r}")
    payload = json.loads(json.dumps(to_dict(definition), ensure_ascii=False))
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    key = f"submission::{kind}::{fingerprint}"
    service = JobService(store)
    try:
        bound = st.session_state.get(key)
        if bound is not None and _binding_expired(service, bound):
            del st.session_state[key]
            bound = None
        if bound is None:
            definition_id = store.save_definition(kind, definition)
            submission_id = _new_submission_id()
            job = service.submit(definition_id, submission_id)
            st.session_state[key] = (definition_id, submission_id, job.job_id)
        else:
            definition_id, submission_id = bound[0], bound[1]
            job = service.submit(definition_id, submission_id)
    except (TypeError, ValueError) as error:
        st.error(f"提交失败：{error}")
    else:
        st.success(
            f"已提交任务 {job.job_id}（定义 {definition_id}，"
            f"提交标识 {job.submission_id}）；执行与取消由任务列表操作"
        )


def _page_research(root: Path, store: DataStore) -> None:
    st.header("研究提交")
    experiments = ExperimentStore(root)
    kind = st.radio(
        "类型", ("排名研究", "数据请求"), horizontal=True, key="research_kind"
    )
    if kind == "排名研究":
        snapshots = [sid for sid, _ in store._published_manifests()]
        if not snapshots:
            st.warning("尚无已发布快照，请先在数据页更新或导入。")
            return
        snapshot_id = st.selectbox("冻结快照", snapshots, key="research_snapshot")
        cutoff = st.date_input(
            "共同截止 cutoff", value=date(2024, 6, 28), key="research_cutoff"
        )
        as_of_text = st.text_input(
            "信息截止 as_of",
            value=datetime.now(CN).isoformat(),
            key="research_as_of",
        )
        benchmark = st.text_input(
            "共同基准", value="csi300_price", key="research_benchmark"
        )
        purpose = st.selectbox(
            "用途", ("ranking_current", "ranking_historical"), key="research_purpose"
        )
        try:
            definition = RankingDefinition(
                as_of=_parse_cn(as_of_text),
                cutoff=cutoff,
                snapshot_id=snapshot_id,
                benchmark_id=benchmark,
                parameters={"purpose": purpose},
            )
        except ValueError as error:
            st.error(f"定义无效：{error}")
            return
        st.subheader("冻结定义（提交后不可修改；修改请另存新定义）")
        st.json(ranking_definition_to_dict(definition))
        ref = SnapshotRef(snapshot_id, store.root)
        if st.button("检查资格与限制", key="check_qualification"):
            try:
                manifest = store.load_manifest(ref)
                product_ids = tuple(
                    sorted(
                        {str(item["product_id"]) for item in manifest.get("items", [])}
                    )
                )
                codes = sorted(
                    {str(code) for code in store.read_table(ref, "daily_bars")["code"]}
                )
                start = (pd.Timestamp(cutoff) - pd.DateOffset(months=6)).date()
                request = DataRequest(
                    product_ids=product_ids,
                    codes=tuple(codes),
                    start=start,
                    end=cutoff,
                    as_of=definition.as_of,
                    purpose=purpose,
                )
                report = store.qualify(request, ref)
            except Exception as error:  # noqa: BLE001
                st.error(f"资格检查失败：{type(error).__name__}: {error}")
            else:
                st.json(
                    {
                        "can_run": report.can_run,
                        "allowed_uses": list(report.allowed_uses),
                        "eligible": len(report.eligible_ids),
                        "excluded": {k: list(v) for k, v in report.excluded.items()},
                        "limitations": list(report.limitations),
                    }
                )
        if st.button("提交任务", key="submit_job"):
            _submit(experiments, "ranking", definition)
    else:
        product_ids = sorted(store.config.get("products", {}).keys())
        chosen = st.multiselect(
            "产品", product_ids, default=product_ids, key="data_products"
        )
        codes_text = st.text_input("代码（逗号分隔）", key="data_codes")
        col_start, col_end = st.columns(2)
        start = col_start.date_input("开始", value=date(2024, 1, 1), key="data_start")
        end = col_end.date_input("结束", value=date(2024, 6, 30), key="data_end")
        as_of_text = st.text_input(
            "信息截止 as_of",
            value=datetime.now(CN).isoformat(),
            key="data_as_of",
        )
        purpose = st.selectbox("用途", list(PURPOSES), key="data_purpose")
        try:
            codes = tuple(
                item.strip() for item in codes_text.split(",") if item.strip()
            )
            definition = DataRequest(
                product_ids=tuple(chosen),
                codes=codes,
                start=start,
                end=end,
                as_of=_parse_cn(as_of_text),
                purpose=purpose,
                mode="free",
            )
        except ValueError as error:
            st.error(f"请求无效：{error}")
            return
        st.subheader("冻结定义（提交后不可修改）")
        st.json(data_request_to_dict(definition))
        if st.button("提交任务", key="submit_data_job"):
            _submit(experiments, "data", definition)


# ---- 任务列表 ----


def _page_jobs(root: Path) -> None:
    st.header("任务列表")
    if st.button("刷新", key="refresh_jobs"):
        st.caption("已查询最新任务状态（刷新不产生新任务）")
    experiments = ExperimentStore(root)
    service = JobService(experiments)
    jobs = service.list()
    rows = []
    for job in jobs:
        rows.append(
            {
                "任务号": job.job_id,
                "类型": experiments.definition_kind(job.definition_id),
                "提交标识": job.submission_id,
                "尝试": job.attempt,
                "状态": job.state,
                "进度": round(job.progress, 2),
                "原因": job.reason or "",
                "产物": str(job.output) if job.output else "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, key="jobs_table")
    st.caption("取消：未完成任务即时生效（queued 直接取消，running 在边界取消）；"
               "重试仅 failed/cancelled/interrupted，生成新任务号与提交标识。")
    for job in jobs:
        cols = st.columns((1, 1, 6))
        if cols[0].button(
            "取消",
            key=f"cancel::{job.job_id}",
            disabled=job.state not in ("queued", "running"),
        ):
            record = service.cancel(job.job_id)
            st.warning(f"{job.job_id} 当前状态 {record.state}")
            st.rerun()
        if cols[1].button(
            "重试",
            key=f"retry::{job.job_id}",
            disabled=job.state not in ("failed", "cancelled", "interrupted"),
        ):
            record = service.retry(job.job_id, _new_submission_id())
            st.success(f"已重试：新任务 {record.job_id}（尝试 {record.attempt}）")
            st.rerun()
        cols[2].markdown(f"`{job.job_id}` {job.state}")


# ---- 结果与导出 ----


_METRIC_COLUMNS = (
    "excess_3m",
    "excess_6m",
    "max_drawdown_6m",
    "up_amount_60d",
    "volatility_60d",
    "trend_pct",
    "drawdown_pct",
    "volume_pct",
    "risk_pct",
    "score",
    "benchmark_return_6m",
    "n_sessions_6m",
)

# 空表（如零席位全排除结果）读回时可能无列；页面按此补齐列名再访问。
_TABLE_SCHEMAS: dict[str, tuple[str, ...]] = {
    "boards": ("board_id", "board_kind", "name", "rank", "selected", "score"),
    "slots": ("board_id", "code", "rank", "score", "duplicate_count"),
    "metrics": ("entity_kind", "entity_id", "group_id") + _METRIC_COLUMNS,
    "cards": (
        "entity_id",
        "group_id",
        "rationale",
        "counter_evidence",
        "limitations",
        "next_check",
        "clue_types",
        "gate_state",
    ),
    "aux": (
        "entity_id",
        "ret_20d",
        "ret_20d_pct",
        "overheated",
        "width_60d_ma",
        "concentration_proxy",
        "range_position",
    ),
    "overlap": ("board_a", "board_b", "intersection_count", "union_count", "jaccard"),
    "series": ("entity_id", "series_kind", "date", "value"),
}


def _read_tables(result_dir: Path) -> dict[str, pd.DataFrame]:
    tables = {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }
    for name, columns in _TABLE_SCHEMAS.items():
        if name in tables and not len(tables[name]):
            tables[name] = tables[name].reindex(columns=list(columns))
    return tables


def _series_chart(series: pd.DataFrame, entity_id: str) -> None:
    if not len(series):
        return
    own = series[series["entity_id"] == entity_id]
    for kind_name in ("price_indexed", "drawdown"):
        part = own[own["series_kind"] == kind_name][["date", "value"]]
        if len(part):
            st.line_chart(part.set_index("date"), height=160)


def _card_markdown(row: pd.Series) -> str:
    return (
        f"- 入选依据（原值／贡献）：{row['rationale']}\n"
        f"- 风险反证：{row['counter_evidence']}\n"
        f"- 缺口与限制：{row['limitations']}\n"
        f"- 下一项核验：{row['next_check']}\n"
        f"- Gate 状态：{row['gate_state']}（网页不改人工 Gate）"
    )


def _page_results(root: Path) -> None:
    st.header("结果与导出")
    experiments = ExperimentStore(root)
    result_ids = experiments.list_results()
    if not result_ids:
        st.info("尚无已发布结果。")
        return
    result_id = st.selectbox("结果", result_ids, key="result_select")
    ref = experiments.get_result(result_id)
    result_dir = root / "results" / result_id
    manifest = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
    if ref.status == "limited":
        st.warning("研究受限（limited）：席位或口径不足，缺口如实列出，不冒充正常完成")
    else:
        st.success("研究完整（complete）")
    counts = manifest.get("counts", {})
    st.markdown(
        f"- 席位 {counts.get('slots', 0)}；"
        f"独立股票 {counts.get('distinct_stocks', 0)}；"
        f"重复股票 {counts.get('duplicate_stocks', 0)}"
    )
    benchmark = manifest.get("benchmark") or {}
    st.markdown(
        f"- 共同基准：{benchmark.get('code', '')}"
        f"（{benchmark.get('return_basis', '')} 口径；三年走势/回撤均相对该基准）"
    )
    tables = _read_tables(result_dir)
    boards = tables.get("boards", pd.DataFrame())
    slots = tables.get("slots", pd.DataFrame())
    series = tables.get("series", pd.DataFrame())
    metrics = tables.get("metrics", pd.DataFrame())
    cards = tables.get("cards", pd.DataFrame())
    overlap = tables.get("overlap", pd.DataFrame())
    aux = tables.get("aux", pd.DataFrame())
    if len(metrics):
        board_metrics = metrics[
            (metrics["entity_kind"] == "board") & (metrics["group_id"] == "")
        ]
    else:
        board_metrics = metrics
    for kind, title in (("industry", "行业榜"), ("theme", "主题榜")):
        st.subheader(title)
        selected = boards[(boards["board_kind"] == kind) & (boards["selected"])]
        if not len(selected):
            st.markdown("- （无入选板块）")
        for _, brow in selected.iterrows():
            board_id = str(brow["board_id"])
            st.markdown(f"**{brow['name']}（{board_id}）第 {brow['rank']} 名**")
            mrow = board_metrics[board_metrics["entity_id"] == board_id]
            if len(mrow):
                st.dataframe(
                    mrow[list(_METRIC_COLUMNS)].round(4),
                    hide_index=True,
                    key=f"board_metrics::{board_id}",
                )
            own_slots = slots[slots["board_id"] == board_id]
            if len(own_slots):
                st.dataframe(
                    own_slots[["code", "rank", "score", "duplicate_count"]],
                    hide_index=True,
                    key=f"seats_table::{board_id}",
                )
            arow = aux[aux["entity_id"] == board_id] if len(aux) else aux
            if len(arow):
                st.dataframe(
                    arow[
                        [
                            "ret_20d",
                            "ret_20d_pct",
                            "overheated",
                            "width_60d_ma",
                            "concentration_proxy",
                            "range_position",
                        ]
                    ].round(4),
                    hide_index=True,
                    key=f"aux_table::{board_id}",
                )
            _series_chart(series, board_id)
            card = cards[cards["entity_id"] == board_id] if len(cards) else cards
            if len(card):
                st.markdown(_card_markdown(card.iloc[0]))
            for code in own_slots["code"].astype(str):
                scard = cards[cards["entity_id"] == code] if len(cards) else cards
                if not len(scard):
                    continue
                with st.expander(f"证据 {code}"):
                    st.markdown(_card_markdown(scard.iloc[0]))
                    _series_chart(series, code)
    st.subheader("重复关系（重复股保留各榜关系，不去重补位）")
    duplicated = slots[slots["duplicate_count"] > 0] if len(slots) else slots
    if len(duplicated):
        st.dataframe(
            duplicated[["board_id", "code", "duplicate_count"]],
            hide_index=True,
            key="duplicates_table",
        )
    if len(overlap):
        st.dataframe(overlap.round(4), hide_index=True, key="overlap_table")
    st.subheader("缺口与限制")
    for limitation in manifest.get("limitations", []):
        st.markdown(f"- {limitation}")
    st.caption(
        "价格收益口径：连续价消除送转/拆并机械跳变，保留现金除息影响；"
        "图表只读 03 保存序列，不重新计算、不混用复权总回报。"
    )
    with st.expander("与其他结果逐项对比（不同定义只显示差异，不合并排名）"):
        others = [item for item in result_ids if item != result_id]
        other_id = st.selectbox(
            "对比结果", ["（不对比）"] + others, key="diff_select"
        )
        if other_id != "（不对比）":
            other_dir = root / "results" / other_id
            other_manifest = json.loads(
                (other_dir / "result.json").read_text(encoding="utf-8")
            )
            mine = manifest.get("definition", {})
            theirs = other_manifest.get("definition", {})
            shown = False
            for key in sorted(set(mine) | set(theirs)):
                if mine.get(key) != theirs.get(key):
                    st.markdown(f"- {key}：{mine.get(key)} ↔ {theirs.get(key)}")
                    shown = True
            if not shown:
                st.markdown("- 定义字段无差异")
            other_boards = _read_tables(other_dir).get("boards", pd.DataFrame())
            for kind in ("industry", "theme"):
                mine_k = boards[(boards["board_kind"] == kind) & (boards["selected"])]
                theirs_k = other_boards[
                    (other_boards["board_kind"] == kind)
                    & (other_boards["selected"])
                ] if len(other_boards) else other_boards
                merged = mine_k[["board_id", "score"]].merge(
                    theirs_k[["board_id", "score"]],
                    on="board_id",
                    how="outer",
                    suffixes=("_本结果", "_对比结果"),
                )
                diff = merged[
                    merged["score_本结果"].fillna(-1)
                    != merged["score_对比结果"].fillna(-1)
                ]
                if len(diff):
                    st.dataframe(diff.round(4), hide_index=True, key=f"diff::{kind}")
    if st.button("导出 ZIP（只读已发布产物）", key="export_result"):
        try:
            zip_path = export_result(ref, Path(tempfile.mkdtemp(prefix="hetu-export-")))
            payload = zip_path.read_bytes()
        except Exception as error:  # noqa: BLE001
            st.error(f"导出失败：{type(error).__name__}: {error}")
        else:
            st.session_state["export_payload"] = (payload, zip_path.name)
            st.success(f"已生成 {zip_path.name}")
    if "export_payload" in st.session_state:
        payload, name = st.session_state["export_payload"]
        st.download_button(
            "下载 ZIP",
            data=payload,
            file_name=name,
            mime="application/zip",
            key="download_zip",
        )


def main() -> None:
    st.set_page_config(page_title="HETU 量化研究平台（一期）")
    root = _data_root()
    store = _make_store(root)
    st.title("HETU 量化研究平台（一期）")
    st.caption(
        "免费模式 free（paid 未启用）；网页只组织交互与读取，"
        "执行由本地 worker 完成；关闭网页后任务可从磁盘找回。"
    )
    page = st.sidebar.radio("页面", _PAGES, index=0, key="page_nav")
    if page == "任务列表":
        _page_jobs(root)
    elif page == "数据":
        _page_data(root, store)
    elif page == "研究提交":
        _page_research(root, store)
    elif page == "回测":
        from hetu_quant.ui.backtest_page import render_backtest_page

        render_backtest_page(root, store)
    else:
        _page_results(root)


if __name__ == "__main__":
    main()
