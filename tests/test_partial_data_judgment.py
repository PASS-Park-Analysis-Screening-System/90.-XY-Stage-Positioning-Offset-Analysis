"""이상치 축별 계산과 부분 데이터 판정 규칙을 고정한다."""

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import analysis_warnings, basis_warnings, build_checklist, result_text
from core.die_analysis import (compute_deviation_matrix, evaluate_deviation_pass,
                               filter_stabilization_die)
from core.statistics import (compute_statistics, detect_outliers, detect_outliers_by_axis,
                             filter_by_method)

SPEC_DEV = {'R': {'spec_range': 2.0, 'spec_stddev': 1.0}}
SPEC_LIM = {'R': {'X': {'lsl': -5, 'usl': 5}, 'Y': {'lsl': -5, 'usl': 5}}}


def _row(die, rep, axis, value):
    return {'lot_name': f'Lot{rep:03d}', 'site_id': f'{die:04d}_X000_Y000', 'method': axis,
            'value': value, 'valid': True, 'is_outlier': False, 'filename': f'{rep}.csv',
            'x_um': 1.0, 'y_um': 1.0}


def _table(dies=range(1, 23), repeats=range(1, 11), axes=('X', 'Y')):
    return [_row(d, r, a, 4000.0 + d / 100 + r / 1000)
            for d in dies for r in repeats for a in axes]


def _checks(rows):
    result = {'short_name': 'R', 'round': '1st', 'raw_data': rows}
    return {r['axis']: r for r in build_checklist([result], SPEC_LIM, SPEC_DEV)}


# ── 이상치 축별 계산 ───────────────────────────────────────────────

def _two_axis_rows():
    """축 평균이 크게 다르고 X에만 튄 값이 하나 있다."""
    rows = [_row(d, r, 'X', 100.0 + d / 10 + r / 100) for d in range(2, 23) for r in range(1, 11)]
    rows += [_row(d, r, 'Y', 9000.0 + d / 10 + r / 100) for d in range(2, 23) for r in range(1, 11)]
    rows[0]['value'] = 500.0
    return rows


def test_outlier_hidden_by_merged_bounds_is_found_per_axis():
    merged = detect_outliers(_two_axis_rows(), method='iqr')
    by_axis = detect_outliers_by_axis(_two_axis_rows(), method='iqr')

    assert sum(r['is_outlier'] for r in merged) == 0
    flagged = [r for r in by_axis if r['is_outlier']]
    assert [(r['method'], r['value']) for r in flagged] == [('X', 500.0)]


def test_axis_bounds_ignore_the_other_axis():
    rows = _two_axis_rows()
    rows[-1]['value'] = 9100.0      # Y에서만 튄 값. 두 축을 합친 경계로는 잡히지 않는다
    assert not any(r['is_outlier'] for r in detect_outliers(copy.deepcopy(rows), method='iqr'))

    detect_outliers_by_axis(rows, method='iqr')
    alone = detect_outliers_by_axis([r for r in copy.deepcopy(rows) if r['method'] == 'Y'],
                                    method='iqr')

    flags = [r['is_outlier'] for r in rows if r['method'] == 'Y']
    assert flags == [r['is_outlier'] for r in alone]
    assert sum(flags) == 1


def test_row_without_axis_is_cleared_and_not_flagged():
    rows = _two_axis_rows() + [dict(_row(5, 1, 'Z', 1e9), is_outlier=True)]
    detect_outliers_by_axis(rows, method='iqr')
    assert rows[-1]['is_outlier'] is False


def test_outlier_method_does_not_change_judgment():
    rows = _two_axis_rows()
    first = _checks(rows)
    detect_outliers_by_axis(rows, method='iqr')
    second = _checks(rows)
    for axis in ('X', 'Y'):
        for key in ('range', 'stddev', 'result', 'cpk', 'avg'):
            assert first[axis][key] == second[axis][key]


# ── 부분 데이터 판정 ───────────────────────────────────────────────

@pytest.mark.parametrize('dies, repeats, expected', [
    (range(1, 3), range(1, 11), None),      # Site 0001을 빼면 1 Die
    (range(1, 23), range(1, 2), None),      # 1 Repeat
    (range(2, 3), range(1, 2), None),       # 값 하나 (Range 0)
    (range(1, 2), range(1, 11), None),      # 안정화 Die만
    (range(1, 4), range(1, 3), True),       # 최소 크기 2 × 2
    (range(1, 23), range(1, 11), True),     # 정상 21 × 10
])
def test_judgment_needs_two_dies_and_two_repeats(dies, repeats, expected):
    checks = _checks(_table(dies, repeats))
    for axis in ('X', 'Y'):
        assert checks[axis]['result'] is expected
        assert result_text(checks[axis]['result']) == ('PASS' if expected else '—')


def test_partial_data_at_minimum_size_can_still_fail():
    rows = _table(range(1, 4), range(1, 3))
    rows[-1]['value'] += 5000.0     # Y축 Die 3
    checks = _checks(rows)
    assert checks['Y']['result'] is False
    assert checks['X']['result'] is True


def test_warning_explains_why_the_axis_is_not_judged():
    messages = basis_warnings({'raw_data': _table(range(1, 3), range(1, 11))})
    assert any('X축 1 Die × 10 Repeat' in m and '판정하지 않습니다' in m for m in messages)
    assert basis_warnings({'raw_data': _table()}) == []


def test_step_path_gives_the_same_dash_as_checklist():
    data = filter_stabilization_die(_table(range(1, 3), range(1, 11)))
    for axis in ('X', 'Y'):
        verdict = evaluate_deviation_pass(compute_statistics(filter_by_method(data, axis)),
                                          compute_deviation_matrix(data, axis), 2.0, 1.0)
        assert verdict is None


# ── ✅ 라벨 ───────────────────────────────────────────────────────

def test_analysis_warnings_include_load_errors_and_csv_warnings():
    raw = _table()
    assert analysis_warnings({'raw_data': raw}) == []
    assert analysis_warnings({'raw_data': raw, 'load_errors': ['Lot9 건너뜀']}) == ['Lot9 건너뜀']
    assert analysis_warnings({'raw_data': raw, 'data_warnings': ['U+FFFD']}) == ['U+FFFD']


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
    win.deleteLater()
    app.processEvents()


def _show(window, rows, **extra):
    result = dict(recipe='R', short_name='R', round='1st', raw_data=rows, statistics={},
                  data_warnings=[], **extra)
    window.recipe_results = [result]
    window.raw_data = rows
    window._display_result(result, {'name': 'R', 'short_name': 'R', 'index': 1})
    return window.filter_info_label


def test_label_is_check_mark_only_without_warnings(window):
    label = _show(window, _table())
    assert label.text().startswith('✅ Analysis basis')
    assert label.toolTip() == ''


@pytest.mark.parametrize('rows, extra, needle', [
    (_table(range(1, 3), range(1, 11)), {}, '판정하지 않습니다'),
    (_table(axes=('X',)), {}, 'Y축 측정값이 없습니다'),
    (_table(), {'load_errors': ['Lot9 건너뜀']}, 'Lot9 건너뜀'),
])
def test_label_warns_and_lists_reasons(window, rows, extra, needle):
    label = _show(window, copy.deepcopy(rows), **extra)
    assert label.text().startswith('⚠ Analysis basis')
    assert '경고 있음' in label.text()
    assert needle in label.toolTip()


def test_card_and_step_button_show_no_verdict_for_partial_data(window):
    _show(window, _table(range(1, 3), range(1, 11)))
    window._compute_all_step_pass_states()
    assert window.step_pass_states[0] is None
    assert window.card_x.badge.text() == '—'
    assert window.card_y.badge.text() == '—'


# ── 리뷰 반영: 실제 로드 경로, 내보내기, 데이터 없는 Step, 비어 있는 칸 ──────────

def test_loader_marks_outliers_per_axis(monkeypatch):
    """로드 경로가 병합 계산으로 돌아가면 실패해야 한다."""
    import core.recipe_scanner as scanner
    rows = _two_axis_rows() + [_row(1, r, 'X', 1e6) for r in range(1, 11)]
    monkeypatch.setattr(scanner, 'batch_load', lambda *a, **k: rows)

    result = scanner.load_recipe_data(
        {'name': 'R', 'short_name': 'R', 'rounds': [{'name': '1st', 'path': 'x'}]})

    assert result['outlier_count'] == 1
    assert [(r['method'], r['value']) for r in rows if r['is_outlier']] == [('X', 500.0)]


def _partial_result():
    return {'recipe': 'R', 'short_name': 'R', 'round': '1st', 'data_warnings': [],
            'load_errors': [], 'statistics': {},
            'raw_data': _table(range(1, 3), range(1, 11))}


def test_excel_shows_dash_and_reason_for_partial_data():
    from openpyxl import Workbook
    from core.exporter import write_checklist_sheets
    wb = Workbook()
    write_checklist_sheets(wb, [_partial_result()], SPEC_LIM, SPEC_DEV)
    rows = list(wb['Checklist'].iter_rows(values_only=True))
    column = rows[0].index('Result')
    assert [r[column] for r in rows[1:]] == ['—', '—']
    basis = [c for r in wb['Analysis Basis'].iter_rows(values_only=True) for c in r if c]
    assert any('판정하지 않습니다' in str(c) for c in basis)


def test_pdf_shows_dash_and_reason_for_partial_data(tmp_path, monkeypatch):
    import matplotlib.axes
    from core.pdf_generator import _basis_notes, generate_pdf_report
    from core.recipe_scanner import compare_recipes
    tables = []
    original = matplotlib.axes.Axes.table

    def spy(self, *args, **kwargs):
        tables.append(kwargs.get('cellText'))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(matplotlib.axes.Axes, 'table', spy)
    result = _partial_result()

    generate_pdf_report(str(tmp_path / 'r.pdf'), 'root', [result], compare_recipes([result]),
                        SPEC_LIM, SPEC_DEV)

    assert (tmp_path / 'r.pdf').stat().st_size > 0
    checklist = next(t for t in tables if t and t[0][1] == 'X')
    assert [row[-1] for row in checklist] == ['—', '—']
    assert any('판정하지 않습니다' in n for n in _basis_notes([result]))


def test_summary_table_shows_dash_without_pass_color(window):
    from PySide6.QtGui import QColor
    from core.recipe_scanner import compare_recipes
    from ui.controllers.table_controller import ROW_X_RANGE, ROW_X_RESULT
    from ui.theme import GREEN
    result = _partial_result()
    window._update_summary_table(compare_recipes([result]), [result])
    assert window.sum_table.item(ROW_X_RESULT, 0).text() == '—'
    assert window.sum_table.item(ROW_X_RANGE, 0).foreground().color() != QColor(GREEN)


def test_card_hides_spec_mark_when_not_judged(window):
    _show(window, _table(range(1, 3), range(1, 11)))
    assert window.card_x.lbl_rng_spec.text() == ''
    assert window.card_x.lbl_std_spec.text() == ''


def test_card_keeps_spec_mark_when_judged(window):
    _show(window, _table())
    assert '✓' in window.card_x.lbl_rng_spec.text()


def test_selecting_step_without_data_clears_previous_verdict(window):
    good = dict(recipe='A', short_name='R', round='1st', raw_data=_table(), statistics={},
                data_warnings=[])
    bad = dict(recipe='B', short_name='R', round='1st', raw_data=[], error='No data loaded',
               load_errors=['Lot1 실패'])
    window.recipes = [{'name': 'A', 'short_name': 'R', 'index': 1, 'rounds': []},
                      {'name': 'B', 'short_name': 'R', 'index': 2, 'rounds': []}]
    window.recipe_results = [good, bad]

    window._select_step(0)
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.filter_info_label.text().startswith('✅')

    window._select_step(1)
    label = window.filter_info_label
    assert label.text().startswith('⚠')
    assert 'No data loaded' in label.toolTip() and 'Lot1 실패' in label.toolTip()
    assert window.card_x.badge.text() == '—'
    assert window.card_y.badge.text() == '—'
    assert '데이터가 없습니다' in window.statusBar().currentMessage()

    window._select_step(0)
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.filter_info_label.text().startswith('✅')


def test_step_not_loaded_yet_also_clears_previous_verdict(window):
    window.recipes = [{'name': 'A', 'short_name': 'R', 'index': 1, 'rounds': []},
                      {'name': 'B', 'short_name': 'R', 'index': 2, 'rounds': []}]
    window.recipe_results = [dict(recipe='A', short_name='R', round='1st', raw_data=_table(),
                                  statistics={}, data_warnings=[])]
    window._select_step(0)
    window._select_step(1)
    assert window.card_x.badge.text() == '—'
    assert not window.filter_info_label.text().startswith('✅')
    assert '로드 중' in window.statusBar().currentMessage()


def test_step_button_shows_fail_when_other_axis_is_not_judged(window):
    rows = (_table(axes=('Y',)) + _table(repeats=range(1, 2), axes=('X',)))
    next(r for r in rows if r['method'] == 'Y' and r['site_id'].startswith('0005'))['value'] += 9000
    _show(window, rows)
    window._compute_all_step_pass_states()
    assert window.step_pass_states[0] is False
    assert window.card_x.badge.text() == '—'
    assert window.card_y.badge.text() == '❌ FAIL'


@pytest.mark.parametrize('remove, filled', [
    (lambda r: r['site_id'].startswith('0005') and r['lot_name'] == 'Lot003', 209),
    (lambda r: r['lot_name'] == 'Lot010' and not r['site_id'].startswith('0002'), 190),
])
def test_empty_cells_warn_even_when_labels_fill_the_matrix(window, remove, filled):
    rows = [r for r in _table() if not remove(r)]
    messages = basis_warnings({'raw_data': rows})
    assert any(f'210칸 중 {filled}칸' in m for m in messages)
    assert _show(window, rows).text().startswith('⚠')


def test_failure_on_stabilization_die_is_a_warning_on_the_label(window):
    rows = _table()
    rows[0].update(valid=False, value=float('nan'))
    assert basis_warnings({'raw_data': rows}) == []
    label = _show(window, rows, failed_measurements={'count': 1, 'text': '측정 실패 1건'})
    assert label.text().startswith('⚠')
    assert '측정 실패 1건' in label.toolTip()


def test_tooltip_is_capped(window):
    label = _show(window, _table(), load_errors=[f'Lot{i} 건너뜀' for i in range(30)])
    assert label.toolTip().count('건너뜀') == 8
    assert '외 22건' in label.toolTip()


def test_matrix_without_labels_is_not_judged():
    assert evaluate_deviation_pass({'count': 5}, {'overall_range': 0.0, 'overall_stddev': 0.0},
                                   2.0, 1.0) is None
