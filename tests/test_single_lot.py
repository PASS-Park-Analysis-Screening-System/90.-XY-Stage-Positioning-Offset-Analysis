"""Lot 폴더 하나를 고른 경우와 1 Repeat 참고 판정.

배경 (1.1.4 현장): 사용자가 Lot 폴더(46LKD105SJE61) 자체를 고르자 '1개 Recipe | 0개 데이터'가
떴다. 실데이터 Lot 폴더에는 *_X_UL.csv, *_Y_UL.csv, 요약 CSV, Height TIFF가 바로 있고
Capture, Debug(Analysis, Raw, Image ...), Info, Log 하위 폴더가 있다. Info와 Debug/Analysis의
CSV도 'Lot ID' 메타와 Site ID 머리글을 가져 그 하위 폴더가 Lot으로 보였고, X/Y 측정 CSV가 없어
0행이 됐다. Die Position Map은 측정 좌표가 없어 격자 칸 단위 표를 µm 축에 그려 (0, 0)에 겹쳤다.
이 Lot은 Repeat No 1 하나뿐이라 판정은 '참고 (1 Repeat)'로 보인다.
"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import basis_warnings, build_checklist, result_text
from core.csv_loader import ScanSession, scan_lot_folders, scan_self_lot
from core.recipe_scanner import (build_recipe, load_all_recipes, load_recipe_data,
                                 recipes_from_mapping, scan_recipes)
from core.workflow_map import NOTE_ROOT, compute_prefill, describe_folder

SPECS = ['Vision Pattern', 'In-Die Align']
SPEC_DEV = {k: {'spec_range': 2.0, 'spec_stddev': 0.4} for k in SPECS}
SPEC_LIM = {k: {'X': {'lsl': -3000.0, 'usl': 3000.0}, 'Y': {'lsl': -3000.0, 'usl': 3000.0}}
            for k in SPECS}
REFERENCE = '참고 (1 Repeat)'

# 실데이터 순서의 22 Site (Site X/Y는 격자 칸, 1칸 = 20 mm)
SITES = [(0, 0), (0, 0), (2, 0), (4, 0), (6, 0), (2, 2), (4, 4), (0, 2), (0, 4), (0, 6),
         (-2, 2), (-4, 4), (-2, 0), (-4, 0), (-6, 0), (-2, -2), (-4, -4), (0, -2), (0, -4),
         (0, -6), (2, -2), (4, -4)]
META = ('Lot ID,Lot2\r\nRecipe ID,04. XY Stage\\1. Vision Pattern Recognize\r\nCarrier ID,\r\n'
        'Sample Location,Stage\r\nSample ID,{sample}\r\nRepeat No,{repeat}\r\n'
        'Data Save Location,E:\\Measurement Data\\Automation\\1. Vision Pattern Recognize\\{lot}\r\n'
        'Start Time,2026.11.11 13:57:57\r\nEnd Time,2026.11.11 14:21:22\r\n\r\n\r\n')
HEADER = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
          'HZ1_O (nm),HZ1_O_Valid,\r\n')
SAMPLE = '46LKD105SJE6'


def _site_id(k, sx, sy):
    return f'{k:04d}_X{sx:03d}_Y{sy:03d}'


def _value(axis, k, spread):
    base = -1500.0 if axis == 'X' else -2600.0
    return base + spread * ((k * 7) % 11 - 5) / 5


def _site_rows(axis, spread):
    rows = []
    for k, (sx, sy) in enumerate(SITES, 1):
        point = 1 if axis == 'X' else 2
        rows.append(f'{_site_id(k, sx, sy)},{sx},{sy},{point},{sx * 20000 - 2540.83},'
                    f'{sy * 20000 + 135.09},{axis},COMPLETED,TRUE,{_value(axis, k, spread):.3f},TRUE,,\r\n')
    return rows


def make_real_lot(folder, name='46LKD105SJE61', repeat=1, spread=500.0, rows=True):
    """실데이터 Lot 폴더와 같은 배치 (\\10.60.20.11 ... Vision Pattern\\46LKD105SJE61)."""
    lot = folder / name
    meta = META.format(sample=SAMPLE, repeat=repeat, lot=name)
    lot.mkdir(parents=True)
    x_rows = _site_rows('X', spread) if rows else []
    y_rows = _site_rows('Y', spread) if rows else []
    (lot / f'{SAMPLE}_X_UL.csv').write_bytes((meta + HEADER + ''.join(x_rows)).encode())
    (lot / f'{SAMPLE}_Y_UL.csv').write_bytes((meta + HEADER + ''.join(y_rows)).encode())
    summary = (meta + 'X_UL\r\nITEM,HZ1_O (nm),\r\nMEAN,-1581.365,\r\n\r\n'
               'Y_UL\r\nITEM,HZ1_O (nm),\r\nMEAN,-2628.951,\r\n\r\n'
               'X_UL\r\n' + HEADER + ''.join(x_rows) + '\r\nY_UL\r\n' + HEADER + ''.join(y_rows))
    (lot / f'{SAMPLE}.csv').write_bytes(summary.encode())
    for k, (sx, sy) in enumerate(SITES, 1):
        for point in (1, 2):
            (lot / f'{SAMPLE}_{_site_id(k, sx, sy)}_{point:04d}_Height.tiff').write_bytes(b'')
    capture = lot / 'Capture'
    capture.mkdir()
    (capture / f'{SAMPLE}_0001_X000_Y000_0001.png').write_bytes(b'')
    # Debug/Analysis: 같은 Lot ID 메타 + Site ID 머리글 (Valid 열 없음) — 예전에 Lot으로 오인
    analysis = lot / 'Debug' / 'Analysis'
    analysis.mkdir(parents=True)
    debug_meta = (f'Port No,2\r\nCarrier ID,Carrier2\r\nSlot No,19\r\nLot ID,Lot2\r\n'
                  f'Sample ID,{SAMPLE}\r\nRecipe ID,04. XY Stage\\1. Vision Pattern Recognize\r\n\r\n')
    for axis in ('X', 'Y'):
        (analysis / f'{SAMPLE}_{axis}_Universal Line.csv').write_bytes(
            (debug_meta + 'Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,HZ1_O (nm),'
             'HZ1_O_Min.,HZ1_O_Max.,HZ1_O_Stdev.,\r\n'
             '0001_X000_Y000,0,0,1,-2540.83,135.09,X,-974.121,-974.121,-974.121,N/A,\r\n').encode())
        (analysis / f'LineData_{axis}_Universal Line.csv').write_bytes(
            b'46LKD105SJE6_0001_X000_Y000_0001_Height Averaged\r\nX (nm),0.0,4.883,\r\n')
    for sub in ('Raw', 'Image', 'SamplePositioning', 'TipPositioning', 'FreqSweep'):
        (lot / 'Debug' / sub).mkdir()
    (lot / 'Debug' / 'Raw' / f'{SAMPLE}_0001_X000_Y000_0001_NCM Phase.tiff').write_bytes(b'')
    # Info: 같은 메타 + Site ID 머리글 — 예전에 이 폴더가 'Lot 1개'로 보였다
    info = lot / 'Info'
    info.mkdir()
    (info / f'{SAMPLE}_Info.csv').write_bytes(
        (debug_meta + 'Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,FileName,Date\r\n'
         f'0001_X000_Y000,0,0,1,-2540.83,135.09,X,COMPLETED,{SAMPLE}_0001_X000_Y000_0001,'
         '2026/11/11 13:59:18.268\r\n').encode())
    (lot / 'Log').mkdir()
    return lot


def _load_mapped(lot, session=None):
    recipes, problems = recipes_from_mapping([('Vision Pattern', str(lot))], SPECS, session=session)
    assert problems == []
    return recipes, load_all_recipes('', recipes=recipes, session=session)


# ── Lot 폴더를 직접 고른다 ──────────────────────────────────────────

def test_selected_lot_folder_is_itself_one_lot(tmp_path):
    lot = make_real_lot(tmp_path / 'Vision Pattern')

    assert [l['lot_name'] for l in scan_self_lot(str(lot))] == ['46LKD105SJE61']
    assert scan_self_lot(str(tmp_path / 'Vision Pattern')) == []     # Lot 묶음은 Lot이 아니다
    # 하위의 Info·Debug/Analysis는 측정 CSV가 없어 Recipe·라운드가 되지 않는다
    assert [l['lot_name'] for l in scan_lot_folders(str(lot))] == ['Info']
    assert scan_recipes(str(lot)) == []
    assert build_recipe(str(lot)) is None
    assert build_recipe(str(lot / 'Debug')) is None


def test_auto_scan_of_lot_folder_offers_it_as_one_lot(tmp_path):
    from ui.controllers.scan_controller import SELF_LOT_REASON, detect_scan_target
    lot = make_real_lot(tmp_path / 'Vision Pattern')
    session = ScanSession()

    target = detect_scan_target(str(lot), SPECS, {}, session)

    assert target['action'] == 'dialog'
    assert target['reason'] == SELF_LOT_REASON and target['mismatched'] == []
    assert target['prefill']['Vision Pattern'] == {'path': str(lot), 'enabled': False,
                                                   'note': NOTE_ROOT}
    # 사용자가 Workflow를 체크하면 그 폴더 하나가 Lot 1개로 읽힌다
    recipes, results = _load_mapped(target['prefill']['Vision Pattern']['path'], session)
    assert recipes[0]['rounds'] == [{'name': '(root)', 'path': str(lot), 'lot_count': 1,
                                     'self_lot': True}]
    result = results[0]
    assert result['round'] == '(root)' and not result.get('error')
    assert {r['lot_name'] for r in result['raw_data']} == {'46LKD105SJE61'}
    assert len(result['raw_data']) == 44           # 22 Site × X/Y, Z Drive 행 없음
    assert result['round_path'] == str(lot)


def test_auto_scan_without_specs_does_not_invent_a_recipe(tmp_path):
    from ui.controllers.scan_controller import detect_scan_target
    lot = make_real_lot(tmp_path / 'Vision Pattern')
    assert detect_scan_target(str(lot), [], {}, ScanSession()) == {'action': 'none'}


def test_parent_of_lot_folders_is_unchanged(tmp_path):
    from ui.controllers.scan_controller import FLAT_ROOT_REASON, detect_scan_target
    parent = tmp_path / 'Vision Pattern'
    make_real_lot(parent, '46LKD105SJE61', repeat=1)
    make_real_lot(parent, '46LKD105SJE62', repeat=2)

    assert describe_folder(str(parent)) == (True, '✅ Lot 2개 · (root)')
    target = detect_scan_target(str(parent), SPECS, {}, ScanSession())
    assert target['action'] == 'dialog' and target['reason'] == FLAT_ROOT_REASON
    _recipes, results = _load_mapped(parent)
    assert [r['lot_name'] for r in results[0]['raw_data'][:1]] == ['46LKD105SJE61']
    assert len({r['lot_name'] for r in results[0]['raw_data']}) == 2


def test_workflow_dialog_row_with_lot_folder(tmp_path):
    lot = make_real_lot(tmp_path / 'Vision Pattern')
    session = ScanSession()

    assert describe_folder(str(lot), session) == (True, '✅ Lot 1개 (선택한 폴더가 Lot 폴더)')
    prefill = compute_prefill(SPECS, str(lot), {}, session)
    assert prefill['Vision Pattern']['path'] == str(lot)
    _recipes, results = _load_mapped(lot, session)
    assert len(results[0]['raw_data']) == 44


def test_workflow_dialog_widget_shows_lot_folder_status(tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.workflow_folder_dialog import WorkflowFolderDialog
    app = QApplication.instance() or QApplication([])
    lot = make_real_lot(tmp_path / 'Vision Pattern')

    dlg = WorkflowFolderDialog(None, SPECS, str(lot), {}, 'reason', session=ScanSession())
    try:
        deadline = time.monotonic() + 15
        while dlg._prefill_pending or dlg._pending:
            assert time.monotonic() < deadline
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        checkbox, edit, status = dlg._rows['Vision Pattern'][:3]
        assert edit.text() == str(lot)
        assert status.text().startswith('✅ Lot 1개 (선택한 폴더가 Lot 폴더)')
        checkbox.setChecked(True)
        app.processEvents()
        assert dlg.selected_mapping() == [('Vision Pattern', str(lot))]
    finally:
        dlg.done(0)
        dlg.deleteLater()
        app.processEvents()


def test_tiff_lookup_uses_the_selected_lot_folder(tmp_path):
    from ui.controllers.tiff_controller import find_tiffs_for_row
    lot = make_real_lot(tmp_path / 'Vision Pattern')
    matched, folder, message = find_tiffs_for_row(str(lot), '46LKD105SJE61', '0004_X004_Y000')
    assert message == '' and folder == str(lot)
    assert [os.path.basename(m) for m in matched] == [
        f'{SAMPLE}_0004_X004_Y000_0001_Height.tiff', f'{SAMPLE}_0004_X004_Y000_0002_Height.tiff']


# ── 0행은 조용한 빈 결과가 아니다 ─────────────────────────────────────

def test_folder_with_only_metadata_csv_is_not_a_recipe(tmp_path):
    """하위 내용이 데이터처럼 보여도(Info·Debug) 측정 CSV가 없으면 0행 Recipe를 만들지 않는다."""
    from ui.controllers.scan_controller import detect_scan_target
    lot = make_real_lot(tmp_path / 'tmp')
    for name in ('46LKD105SJE6_X_UL.csv', '46LKD105SJE6_Y_UL.csv'):
        (lot / name).unlink()       # 측정 CSV가 없는 폴더 (요약·Info·Debug만)

    assert scan_recipes(str(lot)) == []
    assert describe_folder(str(lot)) == (False, '❌ Lot 데이터 없음')
    assert detect_scan_target(str(lot), SPECS, {}, ScanSession()) == {'action': 'none'}
    _recipes, problems = recipes_from_mapping([('Vision Pattern', str(lot))], SPECS)
    assert problems == [('Vision Pattern', str(lot), 'Lot 데이터 없음')]


def test_zero_rows_explains_what_was_found(tmp_path):
    root = tmp_path / 'run'
    (root / 'Info').mkdir(parents=True)
    (root / 'Info' / 'S_Info.csv').write_text('Lot ID,L\n\n' + HEADER, encoding='utf-8')
    recipe = {'name': 'R', 'short_name': 'R', 'rounds': [{'name': '1st', 'path': str(root)}]}

    result = load_recipe_data(recipe)

    assert result['raw_data'] == [] and result['error'] == 'No data loaded'
    assert 'Info' in result['no_rows_detail'] and 'X/Y 측정 CSV' in result['no_rows_detail']
    assert result['no_rows_detail'].startswith('읽은 행 0개')


@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: dict(
        spec_limits=SPEC_LIM, spec_deviation=SPEC_DEV))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win._loader_thread = None
    win.deleteLater()
    app.processEvents()


def _complete(window, results, folder):
    from core.recipe_scanner import compare_recipes
    window.recipes = [dict(r, index=i + 1, short_name=r.get('short_name') or r['recipe'],
                           name=r['recipe']) for i, r in enumerate(results)]
    window._build_nav()
    window._loader_thread = type('Done', (), {'folder': str(folder)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)


def test_empty_lot_gives_log_error_and_dialog(window, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    lot = make_real_lot(tmp_path / 'Vision Pattern', rows=False)    # 측정 중단: 머리글만
    _recipes, results = _load_mapped(lot)
    assert results[0]['raw_data'] == []

    _complete(window, results, lot)

    logged = str(window.logger.export_rows())
    assert '❌ Vision Pattern: No data loaded — 읽은 행 0개' in logged
    assert '데이터 품질 이상 없음' not in logged
    boxes = [b for b in window.findChildren(QMessageBox) if b.windowTitle() == '데이터 없음']
    assert boxes and '분석할 데이터를 하나도 읽지 못했습니다' in boxes[-1].text()
    assert '46LKD105SJE61' in boxes[-1].text()
    assert window.statusBar().currentMessage().startswith('❌')
    for b in boxes:
        b.close()


# ── Die Position Map ─────────────────────────────────────────────────

def test_die_map_without_coordinates_is_not_piled_at_origin():
    import matplotlib
    matplotlib.use('Agg')
    from charts.wafer import plot_die_position_map, plot_die_position_map_mini

    fig = plot_die_position_map(None, 150_000)
    xs = [c.get_offsets()[0][0] for c in fig.axes[0].collections]
    assert max(xs) - min(xs) >= 100_000          # 예전: ±6000 µm 안 (0, 0)에 겹침
    assert '측정 좌표 없음' in fig.axes[0].get_title()
    fig_mini, _ = plot_die_position_map_mini(None, 150_000)
    xs = [c.get_offsets()[0][0] for c in fig_mini.axes[0].collections]
    assert max(xs) - min(xs) >= 100_000


def test_die_map_uses_measured_coordinates_of_single_lot(tmp_path):
    from core.die_analysis import extract_die_positions
    _recipes, results = _load_mapped(make_real_lot(tmp_path / 'vp'))
    positions = extract_die_positions(results[0]['raw_data'])
    assert len(positions) == 22
    assert positions[2] == pytest.approx((37.459, 0.135), abs=1e-3)


# ── 1 Repeat 참고 판정 ───────────────────────────────────────────────

def _rows(dies, repeats, spread=0.3):
    out = []
    for rep in repeats:
        for d in dies:
            for axis in ('X', 'Y'):
                out.append({'lot_name': f'Lot{rep:03d}', 'lot_index': rep,
                            'site_id': f'{d:04d}_X000_Y000', 'method': axis,
                            'value': 4000.0 + spread * 1000 * ((d * 7) % 11 - 5) / 5 + rep / 1000,
                            'valid': True, 'is_outlier': False, 'filename': f'{rep}.csv',
                            'x_um': 20000.0 * (d % 5) + 1.0, 'y_um': 20000.0 * (d // 5) + 1.0})
    return out


def _result(rows, name='Vision Pattern'):
    return {'recipe': name, 'short_name': name, 'round': '(root)', 'raw_data': rows,
            'statistics': {}, 'data_warnings': [], 'load_errors': []}


@pytest.mark.parametrize('spread, verdict', [(0.3, True), (5.0, False)])
def test_one_repeat_is_a_reference_verdict(spread, verdict):
    rows = _rows(range(1, 23), [1], spread)
    checks = build_checklist([_result(rows)], SPEC_LIM, SPEC_DEV)
    for c in checks:
        assert (c['die_count'], c['repeat_count']) == (21, 1)
        assert c['result'] is verdict and c['reference'] is True
        assert c['cpk'] is not None
        assert result_text(c['result'], c['reference']) == (
            f"{'PASS' if verdict else 'FAIL'} {REFERENCE}")
    assert any('참고용' in m and '21 Die × 1 Repeat' in m
               for m in basis_warnings(_result(rows)))


@pytest.mark.parametrize('dies, repeats', [
    (range(1, 3), [1]),        # Site 0001을 빼면 1 Die
    (range(2, 3), [1]),        # 값 하나
])
def test_fewer_than_two_dies_is_still_dash(dies, repeats):
    for c in build_checklist([_result(_rows(dies, repeats))], SPEC_LIM, SPEC_DEV):
        assert c['result'] is None and c['reference'] is False and c['cpk'] is None
        assert result_text(c['result'], c['reference']) == '—'


def test_two_or_more_repeats_unchanged():
    for c in build_checklist([_result(_rows(range(1, 23), range(1, 11)))], SPEC_LIM, SPEC_DEV):
        assert c['result'] is True and c['reference'] is False
        assert result_text(c['result'], c['reference']) == 'PASS'


def _show(window, rows):
    from core.recipe_scanner import compare_recipes
    result = _result(rows)
    window.recipes = [{'name': 'Vision Pattern', 'short_name': 'Vision Pattern', 'index': 1,
                       'rounds': []}]
    window._build_nav()
    window.recipe_results = [result]
    window._update_summary_table(compare_recipes([result]), [result])
    window._compute_all_step_pass_states()
    window.current_recipe_idx = 0
    window.raw_data = rows
    window._display_result(result, window.recipes[0])
    return result


@pytest.mark.parametrize('spread, verdict', [(0.3, 'PASS'), (5.0, 'FAIL')])
def test_screen_marks_reference_verdict(window, spread, verdict):
    from ui.controllers.table_controller import ROW_X_RESULT, ROW_Y_RESULT
    _show(window, _rows(range(1, 23), [1], spread))

    for card in (window.card_x, window.card_y):
        assert card.badge.text() == f'⚠ {verdict} · 1 Repeat 참고 판정'
        assert card.lbl_cpk.text().endswith('(참고)')
        assert card.lbl_basis.text().startswith('21 Die × 1 Repeat')
    assert window.step_pass_states[0] is (verdict == 'PASS')
    assert window.step_buttons[0].text() == '⚠ Step 1: Vision Pattern'
    assert window.step_buttons[0].property('step_active_pass' if verdict == 'PASS'
                                           else 'step_active_fail') is True
    for row in (ROW_X_RESULT, ROW_Y_RESULT):
        assert window.sum_table.item(row, 0).text() == f'{verdict} {REFERENCE}'


def test_screen_keeps_dash_and_plain_pass(window):
    from ui.controllers.table_controller import ROW_X_RESULT
    _show(window, _rows(range(1, 3), [1]))
    assert window.card_x.badge.text() == '—' and window.card_x.lbl_cpk.text() == '—'
    assert window.step_buttons[0].text() == 'Step 1: Vision Pattern'
    assert window.sum_table.item(ROW_X_RESULT, 0).text() == '—'

    _show(window, _rows(range(1, 23), range(1, 11)))
    assert window.card_x.badge.text() == '✅ PASS'
    assert not window.card_x.lbl_cpk.text().endswith('(참고)')
    assert window.step_buttons[0].text() == 'Step 1: Vision Pattern'
    assert window.sum_table.item(ROW_X_RESULT, 0).text() == 'PASS'


def test_real_lot_folder_flow_on_screen(window, tmp_path):
    lot = make_real_lot(tmp_path / 'Vision Pattern')
    _recipes, results = _load_mapped(lot)
    _complete(window, results, lot)
    assert window.card_x.badge.text() == '⚠ PASS · 1 Repeat 참고 판정'
    assert window.step_buttons[0].text() == '⚠ Step 1: Vision Pattern'
    assert window._dynamic_die_positions        # 실측 좌표로 그린다


@pytest.mark.parametrize('spread, verdict', [(0.3, 'PASS'), (5.0, 'FAIL')])
def test_excel_marks_reference_verdict(tmp_path, spread, verdict):
    from openpyxl import load_workbook
    from core.exporter import export_excel_report
    result = _result(_rows(range(1, 23), [1], spread))
    path = str(tmp_path / 'r.xlsx')
    export_excel_report(result['raw_data'], {}, [], path, recipe_results=[result],
                        spec_limits=SPEC_LIM, spec_deviation=SPEC_DEV)
    ws = load_workbook(path)['Checklist']
    header = [c.value for c in ws[1]]
    rows = [dict(zip(header, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    assert [r['Result'] for r in rows] == [f'{verdict} {REFERENCE}'] * 2
    assert all(str(r['Cpk']).endswith('(참고)') for r in rows)
    basis = load_workbook(path)['Analysis Basis']
    assert any('참고용' in str(c.value) for row in basis.iter_rows() for c in row)


def test_excel_keeps_numbers_for_normal_data(tmp_path):
    from openpyxl import load_workbook
    from core.exporter import export_excel_report
    result = _result(_rows(range(1, 23), range(1, 11)))
    path = str(tmp_path / 'r.xlsx')
    export_excel_report(result['raw_data'], {}, [], path, recipe_results=[result],
                        spec_limits=SPEC_LIM, spec_deviation=SPEC_DEV)
    ws = load_workbook(path)['Checklist']
    assert [ws.cell(row=r, column=10).value for r in (2, 3)] == ['PASS', 'PASS']
    assert all(isinstance(ws.cell(row=r, column=9).value, float) for r in (2, 3))


@pytest.mark.parametrize('spread, verdict', [(0.3, 'PASS'), (5.0, 'FAIL')])
def test_pdf_marks_reference_verdict(tmp_path, monkeypatch, spread, verdict):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.axes import Axes
    from core import pdf_generator
    tables, notes = [], []
    real_table = Axes.table

    def spy_table(self, *args, **kwargs):
        tables.append(kwargs.get('cellText'))
        return real_table(self, *args, **kwargs)

    real_notes = pdf_generator._add_notes_pages
    monkeypatch.setattr(Axes, 'table', spy_table)
    monkeypatch.setattr(pdf_generator, '_add_notes_pages',
                        lambda pdf, lines: notes.extend(lines) or real_notes(pdf, lines))
    result = _result(_rows(range(1, 23), [1], spread))

    pdf_generator.generate_pdf_report(str(tmp_path / 'r.pdf'), 'root', [result], [],
                                      SPEC_LIM, SPEC_DEV)

    checklist = tables[0]
    assert [row[-1] for row in checklist] == [f'{verdict} {REFERENCE}'] * 2
    assert all(row[-2].endswith('(참고)') for row in checklist)
    assert any('참고용' in n for n in notes)


@pytest.mark.parametrize('spread, verdict', [(0.3, 'PASS'), (5.0, 'FAIL')])
def test_llm_report_with_one_lot(spread, verdict):
    from core.llm_report import build_llm_report
    result = _result(_rows(range(1, 23), [1], spread))

    md = build_llm_report([result], SPEC_LIM, SPEC_DEV, app_version='1.1.4',
                          exported_at='2026-11-11 10:00:00', source_root='D:/data')

    summary = [l for l in md.splitlines()
               if l.startswith(('| Vision Pattern | (root) | X |', '| Vision Pattern | (root) | Y |'))]
    assert len(summary) == 2
    assert all(f'| {verdict} {REFERENCE} |' in l and '(참고)' in l for l in summary)
    # 2원 분산 분해는 Lot 항이 없어 하지 않는다
    assert '| X | Lot 1개라 계산 안 함 (Die마다 값 1개) |' in md
    assert '- X drift·Lot 교대 패턴 (파생): Lot 1개라 계산 안 함' in md
    assert '- Y drift·Lot 교대 패턴 (파생): Lot 1개라 계산 안 함' in md
    # 평면 피팅·해석 힌트는 21 Die 평균으로 계산된다
    plane = next(l for l in md.splitlines() if l.startswith('| X |') and '%' not in l
                 and l.count('|') == 7 and 'Lot 1개' not in l)
    assert plane.split('|')[2].strip() != '—'
    assert '- 해석 힌트 (파생):' in md
    assert f"판정이 '{REFERENCE}'입니다" in md


def test_llm_report_keeps_normal_verdict_text():
    from core.llm_report import build_llm_report
    md = build_llm_report([_result(_rows(range(1, 23), range(1, 11)))], SPEC_LIM, SPEC_DEV)
    assert REFERENCE not in md.split('## 분석 조건')[1].split('## 결과 요약')[1]
    assert 'Lot 1개라 계산 안 함' not in md
