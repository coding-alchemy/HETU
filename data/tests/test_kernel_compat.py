import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def test_kernel_runs_without_research_imports(tmp_path, source_facts):
    from hetu_data import DataRequest, DataStore
    from hetu_data.providers.tushare import TushareProvider

    if not os.environ.get("HETU_KERNEL_CHILD"):
        script = """
import sys, importlib.util
from pathlib import Path


class Guard:
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in ("hetu_quant", "research", "rqalpha"):
            raise ImportError(fullname)


sys.meta_path.insert(0, Guard())


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load("kernel_test", sys.argv[1])
facts = load("facts", sys.argv[2])
module.test_kernel_runs_without_research_imports(Path(sys.argv[3]), facts)
"""
        subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(Path(__file__).resolve()),
                str(Path(__file__).resolve().parents[2] / "quant/tests/source_cases.py"),
                str(tmp_path),
            ],
            env={**os.environ, "HETU_KERNEL_CHILD": "1"},
            check=True,
        )
        return
    root = tmp_path / "store"
    root.mkdir()
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
    store = DataStore(root, providers={"tushare": TushareProvider(client=source_facts.SourceClient())})
    req = DataRequest(
        ("ts_a_daily",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 5),
        datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
        mode="paid",
    )
    ref = store.prepare(req)
    frame = DataStore(root).read_range(
        ref, "daily_bars", codes=req.codes, start=req.start, end=req.end, columns=("volume_shares", "amount_cny")
    )
    assert frame.iloc[0].tolist() == [1234, 56780]
    assert store.load_manifest(ref)["items"][0]["source"]["name"] == "tushare"


def test_local_import_needs_no_source_extra(tmp_path, monkeypatch):
    import builtins

    from hetu_data import DataStore

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in ("akshare", "baostock", "curl_cffi"):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    store = DataStore(tmp_path / "store", providers={"plain": object()})
    assert store._provider("plain") is not None
    product = dict(store.product("ts_a_daily"))
    product.update(
        product_id="synthetic_local", provider="plain", fee_class="free", permission={"status": "declared_free"}
    )
    product.pop("storage_layout", None)
    store.config["products"]["synthetic_local"] = product
    csv = tmp_path / "bars.csv"
    csv.write_text(
        "date,code,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n2026-01-05,SYNTH.SH,10,11,9,10,1234,56780\n"
    )
    manifest = tmp_path / "import.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "free",
                "items": [
                    {
                        "product_id": "synthetic_local",
                        "table": "daily_bars",
                        "files": [csv.name],
                        "source": {
                            "name": "local_file",
                            "fee_class": "free",
                            "permission_basis": "synthetic",
                            "obtained_at": "2026-10-25T09:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    ref = store.import_files(manifest, (csv,))
    assert float(store.read_table(ref, "daily_bars").iloc[0].volume_shares) == 1234
    import pytest

    from hetu_data.providers.akshare import AKShareProvider

    with pytest.raises(ImportError, match="akshare"):
        AKShareProvider().fetch("ak_a_daily", ("SYNTH.SH",), date(2026, 1, 5), date(2026, 1, 5))


def test_config_precedence_survives_package_split(tmp_path, monkeypatch):
    from hetu_data import DataStore

    root = tmp_path / "root"
    root.mkdir()
    default = DataStore(root).config
    root_value = {**default, "synthetic_marker": "root"}
    (root / "data-products.json").write_text(json.dumps(root_value))
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({**default, "synthetic_marker": "explicit"}))
    monkeypatch.chdir(tmp_path)
    assert DataStore(root, explicit).config["synthetic_marker"] == "explicit"
    assert DataStore(root).config["synthetic_marker"] == "root"
    assert DataStore(tmp_path / "other").config == default
