"""二期 01.2 历史时点证据与按用途资格测试。

合成输入只含事实（证券历史、修订链、公开时点）；资格结论与所选版本
的期望留在本测试侧，不随生产实现反生成。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtest_cases import build_case_store as _build_store
from backtest_cases import case_definition as _definition
from backtest_cases import make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant.backtest_data import _signal_days, prepare_backtest, select_known_versions

CN = ZoneInfo("Asia/Shanghai")


# ---- test_historical_knowledge_proof ----


def _version_frame() -> pd.DataFrame:
    """同一记录的三个版本事实：早年公开的原版（今天取得）、晚修订、无证据版本、被篡改版本。"""
    return pd.DataFrame(
        [
            {  # 原版：2020 年公开，今天才取得——对 2021 年时点可用
                "record_key": "X",
                "revision_id": "original",
                "supersedes": "",
                "published_at": "2020-03-31T18:00:00+08:00",
                "verified": True,
                "value": 1.0,
            },
            {  # 晚修订：2022 年公开——对 2021 年时点不可用
                "record_key": "X",
                "revision_id": "revised",
                "supersedes": "original",
                "published_at": "2022-05-01T10:00:00+08:00",
                "verified": True,
                "value": 2.0,
            },
            {  # 无证据版本：无公开时点——任何时点不可用
                "record_key": "X",
                "revision_id": "unevidenced",
                "supersedes": "revised",
                "published_at": "",
                "verified": True,
                "value": 3.0,
            },
            {  # 篡改版本：核验失败——任何时点不可用
                "record_key": "Y",
                "revision_id": "tampered",
                "supersedes": "",
                "published_at": "2020-01-01T00:00:00+08:00",
                "verified": False,
                "value": 9.0,
            },
        ]
    )


def test_historical_knowledge_proof() -> None:
    frame = _version_frame()
    at_2021 = datetime(2021, 6, 30, 21, 0, tzinfo=CN)
    chosen = select_known_versions(frame, at_2021, keys=("record_key",))
    chosen_x = chosen[chosen["record_key"] == "X"]
    assert chosen_x["revision_id"].tolist() == ["original"]

    # 晚修订公开后，当时版本切换为修订版
    at_2023 = datetime(2023, 1, 15, 21, 0, tzinfo=CN)
    chosen_later = select_known_versions(frame, at_2023, keys=("record_key",))
    assert chosen_later[chosen_later["record_key"] == "X"]["revision_id"].tolist() == ["revised"]

    # 篡改证据（verified=False）的记录任何时点都不得入选
    assert "tampered" not in chosen_later["revision_id"].tolist()
    assert "unevidenced" not in chosen_later["revision_id"].tolist()


def test_historical_knowledge_proof_rejects_missing_columns() -> None:
    frame = pd.DataFrame([{"record_key": "X", "revision_id": "orig", "value": 1.0}])
    with pytest.raises(ValueError, match="published_at"):
        select_known_versions(
            frame, datetime(2021, 6, 30, 21, 0, tzinfo=CN), keys=("record_key",)
        )


# ---- test_historical_universe_not_current_survivors ----


def test_historical_universe_not_current_survivors(tmp_path: Path) -> None:
    case = make_backtest_case("history")
    store, ref = _build_store(tmp_path, case)
    annotate_actions_coverage(
        store, ref, "2020-01-01", "2024-06-30", codes=list(case["codes"]), query_basis="ex_date"
    )
    definition = _definition(case, ref.snapshot_id)
    report = prepare_backtest(definition, store, tmp_path / "bundle")
    assert report.can_run

    universe = pd.read_parquet(tmp_path / "bundle" / "universe.parquet")
    historical_codes = set(universe["code"])
    assert "DELISTED_LATER" in historical_codes
    assert "NOT_YET_LISTED" not in historical_codes

    # 退市对象在退市后的信号日不再出现；退市前仍在池
    first_signal = min(universe["signal_date"])
    early = universe[universe["signal_date"] == first_signal]
    assert "DELISTED_LATER" in set(early["code"])
    late = universe[universe["signal_date"] > "2022-06-30"]
    assert "DELISTED_LATER" not in set(late["code"])

    # 只有当前幸存证券（无历史证券表）的快照不能充当历史股票池证据
    survivors_only = _build_store(
        tmp_path / "s2",
        case,
        tables=("calendar", "daily_bars", "security_status", "corporate_actions", "market_cap"),
    )
    store2, ref2 = survivors_only
    annotate_actions_coverage(
        store2, ref2, "2020-01-01", "2024-06-30", codes=list(case["codes"]), query_basis="ex_date"
    )
    report2 = prepare_backtest(_definition(case, ref2.snapshot_id), store2, tmp_path / "bundle2")
    assert not report2.can_run
    assert any("historical_securities" in note for note in report2.limitations)


def test_backtest_case_rejects_unknown_selection() -> None:
    with pytest.raises(ValueError, match="unknown backtest case"):
        make_backtest_case("insider_trading")
    with pytest.raises(ValueError, match="unknown variant"):
        make_backtest_case("history", variant="cheat")


# ---- 复评反例：逐对象窗口数据、证据原件、月末证明 ----


def test_universe_requires_per_object_window_data(tmp_path: Path) -> None:
    """删除 KEEP_A 全部行情：该股不得入池，排除理由按对象保留，不得表级放行。"""
    case = make_backtest_case("history")
    csvs = dict(case["csvs"])  # type: ignore[arg-type]
    bars_text = csvs["daily_bars"]
    header, *rows = bars_text.splitlines()
    csvs["daily_bars"] = header + "\n" + "\n".join(
        line for line in rows if ",KEEP_A," not in line
    ) + "\n"
    case = {**case, "csvs": csvs}
    store, ref = _build_store(tmp_path, case)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    report = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "bundle")

    universe = pd.read_parquet(tmp_path / "bundle" / "universe.parquet")
    assert "KEEP_A" not in set(universe["code"])
    assert "KEEP_A" not in report.eligible_ids
    assert any("bars_missing" in reason for reason in report.excluded.get("KEEP_A", ()))
    # 其余合格对象不受影响：DELISTED_LATER 退市前仍在池
    assert "DELISTED_LATER" in set(universe["code"])


def test_window_interior_gap_is_not_hidden_by_endpoints(tmp_path: Path) -> None:
    """删除窗口内部一个月末点、保留首尾：不得凭首尾核验放行；
    采集缺失与有证据停牌须区分理由。"""
    case = make_backtest_case("history")
    coverage = case["actions_coverage"]  # type: ignore[index]
    gap_bar = "2021-06-30,KEEP_A,10.0,10.0,10.0,10.0,10.0,10.0,1000,20000"
    csvs = dict(case["csvs"])  # type: ignore[arg-type]
    header, *rows = csvs["daily_bars"].splitlines()
    csvs["daily_bars"] = header + "\n" + "\n".join(
        line for line in rows if line != gap_bar
    ) + "\n"
    gap_case = {**case, "csvs": csvs}
    store, ref = _build_store(tmp_path / "gap", gap_case)
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    report = prepare_backtest(
        _definition(gap_case, ref.snapshot_id), store, tmp_path / "gap" / "bundle"
    )

    universe = pd.read_parquet(tmp_path / "gap" / "bundle" / "universe.parquet")
    keepa_days = set(universe[universe["code"] == "KEEP_A"]["signal_date"])
    assert "2021-07-31" not in keepa_days  # 缺口落在该信号窗口内
    assert any(
        "bars_collection_gap" in reason and "2021-06-30" in reason
        for reason in report.excluded.get("KEEP_A", ())
    )
    # 窗口滑过缺口后重新入池：2022-07-31 窗口起点 2021-07-01，不再要求 2021-06-30
    assert "2022-07-31" in keepa_days

    # 同一缺口但 security_status 登记停牌：理由须区分，不得记为采集缺失
    csvs2 = dict(csvs)
    sheader, *srows = csvs2["security_status"].splitlines()
    csvs2["security_status"] = sheader + "\n" + "\n".join(
        "2021-06-30,KEEP_A,False,True,True"
        if line == "2021-06-30,KEEP_A,False,False,True"
        else line
        for line in srows
    ) + "\n"
    susp_case = {**case, "csvs": csvs2}
    store2, ref2 = _build_store(tmp_path / "susp", susp_case)
    annotate_actions_coverage(
        store2, ref2, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    report2 = prepare_backtest(
        _definition(susp_case, ref2.snapshot_id), store2, tmp_path / "susp" / "bundle"
    )
    reasons2 = report2.excluded.get("KEEP_A", ())
    assert any("bars_suspended_at_required_point" in reason for reason in reasons2)
    assert not any("bars_collection_gap" in reason for reason in reasons2)


def test_late_revision_bad_evidence_is_not_retroactive(tmp_path: Path) -> None:
    """删除 2022-07 才公开的修订原件：此前信号日的有效原版不得被追溯排除；
    修订公开后所选版本原件未核验，对应信号日起排除。"""
    case = make_backtest_case("history")
    store, ref = _build_store(tmp_path, case)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    (store.root / "evidence" / "ev-hist-del-rev2").unlink()  # 修订（2022-07-05 公开）原件缺失
    report = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "bundle")

    universe = pd.read_parquet(tmp_path / "bundle" / "universe.parquet")
    delisted_days = set(universe[universe["code"] == "DELISTED_LATER"]["signal_date"])
    assert "2021-07-31" in delisted_days  # 修订公开前：有效原版仍可用
    assert "2022-07-31" not in delisted_days  # 修订公开后：所选版本原件未核验
    assert any(
        "evidence_original_unverified" in reason
        for reason in report.excluded.get("DELISTED_LATER", ())
    )
    # 未受影响对象不受波及
    assert "KEEP_A" in set(universe["code"])


def test_evidence_originals_verified_at_entry(tmp_path: Path) -> None:
    """原件被实际篡改字节、原件缺失、哈希为占位文本：均不得放行受影响对象。"""
    case = make_backtest_case("history")
    coverage = case["actions_coverage"]  # type: ignore[index]

    def build_and_prepare(root: Path, mutate) -> tuple[object, pd.DataFrame]:
        store, ref = _build_store(root, case)
        annotate_actions_coverage(
            store, ref, coverage["start"], coverage["end"],
            codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
        )
        mutate(store)
        report = prepare_backtest(
            _definition(case, ref.snapshot_id), store, root / "bundle"
        )
        universe = pd.read_parquet(root / "bundle" / "universe.parquet")
        return report, universe

    # 实际篡改原件字节
    def tamper(store) -> None:
        (store.root / "evidence" / "ev-hist-keepa-orig").write_bytes(b"tampered bytes")

    report, universe = build_and_prepare(tmp_path / "tampered", tamper)
    assert "KEEP_A" not in set(universe["code"])
    assert any(
        "evidence_original_unverified" in reason
        for reason in report.excluded.get("KEEP_A", ())
    )

    # 原件不存在
    def remove(store) -> None:
        (store.root / "evidence" / "ev-hist-keepa-orig").unlink()

    report2, universe2 = build_and_prepare(tmp_path / "missing", remove)
    assert "KEEP_A" not in set(universe2["code"])
    assert any(
        "evidence_original_unverified" in reason
        for reason in report2.excluded.get("KEEP_A", ())
    )

    # 哈希为占位文本（未实际核验的登记）
    csvs = dict(case["csvs"])  # type: ignore[arg-type]
    lines = []
    for line in csvs["knowledge_evidence"].splitlines():
        if ",KEEP_A,orig," in line:
            parts = line.split(",")
            parts[6] = "placeholder-not-a-hash"
            line = ",".join(parts)
        lines.append(line)
    csvs["knowledge_evidence"] = "\n".join(lines) + "\n"
    placeholder_case = {**case, "csvs": csvs}
    store3, ref3 = _build_store(tmp_path / "placeholder", placeholder_case)
    annotate_actions_coverage(
        store3, ref3, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    report3 = prepare_backtest(
        _definition(placeholder_case, ref3.snapshot_id), store3, tmp_path / "placeholder" / "bundle"
    )
    universe3 = pd.read_parquet(tmp_path / "placeholder" / "bundle" / "universe.parquet")
    assert "KEEP_A" not in set(universe3["code"])
    assert any(
        "evidence_original_unverified" in reason
        for reason in report3.excluded.get("KEEP_A", ())
    )


def test_signal_days_require_proven_month_end() -> None:
    """月中截止日不得冒充月末：先证明真实月末，再筛选研究范围。"""
    days = pd.date_range("2024-01-01", "2024-03-01", freq="D")
    calendar = pd.DataFrame(
        {"date": [str(day.date()) for day in days], "is_trading_day": True}
    )
    # 完整日历含 2 月 29 日，截止 2 月 15 日：不得产生 2 月 15 日信号
    signals = _signal_days(calendar, date(2024, 2, 1), date(2024, 2, 15))
    assert signals == ["2024-01-31"]

    # 日历自身截断在月内：无法证明 2 月完整，同样不得产生 2 月信号
    truncated = calendar[calendar["date"] <= "2024-02-15"]
    assert _signal_days(truncated, date(2024, 2, 1), date(2024, 2, 15)) == ["2024-01-31"]


def test_qualification_is_use_specific(tmp_path: Path) -> None:
    case = make_backtest_case("history")
    store, ref = _build_store(tmp_path, case)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",
    )

    # 财务链缺失只阻断 E/P，不阻断低波动
    lowvol_quality = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "b1")
    ep_quality = prepare_backtest(
        _definition(case, ref.snapshot_id, strategy_id="ep_value"), store, tmp_path / "b2"
    )
    assert lowvol_quality.can_run and not ep_quality.can_run
    assert any("financial_reports" in note for note in ep_quality.limitations)

    # 原公告年查询证据不能充当实施日（除息日）覆盖
    store2, ref2 = _build_store(tmp_path / "s2", case)
    annotate_actions_coverage(
        store2, ref2, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="announcement_year",
    )
    ann_quality = prepare_backtest(_definition(case, ref2.snapshot_id), store2, tmp_path / "b3")
    assert not ann_quality.can_run
    assert any("actions_not_proven" in reason for reason in ann_quality.excluded.get("KEEP_A", ()))

    # 未知策略不得静默映射到任一用途
    with pytest.raises(ValueError, match="strategy_id"):
        prepare_backtest(
            _definition(case, ref.snapshot_id, strategy_id="momentum_x"), store, tmp_path / "b4"
        )


def test_late_unrelated_domain_does_not_veto_snapshot(tmp_path: Path) -> None:
    """未用于本研究的晚取得域（财务）不得否决全快照；E/P 需要它时仍被阻断。"""
    case = make_backtest_case("history")
    csvs = dict(case["csvs"])  # type: ignore[arg-type]
    csvs["financial_reports"] = (
        "code,report_period,ann_date,available_at\n"
        "KEEP_A,2020-12-31,2021-03-31,2021-03-31T18:00:00+08:00\n"
    )
    table_products = dict(case["table_products"])  # type: ignore[arg-type]
    table_products["financial_reports"] = ("test_bt_financials", "fundamental")
    case = {**case, "csvs": csvs, "table_products": table_products}
    late = {"financial_reports": "2024-07-01T10:00:00+08:00"}  # 晚于 as_of 2024-06-30
    store, ref = _build_store(tmp_path, case, obtained_overrides=late)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",
    )
    lowvol_quality = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "b1")
    assert lowvol_quality.can_run

    ep_quality = prepare_backtest(
        _definition(case, ref.snapshot_id, strategy_id="ep_value"), store, tmp_path / "b2"
    )
    assert not ep_quality.can_run
    assert any("obtained_after_as_of" in note for note in ep_quality.limitations)


def test_public_versions_accept_mixed_iso_precision_independent_of_row_order():
    frame = _version_frame().iloc[[1, 0, 2, 3]].copy()
    frame.loc[frame.revision_id == 'revised', 'published_at'] = '2022-05-01T10:00:00.123456+08:00'
    chosen = select_known_versions(frame, datetime(2021, 6, 30, 21, tzinfo=CN), ('record_key',))
    assert chosen.revision_id.tolist() == ['original']
    reversed_input = select_known_versions(frame.iloc[::-1], datetime(2021, 6, 30, 21, tzinfo=CN), ('record_key',))
    assert reversed_input.revision_id.tolist() == ['original']


def test_import_reuses_proven_assets_without_handwritten_manifest(tmp_path, monkeypatch):
    from backtest_cases import build_prepared_case_store, make_backtest_case

    case = make_backtest_case('formal', 'workflow')
    store, original = build_prepared_case_store(tmp_path/'inputs', case, monkeypatch)
    original_doc = store.load_manifest(original)
    product = case['table_products']['market_cap'][0]
    csv = tmp_path/'new-market-cap.csv'
    csv.write_text(case['csvs']['market_cap'])
    manifest = tmp_path/'new-import.json'
    manifest.write_text(json.dumps(dict(mode='free', base_snapshot_ids=[original.snapshot_id], items=[{
        'product_id':product, 'table':'market_cap', 'files':[csv.name],
        'source':dict(name='synthetic-revision', fee_class='free',
                      permission_basis='合成事实', obtained_at=case['obtained_at']),
    }])))
    child = store.import_files(manifest, (csv,))
    assert not store.read_table(child, 'daily_bars').empty
    assert store.read_table(child, 'daily_bars').equals(store.read_table(original, 'daily_bars'))
    old_actions = [e for e in original_doc['table_files'] if e['table'] == 'corporate_actions']
    inherited = [e for e in store.load_manifest(child)['table_files'] if e['table'] == 'corporate_actions']
    assert len(inherited) == len(old_actions)
    assert [(e['sha256'], e['coverage'], e['query_coverage']) for e in inherited] == [
        (e['sha256'], e['coverage'], e['query_coverage']) for e in old_actions]
    assert len(store.read_table(child, 'market_cap')) == len(store.read_table(original, 'market_cap'))
    assert store.load_manifest(original) == original_doc
    assert store.load_manifest(child)['revises'] == [original.snapshot_id]


def test_signal_does_not_replace_unknown_current_cap_with_previous_price(tmp_path, monkeypatch):
    from backtest_cases import build_prepared_case_store
    from hetu_quant.strategies import _known_market_cap

    case = make_backtest_case('formal', 'workflow')
    caps = pd.read_csv(__import__('io').StringIO(case['csvs']['market_cap']), dtype=str)
    caps.loc[(caps['code'] == 'VOL_01') & (caps['date'] == '2024-01-31'), 'available_at'] = '2025-01-01T18:00:00+08:00'
    case['csvs']['market_cap'] = caps.to_csv(index=False)
    assert _known_market_cap(caps, 'VOL_01', '2024-01-31') is None
    store, ref = build_prepared_case_store(tmp_path/'facts', case, monkeypatch)
    prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path/'bundle')
    exclusions = pd.read_parquet(tmp_path/'bundle/exclusions.parquet')
    selected = exclusions[(exclusions['code'] == 'VOL_01') & (exclusions['date'] == '2024-01-31')]
    assert any('market_cap_unknown_at_signal' in str(r) for r in selected['reason'])


@pytest.mark.parametrize('invalid_base', ['tampered', 'paid'])
def test_import_inheritance_checks_locked_bytes_and_free_access(tmp_path, monkeypatch, invalid_base):
    from backtest_cases import build_prepared_case_store
    from hetu_quant.data import AccessDenied, SnapshotTamperedError

    case = make_backtest_case('formal')
    store, ref = build_prepared_case_store(tmp_path/'facts', case, monkeypatch)
    original = store.load_manifest(ref)
    if invalid_base == 'tampered':
        entry = next(e for e in original['table_files'] if e['table'] == 'daily_bars')
        (store.root/'snapshots'/ref.snapshot_id/entry['path']).write_bytes(b'changed')
    else:
        product = case['table_products']['daily_bars'][0]
        store.config['products'][product]['fee_class'] = 'paid'
    manifest = tmp_path/'inherit.json'
    manifest.write_text(json.dumps(dict(mode='free', base_snapshot_ids=[ref.snapshot_id], items=[])))
    with pytest.raises((AccessDenied, SnapshotTamperedError)):
        store.import_files(manifest, ())
    assert [s for s, _ in store._published_manifests()] == [ref.snapshot_id]


def test_complete_month_end_warmup_does_not_require_unused_month_start_quotes(tmp_path, monkeypatch):
    from backtest_cases import build_prepared_case_store

    case = make_backtest_case('formal')
    # 首次信号2023-12-31的36月收益只需2020-12-31起37个端点。
    # 只裁掉未用的更早行情，必需月末点和行动查询证明均保留。
    for table in ('daily_bars', 'security_status'):
        frame = pd.read_csv(__import__('io').StringIO(case['csvs'][table]), dtype=str)
        case['csvs'][table] = frame[frame['date'] >= '2020-12-31'].to_csv(index=False)
    case['actions_coverage']['start'] = '2020-12-31'
    store, ref = build_prepared_case_store(tmp_path/'facts', case, monkeypatch)
    quality = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path/'bundle')
    assert quality.can_run, quality.excluded
    assert 'VOL_01' in quality.eligible_ids
    assert not any('bars_warmup_gap' in reason for reason in quality.excluded.get('VOL_01', ()))
