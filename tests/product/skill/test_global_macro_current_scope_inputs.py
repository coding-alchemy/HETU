import csv
import json
import re
from pathlib import Path

from tests.product.skill.material_fixtures import materialize_input
from tests.product.skill.test_global_macro_evidence_use_inputs import sheet_rows

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/global_macro'


def test_selected_inputs_and_directory_links(tmp_path: Path) -> None:
    paths = materialize_input('GM11', 'main', '01', tmp_path / 'input', root=ROOT)
    files = {p.name: p for p in paths}
    assert set(files) == {
        'task.md', 'material.md', 'selection.json', 'publisher.html',
        'history.csv', 'quotes.csv', 'finance-catalog.html', 'fiscal.csv', 'auctions.csv',
    }
    for name in ('publisher.html', 'finance-catalog.html'):
        for link in re.findall(r'href="([^"]+)"', files[name].read_text()):
            assert link in files
    assert json.loads(files['selection.json'].read_text())['as_of'] == [
        '2026-10-26T12:00:00+08:00',
    ]
    task = files['task.md'].read_text() + files['material.md'].read_text()
    for excluded in ('评审', '必须发现', '17.7305', '0.9787', '旧报告', '失败原因'):
        assert excluded not in task


def test_reused_values_and_credit_coverage() -> None:
    rows = list(csv.DictReader((ROOT / 'inputs/current-scope-history.csv').open()))
    old = sheet_rows(ROOT / 'inputs/evidence-history.xlsx', 'AFRE')[1:]
    assert [(r['month'], r['AFRE_RMB_loans_CNY100million_NSA']) for r in rows] == [
        (r[0], r[2]) for r in old
    ]
    assert len(rows) == 25
    assert set(rows[0]) == {
        'month', 'energy_USD_per_barrel_effective_day_mean_NSA',
        'AFRE_RMB_loans_CNY100million_NSA',
    }
    fiscal = list(csv.reader((ROOT / 'inputs/current-scope-fiscal.csv').open()))[1:]
    assert fiscal == [r[:4] for r in sheet_rows(
        ROOT / 'inputs/evidence-history.xlsx', 'Fiscal')[1:]]


def test_frequency_and_financing_counterevidence() -> None:
    monthly = list(csv.DictReader((ROOT / 'inputs/current-scope-history.csv').open()))
    daily = list(csv.DictReader((ROOT / 'inputs/current-scope-quotes.csv').open()))
    assert monthly[-1]['month'] == '2026-09'
    assert float(monthly[-1]['energy_USD_per_barrel_effective_day_mean_NSA']) > float(
        monthly[-2]['energy_USD_per_barrel_effective_day_mean_NSA'])
    assert daily[0]['observation_date'] == '2026-10-02'
    assert daily[-1]['observation_date'] == '2026-10-20'
    assert float(daily[-1]['energy_USD_per_barrel_spot']) < float(
        daily[0]['energy_USD_per_barrel_spot'])
    auctions = list(csv.DictReader((ROOT / 'inputs/current-scope-auctions.csv').open()))
    assert [r['bid_to_cover'] for r in auctions] == ['2.2', '2.7']
    assert {r['original_term'] for r in auctions} == {'10Y'}
    assert {r['reopening'] for r in auctions} == {'yes'}
    for row in auctions:
        assert float(row['competitive_tendered_USDbn']) / float(
            row['competitive_accepted_USDbn']) == float(row['bid_to_cover'])
    assert 'when_issued' not in auctions[0]
