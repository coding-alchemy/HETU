"""免费股本候选规范化及有界实际事实的生产导入；不生成信号或运行引擎。"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant.providers.cninfo import select_capital_facts

CN = ZoneInfo("Asia/Shanghai")


def import_capital_facts(store, base, facts: pd.DataFrame, originals: Path,
                         output: Path, obtained_at: str):
    """人工原件摘录+独立发布记录→原价市值/证据登记→真实import_files。

    不接受verified/资格输入；公开日须与官方记录一致。仅改已证有界事实
    对应的键，原表其余事实、原件和行动查询证明全部继承。
    """
    if {"verified", "can_run", "signals"} & set(facts.columns):
        raise ValueError("qualification values are not factual inputs")
    attachments = set()
    evidence = {}
    for row in facts.itertuples():
        if row.fact_kind != "actual_completed":
            continue
        names = [str(row.evidence_ref), str(row.publication_ref)]
        if any(Path(name).name != name for name in names):
            raise ValueError("unsafe evidence path")
        original = originals / row.evidence_ref
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        publication = json.loads((originals / row.publication_ref).read_text())
        records = [r for r in publication["announcements"]
                   if r["secCode"] == row.code.split(".")[0]
                   and r["announcementId"] == row.announcement_id
                   and row.announcement_id in r["adjunctUrl"]]
        if len(records) != 1:
            raise ValueError("publication binding missing or ambiguous")
        published = datetime.fromtimestamp(records[0]["announcementTime"] / 1000, CN).date()
        if published.isoformat() != row.published_date:
            raise ValueError("publication date mismatch")
        known = (published + timedelta(days=1)).isoformat() + "T00:00:00+08:00"
        attachments.update(names)
        evidence[(row.code, row.evidence_ref)] = (digest, known)
    bars = store.read_table(base, "daily_bars")
    caps = store.read_table(base, "market_cap")
    knowledge = store.read_table(base, "knowledge_evidence")
    new_caps, new_knowledge = [], []
    for day, own in bars[bars.code.isin(facts.code)].groupby(bars.date.astype(str)):
        selected = select_capital_facts(facts, str(day))
        for row in selected.itertuples():
            prices = own[own.code == row.code]
            if not len(prices):
                continue
            digest, known = evidence[(row.code, row.evidence_ref)]
            available = max(known, str(day) + "T15:00:00+08:00")
            new_caps.append(dict(code=row.code, date=str(day),
                                 market_cap_cny=round(float(prices.iloc[0].close_raw) * int(row.total_shares), 2),
                                 available_at=available))
            # verified由原件、独立发布绑定及复制后的字节校验产生，不从输入抄入。
            new_knowledge.append(dict(table_name="market_cap", record_key=row.code + "|" + str(day),
                                      revision_id="official-" + row.announcement_id + "-" + str(day), supersedes="",
                                      published_at=available, evidence_ref=row.evidence_ref, sha256=digest,
                                      verified=hashlib.sha256((originals / row.evidence_ref).read_bytes()).hexdigest()
                                      == digest))
    if not new_caps:
        raise ValueError("no proven capital facts overlap existing prices")
    keys = {(r["code"], r["date"]) for r in new_caps}
    caps = caps[[tuple(t) not in keys for t in caps[["code", "date"]].astype(str).itertuples(index=False, name=None)]]
    caps = pd.concat([caps, pd.DataFrame(new_caps)], ignore_index=True)
    record_keys = {c + "|" + d for c, d in keys}
    knowledge = knowledge[~(knowledge.table_name.eq("market_cap") & knowledge.record_key.isin(record_keys))]
    knowledge = pd.concat([knowledge, pd.DataFrame(new_knowledge)], ignore_index=True)
    output.mkdir(parents=True, exist_ok=False)
    evidence_dir = store.root / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    for name in sorted(attachments):
        raw = (originals / name).read_bytes()
        target = evidence_dir / name
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("existing evidence differs")
        target.write_bytes(raw)
        shutil.copy2(originals / name, output / name)
    items = []
    for table, frame in [("market_cap", caps), ("knowledge_evidence", knowledge)]:
        product_id = next(item["product_id"] for item in store.load_manifest(base)["items"]
                          if store.product(item["product_id"])["tables"] == [table])
        frame.drop(columns=["product_id", "source_ref"], errors="ignore").to_csv(output / (table + ".csv"), index=False)
        items.append(dict(product_id=product_id, table=table, files=[table + ".csv", *sorted(attachments)],
                          source=dict(name="bounded-official-capital-" + table, obtained_at=obtained_at,
                                      permission_basis="公开免费原件及独立发布记录的已核实际股数、有界期间；原取得时间另存来源台账",
                                      fee_class="free")))
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(dict(mode="free", base_snapshot_ids=[base.snapshot_id], items=items),
                                   ensure_ascii=False, indent=2))
    return store.import_files(manifest, tuple(output.iterdir()))


def normalize_saved_responses(index: Path, output: Path) -> pd.DataFrame:
    """同一个离线入口消费多对象原字节；索引携带实际取得时间及原始哈希。"""
    from hetu_quant.providers.cninfo import normalize_share_changes

    frames = []
    for item in json.loads(index.read_text()):
        raw = Path(item["path"])
        if hashlib.sha256(raw.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("original response hash mismatch")
        frame = normalize_share_changes(json.loads(raw.read_text()), item["code"], item["obtained_at"])
        frame["evidence_ref"] = raw.name
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    result.to_csv(output, index=False)
    return result


def main() -> None:
    import argparse

    from hetu_quant.data import DataStore
    from hetu_quant.models import SnapshotRef

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize")
    normalize.add_argument("--index", required=True, type=Path)
    normalize.add_argument("--out", required=True, type=Path)
    load = commands.add_parser("import-capital")
    load.add_argument("--data-root", required=True, type=Path)
    load.add_argument("--base", required=True)
    load.add_argument("--facts", required=True, type=Path)
    load.add_argument("--originals", required=True, type=Path)
    load.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "normalize":
        frame = normalize_saved_responses(args.index, args.out)
        print(json.dumps({"rows": len(frame), "objects_with_records": frame.code.nunique()}))
    else:
        store = DataStore(args.data_root, providers={})
        ref = import_capital_facts(store, SnapshotRef(args.base, store.root),
                                   pd.read_csv(args.facts, dtype={"announcement_id": str}), args.originals, args.out,
                                   datetime.now(CN).isoformat())
        print(ref.snapshot_id)


if __name__ == "__main__":
    main()
