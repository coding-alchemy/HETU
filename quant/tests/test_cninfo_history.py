"""合成巨潮原始记录只含事实；版本资格与金额预期留在测试侧。"""
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hetu_quant.providers.cninfo import normalize_share_changes, select_capital_facts

CN = ZoneInfo('Asia/Shanghai')


def _payload():
    return {'total': 3, 'records': [
        {'SECCODE': '688001', 'SECNAME': '合成科创证券', 'DECLAREDATE': '2022-07-05',
         'VARYDATE': '2022-06-30', 'F003N': 43938.821, 'F002V': '可转债转股', 'F027C': '0'},
        {'SECCODE': '688001', 'SECNAME': '合成科创证券', 'DECLAREDATE': '2022-08-31',
         'VARYDATE': '2022-06-30', 'F003N': 43938.821, 'F002V': '定期报告', 'F027C': '1'},
        {'SECCODE': '688001', 'SECNAME': '合成科创证券', 'DECLAREDATE': '2022-09-27',
         'VARYDATE': '2022-09-30', 'F003N': 44059.1565, 'F002V': '股权激励', 'F027C': '0'},
    ]}


def test_raw_share_versions_preserve_dates_units_and_all_versions():
    f = normalize_share_changes(_payload(), '688001.SH', '2026-10-18T12:00:00+08:00')
    assert len(f) == 3  # 不能只保留latest或把同生效日旧版本覆盖
    assert f.total_shares.tolist() == [439388210, 439388210, 440591565]
    assert f.announcement_date.tolist() == ['2022-07-05', '2022-08-31', '2022-09-27']
    assert f.effective_date.tolist() == ['2022-06-30', '2022-06-30', '2022-09-30']
    assert f.obtained_at.nunique() == 1
    assert not {'verified', 'published_at', 'available_at'} & set(f.columns)
    assert normalize_share_changes({'total': 0, 'records': []}, '600005.SH',
                                   '2026-10-18T12:00:00+08:00').empty
    with pytest.raises(ValueError, match='count'):
        normalize_share_changes({'total': 4, 'records': _payload()['records']}, '688001.SH',
                                '2026-10-18T12:00:00+08:00')


def test_capital_requires_actual_fact_publication_and_bounded_period():
    # 事实摘录只提供原件/发布记录引用及已证明期间，没有资格布尔值。
    facts = pd.DataFrame([
        {'code': 'SYNTH.SH', 'valid_from': '2022-06-30', 'valid_to': '2022-07-31',
         'total_shares': 1000, 'published_date': '2022-07-05', 'fact_kind': 'actual_completed',
         'evidence_ref': 'original.pdf', 'publication_ref': 'directory.json'},
        {'code': 'SYNTH.SH', 'valid_from': '2022-08-01', 'valid_to': '2022-08-31',
         'total_shares': 900, 'published_date': '2022-07-28', 'fact_kind': 'planned',
         'evidence_ref': 'plan.pdf', 'publication_ref': 'directory.json'},
    ])
    assert select_capital_facts(facts, '2022-06-30').empty  # 已生效但尚未公开
    assert select_capital_facts(facts, '2022-07-05').empty  # 仅知公日，保守次日
    assert select_capital_facts(facts, '2022-07-29').total_shares.tolist() == [1000]
    assert select_capital_facts(facts, '2022-08-02').empty  # 计划不是完成
    assert select_capital_facts(facts, '2023-01-31').empty  # 不无限延展
    revised = pd.concat([facts, facts.iloc[[0]].assign(total_shares=1100)], ignore_index=True)
    with pytest.raises(ValueError, match='ambiguous'):
        select_capital_facts(revised, '2022-07-29')


@pytest.mark.parametrize("via_cli", [False, True])
def test_bounded_share_facts_flow_through_import_and_prepare(tmp_path, monkeypatch, capsys, via_cli):
    import json

    from quant.probes.probe_free_history import import_capital_facts

    from backtest_cases import build_case_store, case_definition, make_backtest_case
    from fixtures import annotate_actions_coverage
    from hetu_quant.backtest_data import prepare_backtest

    case = make_backtest_case('history')
    store, base = build_case_store(tmp_path / 'store', case)
    annotate_actions_coverage(store, base, '2020-01-01', '2024-06-30',
                              codes=list(case['codes']), query_basis='ex_date')
    originals = tmp_path / 'originals'
    originals.mkdir()
    (originals / 'actual.txt').write_text('合成证券KEEP_A，2021-06-30实际总股数1000。')
    (originals / 'directory.json').write_text(json.dumps({'announcements': [
        {'secCode': 'KEEP_A', 'announcementId': '1200000001',
         'announcementTime': 1625414400000, 'adjunctUrl': 'finalpage/2021-07-05/1200000001.txt'}]}))
    facts = pd.DataFrame([{'code': 'KEEP_A', 'valid_from': '2021-06-30', 'valid_to': '2021-07-31',
                           'total_shares': 1000, 'published_date': '2021-07-05',
                           'fact_kind': 'actual_completed', 'evidence_ref': 'actual.txt',
                           'publication_ref': 'directory.json', 'announcement_id': '1200000001'}])
    if via_cli:
        import sys

        from quant.probes.probe_free_history import main

        from hetu_quant.models import SnapshotRef

        (store.root / 'data-products.json').write_text(json.dumps(store.config))
        csv = tmp_path / 'facts.csv'
        facts.to_csv(csv, index=False)
        monkeypatch.setattr(sys, 'argv', ['probe-free-history', 'import-capital',
                            '--data-root', str(store.root), '--base', base.snapshot_id,
                            '--facts', str(csv), '--originals', str(originals),
                            '--out', str(tmp_path / 'import')])
        main()
        ref = SnapshotRef(capsys.readouterr().out.strip(), store.root)
    else:
        ref = import_capital_facts(store, base, facts, originals, tmp_path / 'import',
                                   '2024-06-30T18:00:00+08:00')
    cap = store.read_table(ref, 'market_cap')
    july = cap[(cap.code == 'KEEP_A') & (cap.date.astype(str) == '2021-07-31')].iloc[0]
    assert float(july.market_cap_cny) == 10000  # 原价10元×实际1000股
    assert july.available_at == '2021-07-31T15:00:00+08:00'
    parent = store.read_table(base, 'market_cap')
    assert float(parent[(parent.code == 'KEEP_A') &
                        (parent.date.astype(str) == '2021-07-31')].iloc[0].market_cap_cny) == 1000000
    from datetime import datetime

    definition = case_definition(case, ref.snapshot_id, as_of=datetime.now(CN))
    report = prepare_backtest(definition, store, tmp_path / 'bundle')
    assert report.can_run and 'KEEP_A' in report.eligible_ids
    knowledge = store.read_table(ref, 'knowledge_evidence')
    new = knowledge[knowledge.record_key == 'KEEP_A|2021-07-31']
    assert len(new) == 1 and new.iloc[0].evidence_ref == 'actual.txt'
    facts['published_date'] = '2021-07-04'  # 发布记录不一致不得登记
    with pytest.raises(ValueError, match='publication'):
        import_capital_facts(store, ref, facts, originals, tmp_path / 'invalid',
                             '2024-06-30T18:00:00+08:00')


def test_missing_public_date_is_retained_as_unknown_not_filled_or_lost():
    payload = _payload()
    payload['records'][0]['DECLAREDATE'] = None
    f = normalize_share_changes(payload, '688001.SH', '2026-10-18T12:00:00+08:00')
    assert len(f) == 3
    assert pd.isna(f.iloc[0].announcement_date)
    assert 'announcement_date_missing' in f.iloc[0].issues
    assert f.iloc[0].effective_date == '2022-06-30'


def test_batch_saved_response_consumer_checks_original_bytes(tmp_path):
    import hashlib
    import json

    from quant.probes.probe_free_history import normalize_saved_responses

    raw = tmp_path / 'response.json'
    raw.write_text(json.dumps(_payload()))
    index = tmp_path / 'index.json'
    index.write_text(json.dumps([{'code': '688001.SH', 'path': str(raw),
                                  'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
                                  'obtained_at': '2026-10-18T12:00:00+08:00'}]))
    frame = normalize_saved_responses(index, tmp_path / 'normalized.csv')
    assert len(frame) == 3 and frame.total_shares.tolist() == [439388210, 439388210, 440591565]
    raw.write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        normalize_saved_responses(index, tmp_path / 'bad.csv')
