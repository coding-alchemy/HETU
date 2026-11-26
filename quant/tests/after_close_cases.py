"""Offline after-close fixtures. All rows and publication evidence are synthetic."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date

import pandas as pd
from backtest_cases import build_case_store, make_backtest_case
from fixtures import annotate_actions_coverage

JAN = date(2024, 1, 31)
FEB = date(2024, 2, 2)


def make_after_close_source(
    path, *, bars_through=JAN, calendar_through=date(2024, 2, 5), calendar_gap=None, perturb=False,
    change_close=None, missing_cap=False, no_pool=False, denied=None, correction_only=False,
    correction=False, action=None, prior=False, rankings=False, future_close=None, evaluation_split=None,
    rename=None, deny_export=False, daily_allowed_uses=None, amount_change=None,
    auxiliary_uses=None, suspended_code=None,
):
    case = make_backtest_case("history", variant="signal_pool")
    codes = tuple(f"VOL_{n:02d}" for n in range(1, 21))
    rename_date = "2024-01-15"

    def ticker(code, day):
        return "VOL_RENAMED" if rename and code == "VOL_01" and day >= rename_date else code
    start = "2020-12-01" if prior else "2021-01-01"
    stamp = "2024-02-02T12:00:00+08:00"
    week = pd.date_range(start, calendar_through)
    calendar = pd.DataFrame(dict(date=week.strftime("%Y-%m-%d"), is_trading_day=week.weekday < 5))
    if calendar_gap:
        calendar = calendar[calendar.date.ne(calendar_gap)]
    sessions = calendar[calendar.is_trading_day].date.tolist()
    points = pd.Series(sessions, dtype=str).groupby(lambda i: sessions[i][:7]).last().tolist()
    available = [day for day in points if day <= str(bars_through)]
    if str(bars_through) in sessions:
        available.append(str(bars_through))
    dates = sorted(set(available) | {day for day in sessions if "2023-07-01" <= day <= str(bars_through)})
    bars, statuses, caps = [], [], []
    for index, code in enumerate(codes, start=1):
        for day in dates:
            n = (pd.Period(day, "M") - pd.Period("2020-12", "M")).n
            amplitude = (index - 1) / 100
            close = round(10 * (1 + amplitude * (n % 2)), 6)
            if prior and not rename and index == 1 and day == "2020-12-31":
                close = 5
            if day == str(JAN) and index == 1 and (correction or correction_only):
                close = 12
            if day == str(JAN) and index == 1 and change_close is not None:
                close = change_close
            if future_close and index == 1 and day in future_close:
                close = future_close[day]
            if perturb and day > str(JAN):
                close *= 9
            amount = amount_change[1] if amount_change and index == 1 and day == amount_change[0] else 30000000
            bars.append(dict(code=ticker(code, day), date=day, open_raw=close, high_raw=close,
                             low_raw=close, close_raw=close, volume_shares=1000 + index, amount_cny=amount))
            statuses.append(dict(code=ticker(code, day), date=day, is_listed=True, is_st=False,
                                 is_suspended=code == suspended_code and day == str(bars_through)))
        for day in ("2023-12-29", str(JAN), "2024-02-29", "2024-04-30", "2024-07-31"):
            if (missing_cap and index == 2 and day == str(JAN)) or day > str(bars_through):
                continue
            cap = {1: 1, 2: 3}.get(index, index) * 1e9
            caps.append(dict(code=ticker(code, day), date=day, market_cap_cny=cap,
                             available_at=f"{day}T14:55:00+08:00"))

    base_hist = pd.read_csv(io.StringIO(case["csvs"]["historical_securities"]), dtype=str).fillna("")
    base_knowledge = pd.read_csv(io.StringIO(case["csvs"]["knowledge_evidence"]), dtype=str).fillna("")
    hist = base_hist[base_hist.code.eq("VOL_01")]
    histories, identity_proofs, objects = [], [], []
    registered_codes = codes + (("VOL_NEXT",) if perturb else ()) + (("VOL_RENAMED",) if rename else ())
    evidence_refs = []
    for code in registered_codes:
        future = code == "VOL_NEXT"
        proof = f"ev-sp-{code.lower()}"
        evidence_refs.append(proof)
        identity = "synthetic-VOL_01" if future or code == "VOL_RENAMED" else f"synthetic-{code}"
        left = rename_date if code == "VOL_RENAMED" else "2024-02-05" if future else "2020-01-02"
        published = f"{left}T09:00:00+08:00"
        histories.append(hist.assign(code=code, valid_from=left, available_at=published, evidence_ref=proof))
        identity_proofs.append(base_knowledge.iloc[:1].assign(
            record_key=code, published_at=published, evidence_ref=proof,
            sha256=hashlib.sha256(f"合成证据原件:{proof}\n".encode()).hexdigest(),
        ))
        right = ""
        if rename and code == "VOL_01":
            right = "2024-01-12"
            revised = "ev-sp-vol_01-renamed"
            announced = "2024-01-05T09:00:00+08:00"
            evidence_refs.append(revised)
            histories.append(hist.assign(code=code, valid_from=left, valid_to=right,
                                         available_at=announced, evidence_ref=revised))
            identity_proofs.append(base_knowledge.iloc[:1].assign(
                record_key=code, published_at=announced, evidence_ref=revised,
                revision_id="rename", supersedes="orig",
                sha256=hashlib.sha256(f"合成证据原件:{revised}\n".encode()).hexdigest(),
            ))
        objects.append(dict(code=code, identity_id=identity, market="SH", board="main",
                            security_type="stock", valid_from=left, valid_to=right, source_ref="synthetic-roster.json"))
        if rename == "proved" and code == "VOL_RENAMED":
            objects[-1]["continuation"] = dict(kind="administrative_rename", from_code="VOL_01", ratio=1,
                                                effective_date=rename_date, published_at="2024-01-05T09:00:00+08:00")

    facts, attestations = [], []
    daily_product = case["table_products"]["daily_bars"][0]
    evidence_name = "closes-revised.json" if (correction or correction_only) else "closes.json"
    for bar in bars:
        code, day, value = bar["code"], bar["date"], bar["close_raw"]
        later = (correction or correction_only) and code == "VOL_01" and day == str(JAN)
        key = f"{code}|{day}|close_raw"
        revision = "revised" if later else "original"
        published = stamp if later else f"{day}T15:00:00+08:00"
        attestations.append(dict(product_id=daily_product, record_key=key, value=value,
                                 revision_id=revision, published_at=published))
        facts.append(dict(table_name="daily_bars", record_key=key, revision_id=revision,
                          supersedes="original" if later else "", published_at=published,
                          evidence_ref=evidence_name, verified=True))
    body = json.dumps(dict(records=attestations)).encode()
    digest = hashlib.sha256(body).hexdigest()
    for fact in facts:
        fact["sha256"] = digest
    events = pd.read_csv(io.StringIO(case["csvs"]["corporate_actions"]), dtype=str)
    if perturb:
        events = pd.DataFrame([dict(code="VOL_01", ex_date="2024-02-02", action_type="rights_issue",
                                    available_at="2024-02-01T12:00:00+08:00")])
    if action:
        events = pd.concat([events, pd.DataFrame([action])], ignore_index=True)

    frames = dict(calendar=calendar, daily_bars=pd.DataFrame(bars), security_status=pd.DataFrame(statuses),
                  historical_securities=pd.concat(histories), market_cap=pd.DataFrame(caps),
                  corporate_actions=events, knowledge_evidence=pd.concat([*identity_proofs, pd.DataFrame(facts)]))
    if rankings:
        frames.update(_ranking_facts(codes, dates))
        for table in ("boards", "memberships", "index_bars", "theme_library"):
            kind = "index" if table == "index_bars" else "catalog"
            case["table_products"][table] = (f"test_close_{table}", kind)
    case["csvs"].update({table: frame.to_csv(index=False) for table, frame in frames.items()})
    case["evidence_refs"] = tuple(evidence_refs)
    uses = ["daily_archive", "ranking_historical", "ranking_current", "backtest_low_volatility"]
    case["permissions"] = {table: {"allowed_uses": uses if table == denied else [*uses, "low_volatility_preparation"]}
                           for table in case["csvs"]}
    if daily_allowed_uses is not None:
        case["permissions"]["daily_bars"]["allowed_uses"] = daily_allowed_uses
    for table, allowed in (auxiliary_uses or {}).items():
        case["permissions"][table] = {} if allowed is None else {"allowed_uses": allowed}
    case["root_config"] = True
    case["source_metadata"] = {
        table: {"data_nature": "离线合成样例", **({"published_at": "2020-10-30T12:00:00+08:00"}
                                               if table == "calendar" else {})} for table in case["csvs"]
    }
    if deny_export:
        case["source_metadata"]["daily_bars"]["allow_export"] = False
    obtained = {table: f"{bars_through}T18:00:00+08:00" for table in frames if table != "knowledge_evidence"}
    store, ref = build_case_store(path, case, obtained_overrides=obtained)
    evidence = store.root / "evidence"
    (evidence / evidence_name).write_bytes(body)
    annotate_actions_coverage(store, ref, start, str(bars_through), codes=registered_codes, query_basis="ex_date")
    if not no_pool:
        _bind_roster(store, ref, objects, start, str(calendar_through), evaluation_split=evaluation_split)
    return store, ref


def _bind_roster(store, ref, objects, start, end, *, evaluation_split=None):
    directory = store.root / "research" / "phase2-lowvol" / "qualification"
    directory.mkdir(parents=True, exist_ok=True)
    roster = store.root / "synthetic-roster.json"
    roster.write_text(json.dumps(objects))
    requirements = dict(start=start, end=end, objects=objects, target_scope=dict(complete=True, source_ref=roster.name))
    if evaluation_split:
        requirements["definitions"] = [dict(definition=dict(
            strategy_id="low_volatility", start="2024-02-01", end=end,
            protocol=dict(time_split={"validate": evaluation_split}),
        ))]
    (directory / "requirements.json").write_text(json.dumps(requirements))
    (directory / "handoff.json").write_text(json.dumps(dict(snapshot_id=ref.snapshot_id, mode="free")))


def _ranking_facts(codes, days):
    stamp = "2023-06-01T10:00:00+08:00"
    boards = [
        dict(board_id="SYN_INDUSTRY", board_kind="industry", name="合成行业", provider="synthetic",
             classification_version="synthetic-v1"),
        dict(board_id="BK0425", board_kind="theme", name="工程建设", provider="eastmoney",
             classification_version="synthetic-v1"),
    ]
    return dict(
        boards=pd.DataFrame(boards),
        theme_library=pd.DataFrame([dict(theme_code="synthetic-theme", theme_name="工程建设")]),
        memberships=pd.DataFrame([
            dict(board_id=board["board_id"], code=code, observed_at=stamp, available_at=stamp,
                 effective_from="2023-06-01", effective_to="", membership_kind="historical")
            for board in boards for code in codes[:10]
        ]),
        index_bars=pd.DataFrame([
            dict(code=code, date=day, close=100 + i / 40 + (i % 3) / 100,
                 amount_cny=3e8, return_basis="price")
            for code in ("000300.SH", "SYN_INDUSTRY") for i, day in enumerate(days)
        ]),
    )


def make_revision_library(path, *, correction_only=False):
    from hetu_data.library import publish_library

    public_tables = ["daily_bars", "knowledge_evidence", "calendar", "historical_securities", "security_status",
                     "market_cap", "corporate_actions"]
    store, old = make_after_close_source(path)
    folder = store.root / "research" / "phase2-lowvol" / "qualification"
    goal = json.loads((folder / "requirements.json").read_text())
    store, new = make_after_close_source(path, correction=True)
    rule = dict(root=str(store.root), product_id="test_bt_daily", table="daily_bars",
                superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=new.snapshot_id,
                codes=["VOL_01"], start=str(JAN), end=str(JAN), evidence_ref="evidence/revision-link.json")
    (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
    asset = dict(root=str(store.root), snapshot_id=new.snapshot_id, tables=public_tables)
    old_asset = dict(root=str(store.root), snapshot_id=old.snapshot_id, tables=["daily_bars", "knowledge_evidence"])
    if not correction_only:
        asset["revision_selections"] = [rule]
    ref = publish_library(store, asset_snapshots=(asset,) if correction_only else (old_asset, asset))
    targets = store.root / "archive" / "targets"
    targets.mkdir(parents=True, exist_ok=True)
    (targets / "synthetic-pool.json").write_text(json.dumps(dict(target_id="synthetic-pool", requirements=goal)))
    coverage = store.root / "archive" / "coverage" / ref.library_id
    coverage.mkdir(parents=True, exist_ok=True)
    (coverage / "synthetic-pool.json").write_text(json.dumps(dict(target_scope_complete=True)))
    return store, ref
