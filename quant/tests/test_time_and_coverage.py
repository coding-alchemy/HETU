"""用途资格、共同截止日与历史资格：未来成员、缺末日、停牌/缺失分离、晚修订、市场覆盖。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fixtures import make_case
from hetu_quant.data import DataStore
from hetu_quant.models import DataRequest

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
ALL_PRODUCTS = (
    "test_calendar",
    "test_daily",
    "test_status",
    "test_boards",
    "test_members",
    "test_index",
    "test_fin",
)
ALL_CODES = ("AAA.SH", "BBB.SZ", "CCC.SZ")


def _build_store(tmp_path: Path, variant: str) -> tuple[DataStore, object]:
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps(make_case("product_config_full", "base"), ensure_ascii=False),
        encoding="utf-8",
    )
    store = DataStore(tmp_path / "store", config_path=config_path)
    case = make_case("snapshot_csvs", variant)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(case["manifest"], ensure_ascii=False), encoding="utf-8")
    files = []
    for name, content in case["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))
    return store, ref


def _request(
    codes: tuple[str, ...] = ALL_CODES,
    purpose: str = "ranking_current",
    products: tuple[str, ...] = ALL_PRODUCTS,
    as_of: datetime = AS_OF,
) -> DataRequest:
    return DataRequest(
        product_ids=products,
        codes=codes,
        start=date(2024, 6, 3),
        end=date(2024, 6, 28),
        as_of=as_of,
        purpose=purpose,
    )


def test_base_all_eligible_and_suspension_not_missing(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "base")
    report = store.qualify(_request(), ref)
    assert report.can_run
    assert set(report.eligible_ids) == set(ALL_CODES)
    assert "BBB.SZ" not in report.excluded
    assert any(
        "BBB.SZ" in item and "suspended" in item for item in report.limitations
    ), "停牌与采集缺失必须分开记录"


def test_member_available_after_as_of_not_eligible(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "member_after_as_of")
    report = store.qualify(_request(), ref)
    assert report.eligible_ids == ()
    for code in ALL_CODES:
        assert "member_not_available_at_as_of" in report.excluded[code]


def test_missing_latest_session_excluded(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "missing_latest_day")
    report = store.qualify(_request(), ref)
    assert "missing_latest_session" in report.excluded["CCC.SZ"]
    assert "AAA.SH" in report.eligible_ids
    assert "BBB.SZ" in report.eligible_ids


def test_financial_failure_does_not_block_ranking(tmp_path: Path) -> None:
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps(make_case("product_config_full", "base"), ensure_ascii=False),
        encoding="utf-8",
    )
    store = DataStore(tmp_path / "store", config_path=config_path)
    case = make_case("snapshot_csvs", "base")
    case["manifest"]["items"] = [
        item for item in case["manifest"]["items"] if item["product_id"] != "test_fin"
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(case["manifest"], ensure_ascii=False), encoding="utf-8")
    files = []
    for name, content in case["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))

    ranking = store.qualify(_request(purpose="ranking_current"), ref)
    assert ranking.can_run
    assert set(ranking.eligible_ids) == set(ALL_CODES)
    assert any("financial_reports" in item for item in ranking.limitations)

    ep = store.qualify(_request(purpose="ep_value_preparation"), ref)
    assert not ep.can_run


def test_late_revision_does_not_overwrite_known_version(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "late_revision")
    report = store.qualify(_request(purpose="ep_value_preparation"), ref)
    assert "AAA.SH" in report.eligible_ids
    assert any("late_revision_ignored" in item for item in report.limitations)


def test_bj_code_reported_as_uncovered_market(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "bj_uncovered")
    report = store.qualify(_request(codes=ALL_CODES + ("830001.BJ",)), ref)
    assert "market_not_covered" in report.excluded["830001.BJ"]
    assert any("BJ" in item for item in report.limitations)


def test_historical_purpose_rejects_current_snapshot_membership(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "base")
    report = store.qualify(_request(purpose="ranking_historical"), ref)
    assert not report.can_run
    for code in ALL_CODES:
        assert "no_pit_membership" in report.excluded[code]


def test_snapshot_obtained_after_as_of_cannot_serve_earlier_task(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "base")
    manifest_path = (
        tmp_path / "store" / "snapshots" / ref.snapshot_id / "manifest.json"  # type: ignore[attr-defined]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["items"]:
        item["source"]["obtained_at"] = "2024-07-05T09:00:00+08:00"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    report = store.qualify(_request(), ref)
    assert not report.can_run
    assert any("obtained_after_as_of" in item for item in report.limitations)


def test_qualification_records_survive_as_of_and_path_changes(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "base")
    first = store.qualify(_request(), ref)
    qual_dir = tmp_path / "store" / "qualification"
    first_files = sorted(qual_dir.glob("qual-*.json"))
    assert len(first_files) == 1
    first_bytes = first_files[0].read_bytes()

    later = _request(as_of=datetime(2024, 8, 1, 9, 0, tzinfo=CN))
    second = store.qualify(later, ref)
    assert second.can_run == first.can_run
    remaining = sorted(qual_dir.glob("qual-*.json"))
    assert len(remaining) == 2
    assert first_files[0].read_bytes() == first_bytes, "旧资格记录不被新请求撤销或改写"


def test_empty_codes_rejected_for_market_product(tmp_path: Path) -> None:
    store, ref = _build_store(tmp_path, "base")
    with pytest.raises(ValueError, match="codes required"):
        store.qualify(_request(codes=(), products=("test_daily",)), ref)
    report = store.qualify(
        _request(codes=(), products=("test_calendar", "test_boards")), ref
    )
    assert report.can_run


def test_ranking_entry_rejects_non_cn_a_market() -> None:
    from hetu_quant.models import RankingDefinition

    with pytest.raises(ValueError, match="market"):
        RankingDefinition(
            as_of=AS_OF,
            cutoff=date(2024, 6, 28),
            snapshot_id="snap-x",
            benchmark_id="csi300_price",
            market="HK",
        )
