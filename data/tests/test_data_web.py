"""Field filtering retains source facts and matches SQL semantics."""

import duckdb
import pandas as pd
import pytest


def test_filter_export_matches_python_and_sql():
    from hetu_data import archive

    bars = pd.DataFrame(
        {
            "code": ["SYNTH.SH"] * 3,
            "date": ["2026-01-05"] * 3,
            "product_id": ["ts_a_daily"] * 3,
            "source_ref": ["one", "two", "three"],
            "volume_shares": [1234.0, 2000.0, None],
            "amount_cny": [56780.0, 80000.0, None],
        }
    )
    # Before this API exists, exercise the previous unfiltered behavior.
    filter_bars = archive.filter_bars
    result = filter_bars(
        bars,
        columns=("volume_shares", "amount_cny", "volume_shares"),
        predicates=({"field": "volume_shares", "op": "le", "value": 1500},),
    )
    assert result.source_ref.tolist() == ["one"]
    with duckdb.connect() as conn:
        conn.register("bars", bars)
        expected = conn.execute(
            "SELECT code, date, product_id, source_ref, volume_shares, amount_cny FROM bars WHERE volume_shares <= 1500"
        ).df()
    pd.testing.assert_frame_equal(result.reset_index(drop=True), expected)
    from io import StringIO

    pd.testing.assert_frame_equal(pd.read_csv(StringIO(result.to_csv(index=False))), expected)
    empty = filter_bars(
        bars, columns=("volume_shares",), predicates=({"field": "volume_shares", "op": "ge", "value": 3000},)
    )
    assert empty.empty and list(empty) == ["code", "date", "product_id", "source_ref", "volume_shares"]
    assert len(filter_bars(bars, columns=("amount_cny",), predicates=())) == 3
    assert filter_bars(
        bars, columns=(), predicates=({"field": "amount_cny", "op": "is_null"},)
    ).source_ref.tolist() == ["three"]
    for predicate in (
        {"field": "code", "op": "eq", "value": 1},
        {"field": "amount_cny", "op": "gt", "value": 1},
        {"field": "amount_cny", "op": "eq", "value": float("inf")},
    ):
        with pytest.raises(ValueError):
            filter_bars(bars, columns=(), predicates=(predicate,))


def test_web_commands_use_explicit_root(isolated_data_env):
    import subprocess
    from pathlib import Path

    from test_kernel_install import run

    env = isolated_data_env
    subprocess.run(
        [
            str(env["python"]),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--find-links",
            str(env["wheelhouse"]),
            "-c",
            str(Path(__file__).parents[1] / "requirements.lock"),
            "hetu-data[web]==0.1.0",
        ],
        cwd=env["workdir"],
        env=env["env"],
        check=True,
        capture_output=True,
        text=True,
    )
    run(
        env,
        r"""
import sys
from pathlib import Path
from hetu_data.cli import main
from streamlit.web import cli
from streamlit.testing.v1 import AppTest
root = Path('explicit-root').resolve()
assert main(['worker', '--root', str(root), '--once']) == 0
calls = []
def launch():
    args = list(sys.argv)
    calls.append(args)
    assert args[-3:] == ['--', '--root', str(root)]
    assert Path(args[2]).parts[-3:] == ('hetu_data', 'ui', 'app.py')
    sys.argv = [args[2], *args[-2:]]
    app = AppTest.from_file(args[2]).run()
    assert not app.exception
    assert app.text_input(key='acquisition_request').value == str(root / 'request.json')
    return 0
cli.main = launch
assert main(['web', '--root', str(root)]) == 0
assert len(calls) == 1
""",
    )


def test_submit_same_click_is_idempotent_and_new_click_is_new_job(tmp_path):
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from hetu_data import jobs
    from hetu_data.catalog import DataTaskStore
    from hetu_data.models import DataRequest, data_request_to_dict

    tasks = DataTaskStore(tmp_path)
    request = DataRequest(
        ("ts_a_daily",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 6),
        datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
        mode="paid",
    )
    plan = {"request": data_request_to_dict(request), "segments": [], "limits": {}}
    submit = jobs.submit_update_plan
    first = submit(tasks, plan, "click")
    assert submit(tasks, plan, "click") == first
    with tasks._connect() as conn:
        assert conn.execute("SELECT count(*) FROM definitions").fetchone()[0] == 1
    with pytest.raises(ValueError):
        submit(tasks, dict(plan, limits={"provider_fetch": 1}), "click")
    jobs.JobService(tasks).cancel(first.job_id)
    assert submit(tasks, plan, "new-click").job_id != first.job_id


def test_maintenance_input_change_requires_preview(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    plan = dict(
        request={"mode": "free"},
        start="2026-01-01",
        target="2026-01-02",
        domains=[],
        counts={},
        limits={"approved_scope_ref": "synthetic"},
        target_scope={},
        unresolved=[],
        segments=[{}],
    )
    monkeypatch.setattr("hetu_data.updates.maintenance_plan", lambda *a, **k: plan)
    (tmp_path / "maintenance-contracts.json").write_text('{"products": {}}')

    def page(root):
        from hetu_data.store import DataStore
        from hetu_data.ui.data_page import render_maintenance

        render_maintenance(DataStore(root), lambda plan: None)

    app = AppTest.from_function(page, args=(tmp_path,)).run()
    app.button(key="maintenance_preview").click().run()
    assert not app.button(key="maintenance_submit").disabled
    app.selectbox(key="maintenance_mode").set_value("paid").run()
    assert app.button(key="maintenance_submit").disabled
    app.selectbox(key="maintenance_mode").set_value("free").run()
    assert not app.button(key="maintenance_submit").disabled
    (tmp_path / "maintenance-contracts.json").write_text('{"products": {}, "changed": true}')
    app.run()
    assert app.button(key="maintenance_submit").disabled


def test_web_launches_package_app_with_explicit_root(tmp_path, monkeypatch):
    import sys

    from streamlit.web import cli

    from hetu_data.cli import main

    calls = []
    monkeypatch.setattr(cli, "main", lambda: calls.append(list(sys.argv)) or 0)
    assert main(["web", "--root", str(tmp_path)]) == 0
    assert calls[0][-3:] == ["--", "--root", str(tmp_path)]
    from pathlib import Path

    assert Path(calls[0][2]).is_file()
    assert Path(calls[0][2]).parts[-3:] == ("hetu_data", "ui", "app.py")


def test_package_web_filter_and_csv(tmp_path, monkeypatch, source_facts):
    import io
    import json
    import sys
    from datetime import date, datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from streamlit.testing.v1 import AppTest

    from hetu_data.models import DataRequest
    from hetu_data.providers.tushare import TushareProvider
    from hetu_data.store import DataStore
    from hetu_data.ui import app as app_module

    grant = dict(
        product_id="ts_a_daily",
        approved_scope_ref="synthetic",
        license_ref="synthetic",
        allowed_operations=["read", "acquire", "import"],
        markets=["SH"],
        codes=["SYNTH.SH"],
        start="2000-01-01",
        end="2099-12-31",
        acquire_until="2099-12-31",
        retained_use=False,
    )
    (tmp_path / "data-entitlements.json").write_text(json.dumps({"grants": [grant]}))

    class ThreeRowClient(source_facts.SourceClient):
        def query(self, api, params, fields):
            payload = json.loads(super().query(api, params, fields))
            first = payload["data"]["items"][0]
            second = list(first)
            second[1], second[-2], second[-1] = "20260106", 20, 80
            third = list(first)
            third[1], third[-2], third[-1] = "20260107", None, None
            payload["data"]["items"] = [first, second, third]
            return json.dumps(payload).encode()

    adapter = TushareProvider(client=ThreeRowClient())
    data = DataStore(tmp_path, providers={"tushare": adapter})
    req = DataRequest(
        ("ts_a_daily",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 6),
        datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
        mode="paid",
    )
    from dataclasses import replace

    ref = data.prepare(replace(req, end=date(2026, 1, 7)))
    count = len(adapter.client.calls)
    exported = {}
    monkeypatch.setattr(
        "streamlit.download_button", lambda label, content, **kw: exported.update({kw["key"]: content}) or False
    )
    monkeypatch.setattr(sys, "argv", ["app.py", "--root", str(tmp_path)])
    app = AppTest.from_file(str(Path(app_module.__file__))).run()
    app.text_input(key="archive_codes").set_value("SYNTH.SH")
    app.date_input(key="archive_start").set_value(date(2026, 1, 5))
    app.date_input(key="archive_end").set_value(date(2026, 1, 7))
    app.text_area(key="archive_predicates").set_value('[{"field":"volume_shares","op":"le","value":1500}]')
    app.button(key="archive_query").click().run()
    assert not app.exception
    csv = pd.read_csv(io.BytesIO(exported["archive_csv"]))
    assert csv.volume_shares.tolist() == [1234]
    assert csv.amount_cny.tolist() == [56780]
    from hetu_data.archive import ARCHIVE_COLUMNS, filter_bars, query_archive

    all_bars = query_archive(data, ref, codes=("SYNTH.SH",), start=date(2026, 1, 5), end=date(2026, 1, 7))["bars"]
    assert len(all_bars) == 3
    filtered = filter_bars(
        all_bars, columns=ARCHIVE_COLUMNS, predicates=({"field": "volume_shares", "op": "le", "value": 1500},)
    )
    with duckdb.connect() as conn:
        conn.register("bars", all_bars)
        fields = ",".join(filtered.columns)
        sql = conn.execute(f"SELECT {fields} FROM bars WHERE volume_shares <= 1500").df()
    # CSV dates use strings; normalize only representation, retaining every column.
    from io import StringIO

    pd.testing.assert_frame_equal(csv, pd.read_csv(StringIO(filtered.to_csv(index=False))))
    pd.testing.assert_frame_equal(csv, pd.read_csv(StringIO(sql.to_csv(index=False))))
    visible = next(frame.value for frame in app.dataframe if list(frame.value.columns) == list(filtered.columns))
    pd.testing.assert_frame_equal(visible, filtered)
    assert len(adapter.client.calls) == count
    app.text_area(key="archive_predicates").set_value('[{"field":"volume_shares","op":"ge","value":3000}]')
    app.button(key="archive_query").click().run()
    assert not app.exception
    assert pd.read_csv(io.BytesIO(exported["archive_csv"])).empty


def test_submit_cli_recovers_original_definition(tmp_path, capsys):
    import json

    from hetu_data.cli import main

    plan = {
        "request": {
            "product_ids": ["ts_a_daily"],
            "codes": ["SYNTH.SH"],
            "start": "2026-01-05",
            "end": "2026-01-06",
            "as_of": "2026-10-25T09:00:00+08:00",
            "purpose": "backtest_low_volatility",
            "mode": "paid",
        },
        "segments": [],
        "limits": {},
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    args = ["submit", "--root", str(tmp_path), "--plan", str(path), "--submission-id", "click"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_package_tasks_only_offer_maintenance_actions(tmp_path, monkeypatch):
    import sys

    from streamlit.testing.v1 import AppTest

    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import JobService
    from hetu_data.ui import app as module

    tasks = DataTaskStore(tmp_path)
    jobs = JobService(tasks)
    old = jobs.submit(tasks._save_payload("data", {}), "old")
    plan = {
        "request": {
            "product_ids": ["ts_a_daily"],
            "codes": ["SYNTH.SH"],
            "start": "2026-01-05",
            "end": "2026-01-06",
            "as_of": "2026-10-25T09:00:00+08:00",
            "purpose": "backtest_low_volatility",
            "mode": "paid",
        },
        "segments": [],
        "limits": {},
    }
    job = jobs.submit(tasks.save_definition("maintenance", plan), "new")
    monkeypatch.setattr(sys, "argv", ["app.py", "--root", str(tmp_path)])
    app = AppTest.from_file(module.__file__).run()
    assert any(old.job_id in str(frame.value) for frame in app.dataframe)
    assert not any(b.key == f"cancel::{old.job_id}" for b in app.button)
    app.checkbox(key=f"confirm_cancel::{job.job_id}").check().run()
    app.button(key=f"cancel::{job.job_id}").click().run()
    assert jobs.get(job.job_id).state == "cancelled"
    app.button(key=f"retry::{job.job_id}").click().run()
    assert len(jobs.list()) == 3
    app.run()
    assert len(jobs.list()) == 3


def test_acquisition_requires_new_preview_after_input_change(tmp_path, monkeypatch):
    import json
    import sys

    from streamlit.testing.v1 import AppTest

    from hetu_data.ui import app as module

    request = {
        "product_ids": ["ts_a_daily"],
        "codes": ["SYNTH.SH"],
        "start": "2026-01-05",
        "end": "2026-01-06",
        "as_of": "2026-10-25T09:00:00+08:00",
        "purpose": "backtest_low_volatility",
        "mode": "paid",
    }
    (tmp_path / "request.json").write_text(json.dumps(request))
    (tmp_path / "contracts.json").write_text(
        json.dumps(
            {
                "products": {"ts_a_daily": {"max_days": 30}},
                "limits": {
                    "approved_scope_ref": "synthetic",
                    "provider_fetch": 1,
                    "upstream_product_requests": 1,
                    "network_attempts": 1,
                },
            }
        )
    )
    grant = dict(
        product_id="ts_a_daily",
        approved_scope_ref="synthetic",
        license_ref="synthetic",
        allowed_operations=["read", "acquire"],
        markets=["SH"],
        codes=["SYNTH.SH"],
        start="2000-01-01",
        end="2099-12-31",
        acquire_until="2099-12-31",
        retained_use=False,
    )
    (tmp_path / "data-entitlements.json").write_text(json.dumps({"grants": [grant]}))
    monkeypatch.setattr(sys, "argv", ["app.py", "--root", str(tmp_path)])
    app = AppTest.from_file(module.__file__).run()
    app.text_input(key="acquisition_request").set_value(str(tmp_path / "request.json"))
    app.text_input(key="acquisition_contract").set_value(str(tmp_path / "contracts.json"))
    app.button(key="acquisition_preview").click().run()
    assert not app.exception
    assert not app.button(key="acquisition_submit").disabled
    app.button(key="acquisition_submit").click().run()
    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import JobService

    jobs = JobService(DataTaskStore(tmp_path))
    assert len(jobs.list()) == 1
    app.run()
    assert len(jobs.list()) == 1
    jobs.cancel(jobs.list()[0].job_id)
    app.button(key="acquisition_submit").click().run()
    assert len(jobs.list()) == 2
    app.text_input(key="acquisition_request").set_value("changed.json").run()
    assert app.button(key="acquisition_submit").disabled


def test_web_query_without_research_package_never_fetches(isolated_data_env, source_facts):
    import subprocess
    from pathlib import Path

    from test_kernel_install import SETUP, run

    env = isolated_data_env
    subprocess.run(
        [
            str(env["python"]),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--find-links",
            str(env["wheelhouse"]),
            "-c",
            str(Path(__file__).parents[1] / "requirements.lock"),
            "hetu-data[web]==0.1.0",
        ],
        cwd=env["workdir"],
        env=env["env"],
        check=True,
        capture_output=True,
        text=True,
    )
    run(env, SETUP.replace("PAYLOAD", repr(source_facts.response().decode())))
    script = r"""
import sys, io, importlib.util
from datetime import date
from pathlib import Path
import pandas as pd
assert importlib.util.find_spec('hetu_quant') is None
assert importlib.util.find_spec('rqalpha') is None
from streamlit.testing.v1 import AppTest
import streamlit as st
from hetu_data.ui import app as module
from hetu_data.providers.tushare import TushareProvider
calls = []
def forbidden(*a, **k):
    calls.append(1)
    raise AssertionError('query must not fetch')
TushareProvider.fetch = forbidden
exports = {}
st.download_button = lambda label, content, **kw: exports.update({kw['key']: content}) or False
sys.argv = ['app.py', '--root', str(Path('store').resolve())]
app = AppTest.from_file(module.__file__).run()
assert not app.exception
app.text_input(key='archive_codes').set_value('SYNTH.SH')
app.date_input(key='archive_start').set_value(date(2026,1,5))
app.date_input(key='archive_end').set_value(date(2026,1,6))
app.button(key='archive_query').click().run()
assert not app.exception
frame = pd.read_csv(io.BytesIO(exports['archive_csv']))
assert frame.volume_shares.tolist() == [1234]
assert frame.amount_cny.tolist() == [56780]
assert not calls
assert any('未核验' in item.value for item in app.warning)
Path('store/data-entitlements.json').write_text('{"grants": []}')
app.button(key='archive_query').click().run()
assert not app.exception
assert any('本地读取失败' in item.value for item in app.error)
assert not calls
"""
    run(env, script)
