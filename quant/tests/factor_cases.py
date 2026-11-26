"""Original synthetic facts shared by the factor library's offline tests."""

import hashlib
import json

import pandas as pd
from backtest_cases import build_case_store
from hetu_quant.factors import TeachingSource
from hetu_quant.factors.sources import teaching_document


def local_volume_source(path, *, missing=None, late=False, deny=False, code=None, amount_unit="CNY"):
    raw = teaching_document(TeachingSource("up_amount_share_60d"))["tables"]
    if code:
        for table in ("daily_bars", "action_coverage"):
            for row in raw[table]:
                row["code"] = code
    facts, proofs, originals = [], [], []
    for row in raw["action_coverage"]:
        coordinate = f"{row['start']}|{row['end']}"
        key = f"action_coverage|{row['code']}|{coordinate}"
        published = row["available_at"]
        proofs.append(
            dict(
                table_name="factor_input.action_coverage",
                record_key=key,
                revision_id="original",
                supersedes="",
                published_at=published,
                evidence_ref="factor-original.json",
                verified=True,
            )
        )
        originals.append(
            dict(
                product_id="factor_proofs",
                record_key=key,
                revision_id="original",
                published_at=published,
                value=dict(row, coordinate=coordinate),
            )
        )
    for record in raw["daily_bars"]:
        code, day, close = record["code"], record["date"], record["close_raw"]
        bar = dict(
            code=code,
            date=day,
            open_raw=close,
            high_raw=close,
            low_raw=close,
            close_raw=close,
            amount_cny=record["amount_cny"],
            volume_shares=100,
        )
        facts.append(bar)
        for field in ("close_raw", "amount_cny"):
            last_amount = record is raw["daily_bars"][-1] and field == "amount_cny"
            if missing == "amount_cny" and field == missing:
                continue
            key = f"{code}|{day}|{field}"
            published = record["available_at"]
            if last_amount and missing == "late":
                published = f"{day}T18:00:00+08:00"
            if last_amount and missing == "date-only":
                published = day
            item = dict(
                table_name="daily_bars",
                record_key=key,
                revision_id="original",
                supersedes="",
                published_at=published,
                evidence_ref="factor-original.json",
                verified=True,
            )
            proofs.append(item)
            originals.append(
                dict(
                    product_id="factor_daily",
                    record_key=key,
                    revision_id="original",
                    published_at=published,
                    value=bar[field],
                    unit=amount_unit if field == "amount_cny" else "CNY",
                )
            )
            if late and last_amount:
                # The future revision is attested but has not replaced the matching raw observation.
                published = f"{day}T18:00:00+08:00"
                proofs.append(dict(item, revision_id="late", supersedes="original", published_at=published))
                originals.append(dict(originals[-1], revision_id="late", published_at=published, value=1))
    if late:
        facts.append(dict(facts[-1], date="2024-04-02", close_raw=1000))
    evidence = json.dumps(dict(records=originals)).encode()
    for proof in proofs:
        proof["sha256"] = hashlib.sha256(evidence).hexdigest()
    csvs = {
        "daily_bars": pd.DataFrame(facts).to_csv(index=False),
        "calendar": pd.DataFrame(raw["calendar"])[["date", "is_trading_day"]].to_csv(index=False),
        "knowledge_evidence": pd.DataFrame(proofs).to_csv(index=False),
    }
    products = {
        "daily_bars": ("factor_daily", "daily"),
        "calendar": ("factor_calendar", "calendar"),
        "knowledge_evidence": ("factor_proofs", "catalog"),
    }
    case = dict(
        csvs=csvs,
        table_products=products,
        obtained_at="2024-04-03T18:00:00+08:00",
        root_config=True,
        permissions={name: {"allowed_uses": ["daily_archive" if deny else "factor_calculation"]} for name in products},
        source_metadata={"calendar": {"published_at": "2023-10-01T09:00:00+08:00"}},
    )
    store, ref = build_case_store(path, case)
    (store.root / "evidence" / "factor-original.json").write_bytes(evidence)
    return store, ref


def run_formula_case(tmp_path, factor_evidence_root, node, factor_id, expected, mutation):
    """Exercise registered teaching facts through the public entry and retain both attempts."""
    from datetime import date

    import pytest
    from factor_assertions import checked
    from hetu_quant.factors import FactorRequest, close_cutoff, compute_factor
    from hetu_quant.factors.catalog import get_factor

    definition = get_factor(factor_id)
    source = TeachingSource(definition.example_id)
    document = teaching_document(source)
    day = date.fromisoformat(document["trade_date"])
    query = FactorRequest(factor_id, tuple(document["subjects"]), day, close_cutoff(day), {})
    with checked(factor_evidence_root, node, factor_id, "positive", expected) as actual:
        result = compute_factor(None, source, query)
        assert result.rows, result.gaps
        for row in result.rows:
            assert row["inputs"]
            assert row["components"]
        values = [row["value"] for row in result.rows]
        actual["value"] = values if isinstance(expected, list) else values[0]
        alternate = json.loads(json.dumps(document))
        for table, versions in alternate["tables"].items():
            if table != "calendar" and versions:
                future = dict(
                    versions[-1],
                    available_at="2099-01-01T18:00:00+08:00",
                    revision_id="future",
                    supersedes=versions[-1].get("revision_id", "original"),
                )
                for key, value in future.items():
                    if type(value) in (int, float):
                        future[key] = value * 2 + 7
                versions.append(future)
        file = tmp_path / f"{source.case_id}.json"
        file.write_text(json.dumps(alternate))
        later = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
        assert result.rows == later.rows
        assert result.gaps == later.gaps
        actual["future_facts_unchanged"] = True
        if isinstance(expected, (int, float, list)):
            assert actual["value"] == pytest.approx(expected)
        else:
            assert actual["value"] == expected
    table, column = mutation
    if column is None:
        document["tables"][table] = []
    else:
        for row in document["tables"][table]:
            row.pop(column, None)
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    with checked(factor_evidence_root, node, factor_id, "boundary", "missing or limited") as actual:
        result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
        actual.update(status=result.status, gaps=result.gaps)
        assert result.status in ("partial", "unavailable")
        assert result.gaps and all(gap["field"] and gap["period"] and gap["reason"] for gap in result.gaps)


def local_fact_source(path, tables, *, allowed=True, native_actions=(), native_action_rows=None,
                      registration_times=None):
    """Import permitted, independently attested synthetic local facts via the archive manifest."""
    proofs, originals = [], []
    for table, rows in tables.items():
        if table == "calendar":
            continue
        for index, row in enumerate(rows):
            coordinate = row.get("coordinate", str(index))
            key = f"{table}|{row['code']}|{coordinate}"
            stamp = row["available_at"]
            revision = row.get("revision_id", "original")
            proofs.append(
                dict(
                    table_name=f"factor_input.{table}",
                    record_key=key,
                    revision_id=revision,
                    supersedes=row.get("supersedes", ""),
                    published_at=(registration_times or {}).get((table, coordinate), stamp),
                    evidence_ref="factor-fields.json",
                    verified=True,
                )
            )
            originals.append(
                dict(
                    product_id="factor_input",
                    record_key=key,
                    revision_id=revision,
                    published_at=stamp,
                    value=dict(row, coordinate=coordinate, source_ref=row.get("source_ref", f"离线合成 {table}")),
                )
            )
    for row in native_actions:
        stamp = row["available_at"]
        for field in ("d", "same_share_basis"):
            key = f"{row['code']}|{row['ex_date']}|{field}"
            revision = row.get("revision_id", row["coordinate"])
            proofs.append(dict(table_name="corporate_actions", record_key=key, revision_id=revision,
                               supersedes=row.get("supersedes", ""),
                               published_at=row.get("registered_at", stamp),
                               evidence_ref="factor-fields.json", verified=True))
            originals.append(dict(
                product_id="factor_actions", record_key=key, revision_id=revision, published_at=stamp,
                value=row[field], source_ref="raw/factor_actions/corporate_actions.csv",
                announcement_ref=row.get("source_ref"), coordinate=row["coordinate"],
                **row.get("field_evidence", {}).get(field, {}),
            ))
    evidence = json.dumps(dict(records=originals), ensure_ascii=False).encode()
    for row in proofs:
        row["sha256"] = hashlib.sha256(evidence).hexdigest()
    case = dict(
        csvs={"knowledge_evidence": pd.DataFrame(proofs).drop_duplicates().to_csv(index=False)},
        table_products={"knowledge_evidence": ("factor_input", "catalog")},
        obtained_at="2024-03-01T18:00:00+08:00",
        root_config=True,
        permissions={"knowledge_evidence": {"allowed_uses": ["factor_calculation" if allowed else "daily_archive"]}},
        source_metadata={"knowledge_evidence": {"data_nature": "离线合成测试快照"}},
    )
    if calendar := tables.get("calendar"):
        case["csvs"]["calendar"] = pd.DataFrame(calendar)[["date", "is_trading_day"]].to_csv(index=False)
        case["table_products"]["calendar"] = ("factor_calendar", "calendar")
        case["permissions"]["calendar"] = {"allowed_uses": ["factor_calculation"]}
        case["source_metadata"]["calendar"] = {"published_at": max(row["available_at"] for row in calendar)}
    if native_actions:
        columns = ["code", "ex_date", "action_type", "d", "same_share_basis", "available_at"]
        observed = native_actions if native_action_rows is None else native_action_rows
        case["csvs"]["corporate_actions"] = pd.DataFrame(observed)[columns].drop_duplicates().to_csv(index=False)
        case["table_products"]["corporate_actions"] = ("factor_actions", "actions")
        case["permissions"]["corporate_actions"] = {"allowed_uses": ["factor_calculation"]}
    store, ref = build_case_store(path, case)
    (store.root / "evidence" / "factor-fields.json").write_bytes(evidence)
    return store, ref


def native_dividend_correction(path, *, observed_at, registered_at=None, cycle_value=None):
    _, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    august, october, *remaining = tables["corporate_actions"]
    tables["corporate_actions"] = [august, *remaining]
    october.update(action_type="cash_dividend", coordinate="row1", source_ref="october.pdf", revision_id="v1",
                   field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    corrected = dict(october, d=.4, revision_id="v2", supersedes="v1", available_at="2024-01-31T18:00:00+08:00")
    if cycle_value is not None:
        october["supersedes"] = "v2"
        corrected.update(d=.31, available_at="2024-01-31T14:00:00+08:00")
    if registered_at:
        corrected["registered_at"] = registered_at
    observed = october if observed_at is None else dict(corrected, available_at=observed_at)
    if cycle_value is not None:
        observed["d"] = cycle_value
    store, ref = local_fact_source(path, tables, native_actions=[october, corrected], native_action_rows=[observed])
    return store, ref, query


def example_query(factor_id, *, params=None):
    from datetime import date

    from hetu_quant.factors import FactorRequest, close_cutoff
    from hetu_quant.factors.catalog import get_factor

    source = TeachingSource(get_factor(factor_id).example_id)
    document = teaching_document(source)
    day = date.fromisoformat(document["trade_date"])
    return source, document, FactorRequest(factor_id, tuple(document["subjects"]), day, close_cutoff(day), params or {})
