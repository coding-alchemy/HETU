"""Read back calendar and quote choices from the actual factor workbench."""

import json
from dataclasses import replace
from datetime import date

import pytest

from backtest_cases import build_case_store
from factor_cases import example_query, local_fact_source, local_volume_source
from hetu_quant.factors import compute_factor
from hetu_quant.factors.workbench_inputs import visible_evidence
from test_factor_workbench import open_factors, run_trial
from test_factor_workbench_review import DAY, _calendar_source, _factor, _library, _revoke


def test_skipped_momentum_uses_proxy_calendar_and_prices(tmp_path, monkeypatch):
    earlier, later = "2024-03-28", "2024-03-29"
    _, document, _ = example_query("up_amount_share")
    earlier_dates = tuple(row["date"] for row in document["tables"]["calendar"] if row["date"] < earlier)
    quotes = dict(code="ETF.SZ", close_by_date={earlier: 12, later: 24}, bars_from=earlier)
    owner, complete = local_volume_source(tmp_path / "complete", calendar_omit=earlier_dates, **quotes)
    reduced, missing = local_volume_source(tmp_path / "missing", calendar_omit=(*earlier_dates, later), **quotes)
    declarations, declared = local_fact_source(tmp_path / "declaration", {"sector_proxies": [
        dict(
            code="SECTOR", kind="etf", symbol="ETF.SZ", valid_from=str(DAY), valid_to="2024-12-31",
            available_at=f"{DAY}T14:00:00+08:00",
        ),
    ]})
    full = _library(owner, (owner, complete), (declarations, declared))
    incomplete = _library(owner, (reduced, missing), (declarations, declared))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    _factor(page, "price_momentum")
    page.date_input(key="factor_date_price_momentum").set_value(DAY)
    page.text_area(key="factor_parameters_price_momentum_默认").set_value(json.dumps({"window": 1, "skip": 1}))
    page.text_input(key="factor_subjects_price_momentum").set_value("ETF.SZ SECTOR")
    for source, blocked in ((full, False), (incomplete, True), (full, False)):
        page.selectbox(key="factor_source_price_momentum").set_value(source.library_id).run()
        assert any("SECTOR" in item.value and "实际取价证券 ETF.SZ" in item.value for item in page.markdown)
        if blocked:
            assert any("ETF.SZ · calendar · 2024-03-29" in item.value for item in page.markdown)
        run_trial(page)
        result = list(page.session_state["factor_attempts"].values())[-1].result
        if blocked:
            assert not result.rows
            calendar = [(gap["field"], gap["period"]) for gap in result.gaps]
            assert calendar == [("calendar", later)] * 2
            print("momentum without March 29 calendar", calendar)
        else:
            assert page.dataframe[0].value["读数"].tolist() == [1, 1]
            assert [row["subject"] for row in result.rows] == ["ETF.SZ", "SECTOR"]
            assert result.rows[1]["components"]["price_subject"] == "ETF.SZ"
            for row in result.rows:
                assert [(item["code"], item["date"], item["close"]) for item in row["inputs"]] == [
                    ("ETF.SZ", earlier, 12), ("ETF.SZ", later, 24),
                ]
            assert result.rows[1]["inputs"][0]["sector_proxy"]["valid_from"] == str(DAY)
            print("momentum", [(row["subject"], row["value"], row["components"]) for row in result.rows])


@pytest.mark.parametrize(
    "scenario", ["holiday", "short", "closed", "ambiguous", "missing", "unattested", "revoked", "unused_open"],
)
def test_pair_preview_and_beta_share_common_dates(tmp_path, monkeypatch, scenario):
    if scenario in ("holiday", "short"):
        dates = ["2024-02-06", "2024-02-07", "2024-02-08", "2024-02-19"]
        expected = dates[:3]
        if scenario == "short":
            dates = dates[1:]
    else:
        dates = ["2024-03-26", "2024-03-27", "2024-03-28", "2024-03-29", str(DAY)]
        expected = [dates[i] for i in (0, 1, 3)]
    ignored_quote = "2024-03-28"
    base_calendar = set(dates)
    if scenario in ("unattested", "revoked", "missing", "unused_open"):
        base_calendar.remove(ignored_quote)
    if scenario == "revoked":
        base_calendar.remove(str(DAY))
    owner, quotes = local_fact_source(tmp_path / "quotes", {
        "calendar": [
            dict(date=day, is_trading_day=True, available_at=f"{day}T12:00:00+08:00")
            for day in dates if day in base_calendar
        ],
        "daily_bars": [
            dict(code=code, date=day, close=price, available_at=f"{day}T14:59:00+08:00")
            for day, price_root in zip(dates, (3, 5, 7, 11, 13)[:len(dates)], strict=True)
            for code, price in (("A.SH", price_root**2), ("B.SZ", price_root))
            if scenario not in ("unattested", "revoked") or day != ignored_quote
        ],
    })
    assets = [(owner, quotes)]
    if scenario in ("closed", "ambiguous"):
        assets.append(_calendar_source(tmp_path / "conflict", "conflict", "2024-03-28"))
    if scenario in ("closed", "unused_open"):
        closed, closed_ref = _calendar_source(tmp_path / "shenzhen", "cn_sz_archive_calendar", ignored_quote)
        assets.append((closed, closed_ref))
    if scenario == "unused_open":
        unused, opened = build_case_store(tmp_path / "open", {
            "root_config": True,
            "obtained_at": f"{ignored_quote}T14:00:00+08:00",
            "csvs": {"calendar": f"date,is_trading_day\n{ignored_quote},1\n"},
            "table_products": {"calendar": ("open_witness", "calendar")},
            "source_metadata": {"calendar": {"published_at": f"{ignored_quote}T14:00:00+08:00"}},
            "permissions": {"calendar": {"allowed_uses": ["factor_calculation"]}},
        })
        assets.append((unused, opened))
    if scenario == "unattested":
        assets.append(build_case_store(tmp_path / "unattested", {
            "root_config": True,
            "obtained_at": "2024-04-02T18:00:00+08:00",
            "csvs": {"daily_bars": (
                "code,date,close_raw,open_raw,high_raw,low_raw,amount_cny,volume_shares\n"
                "A.SH,2024-03-28,36,36,36,36,1000,100\n"
            )},
            "table_products": {"daily_bars": ("unattested_daily", "daily")},
            "permissions": {"daily_bars": {"allowed_uses": ["factor_calculation"]}},
        }))
    if scenario == "revoked":
        unused, unused_ref = _calendar_source(tmp_path / "observation-only", "observation_only", str(DAY))
        assets.append((unused, unused_ref))
    source = _library(owner, *assets)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    _factor(page, "pair_hedge_beta")
    page.selectbox(key="factor_source_pair_hedge_beta").set_value(source.library_id)
    page.date_input(key="factor_date_pair_hedge_beta").set_value(date.fromisoformat(dates[-1]))
    page.text_input(key="factor_role_pair_hedge_beta_0").set_value("A.SH")
    page.text_input(key="factor_role_pair_hedge_beta_1").set_value("B.SZ")
    page.text_area(key="factor_parameters_pair_hedge_beta_默认").set_value(json.dumps({"window": 3})).run()
    preview = [item.value for item in page.markdown]
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    result = attempt.result
    if scenario == "short":
        assert not result.rows
        assert any("共同交易日不足，需要 3 日，已有 2 日" in s for s in preview)
        assert "共同交易日不足，需要 3 日，已有 2 日" in result.gaps[-1]["reason"]
    elif scenario in ("ambiguous", "missing"):
        assert not result.rows
        assert any("2024-03-28" in s and "可能影响共同取样日" in s for s in preview)
        assert (result.gaps[-1]["field"], result.gaps[-1]["period"]) == ("calendar", "2024-03-28")
    else:
        for code in ("A.SH", "B.SZ"):
            assert any(f"{code} · 共同取样日" in s and "、".join(expected) in s for s in preview)
            assert [item["date"] for item in result.rows[0]["inputs"] if item["code"] == code] == expected
        assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(2)]
        if scenario == "revoked":
            _revoke(unused, "observation_only")
            page.run()
            assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(2)]
            assert compute_factor(owner, source, attempt.trial.request).rows[0]["value"] == pytest.approx(2)
        if scenario == "unused_open":
            row, = result.rows
            leg, *remaining = row["inputs"]
            stored_groups = leg["_workbench_calendar"]
            assert not any(witness["product_id"] == "open_witness"
                           for group in stored_groups for witness in group)
            # Previously both legs' calendar groups were stored, including the redundant opener.
            opener = dict(date=ignored_quote, is_trading_day=True, product_id="open_witness",
                          source_root=str(unused.root.resolve()), snapshot_id=opened.snapshot_id,
                          _workbench_fields=("date", "is_trading_day"))
            legacy = replace(result, rows=[dict(row, inputs=[
                dict(leg, _workbench_calendar=[*stored_groups, [opener]]), *remaining,
            ])])
            _revoke(unused, "open_witness")
            fresh = compute_factor(owner, source, attempt.trial.request)
            print("无关开市撤权", fresh.rows[0]["value"])
            assert fresh.rows[0]["value"] == pytest.approx(2)
            for saved in (result, legacy):
                old = visible_evidence(owner, attempt.trial, saved, row_index=0)
                assert old["permitted"] and old["item"]["value"] == pytest.approx(2)
                assert "open_witness" not in json.dumps(old, ensure_ascii=False)
            page.run()
            assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(2)]
            _revoke(closed, "cn_sz_archive_calendar")
            for saved in (result, legacy):
                assert not visible_evidence(owner, attempt.trial, saved)["rows"]

    print("pair", scenario, result.rows and (
        result.rows[0]["value"], [(item["code"], item["date"]) for item in result.rows[0]["inputs"]],
    ), result.gaps)


@pytest.mark.parametrize(
    "extra_basis", [
        None, "total_return", "price", "late_price", "split_price", "close_only", "basis_only",
        "split-revised", "split-reversed", "split-revised-reversed",
    ],
)
def test_native_index_proxy_preview_uses_known_price_return_basis(tmp_path, monkeypatch, extra_basis):
    from hashlib import sha256

    first, last, uncertain = "2024-03-27", "2024-03-28", "2024-03-29"
    code = "INDEX.SH"
    calendar = [
        dict(date=day, is_trading_day=True, available_at="2024-03-26T12:00:00+08:00")
        for day in (first, last, str(DAY))
    ]
    owner, declaration = local_fact_source(tmp_path / "declaration", {
        "calendar": calendar,
        "sector_proxies": [
            dict(code="SECTOR", kind="index", symbol=code, valid_from=first, available_at=calendar[0]["available_at"]),
        ],
    })

    def quote_asset(path, quotes, proof_fields, *, revise_basis=False):
        proofs, registrations = [], []
        for day, value, basis in quotes:
            for field, original in (("close", value), ("return_basis", basis)):
                if field not in proof_fields[day]:
                    continue
                stamp = f"{day}T14:30:00+08:00"
                if extra_basis == "late_price" and day == uncertain and field == "return_basis":
                    stamp = f"{DAY}T18:00:00+08:00"
                key = f"{code}|{day}|{field}"
                proofs.append(dict(
                    product_id="native_index", record_key=key, revision_id="original",
                    published_at=stamp, value=original,
                ))
                registrations.append(f"index_bars,{key},original,,{stamp},index-proof.json")
                if revise_basis and day == uncertain and field == "return_basis":
                    revised = f"{day}T14:45:00+08:00"
                    proofs.append(dict(proofs[-1], revision_id="v2", published_at=revised))
                    registrations.append(f"index_bars,{key},v2,original,{revised},index-proof.json")
        evidence = json.dumps({"records": proofs}).encode()
        digest = sha256(evidence).hexdigest()
        permissions = {"allowed_uses": ["factor_calculation"]}
        quote_store, quote_ref = build_case_store(path, {
            "root_config": True,
            "obtained_at": f"{DAY}T19:00:00+08:00",
            "csvs": {
                "index_bars": "code,date,close,amount_cny,return_basis\n" + "".join(
                    f"{code},{day},{close},1000,{basis}\n" for day, close, basis in quotes
                ),
                "knowledge_evidence": (
                    "table_name,record_key,revision_id,supersedes,published_at,evidence_ref,sha256,verified\n"
                    + "".join(f"{registration},{digest},True\n" for registration in registrations)
                ),
            },
            "table_products": {
                "index_bars": ("native_index", "daily"), "knowledge_evidence": ("index_proofs", "catalog"),
            },
            "permissions": {table: permissions for table in ("index_bars", "knowledge_evidence")},
        })
        (quote_store.root / "evidence" / "index-proof.json").write_bytes(evidence)
        return quote_store, quote_ref

    quotes = [(first, 10, "price"), (last, 12, "price")]
    fields = {day: ("close", "return_basis") for day in (first, last)}
    split = bool(extra_basis and extra_basis.startswith("split"))
    if extra_basis:
        third = (uncertain, 99, "total_return" if extra_basis == "total_return" else "price")
        quotes.append(third)
        fields[uncertain] = ("close",) if split or extra_basis == "close_only" else (
            ("return_basis",) if extra_basis == "basis_only" else ("close", "return_basis")
        )
    assets = [(owner, declaration), quote_asset(tmp_path / "quotes", quotes, fields)]
    if split:
        assets.append(quote_asset(
            tmp_path / "quote-basis", [third], {uncertain: ("return_basis",)},
            revise_basis="revised" in extra_basis,
        ))
        if extra_basis.endswith("reversed"):
            assets[1:] = reversed(assets[1:])
    source = _library(owner, *assets)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    _factor(page, "price_momentum")
    page.date_input(key="factor_date_price_momentum").set_value(DAY)
    page.text_area(key="factor_parameters_price_momentum_默认").set_value(json.dumps({"window": 1, "skip": 1}))
    page.text_input(key="factor_subjects_price_momentum").set_value("SECTOR")
    page.selectbox(key="factor_source_price_momentum").set_value(source.library_id).run()
    alert = any(f"{code} · calendar · {uncertain}" in item.value for item in page.markdown)
    run_trial(page)
    result = next(iter(page.session_state["factor_attempts"].values())).result
    print("指数", extra_basis, "预查缺 29 日日历", alert, [row["value"] for row in result.rows], result.gaps)
    should_block = split or extra_basis == "price"
    assert alert == should_block
    if should_block:
        assert not result.rows
        assert [(gap["field"], gap["period"]) for gap in result.gaps] == [("calendar", uncertain)]
    else:
        assert not result.gaps
        assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(0.2)]
        assert [row["date"] for row in result.rows[0]["inputs"]] == [first, last]
