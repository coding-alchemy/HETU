from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from hetu_quant.factors import FactorRequest, TeachingSource, compute_factor


def test_up_amount_share_from_teaching(request, factor_evidence_root):
    from factor_assertions import checked

    day = date(2024, 4, 1)
    query = FactorRequest(
        "up_amount_share", ("SYN_A",), day, datetime(2024, 4, 1, 15, tzinfo=ZoneInfo("Asia/Shanghai")), {}
    )
    expected = dict(value=0.4, up_amount=400_000_000, total_amount=1_000_000_000, days=60)
    with checked(factor_evidence_root, request.node.nodeid, query.factor_id, "positive", expected) as actual:
        result = compute_factor(None, TeachingSource("up_amount_share_60d"), query)
        assert result.status == "available"
        details = result.rows[0]["components"]
        actual.update(
            value=result.rows[0]["value"],
            up_amount=details["up_amount"],
            total_amount=details["total_amount"],
            days=len(details["observations"]),
        )
        assert actual == expected


def test_up_amount_share_edges(tmp_path, request, factor_evidence_root):
    import json

    from factor_assertions import checked
    from hetu_quant.factors import close_cutoff
    from hetu_quant.factors.sources import teaching_document

    source = TeachingSource("up_amount_share_60d", tmp_path)
    document = teaching_document(TeachingSource(source.case_id))
    query = FactorRequest("up_amount_share", ("SYN_A",), date(2024, 4, 1), close_cutoff(date(2024, 4, 1)), {})

    def calculate():
        (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
        return compute_factor(None, source, query)

    with checked(
        factor_evidence_root, request.node.nodeid, query.factor_id, "boundary", [0.415, 0, "amount_cny"]
    ) as actual:
        bars = document["tables"]["daily_bars"]
        bars[-1]["close_raw"] = bars[-2]["close_raw"] + 1
        actual["changed"] = calculate().rows[0]["value"]
        assert actual["changed"] == pytest.approx(0.415)
        for bar in bars:
            bar["close_raw"] = 100
        actual["flat"] = calculate().rows[0]["value"]
        assert actual["flat"] == 0
        bars[-1].pop("amount_cny")
        missing = calculate()
        actual["missing"] = missing.gaps
        assert missing.status == "unavailable"
        assert (missing.gaps[0]["field"], missing.gaps[0]["period"]) == ("amount_cny", "2024-04-01")
        for bar in bars:
            bar["amount_cny"] = 0
        assert calculate().gaps[0]["field"] == "amount_cny"
        del bars[-2]
        assert calculate().gaps[0]["reason"] == "交易日行情缺失"
        document["tables"]["security_status"] = [
            {
                "code": "SYN_A",
                "date": document["tables"]["calendar"][-2]["date"],
                "available_at": "2024-04-01T09:00:00+08:00",
                "is_suspended": True,
            }
        ]
        assert calculate().gaps[0]["reason"] == "已证停牌"


def test_selftest_records_follow_actual_assertions(tmp_path):
    from factor_assertions import checked
    from hetu_quant.factors.selftest import read_selftest

    def state(params=None, version="1"):
        return read_selftest(tmp_path, "up_amount_share", version, params or {"window": 60})["status"]

    assert state() == "untested"
    with checked(tmp_path, "positive-node", "up_amount_share", "positive", 0.4) as actual:
        actual["value"] = 0.4
        assert actual["value"] == 0.4
    assert state() == "untested"
    with pytest.raises(AssertionError), checked(tmp_path, "boundary-node", "up_amount_share", "boundary", 0.4):
        assert 0.415 == 0.4
    assert state() == "failed"
    with pytest.raises(RuntimeError), checked(tmp_path, "local-node", "up_amount_share", "local", 0.4):
        raise RuntimeError("cannot read facts")
    assert state() == "failed"
    for case in ("boundary", "local"):
        with checked(tmp_path, case, "up_amount_share", case, 0.4) as actual:
            actual["value"] = 0.4
            assert actual["value"] == 0.4
    assert state() == "passed"
    assert state({"window": 5}) == "untested"
    assert state(version="2") == "untested"


def test_local_factor_cutoff_and_permission(tmp_path, request, factor_evidence_root):
    from factor_assertions import checked
    from factor_cases import local_volume_source
    from hetu_data.library import publish_library
    from hetu_quant.factors import close_cutoff

    day = date(2024, 4, 1)
    query = FactorRequest("up_amount_share", ("SYN_A",), day, close_cutoff(day), {})
    with checked(
        factor_evidence_root, request.node.nodeid, query.factor_id, "local", [0.4, 0.4, "amount_cny"]
    ) as actual:
        store, ref = local_volume_source(tmp_path / "positive", late=True)
        library = publish_library(
            store,
            asset_snapshots=(
                dict(
                    root=str(store.root),
                    snapshot_id=ref.snapshot_id,
                    tables=["daily_bars", "calendar", "knowledge_evidence"],
                ),
            ),
        )
        actual["positive"] = [compute_factor(store, source, query).rows[0]["value"] for source in (ref, library)]
        assert actual["positive"] == [0.4, 0.4]
        from dataclasses import replace

        after_revision = compute_factor(store, ref, replace(query, cutoff=query.cutoff.replace(hour=19)))
        assert after_revision.status == "unavailable"
        assert after_revision.gaps[0]["field"] == "amount_cny"

        for missing in ("amount_cny", "late", "date-only"):
            store, ref = local_volume_source(tmp_path / missing, missing=missing)
            result = compute_factor(store, ref, query)
            actual[missing] = result.gaps
            assert result.status == "unavailable"
            assert result.gaps[0]["field"] == "amount_cny"
        store, ref = local_volume_source(tmp_path / "denied", deny=True)
        result = compute_factor(store, ref, query)
        assert result.status == "unavailable"
        assert any("许可" in gap["reason"] for gap in result.gaps)


def test_selftest_stays_failed_in_pytest_subprocess(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from hetu_quant.factors.selftest import read_selftest

    script = tmp_path / "test_child_receipt.py"
    script.write_text(
        "from pathlib import Path\n"
        "from factor_assertions import checked\n"
        "def test_wrong():\n"
        "    with checked(Path('receipts'), 'child', 'up_amount_share', 'positive', 0.4) as actual:\n"
        "        actual['value'] = 0.415\n"
        "        assert actual['value'] == 0.4\n"
        "def test_throws():\n"
        "    with checked(Path('receipts'), 'child', 'up_amount_share', 'local', 0.4):\n"
        "        raise RuntimeError('input unreadable')\n"
    )
    environment = dict(os.environ)
    old_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).parent), old_path])
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
    )
    assert process.returncode == 1, process.stdout.decode()
    result = read_selftest(tmp_path / "receipts", "up_amount_share", "1", {"window": 60})
    assert result["status"] == "failed"
    assert result["cases"][0]["actual"] == {"value": 0.415}
    assert result["cases"][1]["failure"] == "input unreadable"


def test_up_amount_uses_comparable_closes_on_split(tmp_path):
    import json
    from dataclasses import replace

    from factor_cases import example_query

    source, document, query = example_query("up_amount_share")
    docs = document["tables"]
    docs["daily_bars"] = docs["daily_bars"][-2:]
    docs["calendar"] = docs["calendar"][-2:]
    left, right = docs["daily_bars"]
    left.update(close_raw=10, close=10)
    right.update(close_raw=5.5, close=11)
    docs["corporate_actions"] = [
        dict(code="SYN_A", ex_date=right["date"], m=2, d=0, available_at=right["available_at"])
    ]
    path = tmp_path / f"{source.case_id}.json"
    path.write_text(json.dumps(document))
    local = TeachingSource(source.case_id, tmp_path)
    query = replace(query, parameters={"window": 1})
    result = compute_factor(None, local, query)
    assert result.rows[0]["value"] == 1
    right.pop("close")
    docs.pop("action_coverage")
    path.write_text(json.dumps(document))
    result = compute_factor(None, local, query)
    assert result.status == "unavailable"
    assert result.gaps[0]["field"] == "close"


def test_identical_approved_assets_in_library_corroborate_facts(tmp_path):
    from factor_cases import example_query, local_volume_source
    from hetu_data.library import publish_library

    _, _, query = example_query("up_amount_share")
    left, a = local_volume_source(tmp_path / "left")
    right, b = local_volume_source(tmp_path / "right")
    library = publish_library(
        left,
        asset_snapshots=tuple(
            dict(
                root=str(store.root),
                snapshot_id=ref.snapshot_id,
                tables=["daily_bars", "calendar", "knowledge_evidence"],
            )
            for store, ref in ((left, a), (right, b))
        ),
    )
    result = compute_factor(left, library, query)
    assert result.rows and result.rows[0]["value"] == pytest.approx(0.4)
    proof = result.rows[0]["inputs"][-1]["field_evidence"]["amount_cny"]
    assert {entry["snapshot_id"] for entry in proof["corroborating_sources"]} == {a.snapshot_id, b.snapshot_id}
