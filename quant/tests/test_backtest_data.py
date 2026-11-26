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


@pytest.mark.parametrize("publication", [None, "2024-01-31T15:30:00+08:00", "2024-01-31T14:55:00+08:00"])
def test_signal_cap_uses_proof_for_the_same_trading_day(tmp_path, publication):
    from backtest_cases import _evidence_content, _evidence_sha
    from hetu_quant.backtest_data import select_signal_market_cap
    from hetu_quant.strategies import _known_market_cap

    caps = pd.DataFrame([
        {"code": "A.SH", "date": day, "market_cap_cny": value, "revision_id": "same-share-count",
         "available_at": "2024-02-01T09:00:00+08:00"}
        for day, value in (("2023-12-29", 600), ("2024-01-31", 900))
    ])
    (tmp_path / "evidence").mkdir()
    rows = []
    for day, at in (("2023-12-29", "2023-12-29T15:00:00+08:00"), ("2024-01-31", publication)):
        if at is None:
            continue
        ref = f"share-proof-{day}"
        (tmp_path / "evidence" / ref).write_bytes(_evidence_content(ref))
        rows.append(dict(table_name="market_cap", record_key=f"A.SH|{day}", revision_id="same-share-count",
                         evidence_ref=ref, sha256=_evidence_sha(ref), supersedes="", published_at=at, verified=True))
    knowledge = pd.DataFrame(rows)
    fact = select_signal_market_cap(caps, knowledge, "A.SH", "2024-01-31", tmp_path)
    weight_input = _known_market_cap(caps, "A.SH", "2024-01-31", knowledge, tmp_path)
    if publication and "14:55" in publication:
        assert fact["market_cap_cny"] == weight_input == 900
        assert fact["evidence_ref"] == "share-proof-2024-01-31"
    else:
        assert fact is None
        assert weight_input is None


@pytest.mark.parametrize("has_january_proof", [False, True])
def test_prepared_universe_and_formal_weights_share_exact_cap_record(tmp_path, monkeypatch, has_january_proof):
    from io import StringIO

    from backtest_cases import _evidence_sha, build_prepared_case_store
    from hetu_quant.strategies import build_signals

    case = make_backtest_case("formal", "workflow")
    code, dec, jan = "VOL_01", "2023-12-31", "2024-01-31"
    for table in ("calendar", "daily_bars", "security_status"):
        frame = pd.read_csv(StringIO(case["csvs"][table]), dtype=str)
        case["csvs"][table] = frame[frame.date.le("2024-02-29")].to_csv(index=False)
    case["definition"]["end"] = date(2024, 2, 29)
    case["actions_coverage"]["end"] = "2024-02-29"
    caps = pd.read_csv(StringIO(case["csvs"]["market_cap"]), dtype=str)
    caps = caps[caps.date.le("2024-02-29")].copy()
    caps["revision_id"] = ""
    revised = caps["code"].eq(code) & caps["date"].isin([dec, jan])
    caps.loc[revised, ["revision_id", "available_at"]] = ["same-share-count", "2024-02-01T09:00:00+08:00"]
    caps.loc[caps["code"].eq(code) & caps["date"].eq(jan), "market_cap_cny"] = "9000000000"
    case["csvs"]["market_cap"] = caps.to_csv(index=False)
    proof_days = (dec, jan) if has_january_proof else (dec,)
    for day in proof_days:
        ref = f"share-proof-{day}"
        case["csvs"]["knowledge_evidence"] += (
            f"market_cap,{code}|{day},same-share-count,,{day}T14:55:00+08:00,{ref},{_evidence_sha(ref)},True\n"
        )
        case["evidence_refs"] += (ref,)
    store, ref = build_prepared_case_store(tmp_path / "facts", case, monkeypatch)
    definition = _definition(case, ref.snapshot_id)
    prepare_backtest(definition, store, tmp_path / "bundle")
    universe = pd.read_parquet(tmp_path / "bundle" / "universe.parquet")
    january = set(universe.loc[universe.signal_date.eq(jan), "code"])
    lowvol = build_signals(definition, store)
    lowvol_january = lowvol[lowvol.signal_at.eq(jan)]
    baseline = build_signals(_definition(case, ref.snapshot_id, strategy_id="universe_vw"), store)
    baseline_january = baseline[baseline.signal_at.eq(jan)].set_index("code")
    assert set(baseline_january.index) == january
    assert (code in january) == has_january_proof
    assert lowvol_january.code.tolist() == [code if has_january_proof else "VOL_02"]
    if has_january_proof:
        total = caps.loc[caps.date.eq(jan) & caps.code.isin(january), "market_cap_cny"].astype(float).sum()
        assert baseline_january.loc[code, "target_weight"] == pytest.approx(9_000_000_000 / total)


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


@pytest.mark.parametrize("proven", [True, False])
def test_proven_code_change_preserves_warmup(tmp_path, monkeypatch, proven):
    from io import StringIO

    from backtest_cases import _evidence_sha, build_prepared_case_store
    from hetu_quant.backtest_data import _proven_month_ends
    from hetu_quant.strategies import build_signals

    case = make_backtest_case("formal")
    old, new = "VOL_01", "VOL_00"
    last, effective, signal = "2022-06-30", "2022-07-01", "2023-12-31"
    published = "2022-07-01T08:00:00+08:00"
    for field in ("daily_bars", "security_status", "market_cap"):
        frame = pd.read_csv(StringIO(case["csvs"][field]), dtype=str)
        frame.loc[frame.code.eq(old) & frame.date.ge(effective), "code"] = new
        if field in ("daily_bars", "security_status"):
            frame = pd.concat([frame, frame[frame.code.eq(old) & frame.date.eq(last)].assign(date=effective, code=new)])
        case["csvs"][field] = frame.to_csv(index=False)
    calendar = pd.read_csv(StringIO(case["csvs"]["calendar"]), dtype=str)
    case["csvs"]["calendar"] += f"{effective},True\n"
    case["csvs"]["historical_securities"] = case["csvs"]["historical_securities"].replace(
        f"{old},2020-01-02,,", f"{old},2020-01-02,{last},"
    ) + f"{new},{effective},,True,SH,main,{published},ev-sp-rename\n"
    case["csvs"]["knowledge_evidence"] += (
        f"historical_securities,{new},orig,,{published},ev-sp-rename,{_evidence_sha('ev-sp-rename')},True\n"
    )
    case["codes"] += (new,)
    case["evidence_refs"] += ("ev-sp-rename",)
    store, ref = build_prepared_case_store(tmp_path / "facts", case, monkeypatch)
    rows = [
        dict(code=old, identity_id="SYNTHETIC", valid_from="2020-01-02", valid_to=last,
             market="SH", security_type="stock", source_ref="synthetic-roster"),
        dict(code=new, identity_id="SYNTHETIC", valid_from=effective, valid_to="",
             market="SH", security_type="stock", source_ref="synthetic-roster"),
    ]
    if proven:
        rows[1]["continuation"] = dict(kind="administrative_rename", from_code=old, effective_date=effective,
                                      published_at=published, ratio=1)
    delivery = tmp_path / "selected-qualification"
    delivery.mkdir(parents=True)
    (delivery / "handoff.json").write_text(json.dumps(dict(snapshot_id=ref.snapshot_id, mode="free")))
    (delivery / "requirements.json").write_text(json.dumps(dict(
        start="2020-12-01", end="2024-01-31", objects=rows,
        target_scope=dict(complete=True, source_ref="evidence/synthetic-roster.json"),
    )))
    (store.root / "evidence" / "synthetic-roster.json").write_text(json.dumps(rows))
    definition = _definition(case, ref.snapshot_id)
    report = prepare_backtest(definition, store, tmp_path / "bundle", qualification_delivery=delivery)
    if not proven:
        assert not report.can_run
        assert any("code_continuity" in reason for reason in report.excluded[new])
        return
    assert report.can_run
    assert len([day for day in _proven_month_ends(calendar) if "2020-12-31" <= day <= signal]) == 37
    signals = build_signals(definition, store, qualification_delivery=delivery).query("signal_at == @signal")
    assert signals.code.tolist() == [new]
    event = pd.read_parquet(tmp_path / "bundle" / "tables" / "identity_transitions.parquet").iloc[0]
    assert (event.from_code, event.code, event.effective_date) == (old, new, effective)
    assert (tmp_path / "bundle" / "evidence" / event.evidence_ref).read_bytes() == (
        store.root / "evidence" / "synthetic-roster.json"
    ).read_bytes()
    assert pd.read_parquet(tmp_path / "bundle" / "tables" / "daily_bars.parquet").query(
        "code == @old"
    ).date.max() == last


@pytest.mark.parametrize("variant", ["matched", "wrong_terms", "unproved_method", "conflicting"])
def test_cash_exit_originals_gate_production_bundle_and_audit(tmp_path, variant):
    import hashlib

    from hetu_quant.data_archive import archive_requirements
    from hetu_quant.data_research import audit_research_coverage
    from test_backtest_account import _cash_disposal_original

    case = make_backtest_case("formal")
    code, day = "VOL_01", "2024-01-31"
    case["csvs"]["historical_securities"] = case["csvs"]["historical_securities"].replace(
        f"{code},2020-01-02,,", f"{code},2020-01-02,{day},"
    )
    claims = [_cash_disposal_original(case, code, day, 2, name="notice-1.json")]
    if variant == "wrong_terms":
        claims[0]["cash_per_share"] = 8
    if variant == "unproved_method":
        path = claims[0]["evidence_ref"]
        raw = json.dumps(dict(json.loads(case["evidence_files"][path]), kind="other_action")).encode()
        case["evidence_files"][path] = raw
        claims[0]["sha256"] = hashlib.sha256(raw).hexdigest()
    if variant == "conflicting":
        claims.append(_cash_disposal_original(case, code, day, 8, name="notice-2.json"))
    table = "terminal_disposals"
    case["table_products"][table] = ("cash_exits", "event")
    case["permissions"] = {table: {"allowed_uses": ["backtest_low_volatility"]}}
    case["csvs"][table] = pd.DataFrame(claims).to_csv(index=False)
    store, ref = _build_store(tmp_path / "facts", case)
    for name, original in case["evidence_files"].items():
        (store.root / "evidence" / name).write_bytes(original)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                              codes=list(case["codes"]), query_basis="ex_date")
    bundle = tmp_path / "bundle"
    definition = _definition(case, ref.snapshot_id)
    report = prepare_backtest(definition, store, bundle)
    assert report.can_run is (variant == "matched"), report.limitations
    history = store.read_table(ref, "historical_securities")
    history["security_type"] = "stock"
    history = history.loc[history.code.eq(code)]
    dates = store.read_table(ref, "calendar")
    requirements = archive_requirements(history, dates, definition.end, start=definition.start)
    requirements.update(study_start=definition.start.isoformat(), signal_dates=[], definitions=[], legal_exclusions=[])
    audit = audit_research_coverage(requirements, store, ref)
    gaps = [gap for gap in audit["event_limitations"] if gap["domain"] == table]
    if variant == "matched":
        assert gaps == []
        assert (bundle / "evidence" / "notice-1.json").read_bytes() == case["evidence_files"]["notice-1.json"]
        assert pd.read_parquet(bundle / "tables" / f"{table}.parquet").iloc[0].cash_per_share == "2"
        return
    reason = "exit_disposal_conflicting_terms" if variant == "conflicting" else "exit_disposal_terms_unproved"
    sources = sorted(case["evidence_files"])
    assert [(gap["code"], gap["date"], gap["reason"], gap["source_refs"]) for gap in gaps] == [
        (code, day, reason, sources),
    ]
    if variant == "wrong_terms":
        claim, = gaps[0]["cash_claims"]
        assert (claim["cash_per_share"], claim["prepared_cash_per_share"]) == (2, 8)
        assert gaps[0]["unproved_original_refs"] == []
    if variant == "unproved_method":
        assert gaps[0]["cash_claims"] == [] and gaps[0]["unproved_original_refs"] == sources
    exclusions = pd.read_parquet(bundle / "exclusions.parquet").query("field == @table")
    assert exclusions[["code", "reason", "source_ref"]].values.tolist() == [[code, reason, "; ".join(sources)]]


def test_special_exchange_is_bound_to_production_bundle_without_stitching_prices(tmp_path):
    import hashlib
    from io import StringIO

    from backtest_cases import _evidence_sha

    case = make_backtest_case("formal")
    old, new, last, day = "VOL_01", "VOL_SUCCESSOR", "2023-12-31", "2024-01-31"
    published = day + "T08:00:00+08:00"
    case["csvs"]["historical_securities"] = case["csvs"]["historical_securities"].replace(
        f"{old},2020-01-02,,", f"{old},2020-01-02,{last},"
    ) + f"{new},{day},,True,SH,main,{published},ev-special-new\n"
    case["csvs"]["knowledge_evidence"] += (
        f"historical_securities,{new},orig,,{published},ev-special-new,{_evidence_sha('ev-special-new')},True\n"
    )
    case["codes"] += (new,)
    case["evidence_refs"] += ("ev-special-new",)
    for name in ("daily_bars", "security_status", "market_cap"):
        frame = pd.read_csv(StringIO(case["csvs"][name]), dtype=str)
        frame.loc[frame.code.eq(old) & frame.date.eq(day), "code"] = new
        case["csvs"][name] = frame.to_csv(index=False)
    original = dict(
        kind="special_exchange", event_id="ratio-exchange", from_code=old, to_code=new, effective_date=day,
        available_at=published, old_shares=3, new_shares=2, credit_date=day,
        share_rule="whole_shares", fraction_rule="cash_in_lieu", fraction_cash_per_share=6,
        fraction_currency="CNY", fraction_unit="元/股", fraction_pay_date="2024-02-01",
        sellable_date="2024-02-01",
    )
    raw = json.dumps(original, ensure_ascii=False).encode()
    keys = ("event_id", "from_code", "to_code", "effective_date")
    row = dict({key: original[key] for key in keys},
               evidence_ref="share-exchange.json", sha256=hashlib.sha256(raw).hexdigest())
    table = "share_exchanges"
    case["table_products"][table] = ("exchange_product", "event")
    case["permissions"] = {table: {"allowed_uses": ["backtest_low_volatility"]}}
    case["csvs"][table] = pd.DataFrame([row]).to_csv(index=False)
    store, ref = _build_store(tmp_path / "facts", case)
    (store.root / "evidence" / row["evidence_ref"]).write_bytes(raw)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                              codes=list(case["codes"]), query_basis="ex_date")
    bundle = tmp_path / "bundle"
    report = prepare_backtest(_definition(case, ref.snapshot_id), store, bundle)
    assert report.can_run, report.limitations
    assert "share_exchanges" not in " ".join(report.limitations)
    assert (bundle / "evidence" / row["evidence_ref"]).read_bytes() == raw
    assert pd.read_parquet(bundle / "tables" / f"{table}.parquet")[list(keys)].values.tolist() == [
        [row[key] for key in keys],
    ]
    assert not (bundle / "tables" / "identity_transitions.parquet").exists()
    assert pd.read_parquet(bundle / "tables" / "daily_bars.parquet").query("code == @new").date.tolist() == [day]


def test_missing_share_exchange_original_blocks_ready_bundle_and_recovers(tmp_path):
    from backtest_cases import build_disposition_case_store

    case, store, ref = build_disposition_case_store(tmp_path / "facts")
    definition = _definition(case, ref.snapshot_id)
    original = store.root / "evidence/share-exchange.json"
    raw = original.read_bytes()
    original.unlink()
    blocked = tmp_path / "blocked"
    report = prepare_backtest(definition, store, blocked)
    assert not report.can_run
    assert any(all(value in gap for value in ("VOL_01", "2024-07-31", "ratio-exchange", "share-exchange.json"))
               for gap in report.limitations)
    assert json.loads((blocked / "qualification.json").read_text())["can_run"] is False
    assert not (blocked / "tables").exists()
    assert not pd.read_parquet(blocked / "universe.parquet").empty
    original.write_bytes(raw)
    ready = tmp_path / "restored"
    recovered = prepare_backtest(definition, store, ready)
    assert recovered.can_run and json.loads((ready / "qualification.json").read_text())["can_run"] is True
    assert (ready / "evidence" / original.name).read_bytes() == raw
    assert pd.read_parquet(ready / "tables/share_exchanges.parquet").event_id.tolist() == ["ratio-exchange"]


def test_proved_late_exchange_does_not_block_current_cash_disposition(tmp_path):
    from backtest_cases import build_disposition_case_store

    case, store, ref = build_disposition_case_store(tmp_path / "facts", cash_exit=True, late_exchange=True)
    report = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "bundle")
    assert report.can_run, report.limitations
    assert not any("incompatible_account_dispositions" in gap for gap in report.limitations)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("cash_variant", ["alternating", "alternating_mismatch"])
def test_prepare_resolves_alternating_disposition_replacements_at_execution_cutoff(tmp_path, reverse, cash_variant):
    from fractions import Fraction

    from backtest_cases import build_disposition_case_store
    from hetu_quant.account_evidence import account_dispositions

    case, store, ref = build_disposition_case_store(tmp_path / "facts", cash_variant=cash_variant, reverse=reverse)
    day, code = "2024-07-31", "VOL_01"
    for clock in ("08:15", "08:45"):
        cutoff = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=CN)
        case["definition"]["as_of"] = cutoff
        bundle = tmp_path / clock.replace(":", "")
        report = prepare_backtest(_definition(case, ref.snapshot_id), store, bundle)
        if clock == "08:15" and cash_variant == "alternating_mismatch":
            assert not report.can_run
            assert report.limitations == (f"{day} {code}: exit_disposal_terms_unproved (cash-exit.json)",)
            assert not (bundle / "tables").exists()
            print(f"reverse={reverse} {clock}: {report.limitations[0]}")
            continue
        assert report.can_run, report.limitations
        assert not report.limitations
        cash_rows = pd.read_parquet(bundle / "tables" / "terminal_disposals.parquet").to_dict("records")
        swap_rows = pd.read_parquet(bundle / "tables" / "share_exchanges.parquet").to_dict("records")
        cash, swaps, *gaps = account_dispositions(cash_rows, swap_rows, bundle / "evidence", day, day, as_of=cutoff)
        assert not any(gaps)
        if clock == "08:15":
            assert not swaps
            event = cash[code, day]
            assert event["cash_per_share"] == 2
            expected = ["cash-exit.json", "share-exchange.json"]
        else:
            assert not cash
            event = swaps[code, day]
            assert event["ratio"] == Fraction(1, 3)
            assert event["code"] == "VOL_SUCCESSOR"
            expected = ["share-replacement.json", "cash-exit.json", "share-exchange.json"]
        selected, = event["original_versions"]
        assert [selected["source_ref"], *(item["source_ref"] for item in selected["prior_cross_versions"])] == expected
        for name in ("share-exchange.json", "cash-exit.json", "share-replacement.json"):
            assert (bundle / "evidence" / name).read_bytes() == (store.root / "evidence" / name).read_bytes()
        print(f"{cash_variant} reverse={reverse} {clock}: disposition={expected[0]}, trace={expected}")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("effective", [None, "exchange"])
def test_same_revision_name_does_not_cross_account_announcements(tmp_path, reverse, effective):
    from backtest_cases import build_disposition_case_store

    case, store, ref = build_disposition_case_store(tmp_path / "facts", cash_variant="revision",
                                                    effective=effective, reverse=reverse)
    quality = prepare_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "bundle")
    assert quality.can_run is (effective == "exchange"), quality.limitations
    if effective is None:
        conflict, = [gap for gap in quality.limitations if "incompatible_account_dispositions" in gap]
        assert all(value in conflict for value in (
            "cash_exit 3 CNY/share", "share_exchange 2/3", "cash-v2.json", "share-exchange.json",
        ))
        assert "cash-exit.json" not in conflict


def test_replacing_one_exchange_cannot_hide_another_unproved_original(tmp_path):
    from backtest_cases import build_disposition_case_store

    case, store, ref = build_disposition_case_store(tmp_path / "facts", effective="cash", independent_exchange=True)
    (store.root / "evidence/independent-exchange.json").unlink()
    bundle = tmp_path / "bundle"
    report = prepare_backtest(_definition(case, ref.snapshot_id), store, bundle)
    assert not report.can_run
    assert any("share_exchange_original_unavailable" in gap and "independent-exchange.json" in gap
               for gap in report.limitations)
    assert any("incompatible_account_dispositions" in gap for gap in report.limitations)
    assert not (bundle / "tables").exists()
