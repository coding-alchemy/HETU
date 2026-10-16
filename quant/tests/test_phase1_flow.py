"""05.1 跨模块集成：免费导入→保存定义→worker→两榜→重开→导出。

全程临时目录、离线、合成材料（fixtures.ranking_universe，构造器只含事实，
预期排序与排除判断在本文件断言侧）。数据任务的取数以模块级接缝返回
已导入快照并走真实资格判定，避免网络；排名计算走真实 run_ranking。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from fixtures import SYNTHETIC_THEME_AUDIT, annotate_actions_coverage, make_case
from hetu_quant import ranking as ranking_module
from hetu_quant import worker
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import (
    DataRequest,
    RankingDefinition,
    SnapshotRef,
)
from hetu_quant.reporting import export_result

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
WINDOW_START = date(2021, 6, 1)
DATA_PRODUCTS = (
    "test_calendar",
    "test_daily",
    "test_status",
    "test_boards",
    "test_members",
    "test_index",
)


def _build_root(
    tmp_path: Path, variant: str = "base"
) -> tuple[Path, DataStore, SnapshotRef]:
    """临时量化根：产品配置 + 免费导入合成两榜材料，返回 (root, data, ref)。"""
    root = tmp_path / "qroot"
    root.mkdir(parents=True)
    (root / "data-products.json").write_text(
        json.dumps(make_case("product_config_full", "base"), ensure_ascii=False),
        encoding="utf-8",
    )
    data = DataStore(root)
    case = make_case("ranking_universe", variant)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(case["manifest"], ensure_ascii=False), encoding="utf-8"
    )
    files = []
    for name, content in case["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = data.import_files(manifest_path, tuple(files))
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(data, ref, "2023-12-28", "2024-06-28")
    return root, data, ref


def _data_request() -> DataRequest:
    return DataRequest(
        product_ids=DATA_PRODUCTS,
        codes=("600011.SH", "600901.SH"),
        start=WINDOW_START,
        end=CUTOFF,
        as_of=AS_OF,
        purpose="ranking_current",
    )


def _ranking_definition(ref: SnapshotRef) -> RankingDefinition:
    return RankingDefinition(
        as_of=AS_OF,
        cutoff=CUTOFF,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters={
                "purpose": "ranking_current",
                # v2 分类门：合成主题核验条目（THM1–THM6 = industrial_theme）
                "category": {
                    "theme_basis": {"audited_boards": SYNTHETIC_THEME_AUDIT}
                },
            },
    )


def _offline_data_execution(monkeypatch, ref: SnapshotRef) -> None:
    """数据任务取数接缝：复用已导入快照，资格判定仍走真实 qualify。"""

    def execute(request: DataRequest, data: DataStore):
        snapshot = SnapshotRef(ref.snapshot_id, data.root)
        report = data.qualify(request, snapshot)
        return snapshot, report

    monkeypatch.setattr(worker, "_execute_data", execute)


def _read_tables(result_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }


def _assert_same_table(exported: pd.DataFrame, published: pd.DataFrame) -> None:
    """导出 CSV 与库内 parquet 同表一致：数值按精度比较，文本按归一化比较。

    CSV 往返有空串/NaN 归一与浮点末位解析噪声，不能直接整帧相等。
    """
    assert list(exported.columns) == list(published.columns)
    assert len(exported) == len(published)
    for column in published.columns:
        left, right = exported[column], published[column]
        if pd.api.types.is_numeric_dtype(right):
            left_num = pd.to_numeric(left, errors="coerce")
            right_num = pd.to_numeric(right, errors="coerce")
            assert (left_num.isna() == right_num.isna()).all(), column
            left_vals = left_num.dropna().to_numpy(dtype=float)
            right_vals = right_num.dropna().to_numpy(dtype=float)
            assert np.isclose(left_vals, right_vals, rtol=1e-9, atol=1e-12).all(), (
                column
            )
        else:
            left_text = left.fillna("<NA>").astype(str).replace("", "<NA>")
            right_text = right.fillna("<NA>").astype(str).replace("", "<NA>")
            assert (left_text.values == right_text.values).all(), column


def test_import_define_worker_reopen_export_flow(tmp_path, monkeypatch) -> None:
    root, data, ref = _build_root(tmp_path)
    _offline_data_execution(monkeypatch, ref)
    store = ExperimentStore(root)
    data_def_id = store.save_definition("data", _data_request())
    rank_def_id = store.save_definition("ranking", _ranking_definition(ref))
    service = JobService(store)
    data_job = service.submit(data_def_id, "data-click-1")
    rank_job = service.submit(rank_def_id, "rank-click-1")

    data_done = worker.run_job(data_job.job_id, store, data)
    assert data_done.state == "succeeded"
    assert data_done.output == root / "snapshots" / ref.snapshot_id
    assert (root / "qualification").is_dir(), "数据任务必须留下资格记录"

    rank_done = worker.run_job(rank_job.job_id, store, data)
    assert rank_done.state == "succeeded"
    result_id = rank_done.output.name
    result_dir = root / "results" / result_id

    # 重开：全新实例找回任务、冻结定义与已发布结果
    reopened = ExperimentStore(root)
    service2 = JobService(reopened)
    states = {job.job_id: job.state for job in service2.list()}
    assert states[data_job.job_id] == "succeeded"
    assert states[rank_job.job_id] == "succeeded"
    assert service2.store.read_definition(rank_def_id).cutoff == CUTOFF
    result = reopened.get_result(result_id)
    assert result.status == "complete"

    tables = _read_tables(result_dir)
    boards, slots = tables["boards"], tables["slots"]
    industries = boards[boards["board_kind"] == "industry"]
    themes = boards[boards["board_kind"] == "theme"]
    assert industries["selected"].sum() == 5, "行业榜 5 板块"
    assert themes["selected"].sum() == 5, "主题榜 5 板块"
    assert len(slots) == 50, "两榜各 5 股，最多 50 席位"
    assert slots["board_id"].str.startswith("IND").sum() == 25
    assert slots["board_id"].str.startswith("THM").sum() == 25
    dup = slots[slots["code"] == "600901.SH"]
    assert len(dup) == 2, "重复股在两榜各保留一个席位"
    assert (dup["duplicate_count"] == 1).all()
    assert slots["code"].nunique() < 50, "重复股不占额外席位、不去重补位"

    # 导出 ZIP 与库内已发布产物一致，且导出不重新计算
    zip_path = export_result(result, tmp_path / "export")
    manifest = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(zip_path) as archive:
        assert json.loads(archive.read("definition.json")) == manifest["definition"]
        assert json.loads(archive.read("limitations.json")) == manifest["limitations"]
        assert json.loads(archive.read("sources.json")) == manifest["sources"]
        for name in manifest["tables"]:
            exported = pd.read_csv(io.BytesIO(archive.read(f"{name}.csv")))
            _assert_same_table(exported, tables[name])
    assert {job.job_id for job in service2.list()} == {
        data_job.job_id,
        rank_job.job_id,
    }, "导出不得新增任务"


def test_stock_missing_latest_trading_day_cannot_take_seat(
    tmp_path, monkeypatch
) -> None:
    """600011.SH 缺 cutoff 当日行情：有排除记录且不能入选席位。"""
    root, data, ref = _build_root(tmp_path, variant="missing_last_day")
    _offline_data_execution(monkeypatch, ref)
    store = ExperimentStore(root)
    def_id = store.save_definition("ranking", _ranking_definition(ref))
    job = JobService(store).submit(def_id, "rank-click-1")
    done = worker.run_job(job.job_id, store, data)
    assert done.state == "succeeded"
    result = store.get_result(done.output.name)
    tables = _read_tables(root / "results" / result.result_id)
    slots = tables["slots"]
    assert "600011.SH" not in set(slots["code"]), "缺最新交易日者不得入选"
    hit = tables["exclusions"][tables["exclusions"]["entity_id"] == "600011.SH"]
    assert len(hit), "缺最新交易日者必须有排除记录"
    joined = " ".join(str(reason) for reason in hit["reason"])
    assert "missing_sessions" in joined and "missing_latest_session" in joined


def test_one_submission_computes_once(tmp_path, monkeypatch) -> None:
    """同一 submission_id 重复提交不新增任务，终态任务不被重复计算。"""
    root, data, ref = _build_root(tmp_path)
    store = ExperimentStore(root)
    def_id = store.save_definition("ranking", _ranking_definition(ref))
    service = JobService(store)
    job = service.submit(def_id, "rank-click-1")

    calls: list[str] = []
    real_run = ranking_module.run_ranking

    def counted(definition, snapshot, output_root):
        calls.append(definition.snapshot_id)
        return real_run(definition, snapshot, output_root)

    monkeypatch.setattr(worker, "_execute_ranking", counted)
    first = worker.run_job(job.job_id, store, data)
    assert first.state == "succeeded"

    again = service.submit(def_id, "rank-click-1")
    assert again.job_id == job.job_id, "同提交标识返回原任务"
    assert len(service.list()) == 1, "重复提交不新增任务"
    rerun = worker.run_job(job.job_id, store, data)
    assert rerun.state == "succeeded"
    assert rerun.output == first.output
    assert calls == [ref.snapshot_id], "一次提交只计算一次"


def test_page_cancel_and_retry_queued_job(tmp_path, monkeypatch) -> None:
    """任务列表页的取消／重试按钮走真实 JobService：取消不新增任务，重试生成新任务号。"""
    from streamlit.testing.v1 import AppTest

    from hetu_quant.ui import app as app_module

    root, _, ref = _build_root(tmp_path)
    store = ExperimentStore(root)
    def_id = store.save_definition("ranking", _ranking_definition(ref))
    service = JobService(store)
    job = service.submit(def_id, "cancel-click-1")
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(Path(app_module.__file__).resolve())).run()
    assert not app.exception
    app.button(key=f"cancel::{job.job_id}").click().run()
    assert not app.exception
    assert service.get(job.job_id).state == "cancelled"
    assert len(service.list()) == 1, "取消不产生新任务"
    app.button(key=f"retry::{job.job_id}").click().run()
    assert not app.exception
    jobs = service.list()
    assert len(jobs) == 2, "重试生成新任务"
    retried = [item for item in jobs if item.job_id != job.job_id][0]
    assert retried.attempt == 2 and retried.state == "queued"
    assert retried.submission_id != job.submission_id, "重试使用新提交标识"
