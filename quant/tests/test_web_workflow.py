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
    _, payload, name = app.session_state["export_payload"]
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
    _, payload, name = app.session_state["export_payload"]
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


def test_result_export_is_bound_to_selection(tmp_path, monkeypatch):
    root, first = _seed_zero_seat_root(tmp_path)
    # Preserve the second published artifact's own identifier.
    import shutil

    first_dir = root / "results" / first.result_id
    second_id = "res-synthetic-second"
    second_dir = root / "results" / second_id
    shutil.copytree(first_dir, second_dir)
    manifest_path = second_dir / "result.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["result_id"] = second_id
    manifest["limitations"] = ["synthetic second result limitation"]
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    app.selectbox(key="result_select").set_value(first.result_id).run()
    app.button(key="export_result").click().run()
    assert "export_payload" in app.session_state
    app.selectbox(key="result_select").set_value(second_id).run()
    assert "export_payload" not in app.session_state
    app.button(key="export_result").click().run()
    selected, payload, _ = app.session_state["export_payload"]
    assert selected == second_id
    archive = zipfile.ZipFile(io.BytesIO(payload))
    assert json.loads(archive.read("limitations.json")) == ["synthetic second result limitation"]
    assert _jobs(root) == []


def test_formal_data_root_is_separate_from_the_research_root(tmp_path, monkeypatch):
    research, _ = _seed_root(tmp_path / "research")
    formal = tmp_path / "formal"
    formal.mkdir()
    (formal / "last_success.json").write_text(json.dumps({"cn_a_daily": "2026-11-05T15:00:00+08:00"}))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(research))
    monkeypatch.setenv("HETU_DATA_ROOT", str(formal))

    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("数据").run()
    assert not app.exception
    assert any("2026-11-05T15:00:00+08:00" in str(item.value) for item in app.markdown)
    assert any(str(formal) in str(item.value) for item in app.caption)
    assert _jobs(research) == []
    assert not (formal / "catalog.sqlite").exists()


def _after_close_app(monkeypatch, data_root, research_root, *, trade_date=None, snapshot_id=None):
    monkeypatch.setenv("HETU_DATA_ROOT", str(data_root))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(research_root))
    app = AppTest.from_file(str(APP_PATH), default_timeout=40).run()
    app.sidebar.radio[0].set_value("研究提交").run()
    app.radio(key="research_kind").set_value("盘后研究").run()
    if snapshot_id:
        app.selectbox(key="after_close_source").set_value(snapshot_id)
    if trade_date:
        app.date_input(key="after_close_trade_date").set_value(trade_date)
    app.button(key="after_close_generate").click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    return app


def test_after_close_month_end_target_and_changes(tmp_path, monkeypatch):
    from after_close_cases import JAN, make_after_close_source

    store, ref = make_after_close_source(tmp_path / "data", prior=True, rankings=True)
    app = _after_close_app(monkeypatch, store.root, tmp_path / "research", trade_date=JAN,
                           snapshot_id=ref.snapshot_id)
    assert "上次有效信号 2023-12-29" in _text(app)
    frame = next(x.value for x in app.dataframe if "target_weight" in x.value.columns)
    assert frame.code.tolist() == ["VOL_01", "VOL_02"]
    assert frame.target_weight.tolist() == [0.25, 0.75]
    assert "2024-02-01" in _text(app)
    change = next(x.value for x in app.dataframe if "change" in x.value.columns)
    assert set(zip(change.code, change.change, strict=True)) == {
        ("VOL_01", "入选"), ("VOL_02", "保留"), ("VOL_03", "退出")
    }
    assert any("board_id" in element.value.columns for element in app.dataframe)
    _print_after_close_page(app)


def _print_after_close_page(app):
    for node in app.main:
        if node.type in ("subheader", "markdown", "success"):
            print(node.value)
    for table in app.dataframe:
        print(table.value.dropna(axis=1, how="all").to_string(index=False))


def test_after_close_ordinary_day_previous_target(tmp_path, monkeypatch):
    from after_close_cases import FEB, make_after_close_source

    store, ref = make_after_close_source(tmp_path / "data", bars_through=FEB, rankings=True)
    app = _after_close_app(monkeypatch, store.root, tmp_path / "research", trade_date=FEB,
                           snapshot_id=ref.snapshot_id)
    assert "今日无新调仓信号" in _text(app)
    assert "上次有效信号 2024-01-31 · 本次按当时事实回算" in _text(app)
    frame = next(x.value for x in app.dataframe if "target_weight" in x.value.columns)
    assert frame.code.tolist() == ["VOL_01", "VOL_02"]
    assert frame.target_weight.tolist() == [0.25, 0.75]
    _print_after_close_page(app)
    other, no_pool = make_after_close_source(tmp_path / "limited", bars_through=FEB, no_pool=True)
    limited = _after_close_app(monkeypatch, other.root, tmp_path / "limited-results", trade_date=FEB,
                               snapshot_id=no_pool.snapshot_id)
    assert "尚无合格历史信号" in _text(limited)


def test_after_close_reopens_saved_result(tmp_path, monkeypatch):
    from after_close_cases import FEB, JAN, make_after_close_source
    from hetu_quant import after_close
    from hetu_quant.ui import after_close_page

    root, rank = _seed_root(tmp_path / "ranking")
    store, ref = make_after_close_source(tmp_path / "data", rankings=True)
    _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=ref.snapshot_id)
    close_id = next(item for item in ExperimentStore(root).list_results() if item != rank.result_id)
    assert "新调仓信号" in _text(_after_close_app(monkeypatch, store.root, root, trade_date=JAN,
                                                    snapshot_id=ref.snapshot_id))
    late = _after_close_app(monkeypatch, store.root, root, trade_date=FEB, snapshot_id=ref.snapshot_id)
    assert _saved_after_close(late, root)["previous"]["result_id"] == close_id
    assert "请求日行情未到，当前观察保留原截止日 2024-01-31" in _text(late)
    late.button(key="after_close_open_earlier").click().run()
    assert not late.exception and "请求交易日 2024-01-31" in _text(late)
    frozen = after_close.read_after_close(ExperimentStore(root).get_result(close_id))
    assert frozen["source"]["root"] == str(store.root)
    assert not list((store.root / "results").glob("*/result.json"))

    def no_calculation(*args, **kwargs):
        raise AssertionError("opening a result must not recalculate it")

    monkeypatch.setattr(after_close_page, "generate_after_close", no_calculation)
    reopened = AppTest.from_file(str(APP_PATH)).run()
    reopened.sidebar.radio[0].set_value("结果与导出").run()
    reopened.selectbox(key="result_select").set_value(close_id).run()
    assert "新调仓信号 2024-01-31" in _text(reopened)
    reopened.button(key="export_result").click().run()
    selected, payload, _ = reopened.session_state["export_payload"]
    assert selected == close_id
    archive = zipfile.ZipFile(io.BytesIO(payload))
    assert json.loads(archive.read("result.json")) == frozen
    reopened.selectbox(key="result_select").set_value(rank.result_id).run()
    assert not reopened.exception
    if reopened.selectbox:
        assert all(close_id not in widget.options for widget in reopened.selectbox if widget.key == "diff_select")
    reopened.button(key="export_result").click().run()
    selected, payload, _ = reopened.session_state["export_payload"]
    assert selected == rank.result_id and "boards.csv" in zipfile.ZipFile(io.BytesIO(payload)).namelist()


def test_after_close_observation_and_rankings(tmp_path):
    from after_close_cases import JAN, make_after_close_source
    from hetu_quant.after_close import _read_observations

    store, ref = make_after_close_source(tmp_path / "data", rankings=True)
    prepared = _read_observations(store, ref, trade_date=JAN, staging=tmp_path / "nested")
    expected = run_ranking(RankingDefinition(
        as_of=datetime(2024, 1, 31, 23, 59, 59, 999999, tzinfo=CN), cutoff=JAN,
        snapshot_id=ref.snapshot_id, benchmark_id="csi300_price", parameters={"purpose": "ranking_historical"},
    ), ref, tmp_path / "direct")
    boards = pd.read_parquet(tmp_path / "direct/results" / expected.result_id / "boards.parquet")
    actual = pd.DataFrame(prepared["rankings"]["boards"])
    assert set(actual.board_kind) == {"industry", "theme"}
    expected_projection = ["board_kind", "board_id", "rank", "score", "eligible_members", "selected"]
    pd.testing.assert_frame_equal(actual[expected_projection], boards[expected_projection], check_dtype=False)
    assert prepared["actual_cutoff"] == "2024-01-31"
    assert prepared["coverage"]["count"] == 20 and prepared["coverage"]["pool_size"] is None


def test_after_close_shows_observations_with_partial_inputs(tmp_path):
    from after_close_cases import FEB, make_after_close_source
    from hetu_quant.after_close import _read_observations

    store, ref = make_after_close_source(tmp_path / "data", no_pool=True)
    result = _read_observations(store, ref, trade_date=FEB, staging=tmp_path / "out")
    assert result["coverage"]["count"] == 0 and result["actual_cutoff"] == "2024-01-31"
    assert len(result["observations"]) == 20
    assert result["rankings"]["status"] == "limited"
    assert result["coverage"]["pool_size"] is None
    assert any("请求日行情未到" in row["reason"] for row in result["observation_gaps"])


def test_after_close_publication_is_atomic(tmp_path, monkeypatch):
    import pytest

    from after_close_cases import JAN, make_after_close_source
    from hetu_quant import after_close

    store, ref = make_after_close_source(tmp_path / "data", rankings=True)
    output = tmp_path / "results-root"
    before = ExperimentStore(output).list_results()
    with monkeypatch.context() as patch:
        def fail(path, _value):
            assert list((path.parent / "ranking" / "results").glob("*/result.json"))
            raise OSError("simulated publication failure")

        patch.setattr(after_close, "_publish_json", fail)
        with pytest.raises(OSError, match="simulated publication failure"):
            after_close.generate_after_close(store, ref, trade_date=JAN, output_root=output)
    assert list((output / "results").iterdir()) == []
    assert ExperimentStore(output).list_results() == before
    first = after_close.generate_after_close(store, ref, trade_date=JAN, output_root=output)
    second = after_close.generate_after_close(store, ref, trade_date=JAN, output_root=output)
    assert first.result_id != second.result_id
    assert after_close.read_after_close(first)["signal_date"] == str(JAN)


def test_after_close_original_revision_and_follow_up(tmp_path, monkeypatch):
    from after_close_cases import JAN, make_after_close_source
    from hetu_quant.after_close import read_after_close

    store, original_source = make_after_close_source(tmp_path / "input")
    root = tmp_path / "research"
    app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=original_source.snapshot_id)
    original_id = app.session_state["after_close_result"]
    published = root / "results" / original_id
    original_bytes = {str(path.relative_to(published)): path.read_bytes()
                      for path in published.rglob("*") if path.is_file()}
    _, corrected = make_after_close_source(tmp_path / "input", correction_only=True)
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    app.selectbox(key="result_select").set_value(original_id).run()
    app.selectbox(key="after_close_followup_source").set_value(corrected.snapshot_id).run()
    app.button(key="after_close_revise").click().run()
    assert not app.exception and not app.error
    revision_id = app.selectbox(key="result_select").value
    assert revision_id != original_id
    revision = read_after_close(ExperimentStore(root).get_result(revision_id))
    assert revision["revision_of"] == original_id
    assert any(row["code"] == "VOL_01" and row["field"] == "close_raw" for row in revision["revision_changes"])
    reopened = AppTest.from_file(str(APP_PATH)).run()
    reopened.sidebar.radio[0].set_value("结果与导出").run()
    reopened.selectbox(key="result_select").set_value(original_id).run()
    assert "新调仓信号 2024-01-31" in _text(reopened)
    assert all((published / name).read_bytes() == raw for name, raw in original_bytes.items())
    print(f"original={original_id} revision={revision_id} original_bytes_match=True")


def _saved_after_close(app, root):
    from hetu_quant.after_close import read_after_close

    return read_after_close(ExperimentStore(root).get_result(app.session_state["after_close_result"]))


def test_after_close_page_two_roots_and_known_change(tmp_path, monkeypatch):
    from after_close_cases import FEB, JAN, make_after_close_source

    results, engine = [], []
    monkeypatch.setattr("hetu_quant.backtest.run_engine_subprocess", lambda *a, **kw: engine.append(1))
    for name, options in (("original", {}), ("future", dict(bars_through=FEB, perturb=True)),
                          ("known", dict(change_close=25))):
        store, ref = make_after_close_source(tmp_path / name, **options)
        root = tmp_path / name / "research"
        ledger = root / "research" / "phase2-lowvol" / "trials.jsonl"
        assert not ExperimentStore(root).list_results() and not ledger.exists()
        app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=ref.snapshot_id)
        results.append(_saved_after_close(app, root)["target"]["targets"])
        assert not ledger.exists()
    assert results[0] == results[1]
    assert [row["code"] for row in results[2]] == ["VOL_02", "VOL_03"]
    assert [row["target_weight"] for row in results[2]] == [0.5, 0.5]
    assert engine == []
    print("Independent recalculations: " + json.dumps([
        [{key: row[key] for key in ("code", "signal_value", "rank", "target_weight", "reason")} for row in result]
        for result in results
    ], ensure_ascii=False))


def test_after_close_page_calendar_and_original_close(tmp_path, monkeypatch):
    from after_close_cases import JAN, make_after_close_source, make_revision_library

    obtained = []
    for name, values in (("next", {}), ("no_next", dict(calendar_through=JAN)),
                         ("missing_month", dict(calendar_gap=str(JAN)))):
        store, ref = make_after_close_source(tmp_path / name, **values)
        root = tmp_path / name / "research"
        app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=ref.snapshot_id)
        obtained.append(_saved_after_close(app, root))
        assert "请求日实到" in _text(app)
    assert obtained[0]["target"]["targets"] == obtained[1]["target"]["targets"]
    assert [row["target"]["executable_from"] for row in obtained] == ["2024-02-01", None, None]
    assert obtained[2]["target"]["status"] == "calendar_unknown" and not obtained[2]["target"]["targets"]
    for correction_only in (False, True):
        store, ref = make_revision_library(tmp_path / f"revision-{correction_only}", correction_only=correction_only)
        root = tmp_path / f"revision-{correction_only}" / "research"
        app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=ref.library_id)
        evidence = _saved_after_close(app, root)
        observations = next(x.value for x in app.dataframe if "open_raw" in x.value.columns)
        vol1 = observations[observations.code.eq("VOL_01")].iloc[0]
        assert vol1.close_raw == 12 and str(vol1.revised_published_at).startswith("2024-02-02")
        fact = [r for r in evidence["close_evidence"] if r["date"] == str(JAN) and r["code"] == "VOL_01"]
        if correction_only:
            assert not fact and evidence["target"]["status"] == "blocked"
            signal_gaps = next(x.value for x in app.dataframe if "revised_value" in x.value.columns)
            assert any(row.field == "close_raw" and row.code == "VOL_01" for row in signal_gaps.itertuples())
        else:
            assert fact[0]["close_raw"] == 10
            assert evidence["target"]["targets"] == obtained[0]["target"]["targets"]
        print(f"correction_only={correction_only}, observed={vol1.close_raw}, "
              f"published={vol1.revised_published_at}, formal={fact[0]['close_raw'] if fact else None}, "
              f"status={evidence['target']['status']}")
    print("calendar: " + str([(row["target"]["status"], row["target"]["executable_from"]) for row in obtained]))


def test_after_close_evidence_belongs_to_selected_signal(tmp_path, monkeypatch):
    import pytest

    from after_close_cases import JAN, make_after_close_source
    from backtest_cases import case_definition, make_backtest_case
    from hetu_quant import backtest
    from hetu_quant.after_close import link_after_close_evidence, list_after_close_evidence, read_after_close_evidence
    from hetu_quant.backtest_evaluation import publish_after_close_evaluation

    through = date(2024, 7, 31)
    store, source = make_after_close_source(
        tmp_path / "data", bars_through=through, calendar_through=through,
        future_close={"2024-02-29": 11, "2024-04-30": 12, "2024-07-31": 13},
    )
    root = tmp_path / "research"
    ledger = root / "research" / "phase2-lowvol" / "trials.jsonl"
    assert not ledger.exists()
    actual_engines = []

    def no_engine(*args, **kwargs):
        actual_engines.append(1)
        raise AssertionError("after-close paths must not start an engine")

    monkeypatch.setattr(backtest, "run_engine_subprocess", no_engine)
    first = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=source.snapshot_id)
    original_id = first.session_state["after_close_result"]
    original = ExperimentStore(root).get_result(original_id)
    original_manifest = root / "results" / original_id / "result.json"
    frozen = original_manifest.read_bytes()
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("结果与导出").run()
    app.selectbox(key="result_select").set_value(original_id).run()
    app.button(key="after_close_revise").click().run()
    assert not app.exception and not app.error
    revision_id = app.selectbox(key="result_select").value

    app.selectbox(key="result_select").set_value(original_id).run()
    app.date_input(key="after_close_evaluation_through").set_value(date(2024, 2, 29))
    app.button(key="after_close_evaluate").click().run()
    assert not app.exception and not app.error
    evaluation_id = app.session_state["last_after_close_evaluation"]
    assert evaluation_id in _text(app)
    records = list_after_close_evidence(root, original_id)
    assert {record["kind"] for record in records} == {"evaluation"}
    first_labels = read_after_close_evidence(root, records[0])["horizons"]
    assert first_labels[0]["status"] == "mature" and first_labels[0]["label_return"] == pytest.approx(0.1)
    assert all(row["status"] == "pending" for row in first_labels if row["horizon_months"] in (3, 6))
    full_id = publish_after_close_evaluation(store, source, original, through=through)
    after_april_id = publish_after_close_evaluation(store, source, original, through=date(2024, 4, 30))
    by_id = {row["source_id"]: row for row in list_after_close_evidence(root, original_id)}
    full = read_after_close_evidence(root, by_id[full_id])["horizons"]
    april = read_after_close_evidence(root, by_id[after_april_id])["horizons"]
    assert [row["status"] for row in april if row["code"] == "VOL_01"] == ["mature", "mature", "pending"]
    assert [row["label_return"] for row in full if row["code"] == "VOL_01"] == pytest.approx([.1, .2, .3])
    assert list_after_close_evidence(root, revision_id) == []
    assert actual_engines == [] and not ledger.exists()

    qdir = root / "qualification" / "phase2" / "engine-admission-synthetic"
    qdir.mkdir(parents=True)
    (qdir / "engine-admission.json").write_text(json.dumps(dict(decision="candidate_ready")))
    fake_calls = []

    def offline_account(_interpreter, _runner, bundle, output, **_kwargs):
        fake_calls.append(1)
        output.mkdir()
        signal = pd.read_parquet(bundle / "signals.parquet")
        signal.loc[:, list(backtest.TABLE_COLUMNS["signals"])].to_parquet(output / "signals.parquet", index=False)
        signal.loc[:, list(backtest.TABLE_COLUMNS["targets"])].to_parquet(output / "targets.parquet", index=False)
        first = signal.iloc[0]
        day, code = first.executable_from, first.code
        frames = dict(
            orders=[dict(order_id="order-1", signal_at=first.signal_at, submitted_at=day, code=code, side="buy",
                         requested_qty=100, filled_qty=100, status="filled", reason="")],
            trades=[dict(trade_id="trade-1", order_id="order-1", executed_at=day, code=code,
                         side="buy", qty=100, price_raw=10, execution_price=10, commission=5,
                         stamp_tax=0, transfer_fee=0, dividend_tax=0, slippage_cost=0)],
            costs=[dict(date=day, order_id="order-1", code=code, cost_type="commission", amount=5)],
        )
        for table, rows in frames.items():
            pd.DataFrame(rows).to_parquet(output / f"{table}.parquet", index=False)
        (output / "backtest.json").write_text(json.dumps(dict(status="complete", limitations=[])))

    definition = case_definition(make_backtest_case("history"), source.snapshot_id, as_of=datetime(
        2024, 8, 1, 0, tzinfo=CN), start=date(2024, 2, 1), end=date(2024, 2, 29))
    with monkeypatch.context() as isolated:
        isolated.setattr(backtest, "run_engine_subprocess", offline_account)
        account = backtest.run_backtest(definition, store, root, research_context={"after_close_origin": {
            "publication_id": original_id, "strategy_id": "low_volatility", "signal_date": str(JAN),
        }})
    assert len(fake_calls) == 1 and actual_engines == []
    links = list_after_close_evidence(root, original_id)
    assert any(row["source_id"] == account.run_id and row["kind"] == "simulation" for row in links)
    assert len(links) == 4
    for invalid_id, invalid_day, invalid_strategy in (
        (revision_id, JAN, "low_volatility"), (original_id, through, "low_volatility"),
        (original_id, JAN, "universe_vw"),
    ):
        with pytest.raises(ValueError):
            link_after_close_evidence(root, invalid_id, signal_date=invalid_day, strategy_id=invalid_strategy,
                                      kind="simulation", source_id=account.run_id)
    link_after_close_evidence(root, original_id, signal_date=JAN, strategy_id="low_volatility",
                              kind="simulation", source_id=account.run_id)
    assert list_after_close_evidence(root, original_id) == links

    reopened = AppTest.from_file(str(APP_PATH)).run()
    reopened.sidebar.radio[0].set_value("结果与导出").run()
    reopened.selectbox(key="result_select").set_value(original_id).run()
    assert account.run_id in _text(reopened) and evaluation_id in _text(reopened) and full_id in _text(reopened)
    tables = [table.value for table in reopened.dataframe]
    assert any("commission" in value and value.commission.tolist() == [5] for value in tables)
    assert any("order_id" in value and "cost_type" in value and value.amount.tolist() == [5] for value in tables)
    assert original_manifest.read_bytes() == frozen
    reopened.selectbox(key="result_select").set_value(revision_id).run()
    assert account.run_id not in _text(reopened) and evaluation_id not in _text(reopened)
    assert actual_engines == [] and not ledger.exists()
    print(f"original={original_id} revision={revision_id} simulation={account.run_id} "
          f"evaluation_1m={evaluation_id} evaluation_3m={after_april_id} evaluation_6m={full_id} "
          "order=order-1 fill=trade-1 fee=5 real_engine=0 mock_account=1 official_ledger_rows=0")


def test_after_close_evaluation_keeps_original_split(tmp_path, monkeypatch):
    from after_close_cases import JAN, make_after_close_source
    from hetu_quant.after_close import list_after_close_evidence, read_after_close_evidence
    from hetu_quant.backtest_evaluation import publish_after_close_evaluation

    through = date(2024, 7, 31)
    store, source = make_after_close_source(tmp_path / "data", bars_through=through, calendar_through=through,
                                           evaluation_split=["2024-02-01", "2024-03-31"])
    root = tmp_path / "research"
    app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=source.snapshot_id)
    identifier = app.session_state["after_close_result"]
    original = ExperimentStore(root).get_result(identifier)
    eval_id = publish_after_close_evaluation(store, source, original, through=through)
    record = next(row for row in list_after_close_evidence(root, identifier) if row["source_id"] == eval_id)
    rows = read_after_close_evidence(root, record)["horizons"]
    assert {row["status"] for row in rows if row["horizon_months"] == 1} == {"mature"}
    assert {row["status"] for row in rows if row["horizon_months"] in (3, 6)} == {"limited"}
    assert {row["reason"] for row in rows if row["status"] == "limited"} == {"越过冻结评价切分"}


def test_after_close_code_change_retains_proved_identity(tmp_path, monkeypatch):
    from after_close_cases import JAN, make_after_close_source

    store, ref = make_after_close_source(tmp_path / "proved", prior=True, rename="proved")
    root = tmp_path / "results"
    app = _after_close_app(monkeypatch, store.root, root, trade_date=JAN, snapshot_id=ref.snapshot_id)
    saved = _saved_after_close(app, root)
    assert saved["signal_date"] == str(JAN)
    row = next(row for row in saved["changes"] if row["code"] == "VOL_RENAMED")
    assert row["change"] == "保留" and row["prior_code"] == "VOL_01"
    assert "已证同一证券换码" in row["reason"]
    assert {fact["code"] for fact in saved["close_evidence"] if fact["target_code"] == "VOL_RENAMED"} == {
        "VOL_01", "VOL_RENAMED",
    }
    other, blocked = make_after_close_source(tmp_path / "unproved", prior=True, rename="unproved")
    blocked_root = tmp_path / "blocked"
    limited = _after_close_app(monkeypatch, other.root, blocked_root, trade_date=JAN,
                               snapshot_id=blocked.snapshot_id)
    saved = _saved_after_close(limited, blocked_root)
    assert saved["signal_date"] is None
    assert any(gap["code"] == "VOL_RENAMED" and gap["field"] == "code_continuity" for gap in saved["target"]["gaps"])


def test_after_close_export_respects_origin_license(tmp_path, monkeypatch):
    import pytest

    from after_close_cases import FEB, JAN, make_after_close_source
    from hetu_quant.after_close import generate_after_close
    from hetu_quant.reporting import export_result

    locked_store, locked = make_after_close_source(tmp_path / "restricted", deny_export=True)
    root = tmp_path / "research"
    app = _after_close_app(monkeypatch, locked_store.root, root, trade_date=JAN, snapshot_id=locked.snapshot_id)
    original = _saved_after_close(app, root)
    assert original["signal_date"] == str(JAN)
    free, source = make_after_close_source(tmp_path / "unrestricted", bars_through=FEB)
    later = _after_close_app(monkeypatch, free.root, root, trade_date=FEB, snapshot_id=source.snapshot_id)
    current = _saved_after_close(later, root)
    assert current["previous"]["result_id"] == original["result_id"]
    ref = ExperimentStore(root).get_result(current["result_id"])
    with pytest.raises(PermissionError, match="未获导出许可"):
        export_result(ref, tmp_path / "export")
    revision = generate_after_close(free, source, trade_date=JAN, output_root=root, revision_of=original["result_id"])
    with pytest.raises(PermissionError, match="未获导出许可"):
        export_result(revision, tmp_path / "export")
