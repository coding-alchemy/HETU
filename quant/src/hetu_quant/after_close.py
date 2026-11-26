"""Generate and reopen independent, immutable after-close research publications."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

import pandas as pd
from hetu_data.library import (
    daily_codes,
    query_library,
    read_known_closes,
    read_scoped_product,
    selected_snapshot_assets,
)
from hetu_data.models import LibraryRef

from hetu_quant.backtest_data import (
    CN,
    _calendar_flags,
    _complete_month_ends,
    _permission_sources,
    _public_at,
    _signal_calendar,
    read_low_volatility_inputs,
)
from hetu_quant.data import AccessDenied, DataQualityError, DataStore, SnapshotTamperedError
from hetu_quant.models import RankingDefinition, ResultRef, SnapshotRef
from hetu_quant.ranking import freeze_ranking_protocol, run_ranking
from hetu_quant.strategies import build_signal_targets

STRATEGY = "low_volatility"
_RESULT_KIND = "after_close"
_OBS = ("code", "date", "open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny",
        "product_id", "source_ref")
_OBSERVATION_USE = "daily_archive"
_OBSERVATION_AUXILIARIES = ("calendar", "security_status", "security_events", "securities")
_RULE = "37个完整月末形成36个月税前总收益；样本标准差、最低十分位、并列按代码、收盘时点市值加权"


def _json_records(frame):
    return json.loads(frame.to_json(orient="records", double_precision=15, date_format="iso"))


def _publish_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _manifest_path(root, result_id):
    if not result_id or Path(result_id).name != result_id or result_id.startswith("."):
        raise ValueError("无效的盘后发布标识")
    return Path(root) / "results" / result_id / "result.json"


def read_after_close(ref: ResultRef) -> dict:
    path = _manifest_path(ref.root, ref.result_id)
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("kind") != _RESULT_KIND or result.get("result_id") != ref.result_id:
        raise ValueError("所选结果不是有效的盘后发布")
    return result


def _public_result_docs(root):
    base = Path(root) / "results"
    for path in sorted(base.glob("*/result.json")):
        if path.parent.name.startswith(".") or path.is_symlink() or path.parent.is_symlink():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("kind") == _RESULT_KIND and value.get("result_id") == path.parent.name:
            yield value


def _sources(store, source, tables):
    seen = {}
    for table in tables:
        for owner, ref, manifest in selected_snapshot_assets(store, source, table):
            for entry in manifest.get("table_files", []):
                if entry["table"] != table:
                    continue
                product = entry["product_id"]
                key = str(owner.root), ref.snapshot_id, table, product
                if key in seen:
                    continue
                try:
                    sources, _ = _permission_sources(owner, manifest, table, product)
                except AccessDenied:
                    continue
                seen[key] = dict(root=key[0], snapshot_id=ref.snapshot_id, table=table, product_id=product,
                                 sources=sources, fee_class=owner.product(product).get("fee_class"))
    return list(seen.values())


def _observation_products(store, source, day, table):
    products, gaps = {}, []
    for owner, ref, manifest in selected_snapshot_assets(store, source, table):
        root = str(owner.root.resolve())
        registered = {entry["product_id"] for entry in manifest["table_files"] if entry["table"] == table}
        for product in sorted(registered):
            try:
                sources, permission = _permission_sources(owner, manifest, table, product)
                uses = [entry["allowed_uses"] for entry in [permission, *sources] if "allowed_uses" in entry]
                explicit_use = table in {"calendar", "security_status", "knowledge_evidence"}
                if ((uses or not explicit_use)
                        and all(isinstance(scope, list) and _OBSERVATION_USE in scope for scope in uses)):
                    products.setdefault((root, ref.snapshot_id), []).append(product)
                    continue
                reason = "原价观察用途未许可"
            except AccessDenied as error:
                reason = str(error)
            gaps.append(dict(date=day, field=table, source_root=root, snapshot_id=ref.snapshot_id,
                             product_id=product, purpose=_OBSERVATION_USE, reason=reason))
    return products, gaps


def _read_observation_table(store, source, table, *, start, end, codes, fields, products):
    frames = []
    for owner, ref, _ in selected_snapshot_assets(store, source, table):
        key = str(owner.root.resolve()), ref.snapshot_id
        for product_id in products.get(key, ()):
            frame = read_scoped_product(owner, ref, table, codes=codes, start=start, end=end, columns=fields,
                                        product_id=product_id)
            if not frame.empty:
                frames.append(frame.assign(source_root=key[0], snapshot_id=key[1]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=fields)


def _observation_bars(store, source, start, through, codes, products):
    if isinstance(source, LibraryRef) and codes:
        result = query_library(store, source, codes=codes, start=start, end=through,
                               daily_products=products["daily_bars"],
                               auxiliary_products={table: products[table] for table in _OBSERVATION_AUXILIARIES})
        return result["bars"], _json_records(result["gaps"])
    return _read_observation_table(store, source, "daily_bars", start=start, end=through, codes=codes, fields=_OBS,
                                   products=products["daily_bars"]), []


def _conflicting_observation(rows, code, day, field):
    coordinates = [column for column in ("source_root", "snapshot_id", "product_id", "source_ref", field)
                   if column in rows]
    details = rows.loc[rows[field].notna(), coordinates].drop_duplicates().sort_values(coordinates)
    details = details.rename(columns={field: "value"})
    if field == "amount_cny":
        details["unit"] = "人民币元"
    return dict(code=code, date=day, field=field, reason="行情冲突", sources=_json_records(details))


def _observation_metrics(bars, calendar, day, codes):
    observations, gaps = [], []
    documented = _calendar_flags(calendar)
    sessions = sorted(period for period, trading in documented.items() if trading and period <= day)
    for code in codes:
        own = bars[bars.code.eq(code)]
        row = own[own.date.eq(day)]
        if row.empty:
            gaps.append(dict(code=code, date=day, field="daily_bars", reason="无行情，暂停或缺采原因待证"))
            continue
        fact = _json_records(row.iloc[:1])[0]
        for field in _OBS[2:8]:
            values = row[field].dropna().unique()
            if len(values) == 1:
                fact[field] = values[0]
            else:
                fact[field] = None
                gaps.append(_conflicting_observation(row, code, day, field) if len(values) else
                            dict(code=code, date=day, field=field, reason="原价或成交字段缺证"))
        fact["price_basis"] = "原价"
        by_date = own.groupby("date")[["close_raw", "amount_cny"]]
        counts = by_date.nunique()
        history = by_date.first().where(counts.eq(1))
        conflicts = set()
        for needed, field in ((2, "raw_change"), (20, "amount_median_20d"), (61, "raw_volatility_60d")):
            recent = sessions[-needed:]
            missing_dates = []
            input_field = "amount_cny" if field == "amount_median_20d" else "close_raw"
            if (len(recent) != needed or recent[-1] != day
                    or not set(pd.date_range(recent[0], day).strftime("%Y-%m-%d")).issubset(documented)):
                gap = dict(reason="获许可的连续交易日历不足", input_field="calendar", required_sessions=needed)
            else:
                competing = counts[input_field].reindex(recent[:-1]).gt(1)
                conflicts.update((conflict_day, input_field) for conflict_day in competing.index[competing])
                values = pd.to_numeric(history[input_field].reindex(recent), errors="coerce")
                usable = values.ge(0) if field == "amount_median_20d" else values.gt(0)
                missing_dates = values.index[~usable].tolist()
                gap = dict(reason="连续有效交易日或字段不足", input_field=input_field)
                if not missing_dates:
                    if field == "amount_median_20d":
                        fact[field] = float(values.median())
                    else:
                        returns = values.pct_change(fill_method=None).dropna()
                        fact[field] = float(returns.iloc[-1] if needed == 2 else returns.std(ddof=1) * (252 ** 0.5))
            if field not in fact:
                if missing_dates:
                    gap.update(start=missing_dates[0], end=missing_dates[-1], missing_dates=missing_dates)
                gaps.append(dict(code=code, date=day, field=field, **gap))
        for conflict_day, field in sorted(conflicts):
            gaps.append(_conflicting_observation(own[own.date.eq(conflict_day)], code, conflict_day, field))
        observations.append(fact)
    return observations, gaps


def _ranking_input(store, source, purpose):
    tables = {"calendar", "daily_bars", "security_status", "corporate_actions", "boards", "memberships", "index_bars"}
    if isinstance(source, LibraryRef):
        from hetu_data.library import load_library

        refs = {(item["root"], item["snapshot_id"]) for item in load_library(store, source)["asset_snapshots"]
                if tables <= set(item["tables"])}
    else:
        refs = {(str(store.root), source.snapshot_id)}
    matched = []
    for owner, ref, manifest in selected_snapshot_assets(store, source, "daily_bars"):
        if (str(owner.root), ref.snapshot_id) not in refs or manifest.get("mode", "free") != "free":
            continue
        entries = {(r["table"], r["product_id"]) for r in manifest["table_files"] if r["table"] in tables}
        if {table for table, _ in entries} != tables:
            continue
        denied = False
        for table, product in entries:
            sources, permission = _permission_sources(owner, manifest, table, product)
            if not sources or any(purpose not in item["allowed_uses"]
                                  for item in [permission, *sources] if "allowed_uses" in item):
                denied = True
                break
        if not denied:
            matched.append(ref)
    return matched[0] if len(matched) == 1 else None


def _rankings(store, source, day, staging):
    as_of = datetime.combine(day, time.max, CN)
    purpose = "ranking_current" if day == datetime.now(CN).date() else "ranking_historical"
    try:
        ref = _ranking_input(store, source, purpose)
        if ref is None:
            raise ValueError(f"{purpose} 缺少唯一获许可的配套快照、板块成员、基准或行动数据")
        # The original ranking endpoint freezes and applies its own snapshot, membership and benchmark rules.
        definition = freeze_ranking_protocol(RankingDefinition(
            as_of=as_of, cutoff=day, snapshot_id=ref.snapshot_id, benchmark_id="csi300_price",
            parameters={"purpose": purpose},
        ))
        ranked = run_ranking(definition, ref, staging / "ranking", as_of_boundary=True)
    except (AccessDenied, DataQualityError, SnapshotTamperedError, OSError, ValueError) as error:
        return dict(status="limited", trade_date=str(day), as_of=as_of.isoformat(), purpose=purpose,
                    limitations=[str(error)], boards=[], slots=[], cards=[], exclusions=[])
    base = ranked.root / "results" / ranked.result_id
    manifest = json.loads((base / "result.json").read_text(encoding="utf-8"))
    return dict(status=ranked.status, result_id=ranked.result_id, relative=str(base.relative_to(staging)),
                trade_date=str(day), as_of=as_of.isoformat(), purpose=purpose, sources=manifest["sources"],
                benchmark=manifest["benchmark"], limitations=manifest["limitations"], **{
                    key: _json_records(pd.read_parquet(base / f"{key}.parquet"))
                    for key in ("boards", "slots", "cards", "exclusions")
                })


def _read_observations(store, source, *, trade_date, staging):
    start = (pd.Timestamp(trade_date) - pd.DateOffset(years=1)).date()
    day = str(trade_date)
    gaps = []
    products = {table: {} for table in ("daily_bars", *_OBSERVATION_AUXILIARIES)}
    try:
        for table in products:
            products[table], denied = _observation_products(store, source, day, table)
            gaps.extend(denied)
        codes = daily_codes(store, source, start=date(1900, 1, 1), end=trade_date,
                            daily_products=products["daily_bars"])
        bars, issues = _observation_bars(store, source, start, trade_date, codes, products)
        if bars.empty and codes:
            bars, issues = _observation_bars(store, source, date(1900, 1, 1), trade_date, codes, products)
        gaps.extend(issues)
    except (AccessDenied, SnapshotTamperedError) as error:
        bars = pd.DataFrame(columns=_OBS)
        codes = ()
        gaps.append(dict(date=day, field="daily_bars", reason=str(error)))
    valid = bars[bars[list(_OBS[2:8])].notna().any(axis=1)]
    actual = str(valid.date.max()) if not valid.empty else None
    observations = []
    if actual:
        try:
            calendar = _read_observation_table(store, source, "calendar", start=start, end=date.fromisoformat(actual),
                                               codes=(), fields=("date", "is_trading_day"),
                                               products=products["calendar"])
        except (AccessDenied, SnapshotTamperedError) as error:
            calendar = pd.DataFrame(columns=["date", "is_trading_day"])
            gaps.append(dict(date=day, field="calendar", reason=str(error)))
        observations, observed_gaps = _observation_metrics(valid, calendar, actual, codes)
        gaps.extend(observed_gaps)
        at = datetime.combine(trade_date, time(15), CN)
        try:
            knowledge_products, denied = _observation_products(store, source, actual, "knowledge_evidence")
            gaps.extend(denied)
            current, _ = read_known_closes(store, source, codes=codes, start=date.fromisoformat(actual),
                                           end=date.fromisoformat(actual), at=datetime.now(CN),
                                           daily_products=products["daily_bars"], knowledge_products=knowledge_products)
        except (AccessDenied, SnapshotTamperedError) as error:
            current = pd.DataFrame(columns=["code", "close_raw", "published_at"])
            gaps.append(dict(date=day, field="close_raw_version", reason=str(error)))
        for row in observations:
            match = current[current.code.eq(row["code"]) & current.close_raw.eq(row["close_raw"])]
            published = match.iloc[0].published_at if len(match) else None
            if published and not _public_at(published, at):
                row["revised_published_at"] = published
            elif not published:
                row["version_note"] = "原价公开时点未知"
    if actual != day:
        gaps.append(dict(date=day, field="daily_bars", reason=f"请求日行情未到，实际截止 {actual or '未知'}"))
    # A same-day known suspension is distinct from an unresolved absence.
    if codes:
        try:
            states = _read_observation_table(store, source, "security_status", start=trade_date, end=trade_date,
                                             codes=codes, fields=("code", "is_suspended"),
                                             products=products["security_status"])
            suspended = sorted({str(row.code) for row in states.itertuples()
                                if str(row.is_suspended).lower() in ("true", "1")})
        except (AccessDenied, SnapshotTamperedError) as error:
            suspended = []
            gaps.append(dict(date=day, field="security_status", reason=str(error)))
    else:
        suspended = []
    present = sorted({row["code"] for row in observations if row["date"] == day})
    return dict(actual_cutoff=actual, coverage=dict(codes=present, count=len(present), pool_size=None,
                                                    suspended=suspended,
                                                    unresolved=sorted(set(codes) - set(present) - set(suspended))),
                observations=observations, observation_gaps=gaps,
                rankings=_rankings(store, source, trade_date, staging))


def _target_record(target):
    fields = asdict(target)
    fields["signal_date"] = str(target.signal_date)
    fields["executable_from"] = str(target.executable_from) if target.executable_from else None
    fields["candidates"] = _json_records(target.candidates)
    fields["targets"] = _json_records(target.targets)
    fields["gaps"] = json.loads(pd.DataFrame(target.gaps).to_json(orient="records", date_format="iso"))
    return fields


def _previous(store, source, root, before, cutoff):
    saved = {}
    original_releases = sorted(_public_result_docs(root), key=lambda d: (d["generated_at"], d["result_id"]))
    for doc in original_releases:
        day = doc["signal_date"]
        if (doc["strategy_id"] == STRATEGY and day and day < before
                and doc["revision_of"] is None and doc["target"]["status"] == "ready"):
            saved.setdefault(day, doc)
    calendar = _signal_calendar(store, source, cutoff)
    ends = [d for d in _complete_month_ends(calendar) if d < before]
    rejected = []
    for day in sorted(set(saved) | set(ends[36:]), reverse=True):
        if doc := saved.get(day):
            return dict(signal_date=day, targets=doc["target"]["targets"], origin="saved", result_id=doc["result_id"],
                        computed_at=doc["generated_at"], export_allowed=doc.get("export_allowed", False)), None
        date_value = date.fromisoformat(day)
        target = build_signal_targets(read_low_volatility_inputs(store, source, signal_date=date_value), date_value)
        if target.status == "ready":
            return dict(signal_date=day, targets=_json_records(target.targets), origin="recomputed", result_id=None,
                        computed_at=datetime.now(CN).isoformat(),
                        source_id=getattr(source, "library_id", None) or source.snapshot_id), None
        rejected.append(f"{day} {target.gaps[0]['reason'] if target.gaps else target.status}")
    return None, "；".join(rejected) or "历史日历或合格信号所需证据不足"


def _target_changes(current, previous, candidates):
    old = previous["targets"] if previous else []
    matched = set()
    ranks = {row["code"]: row for row in candidates if row.get("rank") is not None}
    out = []
    for row in current:
        peers = [ref for ref in old if row["identity_id"] and ref["identity_id"] == row["identity_id"]]
        peer = peers[0] if len(peers) == 1 else None
        if peer:
            matched.add(peer["code"])
        uncertain = previous and not peer and (
            not row["identity_id"] or any(not ref["identity_id"] or ref["code"] == row["code"] for ref in old)
        )
        change = "保留" if peer else "身份待证" if uncertain else "入选"
        out.append(dict(change=change, code=row["code"], prior_code=peer["code"] if peer else None,
                        prior_weight=peer["target_weight"] if peer else None, weight=row["target_weight"],
                        reason=f"第 {row['rank']} 名，{row['reason']}" + ("；已证同一证券换码" if peer
                               and peer["code"] != row["code"] else "")))
    for row in old:
        if row["code"] in matched:
            continue
        rank = ranks.get(row["code"])
        identity_gap = not row["identity_id"] or any(not cur["identity_id"] or cur["code"] == row["code"]
                                                      for cur in current)
        out.append(dict(change="身份待证" if identity_gap else "退出", code=row["code"], prior_code=row["code"],
                        prior_weight=row["target_weight"], weight=None, reason=(
                            "两期证券身份不能确认" if identity_gap else
                            f"本期第 {rank['rank']} 名，未进入最低十分位" if rank else
                            next((x["reason"] for x in candidates if x["code"] == row["code"]), "不在本期合格池"))))
    return out


def generate_after_close(
    store: DataStore, source: SnapshotRef | LibraryRef, *, trade_date: date, output_root: Path,
    revision_of: str | None = None,
) -> ResultRef:
    """Explicit workbench operation. Publish complete results with one rename."""
    output_root = Path(output_root)
    if datetime.combine(trade_date, time(15), CN) > datetime.now(CN):
        raise ValueError("只能生成已经收盘的交易日观察")
    original = None
    if revision_of is not None:
        original = read_after_close(ResultRef(revision_of, output_root, "limited"))
        if original["trade_date"] != str(trade_date) or original["strategy_id"] != STRATEGY or original["revision_of"]:
            raise ValueError("修订必须引用同日、同策略的原发布")
    result_id = f"close-{trade_date:%Y%m%d}-{uuid4().hex[:12]}"
    final = _manifest_path(output_root, result_id).parent
    final.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".tmp-", dir=final.parent) as scratch:
        staging = Path(scratch)
        inputs = read_low_volatility_inputs(store, source, signal_date=trade_date)
        signal = build_signal_targets(inputs, trade_date)
        observations = _read_observations(store, source, trade_date=trade_date, staging=staging)
        observations["coverage"]["pool_size"] = signal.pool_size
        target = _target_record(signal)
        previous, previous_gap = _previous(store, source, output_root, str(trade_date), inputs.cutoff)
        status = ("limited" if signal.status in {"blocked", "calendar_unknown"} or target["gaps"]
                  or observations["observation_gaps"] or observations["rankings"]["status"] != "complete"
                  else "complete")
        source_id = source.library_id if isinstance(source, LibraryRef) else source.snapshot_id
        documents = _sources(store, source, ("daily_bars", *_OBSERVATION_AUXILIARIES, "historical_securities",
                                             "corporate_actions", "market_cap", "knowledge_evidence", "boards",
                                             "memberships", "index_bars", "theme_library"))
        data_marks = {src.get("data_nature") for document in documents for src in document["sources"]}
        export_allowed = not any(
            doc["fee_class"] == "paid" or any(src.get("allow_export") is False for src in doc["sources"])
            for doc in documents
        )
        if previous and previous["origin"] == "saved":
            export_allowed &= previous["export_allowed"]
        if original is not None:
            export_allowed &= original.get("export_allowed", False)
        value = dict(kind=_RESULT_KIND, result_id=result_id, status=status, strategy_id=STRATEGY,
                     trade_date=str(trade_date), signal_date=str(trade_date) if signal.status == "ready" else None,
                     cutoff=inputs.cutoff.isoformat(), generated_at=datetime.now(CN).isoformat(), rule=_RULE,
                     source=dict(kind="library" if isinstance(source, LibraryRef) else "snapshot",
                                 id=source_id, root=str(store.root.resolve()), assets=documents),
                     data_nature="离线合成样例" if data_marks == {"离线合成样例"} else "已发布本地输入",
                     export_allowed=export_allowed, target=target, previous=previous, previous_gap=previous_gap,
                     revision_of=revision_of,
                     changes=_target_changes(target["targets"], previous, target["candidates"]),
                     close_evidence=_json_records(inputs.closes), identity_evidence=_json_records(inputs.identities),
                     **observations)
        if value["signal_date"]:
            value.update(_frozen_evaluation_split(store, source, trade_date))
        if original is not None:
            value["revision_changes"] = _revision_changes(original, value)
        _publish_json(staging / "result.json", value)
        staging.rename(final)
    return ResultRef(result_id, output_root, status)



def _frozen_evaluation_split(store, source, trade_date):
    """Resolve any applicable split recorded by the selected publication's frozen study."""
    from hetu_quant.data_research import _read_delivery_file

    split_definitions = []
    for owner, ref, manifest in selected_snapshot_assets(store, source, "calendar"):
        directory = owner.root / manifest.get("qualification_delivery", "research/phase2-lowvol/qualification")
        handoff = _read_delivery_file(directory, "handoff.json")
        if handoff.get("snapshot_id") != ref.snapshot_id or handoff.get("supplemented_snapshots"):
            continue
        requirements = _read_delivery_file(directory, "requirements.json", "research-requirements.json")
        split_definitions.extend(
            item["definition"] for item in requirements.get("definitions", [])
            if item["definition"].get("strategy_id") == STRATEGY
        )
    segments = set()
    applicable = False
    for definition in split_definitions:
        first = max(str(trade_date + timedelta(days=1)), str(definition["start"]))
        if first > str(definition["end"]):
            continue
        split = definition.get("protocol", {}).get("time_split", {})
        if not split:
            continue
        applicable = True
        for start, end in split.values():
            if str(start) <= first <= str(end):
                segments.add((str(start), min(str(end), str(definition["end"]))))
    if not applicable:
        return {}
    if len(segments) != 1:
        return {"evaluation_split_unproved": True}
    start, end = next(iter(segments))
    return {"evaluation_split_start": start, "evaluation_split_end": end}

def _revision_changes(original, revised):
    old = {(r["code"], r["date"]): r for r in original["observations"]}
    new = {(r["code"], r["date"]): r for r in revised["observations"]}
    changes = []
    for code, day in sorted(old.keys() | new.keys()):
        left, right = old.get((code, day), {}), new.get((code, day), {})
        for field in ("open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny",
                      "revised_published_at"):
            if left.get(field) != right.get(field):
                changes.append(dict(code=code, date=day, field=field, before=left.get(field), after=right.get(field)))
    old_targets = {row["code"]: row for row in original["target"]["targets"]}
    new_targets = {row["code"]: row for row in revised["target"]["targets"]}
    for code in sorted(old_targets.keys() | new_targets.keys()):
        for field in ("rank", "signal_value", "target_weight"):
            left = old_targets.get(code, {}).get(field)
            right = new_targets.get(code, {}).get(field)
            if left != right:
                changes.append(dict(code=code, date=revised["trade_date"], field=field, before=left, after=right))
    return changes


def after_close_origin(root: Path, publication_id: str, signal_date: date, strategy_id: str) -> dict:
    value = read_after_close(ResultRef(publication_id, Path(root), "complete"))
    if not value["signal_date"] or str(signal_date) != value["signal_date"] or strategy_id != value["strategy_id"]:
        raise ValueError("发布版本、策略和信号日不匹配")
    return value


def _evidence_path(root, kind, source_id):
    if not source_id or Path(source_id).name != source_id or source_id.startswith("."):
        raise ValueError("无效的后续证据标识")
    if kind == "simulation":
        folder, name = "backtests", "backtest.json"
    else:
        folder, name = "after-close-evaluations", "evaluation.json"
    return Path(root) / folder / source_id / name


def _simulation_signal(root, source_id, origin):
    from hetu_quant.backtest_reporting import read_backtest
    from hetu_quant.experiments import ExperimentStore

    account = read_backtest(ExperimentStore(root).get_backtest(source_id))
    manifest = account["manifest"]
    tables = account["tables"]
    definition = manifest["definition"]
    day = origin["signal_date"]
    if definition["strategy_id"] != origin["strategy_id"]:
        raise ValueError("模拟策略与所选发布不一致")
    targets = tables["targets"][tables["targets"].signal_at.astype(str).str[:10].eq(day)]
    signals = tables["signals"][tables["signals"].signal_at.astype(str).str[:10].eq(day)]
    expected = origin["target"]["targets"]
    if originating := (manifest.get("research") or {}).get("after_close_origin"):
        if originating != _origin_coordinate(origin):
            raise ValueError("模拟明确属于另一版盘后发布")
        if not _same_signal(targets, signals, expected):
            raise ValueError("模拟信号、排名或权重与所选发布不一致")
    else:
        # A legacy account only carries a snapshot ID. Require its retained input
        # files to corroborate the unique matching release before assigning it.
        bundle = Path(root) / "backtests" / source_id / "bundle"
        args = bundle / "signals.parquet", bundle / "tables" / "daily_bars.parquet"
        if not all(path.is_file() for path in args):
            raise ValueError("旧模拟缺少可核对的信号和行情输入")
        original_scores = pd.read_parquet(args[0])
        if not {"signal_at", "rank", "signal_value", "code"} <= set(original_scores.columns):
            raise ValueError("旧模拟缺少可核对的原始信号分数")
        original_scores = original_scores[original_scores.signal_at.astype(str).str[:10].eq(day)]
        bars = pd.read_parquet(args[1], columns=["code", "date", "close_raw"])
        closes = bars.groupby(["code", "date"]).close_raw.agg(lambda group: set(pd.to_numeric(group, errors="coerce")))
        matching = [doc["result_id"] for doc in _public_result_docs(root)
                    if doc["signal_date"] == day and doc["strategy_id"] == definition["strategy_id"]
                    and doc["source"]["kind"] == "snapshot" and doc["source"]["id"] == definition["snapshot_id"]
                    and _same_signal(targets, original_scores, doc["target"]["targets"])
                    and doc["close_evidence"] and all(closes.get((fact["code"], fact["date"])) == {fact["close_raw"]}
                                                      for fact in doc["close_evidence"])]
        if matching != [origin["result_id"]]:
            raise ValueError("旧模拟不能唯一对应所选盘后发布版本")
    return account


def _same_signal(targets, signals, expected):
    import math

    if (len(targets) != len(expected) or len(signals) != len(expected)
            or set(targets.code) != {r["code"] for r in expected}
            or set(signals.code) != {r["code"] for r in expected}):
        return False
    weights = targets.set_index("code").target_weight
    scores = signals.set_index("code")
    try:
        return all(math.isclose(float(weights[r["code"]]), r["target_weight"], abs_tol=1e-12)
                   and math.isclose(float(scores.loc[r["code"], "signal_value"]), r["signal_value"], abs_tol=1e-12)
                   and int(scores.loc[r["code"], "rank"]) == r["rank"] for r in expected)
    except (TypeError, ValueError):
        return False


def _origin_coordinate(origin):
    return dict(publication_id=origin["result_id"], signal_date=origin["signal_date"],
                strategy_id=origin["strategy_id"])


def link_after_close_evidence(
    output_root: Path, publication_id: str, *, signal_date: date, strategy_id: str,
    kind: Literal["simulation", "evaluation"], source_id: str,
) -> None:
    """Register only a published source whose exact signal version can be proved."""
    if kind not in {"simulation", "evaluation"}:
        raise ValueError("未知的后续证据类型")
    origin = after_close_origin(output_root, publication_id, signal_date, strategy_id)
    path = _evidence_path(output_root, kind, source_id)
    published = json.loads(path.read_text(encoding="utf-8"))
    if kind == "simulation":
        _simulation_signal(output_root, source_id, origin)
    elif (published.get("evaluation_id") != source_id or any(
        published.get(key) != value for key, value in _origin_coordinate(origin).items()
    )):
        raise ValueError("评价证据属于另一版盘后发布")
    record = {**_origin_coordinate(origin), "kind": kind, "source_id": source_id}
    directory = Path(output_root) / "after-close-evidence" / publication_id
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".tmp-", dir=directory) as scratch:
        candidate = Path(scratch) / "record.json"
        _publish_json(candidate, record)
        try:
            os.link(candidate, directory / f"{kind}-{source_id}.json")
        except FileExistsError:
            pass


def list_after_close_evidence(output_root: Path, publication_id: str) -> list[dict]:
    original = read_after_close(ResultRef(publication_id, Path(output_root), "complete"))
    directory = Path(output_root) / "after-close-evidence" / publication_id
    records = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if all(record.get(key) == value for key, value in _origin_coordinate(original).items()):
            records.append(record)
    return records


def read_after_close_evidence(output_root: Path, record: dict) -> dict:
    kind, source = record["kind"], record["source_id"]
    publication = after_close_origin(
        output_root, record["publication_id"], record["signal_date"], record["strategy_id"],
    )
    if kind == "evaluation":
        result = json.loads(_evidence_path(output_root, kind, source).read_text(encoding="utf-8"))
        if any(result.get(key) != value for key, value in _origin_coordinate(publication).items()):
            raise ValueError("评价归属不一致")
        return result
    account = _simulation_signal(output_root, source, publication)
    tables = account["tables"]
    orders = tables["orders"][tables["orders"].signal_at.astype(str).str[:10].eq(record["signal_date"])]
    trades = tables["trades"][tables["trades"].order_id.isin(orders.order_id)]
    fees = tables["costs"][tables["costs"].order_id.isin(orders.order_id)]
    execution_dates = set(trades.executed_at.astype(str).str[:10]) | set(orders.submitted_at.astype(str).str[:10])
    other = tables["costs"][tables["costs"].order_id.fillna("").eq("")
                           & tables["costs"].date.astype(str).str[:10].isin(execution_dates)]
    label_path = Path(output_root) / "backtests" / source / "factor-labels.parquet"
    labels = pd.read_parquet(label_path) if label_path.is_file() else pd.DataFrame()
    if not labels.empty:
        labels = labels[labels.signal_at.astype(str).str[:10].eq(record["signal_date"])]
    excluded = (account["evaluation"].get("factor_diagnostics") or {}).get("excluded", [])
    return dict(orders=_json_records(orders), trades=_json_records(trades), costs=_json_records(fees),
                unassigned_costs=_json_records(other), labels=_json_records(labels),
                excluded=[row for row in excluded if str(row["signal_at"])[:10] == record["signal_date"]])
