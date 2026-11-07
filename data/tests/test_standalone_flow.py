"""Installed CLI → durable worker publication → independent readers, using synthetic transport facts."""

import json
import subprocess
from pathlib import Path

from test_kernel_install import SETUP, run


def test_installed_data_flow_survives_process_restart(isolated_data_env, source_facts):
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
    # Reuse the grant/request baseline only, never its prepared snapshot or answer.
    setup = (
        SETUP[: SETUP.index("s = DataStore")] + SETUP[SETUP.index("r = DataRequest") : SETUP.index("ref = s.prepare")]
    )
    setup = setup.replace("PAYLOAD", repr(source_facts.response().decode()))
    run(
        env,
        setup
        + """
Path('contracts.json').write_text(json.dumps({'products': {'ts_a_daily': {'max_days': 30}},
    'limits': {'approved_scope_ref': 'synthetic', 'provider_fetch': 1,
               'upstream_product_requests': 1, 'network_attempts': 1}}))
""",
    )
    guard = """
import socket, json, sys, importlib.util, importlib.metadata
from pathlib import Path
import hetu_data
assert sys.prefix != sys.base_prefix
assert 'include-system-site-packages = false' in (Path(sys.prefix)/'pyvenv.cfg').read_text()
assert str(Path(sys.prefix)) in hetu_data.__file__
for name in ('hetu_quant', 'research', 'rqalpha'):
    assert importlib.util.find_spec(name) is None
assert not any('/workdir/HETU' in p for p in sys.path)
def forbidden(*a, **k):
    raise AssertionError('external network forbidden')
socket.create_connection = forbidden
socket.socket.connect = forbidden
from hetu_data.cli import main
from hetu_data.providers.tushare import TushareProvider
"""

    def cli(*args):
        return run(env, guard + f"assert main({list(args)!r}) == 0\n")

    cli(
        "plan", "--root", "store", "--request", "request.json", "--contracts", "contracts.json", "--output", "plan.json"
    )
    plan = json.loads((env["workdir"] / "plan.json").read_text())
    assert len(plan["segments"]) == 1
    _submitted = json.loads(cli("submit", "--root", "store", "--plan", "plan.json", "--submission-id", "standalone"))
    # Supply raw source bytes at the HTTP boundary; all provider/task/store logic remains real.
    run(
        env,
        guard
        + f"""
from hetu_data.providers.tushare import TushareProvider
calls = []
class SourceClient:
    def query(self, api, params, fields):
        calls.append((api, params, fields))
        return {source_facts.response()!r}
original_init = TushareProvider.__init__
def init(self, **kwargs):
    original_init(self, **dict(kwargs, client=SourceClient()))
TushareProvider.__init__ = init
assert main(['worker', '--root', 'store', '--once']) == 0
assert len(calls) == 1, calls
print(calls)
""",
    )
    result = run(
        env,
        guard
        + """
TushareProvider._query = forbidden
from datetime import date
from io import BytesIO, StringIO
import pandas as pd
import duckdb
from hetu_data import DataStore, SnapshotRef
from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import JobService
from hetu_data.archive import ARCHIVE_COLUMNS, filter_bars, query_archive
jobs = JobService(DataTaskStore(Path('store'))).list()
assert len(jobs) == 1 and jobs[0].state == 'succeeded', jobs
update = json.loads((jobs[0].output/'update.json').read_text())
assert len(update['snapshot_ids']) == 1, update
store = DataStore(Path('store'))
ref = SnapshotRef(update['snapshot_ids'][0], store.root)
ledger = Path(update['approved_usage_ledger']).read_text()
assert '"type": "completed"' in ledger
bars = query_archive(store, ref, codes=('SYNTH.SH',), start=date(2026,1,5), end=date(2026,1,5))['bars']
filtered = filter_bars(bars, columns=ARCHIVE_COLUMNS, predicates=({'field':'volume_shares','op':'le','value':1500},))
assert filtered.volume_shares.tolist() == [1234]
assert filtered.amount_cny.tolist() == [56780]
assert main(['query','--root','store','--snapshot',ref.snapshot_id,'--codes','SYNTH.SH',
             '--start','2026-01-05','--end','2026-01-05',
             '--columns',','.join(filtered.columns),'--output','query.csv']) == 0
csv = pd.read_csv('query.csv')
pd.testing.assert_frame_equal(csv, pd.read_csv(StringIO(filtered.to_csv(index=False))))
with duckdb.connect() as conn:
    conn.register('bars',bars)
    sql = conn.execute('SELECT '+','.join(filtered.columns)+' FROM bars WHERE volume_shares <= 1500').df()
pd.testing.assert_frame_equal(csv,pd.read_csv(StringIO(sql.to_csv(index=False))))
import streamlit as st
from streamlit.testing.v1 import AppTest
from hetu_data.ui import app as module
exports = {}
st.download_button = lambda label, content, **kw: exports.update({kw['key']:content}) or False
sys.argv = ['app.py','--root',str(Path('store').resolve())]
app = AppTest.from_file(module.__file__).run()
app.text_input(key='archive_codes').set_value('SYNTH.SH')
app.date_input(key='archive_start').set_value(date(2026,1,5))
app.date_input(key='archive_end').set_value(date(2026,1,5))
app.text_area(key='archive_predicates').set_value('[{"field":"volume_shares","op":"le","value":1500}]')
app.button(key='archive_query').click().run()
assert not app.exception
pd.testing.assert_frame_equal(csv,pd.read_csv(BytesIO(exports['archive_csv'])))
visible = next(f.value for f in app.dataframe if list(f.value.columns)==list(filtered.columns))
pd.testing.assert_frame_equal(visible,filtered)
assert any('未核验' in w.value for w in app.warning)
app.text_area(key='archive_predicates').set_value('[{"field":"volume_shares","op":"ge","value":3000}]')
app.button(key='archive_query').click().run()
assert not app.exception
empty = pd.read_csv(BytesIO(exports['archive_csv']))
assert empty.empty and list(empty.columns)==list(filtered.columns)
app.run()
assert Path(update['approved_usage_ledger']).read_text()==ledger
print(json.dumps({'python':sys.executable,
 'packages':sorted(d.metadata['Name'] for d in importlib.metadata.distributions()),
 'job':jobs[0].job_id,'snapshot':ref.snapshot_id,'values':csv.to_dict('records'),'update':update,'external_requests':0},default=str))
""",
    )
    print(result)


def test_submit_while_resident_worker_holds_lock(isolated_data_env, source_facts):
    """Synthetic transport only; catches submission contending with the writer lock."""
    from functools import partial

    bounded_run = partial(run, timeout=30)
    env = isolated_data_env
    setup = SETUP[: SETUP.index("s = DataStore")] + SETUP[
        SETUP.index("r = DataRequest") : SETUP.index("ref = s.prepare")
    ]
    bounded_run(env, setup.replace("PAYLOAD", repr(source_facts.response().decode())) + """
Path('contracts.json').write_text(json.dumps({'products': {'ts_a_daily': {'max_days': 30}},
    'limits': {'approved_scope_ref': 'synthetic', 'provider_fetch': 1,
               'upstream_product_requests': 1, 'network_attempts': 1}}))
""")
    guard = """
import socket, json, time
from pathlib import Path
def forbidden(*a, **k):
    raise AssertionError('external network forbidden')
socket.create_connection = forbidden
socket.socket.connect = forbidden
from hetu_data.cli import main
"""
    worker_script = guard + f"""
from hetu_data.providers.tushare import TushareProvider
class Client:
    def query(self, api, params, fields):
        with Path('calls.jsonl').open('a') as out:
            out.write(json.dumps([api, params, fields]) + '\\n')
        return {source_facts.response()!r}
original = TushareProvider.__init__
def init(self, **kwargs):
    original(self, **dict(kwargs, client=Client()))
TushareProvider.__init__ = init
assert main(['worker', '--root', 'store']) == 0
"""
    with (env["workdir"] / "resident.log").open("w") as log:
        worker = subprocess.Popen(
            [str(env["python"]), "-c", worker_script], cwd=env["workdir"],
            env=env["env"], stdout=log, stderr=log,
        )
        try:
            bounded_run(env, guard + """
from hetu_data.locking import worker_lock
for _ in range(100):
    try:
        with worker_lock(Path('store')):
            pass
    except RuntimeError:
        break
    time.sleep(.05)
else:
    raise AssertionError('worker did not acquire lock')
assert main(['worker', '--root', 'store', '--once']) == 2
assert main(['plan', '--root', 'store', '--request', 'request.json',
             '--contracts', 'contracts.json', '--output', 'plan.json']) == 0
""")
            assert worker.poll() is None
            submit = guard + """
assert main(['submit', '--root', 'store', '--plan', 'plan.json',
             '--submission-id', 'resident']) == 0
"""
            first = json.loads(bounded_run(env, submit))
            assert json.loads(bounded_run(env, submit)) == first
            result = bounded_run(env, guard + """
from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import JobService
from hetu_data import DataStore, SnapshotRef
jobs = JobService(DataTaskStore(Path('store')))
for _ in range(200):
    records = jobs.list()
    if records and records[0].state in ('succeeded', 'failed'):
        break
    time.sleep(.05)
assert len(records) == 1 and records[0].state == 'succeeded', records
update = json.loads((records[0].output/'update.json').read_text())
store = DataStore(Path('store'))
assert len(update['snapshot_ids']) == 1
bars = store.read_table(SnapshotRef(update['snapshot_ids'][0], store.root), 'daily_bars')
assert bars.volume_shares.tolist() == [1234]
assert bars.amount_cny.tolist() == [56780]
assert len(Path('calls.jsonl').read_text().splitlines()) == 1
ledger = Path(update['approved_usage_ledger']).read_text()
assert '"type": "completed"' in ledger
print(json.dumps({'update': update, 'ledger': ledger, 'external_requests': 0}))
""")
            assert worker.poll() is None
            print(result)
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            print(f"resident worker reaped: {worker.returncode}")
