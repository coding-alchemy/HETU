"""04.3 网页闭环：AppTest 走真实代码与真实 SQLite/产物，root 为临时目录。

数据与结果用 03 的合成材料（fixtures.ranking_universe / mini_boards）预先发布；
免费源失败以模块级接缝注入，不断网、零收费调用。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from streamlit.testing.v1 import AppTest

from fixtures import SYNTHETIC_THEME_AUDIT, annotate_actions_coverage, make_case
from hetu_quant import worker
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import RankingDefinition
from hetu_quant.ranking import run_ranking
from hetu_quant.ui import app as app_module

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
APP_PATH = Path(app_module.__file__).resolve()
REPO_QUANT = APP_PATH.parents[3]


def _seed_root(tmp_path: Path, case: str = "ranking_universe", variant: str = "base"):
    """临时量化根：产品配置 + 导入快照 + 一次已发布排名结果。"""
    root = tmp_path / "qroot"
    root.mkdir(parents=True)
    config_text = json.dumps(
        make_case("product_config_full", "base"), ensure_ascii=False
    )
    (root / "data-products.json").write_text(config_text, encoding="utf-8")
    store = DataStore(root)
    data = make_case(case, variant)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(data["manifest"], ensure_ascii=False))
    files = []
    for name, content in data["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(store, ref, "2023-12-28", "2024-06-28")
    definition = RankingDefinition(
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
    result = run_ranking(definition, ref, root)
    return root, result


def _seed_zero_seat_root(tmp_path: Path) -> tuple[Path, object]:
    """全排除宇宙：清空日线，所有股票因缺行情被排除，产出零席位 limited 结果。"""
    root = tmp_path / "qroot"
    root.mkdir(parents=True)
    (root / "data-products.json").write_text(
        json.dumps(make_case("product_config_full", "base"), ensure_ascii=False),
        encoding="utf-8",
    )
    store = DataStore(root)
    data = make_case("ranking_universe", "base")
    data["files"]["daily_bars.csv"] = "date,code,close_raw,volume_shares,amount_cny\n"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(data["manifest"], ensure_ascii=False))
    files = []
    for name, content in data["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(store, ref, "2023-12-28", "2024-06-28")
    definition = RankingDefinition(
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
    result = run_ranking(definition, ref, root)
    return root, result


def _text(container) -> str:
    parts = []
    for element in list(container.markdown) + list(container.subheader):
        parts.append(str(getattr(element, "value", "")))
    return "\n".join(parts)


def _jobs(root: Path) -> list:
    return JobService(ExperimentStore(root)).list()


def test_refresh_and_reopen_find_job(tmp_path, monkeypatch):
    root, _ = _seed_root(tmp_path)
    store = ExperimentStore(root)
    definition_id = store.save_definition(
        "ranking",
        RankingDefinition(
            as_of=AS_OF,
            cutoff=CUTOFF,
            snapshot_id="snap-any",
            benchmark_id="csi300_price",
            parameters={
                "purpose": "ranking_current",
                # v2 分类门：合成主题核验条目（THM1–THM6 = industrial_theme）
                "category": {
                    "theme_basis": {"audited_boards": SYNTHETIC_THEME_AUDIT}
                },
            },
        ),
    )
    first = JobService(store).submit(definition_id, "click-1")
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    app.button(key="refresh_jobs").click().run()
    assert not app.exception
    assert len(_jobs(root)) == 1, "刷新只查询，不新增任务"
    reopened = AppTest.from_file(str(APP_PATH)).run()
    assert not reopened.exception
    table = reopened.dataframe[0].value
    assert first.job_id in set(table["任务号"].astype(str))


def test_data_update_goes_through_job_queue(tmp_path, monkeypatch):
    """数据页"更新"提交持久化任务：页面立即返回 queued，执行由 worker 完成。

    可控慢源替身验证：取消在边界生效（queued 直接取消，worker 不再执行）；
    失败原因持久化到任务记录，重开网页找回同一任务与原因；
    最后成功旧时点仍在数据页展示。
    """
    root, _ = _seed_root(tmp_path)
    (root / "last_success.json").write_text(
        json.dumps({"test_daily": "2024-06-01T09:00:00+08:00"}), encoding="utf-8"
    )

    def failing_execute(request, data):
        raise ConnectionError("免费源连接失败")

    monkeypatch.setattr(worker, "_execute_data", failing_execute)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("数据").run()
    app.button(key="update_data").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 1, "更新必须进入任务队列而非同步执行"
    job = jobs[0]
    assert job.state == "queued" and job.submission_id.startswith("ui-")
    successes = "\n".join(str(item.value) for item in app.success)
    assert "已提交任务" in successes and job.job_id in successes

    # 取消在边界生效：queued 直接取消，worker 领取不到，计算替身不被调用
    service = JobService(ExperimentStore(root))
    assert service.cancel(job.job_id).state == "cancelled"
    rerun = worker.run_job(job.job_id, ExperimentStore(root), DataStore(root))
    assert rerun.state == "cancelled", "取消后 worker 不得执行"

    # 新会话再次更新：提交新任务，worker 异步执行，失败原因持久化
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("数据").run()
    app.button(key="update_data").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 2
    failed_job = [item for item in jobs if item.job_id != job.job_id][0]
    done = worker.run_job(failed_job.job_id, ExperimentStore(root), DataStore(root))
    assert done.state == "failed"
    assert "免费源连接失败" in (done.reason or "")

    # 重开：任务列表找回同一任务与失败原因
    reopened = AppTest.from_file(str(APP_PATH)).run()
    assert not reopened.exception
    table = reopened.dataframe[0].value
    assert failed_job.job_id in set(table["任务号"].astype(str))
    reasons = " ".join(str(value) for value in table["原因"].astype(str))
    assert "免费源连接失败" in reasons

    # 数据页仍如实显示最后成功旧时点，不冒充更新成功
    reopened.sidebar.radio[0].set_value("数据").run()
    assert not reopened.exception
    assert "2024-06-01" in _text(reopened)
    assert "更新完成" not in "\n".join(
        str(item.value) for item in reopened.success
    )


def test_two_sessions_submit_distinct_jobs(tmp_path, monkeypatch):
    """两个独立会话各自首次提交都成功入库（提交标识全局唯一，不冲突）。"""
    root, _ = _seed_root(tmp_path)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    for _ in range(2):
        app = AppTest.from_file(str(APP_PATH)).run()
        assert not app.exception
        app.sidebar.radio[0].set_value("研究提交").run()
        app.button(key="submit_job").click().run()
        assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 2, "两个会话各提交一次必须产生两个任务"
    assert len({item.submission_id for item in jobs}) == 2


def test_same_submission_retrigger_does_not_duplicate(tmp_path, monkeypatch):
    """同一会话重复触发同一提交不新增任务（幂等键为定义内容 + 本次提交标识）。"""
    root, _ = _seed_root(tmp_path)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("研究提交").run()
    app.button(key="submit_job").click().run()
    assert not app.exception
    app.button(key="submit_job").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 1, "重复触发同一提交不得新建任务"
    successes = "\n".join(str(item.value) for item in app.success)
    assert jobs[0].job_id in successes


def test_resubmit_after_cancel_creates_new_job(tmp_path, monkeypatch):
    """反例（评审 R4）：任务取消后，同参数再次明确点击提交必须产生新任务与新提交标识，
    不得把原 cancelled 任务当"已提交"返回；同次 rerun 重放仍不产生新任务。"""
    root, _ = _seed_root(tmp_path)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("研究提交").run()
    app.button(key="submit_job").click().run()
    assert not app.exception
    first = _jobs(root)[0]
    # 任务进入终态（cancelled）：session_state 中的提交绑定随之失效
    JobService(ExperimentStore(root)).cancel(first.job_id)

    app.button(key="submit_job").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 2, "取消后的再次明确点击必须新建任务"
    newest = jobs[0]
    assert newest.job_id != first.job_id
    assert newest.submission_id != first.submission_id, "新提交操作必须生成新提交标识"
    assert newest.state == "queued", "页面应展示新任务而非 cancelled 旧任务"
    successes = "\n".join(str(item.value) for item in app.success)
    assert "已提交任务" in successes and newest.job_id in successes


def test_zero_seat_result_page_and_export(tmp_path, monkeypatch):
    """零席位（全排除）limited 结果：页面查看与导出 ZIP 不抛 KeyError，
    各榜如实显示空，导出与库内产物一致。"""
    root, result = _seed_zero_seat_root(tmp_path)
    assert result.status == "limited"
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    assert not app.exception
    warnings = "\n".join(str(item.value) for item in app.warning)
    assert "受限" in warnings
    assert "（无入选板块）" in _text(app), "零席位两榜如实显示为空"
    app.button(key="export_result").click().run()
    assert not app.exception
    payload, name = app.session_state["export_payload"]
    assert name.endswith(".zip")
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = set(archive.namelist())
    for table in ("boards", "slots", "stocks", "metrics", "exclusions", "cards"):
        assert f"{table}.csv" in names
    manifest = json.loads(
        (root / "results" / result.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"]["slots"] == 0
    assert json.loads(archive.read("limitations.json")) == manifest["limitations"]
    published_slots = pd.read_parquet(
        root / "results" / result.result_id / "slots.parquet"
    )
    assert archive.read("slots.csv").decode("utf-8") == published_slots.to_csv(
        index=False
    ), "导出 ZIP 与库内已发布空表一致"


def test_separate_rankings_and_evidence(tmp_path, monkeypatch):
    root, complete = _seed_root(tmp_path)
    limited_root_result = _seed_root(tmp_path / "limited", "mini_boards", "base")
    # 把 limited 结果搬进同一根目录，便于页面切换选择
    limited_src = limited_root_result[0] / "results" / limited_root_result[1].result_id
    limited_dst = root / "results" / limited_root_result[1].result_id
    import shutil

    shutil.copytree(limited_src, limited_dst)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    assert not app.exception
    app.selectbox(key="result_select").set_value(complete.result_id).run()
    assert not app.exception
    text = _text(app)
    assert "行业榜" in text and "主题榜" in text, "两榜必须分开呈现"
    assert "共同基准" in text and "000300.SH" in text
    assert "重复" in text
    assert "入选依据" in text, "无 LLM 也显示完整数值证据摘要"
    assert "观察／待人工判断" in text, "行业人工 Gate 不被网页修改"
    assert "价格收益口径" in text
    assert "缺口" in text
    seat_frames = [
        element.value
        for element in app.dataframe
        if element.value is not None
        and {"code", "rank", "score", "duplicate_count"} <= set(element.value.columns)
    ]
    seats = pd.concat(seat_frames, ignore_index=True)
    assert len(seats) == 50
    dup = seats[seats["code"] == "600901.SH"]
    assert len(dup) == 2 and (dup["duplicate_count"] == 1).all(), (
        "600901.SH 重复股两席保留并标注重复"
    )
    # 受限结果保留受限标识
    app.selectbox(key="result_select").set_value(limited_root_result[1].result_id).run()
    warnings = "\n".join(str(item.value) for item in app.warning)
    assert "受限" in warnings


def test_export_reads_existing_result(tmp_path, monkeypatch):
    root, result = _seed_root(tmp_path)
    store = ExperimentStore(root)
    definition_id = store.save_definition(
        "ranking",
        RankingDefinition(
            as_of=AS_OF,
            cutoff=CUTOFF,
            snapshot_id="snap-any",
            benchmark_id="csi300_price",
            parameters={
                "purpose": "ranking_current",
                # v2 分类门：合成主题核验条目（THM1–THM6 = industrial_theme）
                "category": {
                    "theme_basis": {"audited_boards": SYNTHETIC_THEME_AUDIT}
                },
            },
        ),
    )
    JobService(store).submit(definition_id, "click-1")
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    app.selectbox(key="result_select").set_value(result.result_id).run()
    app.button(key="export_result").click().run()
    assert not app.exception
    payload, name = app.session_state["export_payload"]
    assert name.endswith(".zip")
    assert app.download_button(key="download_zip").label == "下载 ZIP"
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = set(archive.namelist())
    assert "definition.json" in names
    assert "limitations.json" in names
    for table in ("boards", "slots", "stocks", "metrics", "series", "overlap", "cards"):
        assert f"{table}.csv" in names
    manifest = json.loads(
        (root / "results" / result.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    exported = json.loads(archive.read("limitations.json"))
    assert exported == manifest["limitations"]
    slots_csv = pd.read_csv(io.BytesIO(archive.read("slots.csv")))
    assert len(slots_csv) == 50
    assert len(_jobs(root)) == 1, "导出不能提交新任务"


def test_notebook_reads_same_result(tmp_path, monkeypatch):
    root, result = _seed_root(tmp_path)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    notebook = json.loads(
        (REPO_QUANT / "notebooks" / "read_result.ipynb").read_text(encoding="utf-8")
    )
    namespace: dict[str, object] = {"__name__": "__main__"}
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        exec(compile(source, "read_result.ipynb", "exec"), namespace)  # noqa: S102
    assert namespace["result"].result_id == result.result_id
    assert namespace["result"].status == "complete"
    slots = namespace["slots"]
    assert len(slots) == 50
    assert namespace["zip_path"].exists()
    archive = zipfile.ZipFile(namespace["zip_path"])
    assert "slots.csv" in archive.namelist()
