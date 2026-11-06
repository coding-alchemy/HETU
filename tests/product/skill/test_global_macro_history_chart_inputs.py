import csv
import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/global_macro'


def rows():
    with (ROOT / 'inputs/history-and-charts.csv').open() as stream:
        return {row['month']: row for row in csv.DictReader(stream)}


@pytest.mark.parametrize('variant', ['main', 'no-chart'])
def test_selection_and_no_scoring_leak(tmp_path: Path, variant: str) -> None:
    paths = materialize_input('GM12', variant, '01', tmp_path / variant, root=ROOT)
    files = {p.name: p for p in paths}
    assert set(files) == {'task.md', 'material.md', 'selection.json', 'history.csv'}
    assert files['history.csv'].read_bytes() == (
        ROOT / 'inputs/history-and-charts.csv').read_bytes()
    selection = json.loads(files['selection.json'].read_text())
    assert selection['context'] == 'new'
    assert selection['as_of'] == ['2026-10-27T12:00:00+08:00']
    text = files['task.md'].read_text() + files['material.md'].read_text()
    for forbidden in ('review-expectations', '评审标准', '必须发现', '预期答案', 'V1', 'V2'):
        assert forbidden not in text
    assert '合成' in text
    assert '年利率' in text and '净比例' in text and '未季调' in text


def test_calendar_and_internal_missing_value() -> None:
    data = rows()
    periods = [f'{2024 + n // 12:04d}-{n % 12 + 1:02d}' for n in range(8, 33)]
    assert list(data) == periods
    assert data['2026-04']['long_flow_USDbn_NSA'] == ''
    assert data['2026-03']['long_flow_USDbn_NSA']
    assert data['2026-05']['long_flow_USDbn_NSA']
    assert data['2026-01']['long_flow_USDbn_NSA'] == '0'
    assert data['2026-04']['total_flow_USDbn_NSA'] != ''


def test_comparison_conditions_and_units() -> None:
    data = rows()
    key = 'long_flow_USDbn_NSA'
    assert float(data['2025-11'][key]) > 0 and data['2024-11'][key] == '0'
    assert float(data['2024-12'][key]) < 0
    assert float(data['2026-02'][key]) < 0 and data['2026-01'][key] == '0'
    assert float(data['2025-02'][key]) > 0
    assert data['2026-05'][key] and data['2025-05'][key]
    assert data['2026-04'][key] == ''
    assert {'loan_rate_pct', 'loan_demand_net_pct'} <= set(data['2026-09'])
    assert float(data['2026-09']['loan_rate_pct']) < 10
    assert float(data['2026-09']['loan_demand_net_pct']) > 20


@pytest.mark.parametrize('variant', ['unknown', 'main/extra'])
def test_unknown_selection_does_not_write(tmp_path: Path, variant: str) -> None:
    output = tmp_path / 'input'
    with pytest.raises(KeyError):
        materialize_input('GM12', variant, '01', output, root=ROOT)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / 'input'
    output.mkdir()
    keep = output / 'keep.txt'
    keep.write_text('保留')
    with pytest.raises(FileExistsError):
        materialize_input('GM12', 'main', '01', output, root=ROOT)
    assert keep.read_text() == '保留'
    assert set(output.iterdir()) == {keep}


def test_scoring_route_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'fixtures'
    shutil.copytree(ROOT, root)
    catalog = json.loads((root / 'catalog.json').read_text())
    catalog['GM12/main/01']['material'] = [['review-expectations.md', 'G24']]
    (root / 'catalog.json').write_text(json.dumps(catalog))
    output = tmp_path / 'input'
    with pytest.raises(KeyError):
        materialize_input('GM12', 'main', '01', output, root=root)
    assert not output.exists()
