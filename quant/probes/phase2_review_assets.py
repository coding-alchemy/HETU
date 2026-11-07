"""2026-10-18 二期复评的离线核算。只读现有数据，输出本轮证据；不取数。"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from hetu_quant.data import _action_query_records


def action_gaps(start: str, end: str, records: list[dict]) -> list[list[str]]:
    """仅扣除已证明实施日期口径；公告年和失败不能填缺口。"""
    cursor, target = date.fromisoformat(start), date.fromisoformat(end)
    gaps = []
    for record in sorted(records, key=lambda r: r['start']):
        if record.get('basis') != 'ex_date' or record.get('failed'):
            continue
        left, right = date.fromisoformat(record['start']), date.fromisoformat(record['end'])
        if right < cursor or left > target:
            continue
        if left > cursor:
            gaps.append([cursor.isoformat(), min(target, left-timedelta(days=1)).isoformat()])
        cursor = max(cursor, right+timedelta(days=1))
    if cursor <= target:
        gaps.append([cursor.isoformat(), target.isoformat()])
    return gaps


def request_counts(gaps: list[list[str]]) -> dict:
    return dict(provider_fetch=len(gaps), upstream_product_queries=sum(
        date.fromisoformat(right).year-date.fromisoformat(left).year+1 for left, right in gaps),
        pagination=0, automatic_retries=0, retry_policy='失败后保留进度，新增重试须计数并核对授权')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root: Path, output: Path) -> dict:
    from hetu_quant.data import DataStore
    from hetu_quant.models import SnapshotRef

    output.mkdir(parents=True, exist_ok=True)
    store = DataStore(root)
    state_path = root/'qualification/phase2/acquire-state.json'
    state = json.loads(state_path.read_text())
    (output/'acquire-state-at-pause.json').write_bytes(state_path.read_bytes())
    manifests = []
    for path in sorted((root/'snapshots').glob('*/manifest.json')):
        if path.parent.name.startswith('.'):
            continue
        manifests.append((path.parent, json.loads(path.read_text())))
    sec_dir, sec_manifest = next((directory, m) for directory, m in reversed(manifests)
                               if any(e.get('table') == 'securities' for e in m['table_files']))
    securities = store.read_table(SnapshotRef(snapshot_id=sec_dir.name, root=root), 'securities')
    eligible = securities[(securities['market'].astype(str).isin(['SH', 'SZ']))
        & (securities['security_type'].astype(str) == 'stock')
        & ((securities['status'].astype(str) == 'listed') | (securities['out_date'].astype(str) >= '2016-04-29'))]
    codes = sorted(set(eligible['code'].astype(str)))
    proofs: dict[str, list[dict]] = {}
    references: dict[str, list[dict]] = {}
    raw_proofs: dict[str, list[dict]] = {}
    for directory, manifest in manifests:
        for entry in manifest['table_files']:
            if entry.get('table') != 'corporate_actions':
                continue
            records = _action_query_records(entry)
            if not records:
                continue
            path = directory/entry['path']
            if not path.is_file() or sha(path) != entry['sha256']:
                continue
            code = entry['partition'].removeprefix('code=').split('|')[0]
            for record in records:
                if record.get('basis') == 'ex_date':
                    proofs.setdefault(code, []).append(record)
                    references.setdefault(code, []).append(dict(snapshot=directory.name, path=entry['path'],
                        sha256=entry['sha256'], coverage=record))
        for item in manifest['items']:
            if item.get('product_id') != 'cn_a_actions':
                continue
            for raw in item.get('raw_files', []):
                records = raw.get('query_evidence', [])
                path = directory/raw['path']
                if records and path.is_file() and sha(path) == raw['sha256']:
                    for record in records:
                        if record.get('basis') == 'ex_date':
                            code = record['code']
                            raw_proofs.setdefault(code, []).append(record)
                            references.setdefault(code, []).append(dict(snapshot=directory.name, raw=raw['path'],
                                sha256=raw['sha256'], coverage=record, reuse='原件重标准化，无新增查询'))
    rows = []
    life_cycle = eligible.set_index(eligible['code'].astype(str))
    catalog_entries = [e for e in sec_manifest['table_files'] if e['table'] == 'securities']
    for code in codes:
        facts = life_cycle.loc[code]
        ipo, out = str(facts['ipo_date']), str(facts['out_date'])
        def valid_date(value):
            return len(value) == 10 and value[:4].isdigit()
        start = max('2016-04-29', ipo) if valid_date(ipo) else '2016-04-29'
        end = min('2026-10-13', out) if valid_date(out) else '2026-10-13'
        gaps = action_gaps(start, end, proofs.get(code, [])+raw_proofs.get(code, []))
        rows.append(dict(code=code, product='cn_a_actions', period=[start, end] if start <= end else None,
            lifecycle_evidence=dict(snapshot=sec_dir.name, files=catalog_entries,
                ipo_date=ipo, out_date=out,
                scope='目录原件支持生命周期事实用于取数边界；不据此证明该字段在历史信号时已公开'),
            evidence=references.get(code, []), gaps=gaps, requests=request_counts(gaps), data_fee_cny=0,
            qualification='仅取数边界和实施日期查询缺口，不证明内容完整／历史时点可用'))
    (output/'actions-incremental.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    sample_ids = ['prep-20261017T222906-8c46b4ce', 'prep-20261017T223408-ee4be581',
                  'prep-20261017T223522-6e7d7c11', 'prep-20261017T223727-eeb4b6d4',
                  'prep-20261017T224031-163c3b7d']
    sample_raw = []
    sample_queries = 0
    for directory, manifest in manifests:
        if directory.name not in sample_ids:
            continue
        for item in manifest['items']:
            for raw in item.get('raw_files', []):
                sample_raw.append(dict(snapshot=directory.name, product=item['product_id'], **raw))
                if item['product_id'] == 'cn_a_actions':
                    records = raw.get('query_evidence', [])
                    sample_queries += sum(date.fromisoformat(r['end']).year-date.fromisoformat(r['start']).year+1
                                          for r in records) if records else 0
                else:
                    sample_queries += 1
    (output/'sample-raw-ledger.json').write_text(json.dumps(sample_raw, ensure_ascii=False, indent=2))
    temporary = list((root/'snapshots').glob('.tmp-*/raw/cn_a_status/*.parquet'))
    status_published = [raw for directory, m in manifests if directory.name >= 'prep-20261018T083000'
                        for item in m['items'] if item['product_id'] == 'cn_a_status'
                        for raw in item.get('raw_files', [])]
    full_daily = sum(len(run.get('ledger', [])) for run in state['runs'] if run['tables'] == ['daily_bars'])
    status_raw = len(temporary)+len(status_published)
    report = dict(
        state_sha256=sha(state_path), state_completed={k:len(v) for k,v in state['completed'].items()},
        paused_processes=[64375, 77894], resume_authorized=False,
        sample=dict(provider_fetch=len(sample_raw), limit=30, exceeded_by=max(0,len(sample_raw)-30),
            debug_rerun_fetch=20, upstream_product_queries=sample_queries,
            upstream_count_note='仅manifest可解释的产品查询下限；无query_evidence的行动raw展开另列未知',
            gbjg_top_level_calls=9, gbjg_http_requests='未知：未保存逐HTTP日志，不将顶层调用等同网络次数',
            pagination='未知；名义单页500，未保存逐页日志', transport_retries='未知',
            login_count='未知；缺逐会话日志', raw_files=len(sample_raw)),
        full=dict(daily_provider_fetch=full_daily, status_provider_fetch_lower_bound=status_raw,
            status_published_raw=len(status_published), status_temporary_raw=len(temporary),
            in_flight_or_unflushed_requests='未知；暂停时内存ledger尚未保存，不能只按completed=200计数',
            login_count='未知', transport_retries='未知', market_cap_not_started=True),
        universe_count=len(codes), universe_source=sec_dir.name,
        actions_incremental=dict(provider_fetch=sum(r['requests']['provider_fetch'] for r in rows),
            upstream_product_queries=sum(r['requests']['upstream_product_queries'] for r in rows),
            automatic_retries=0, data_fee_cny=0, authorization='待逐项批准；不是新的获批预算'),
        historical_qualification='blocked：市值公开时间及历史身份公告证据缺失；来源哈希不替代公开时点',
        this_review=dict(provider_fetch=0, upstream_product_queries=0, real_research_runs=0,
            stock_analysis=0, paid_services=0, data_fee_cny=0))
    (output/'data-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    report = audit(Path('.hetu/quant'), Path('.hetu/quant/acceptance/phase2/review-20261018'))
    print(json.dumps(report, ensure_ascii=False, indent=2))
