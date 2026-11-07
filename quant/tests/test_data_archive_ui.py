"""04.2 真实组件：共同样本经正式网页、Notebook、SQL及下载。"""

import io
import json
from datetime import date
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from backtest_cases import build_case_store, make_backtest_case
from hetu_quant.ui import app as app_module

NOTEBOOK = Path(__file__).parents[1] / "notebooks/data_archive.ipynb"


def test_archive_ui_notebook_sql_share_snapshot(tmp_path, monkeypatch):
    store, ref = build_case_store(tmp_path, make_backtest_case("coverage"))
    (store.root / "data-products.json").write_text(json.dumps(store.config))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(store.root))
    monkeypatch.setenv("HETU_ARCHIVE_SNAPSHOT", ref.snapshot_id)
    monkeypatch.setenv("HETU_ARCHIVE_QUERY", json.dumps({
        "codes": ["SYN_EXIT.SZ"], "start": "2023-12-01", "end": "2024-01-04"
    }))
    exported = {}

    def capture(label, data, *args, **kwargs):
        exported[kwargs["key"]] = data
        return False

    # 仅捕获Streamlit传输边界，数据查询、正式页面和序列化保持生产实现。
    monkeypatch.setattr("streamlit.download_button", capture)
    app = AppTest.from_file(str(Path(app_module.__file__))).run()
    app.sidebar.radio[0].set_value("数据").run()
    assert not app.exception
    app.text_input(key="archive_codes").set_value("SYN_EXIT.SZ")
    app.date_input(key="archive_start").set_value(date(2023, 12, 1))
    app.date_input(key="archive_end").set_value(date(2024, 1, 4))
    app.button(key="archive_query").click().run()
    assert not app.exception
    downloaded = pd.read_csv(io.BytesIO(exported["archive_csv"]))
    assert downloaded.date.tolist() == ["2023-12-29"]
    env = {}
    for cell in json.loads(NOTEBOOK.read_text())["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), env)
    pd.testing.assert_frame_equal(downloaded, env["bars"], check_dtype=False)
    pd.testing.assert_frame_equal(env["bars"], env["sql_bars"])
    visible = next(x.value for x in app.dataframe if list(x.value.columns) == list(downloaded.columns))
    pd.testing.assert_frame_equal(visible, env["bars"])
    app.text_input(key="archive_codes").set_value("SYN_NEW.BJ")
    app.date_input(key="archive_start").set_value(date(2023, 1, 4))
    app.date_input(key="archive_end").set_value(date(2023, 12, 29))
    app.button(key="archive_query").click().run()
    assert not app.exception
    assert pd.read_csv(io.BytesIO(exported["archive_csv"])).empty
    gaps = next(x.value for x in app.dataframe if "reason" in x.value.columns)
    assert "not_listed" in set(gaps.reason)


def test_data_update_queue_cancel_reopen_and_offline_read(tmp_path, monkeypatch):
    import threading

    from hetu_quant import worker
    from hetu_quant.data_archive import query_archive
    from hetu_quant.experiments import ExperimentStore
    from hetu_quant.jobs import JobService
    from hetu_quant.models import SnapshotRef
    from test_data_sources import request
    from test_data_updates import maintenance_case

    ds, adapter, spec = maintenance_case(tmp_path)
    parent = ds.prepare(request(start="2026-01-05", end="2026-01-05"))
    (ds.root / "data-products.json").write_text(json.dumps(ds.config))
    spec["products"].pop("ts_action_versions")
    config = ds.root / "maintenance-contracts.json"
    config.write_text(json.dumps(spec))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(ds.root))
    import time
    from types import SimpleNamespace

    monkeypatch.setattr("hetu_quant.data_updates.time", SimpleNamespace(sleep=lambda _: None, monotonic=time.monotonic))
    app = AppTest.from_file(str(Path(app_module.__file__))).run()
    app.sidebar.radio[0].set_value("数据").run()
    app.selectbox(key="maintenance_mode").set_value("paid")
    app.date_input(key="maintenance_target").set_value(date(2026, 1, 15))
    count = len(adapter.client.calls)
    app.button(key="maintenance_preview").click().run()
    assert not app.exception
    app.button(key="maintenance_submit").click().run()
    assert not app.exception and len(adapter.client.calls) == count
    service = JobService(ExperimentStore(ds.root))
    job = service.list()[0]
    frozen = service.store.read_definition(job.definition_id)
    assert frozen["request"]["mode"] == "paid" and frozen["target"] == "2026-01-15"
    # 页面刷新及第二会话找回，不新增任务、不执行请求。
    app.run()
    assert len(service.list()) == 1
    reopened = AppTest.from_file(str(Path(app_module.__file__))).run()
    reopened.sidebar.radio[0].set_value("任务列表").run()
    assert job.job_id in next(x.value for x in reopened.dataframe if "任务号" in x.value.columns)["任务号"].tolist()
    entered, release = threading.Event(), threading.Event()
    original = adapter.client.query

    def slow(api, params, fields):
        entered.set()
        assert release.wait(5)
        return original(api, params, fields)

    adapter.client.query = slow
    thread = threading.Thread(target=worker.run_job, args=(job.job_id, service.store, ds))
    thread.start()
    assert entered.wait(5)
    reopened.button(key=f"cancel::{job.job_id}").click().run()
    assert service.get(job.job_id).state == "cancel_requested"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    cancelled = service.get(job.job_id)
    assert cancelled.state == "cancelled" and len(adapter.client.calls) == count + 1
    report = json.loads((cancelled.output / "update.json").read_text())
    assert report["pending"] and report["incremental_usage"]["provider_fetch"] == 1
    old = query_archive(
        ds, SnapshotRef(parent.snapshot_id, ds.root), codes=("SYNTH.SH",), start=date(2026, 1, 5), end=date(2026, 1, 5)
    )
    assert old["bars"].close_raw.tolist() == [10]
    # 同一冻结计划显式重开任务，账本复用而非重置。
    retry = service.retry(job.job_id, "synthetic-resume")
    adapter.client.query = original
    worker.run_job(retry.job_id, service.store, ds)
    done = service.get(retry.job_id)
    assert done.state == "succeeded"
    final = json.loads((done.output / "update.json").read_text())
    assert final["actual_usage"]["provider_fetch"] > final["incremental_usage"]["provider_fetch"]
    assert final["maintenance"]["domains"][0]["missing"]  # 原始替身始终无1月2日，任务成功不得伪装内容齐备。
    before = len(adapter.client.calls)
    duplicate = service.submit(job.definition_id, "synthetic-recheck")
    worker.run_job(duplicate.job_id, service.store, ds)
    repeated = json.loads((service.get(duplicate.job_id).output / "update.json").read_text())
    assert repeated["incremental_usage"]["provider_fetch"] == 0 and len(adapter.client.calls) == before
    reopened.run()
    assert not reopened.exception
    assert any("本次新增" in x.value for x in reopened.caption)
    # 终态后明确新点击会新建定义，但同一获批账本不能重置或重复发送已完成段。
    cloned = service.store.save_definition("maintenance", frozen)
    cloned_job = service.submit(cloned, "synthetic-new-click-same-approval")
    worker.run_job(cloned_job.job_id, service.store, ds)
    report = json.loads((service.get(cloned_job.job_id).output / "update.json").read_text())
    assert report["incremental_usage"]["provider_fetch"] == 0 and len(adapter.client.calls) == before
