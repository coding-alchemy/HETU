"""离线 wheel 行为；只有合成传输输入进入子环境。"""

import subprocess


def run(env, script, *, timeout=None):
    result = subprocess.run(
        [str(env["python"]), "-c", script], cwd=env["workdir"], env=env["env"], text=True, capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_base_wheel_prepares_and_reopens_without_quant(isolated_data_env, source_facts):
    env = isolated_data_env
    payload = source_facts.response().decode()
    script = SETUP.replace("PAYLOAD", repr(payload))
    snapshot = run(env, script).strip()
    run(
        env,
        "from hetu_data import DataStore, SnapshotRef;"
        "from pathlib import Path;"
        "s=DataStore(Path('store'));"
        f"r=SnapshotRef({snapshot!r},s.root);"
        "f=s.read_table(r,'daily_bars');"
        "assert f.iloc[0].volume_shares==1234;"
        "assert f.iloc[0].amount_cny==56780;"
        "assert s.load_manifest(r)['items'][0]['source']['name']=='tushare'",
    )


SETUP = """
import sys, json
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo


class Guard:
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in (
            "hetu_quant",
            "research",
            "rqalpha",
            "akshare",
            "baostock",
            "curl_cffi",
        ):
            raise ImportError(fullname)


sys.meta_path.insert(0, Guard())
import hetu_data

assert str(Path(sys.prefix)) in hetu_data.__file__
assert sys.prefix != sys.base_prefix
assert "include-system-site-packages = false" in (Path(sys.prefix) / "pyvenv.cfg").read_text()
from importlib.resources import files

assert files("hetu_data").joinpath("resources/data-products.json").is_file()
from hetu_data import DataStore, DataRequest
from hetu_data.models import data_request_to_dict
from hetu_data.providers.tushare import TushareProvider


class Client:
    def query(self, api, params, fields):
        return PAYLOAD.encode()


root = Path("store")
root.mkdir(exist_ok=True)
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
(root / "data-entitlements.json").write_text(json.dumps({"grants": [grant]}))
s = DataStore(root, providers={"tushare": TushareProvider(client=Client())})
r = DataRequest(
    ("ts_a_daily",),
    ("SYNTH.SH",),
    date(2026, 1, 5),
    date(2026, 1, 5),
    datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
    "backtest_low_volatility",
    mode="paid",
)
Path("request.json").write_text(json.dumps(data_request_to_dict(r)))
Path("contracts.json").write_text("{}")
ref = s.prepare(r)
Path("snapshot").write_text(ref.snapshot_id)
print(ref.snapshot_id)
"""


def test_cli_plan_and_query_are_offline(isolated_data_env, source_facts):
    env = isolated_data_env
    run(env, SETUP.replace("PAYLOAD", repr(source_facts.response().decode())))
    run(
        env,
        """
import json, socket
from pathlib import Path
from hetu_data.cli import main
from hetu_data import DataStore, SnapshotRef
from hetu_data.models import data_request_from_dict
from hetu_data.updates import plan_update


def forbidden(*args, **kwargs):
    raise AssertionError("network forbidden")


socket.socket = forbidden
assert (
    main(
        [
            "plan",
            "--root",
            "store",
            "--request",
            "request.json",
            "--contracts",
            "contracts.json",
            "--output",
            "plan.json",
        ]
    )
    == 0
)
s = DataStore(Path("store"))
assert json.loads(Path("plan.json").read_text()) == plan_update(
    data_request_from_dict(json.loads(Path("request.json").read_text())), s, {}
)
assert not (Path("store") / "jobs").exists()
assert (
    main(
        [
            "query",
            "--root",
            "store",
            "--snapshot",
            Path("snapshot").read_text(),
            "--codes",
            "SYNTH.SH",
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-05",
            "--columns",
            "volume_shares,amount_cny",
            "--output",
            "out.csv",
        ]
    )
    == 0
)
from datetime import date

expected = s.read_range(
    SnapshotRef(Path("snapshot").read_text(), s.root),
    "daily_bars",
    codes=("SYNTH.SH",),
    start=date(2026, 1, 5),
    end=date(2026, 1, 5),
    columns=("volume_shares", "amount_cny"),
)
assert Path("out.csv").read_text() == expected.to_csv(index=False)
""",
    )


def test_cli_requires_root_and_reports_bad_selection(isolated_data_env, source_facts):
    env = isolated_data_env
    run(env, SETUP.replace("PAYLOAD", repr(source_facts.response().decode())))
    base = [
        str(env["cli"]),
        "query",
        "--snapshot",
        (env["workdir"] / "snapshot").read_text(),
        "--codes",
        "SYNTH.SH",
        "--start",
        "2026-01-05",
        "--end",
        "2026-01-05",
        "--columns",
        "volume_shares",
        "--output",
        "out.csv",
    ]
    for extra in [[], ["--root", "store", "--columns", "invalid"], ["--root", "store", "--start", "2026-01-06"]]:
        result = subprocess.run(base + extra, cwd=env["workdir"], env=env["env"], capture_output=True, text=True)
        assert result.returncode != 0
        assert result.stderr
    # 成功空使用已授权代码的未覆盖日期。
    result = subprocess.run(
        base + ["--root", "store", "--start", "2026-01-06", "--end", "2026-01-06"],
        cwd=env["workdir"],
        env=env["env"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (env["workdir"] / "out.csv").read_text() == "volume_shares\n"
