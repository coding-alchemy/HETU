import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/global_macro'
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def sheet_rows(path: Path, name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        book = ET.fromstring(z.read('xl/workbook.xml'))
        names = [s.attrib['name'] for s in book.findall('m:sheets/m:sheet', NS)]
        index = names.index(name) + 1
        sheet = ET.fromstring(z.read(f'xl/worksheets/sheet{index}.xml'))
        rows = []
        for row in sheet.findall('m:sheetData/m:row', NS):
            values = [''] * 8
            for cell in row.findall('m:c', NS):
                col = ord(cell.attrib['r'][0]) - ord('A')
                tag = 'm:is/m:t' if cell.get('t') == 'inlineStr' else 'm:v'
                node = cell.find(tag, NS)
                values[col] = '' if node is None else (node.text or '')
            rows.append(values)
        return rows


def check_selected(paths: tuple[Path, ...], variant: str) -> None:
    files = {p.name: p for p in paths}
    expected = {'task.md', 'material.md', 'selection.json', 'prices.csv'}
    if variant != 'scope':
        expected |= {'publisher.html', 'history.xlsx', 'request-results.json'}
    assert set(files) == expected
    selection = json.loads(files['selection.json'].read_text())
    assert selection == {
        'case': 'GM10', 'variant': variant, 'stage': '01', 'context': 'new',
        'as_of': ['2026-10-25T13:55:24+08:00'],
    }
    text = files['task.md'].read_text() + files['material.md'].read_text()
    for forbidden in ('review-expectations', '评审专用判定', '期望结论', '3347', '3469'):
        assert forbidden not in text
    assert ('## G14 ' in text) == (variant != 'limited')
    if variant == 'scope':
        assert '只研究' in text and '未来一个季度' not in files['task.md'].read_text()
    else:
        rows = {r[0]: r for r in sheet_rows(files['history.xlsx'], 'AFRE')[1:]}
        assert len(rows) == 25
        assert rows['2026-09'][2:4] == ['500', '440']
        assert rows['2025-09'][2] == ('' if variant == 'limited' else '624')
        assert rows['2025-09'][3] == '320'


@pytest.mark.parametrize('variant', ['main', 'limited', 'scope'])
def test_selected_input(tmp_path: Path, variant: str) -> None:
    paths = materialize_input('GM10', variant, '01', tmp_path / 'input', root=ROOT)
    check_selected(paths, variant)


def test_workbook_shape_and_partial_cells(tmp_path: Path) -> None:
    for variant in ('main', 'limited'):
        files = materialize_input('GM10', variant, '01', tmp_path / variant, root=ROOT)
        book = next(p for p in files if p.name == 'history.xlsx')
        for name, count in [('Labor', 25), ('Consumption', 25), ('Japan', 25),
                            ('AFRE', 25), ('Lending', 13), ('Fiscal', 6)]:
            assert len(sheet_rows(book, name)) == count + 1
        labor = {r[0]: r for r in sheet_rows(book, 'Labor')[1:]}
        assert labor['2025-02'][1] == ('' if variant == 'limited' else '150250')
        assert labor['2025-02'][2] == '30.5'
        assert sheet_rows(book, 'Legacy_RMB')[1][2] == '不含部分主体；无版本衔接'


def test_failure_is_scoped_to_the_actual_series(tmp_path: Path) -> None:
    paths = materialize_input('GM10', 'main', '01', tmp_path / 'input', root=ROOT)
    data = json.loads(next(p for p in paths if p.name == 'request-results.json').read_text())
    failures = [r for r in data['records'] if r['status'] == 503]
    assert len(failures) == 1 and failures[0]['series'] == 'SYN_CPI_ALL'
    assert all(r['series'] != 'SYN_PAYROLL_HISTORY' for r in data['records'])


def test_complete_workbook_pollution_is_detected(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    shutil.copytree(ROOT, root)
    path = root / 'catalog.json'
    catalog = json.loads(path.read_text())
    assets = catalog['GM10/limited/01']['assets']
    for asset in assets:
        if asset[1] == 'history.xlsx':
            asset[0] = 'inputs/evidence-history.xlsx'
    path.write_text(json.dumps(catalog))
    paths = materialize_input('GM10', 'limited', '01', tmp_path / 'input', root=root)
    with pytest.raises(AssertionError):
        check_selected(paths, 'limited')


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / 'input'
    with pytest.raises(KeyError):
        materialize_input('GM10', 'unknown', '01', output, root=ROOT)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / 'input'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('旧产物')
    with pytest.raises(FileExistsError):
        materialize_input('GM10', 'main', '01', output, root=ROOT)
    assert list(output.iterdir()) == [marker] and marker.read_text() == '旧产物'


def test_review_route_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    shutil.copytree(ROOT, root)
    path = root / 'catalog.json'
    catalog = json.loads(path.read_text())
    catalog['GM10/main/01']['task'] = [['review-expectations.md', 'GM10']]
    path.write_text(json.dumps(catalog))
    output = tmp_path / 'input'
    with pytest.raises(KeyError):
        materialize_input('GM10', 'main', '01', output, root=root)
    assert not output.exists()
