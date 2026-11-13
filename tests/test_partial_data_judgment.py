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
    # 1 Repeat: 1.1.4부터 판정하되 '참고 (1 Repeat)'로 표시한다 (사용자 결정. 표시는 test_single_lot)
    (range(1, 23), range(1, 2), True),
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
    win._expert_window.close()
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


def test_step_without_result_clears_previous_verdict(window):
    window.recipes = [{'name': 'A', 'short_name': 'R', 'index': 1, 'rounds': []},
                      {'name': 'B', 'short_name': 'R', 'index': 2, 'rounds': []}]
    window.recipe_results = [dict(recipe='A', short_name='R', round='1st', raw_data=_table(),
                                  statistics={}, data_warnings=[])]
    window._select_step(0)
    window._select_step(1)
    assert window.card_x.badge.text() == '—'
    assert window.filter_info_label.text() == '⚠ 스캔 결과가 없습니다'
    assert '스캔 결과가 없습니다' in window.statusBar().currentMessage()


def test_step_button_shows_fail_when_other_axis_is_not_judged(window):
    # 판정하지 않는 축: 1.1.4부터 1 Repeat은 참고 판정이라, Die가 1개(Site 0001 제외)인 축으로 만든다
    rows = (_table(axes=('Y',)) + _table(dies=range(1, 3), axes=('X',)))
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


# ── 재리뷰 반영: 차트 잔상, 재스캔 중 이전 판정, 툴팁 ─────────────────────────

_STALE_CHARTS = ('Contour X', 'Contour Y', 'X*Y Offset', 'Vector Map',
                 'Correlation', '3D X', '3D Y', '3D X+Y')


def _chart_is_empty(widget):
    return getattr(widget, '_canvas', None) is None and getattr(widget, '_widget', None) is None


def _two_steps(window, second=None):
    window.recipes = [{'name': 'A', 'short_name': 'R', 'index': 1, 'rounds': []},
                      {'name': 'B', 'short_name': 'R', 'index': 2, 'rounds': []}]
    window.recipe_results = [dict(recipe='A', short_name='R', round='1st', raw_data=_table(),
                                  statistics={}, data_warnings=[])]
    if second is not None:
        window.recipe_results.append(second)


def test_step_without_data_leaves_no_chart_from_previous_step(window):
    window._open_expert_window()        # Correlation·3D는 전문 분석 창이 열려 있을 때 그린다
    _two_steps(window, dict(recipe='B', short_name='R', round='1st', raw_data=[],
                            error='No data loaded'))
    window._select_step(0)
    assert not any(_chart_is_empty(window.chart_widgets[n]) for n in _STALE_CHARTS)

    window._select_step(1)
    assert all(_chart_is_empty(window.chart_widgets[n]) for n in _STALE_CHARTS)

    window._select_step(0)
    assert not any(_chart_is_empty(window.chart_widgets[n]) for n in _STALE_CHARTS)


def test_axis_without_data_clears_only_charts_that_need_it(window):
    window._open_expert_window()        # Correlation·3D는 전문 분석 창이 열려 있을 때 그린다
    _show(window, _table())
    _show(window, _table(axes=('X',)))
    assert not _chart_is_empty(window.chart_widgets['Contour X'])
    assert not _chart_is_empty(window.chart_widgets['3D X'])
    for name in ('Contour Y', '3D Y', 'X*Y Offset', 'Vector Map', 'Correlation', '3D X+Y'):
        assert _chart_is_empty(window.chart_widgets[name])


@pytest.fixture
def scan(window, monkeypatch, tmp_path):
    """실제 _scan_folder를 가짜 로더 스레드로 돌린다 (설정 파일과 대화상자는 막는다)."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QMessageBox
    import ui.controllers.scan_controller as controller

    class FakeThread(QObject):
        finished = Signal(list, list, float)
        error = Signal(str, str)     # 실제 DataLoaderThread와 같은 형태
        progress = Signal(str)
        cancelled = Signal()
        count = Signal(int, int)

        def __init__(self, folder, recipes=None, session=None):
            super().__init__()
            self.folder = folder
            self.recipes = recipes

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(controller, 'DataLoaderThread', FakeThread)
    # 폴더 확인 스레드는 이 테스트에서 바로 실행한다 (시그널이 곧바로 슬롯을 부른다)
    monkeypatch.setattr(controller.FolderScanThread, 'start', lambda self: self.run())

    class CancelledDialog:  # 폴더명 불일치 때 여는 Workflow별 폴더 선택을 취소한다
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(controller, 'WorkflowFolderDialog', CancelledDialog)
    monkeypatch.setattr(controller, 'add_recent_folder', lambda settings, _: settings)
    monkeypatch.setattr(window, '_save_settings', lambda: None)
    for name in ('warning', 'information', 'critical'):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    window.path_edit.setText(str(tmp_path))
    window.settings['spec_deviation'] = {'R': SPEC_DEV['R']}

    def run(names):
        recipes = [{'name': n, 'short_name': n, 'index': i + 1, 'rounds': []}
                   for i, n in enumerate(names)]
        monkeypatch.setattr(controller, 'scan_recipes', lambda *_a, **_k: recipes)
        window._scan_folder()
    yield run
    window._loader_thread = None


def _finish_scan(window):
    from core.recipe_scanner import compare_recipes
    result = dict(recipe='R', short_name='R', round='1st', raw_data=_table(), statistics={},
                  data_warnings=[], load_errors=[], trend=[], trend_x=[], trend_y=[])
    window._loader_thread.finished.emit([result], compare_recipes([result]), 1.0)


def test_rescan_hides_verdict_of_previous_folder_while_loading(window, scan):
    scan(['R'])
    _finish_scan(window)
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.sum_table.columnCount() == 1

    scan(['R'])

    assert window.recipe_results == []
    assert window.step_pass_states == {}
    assert window.sum_table.columnCount() == 0
    assert window.card_x.badge.text() == '—'
    assert window.filter_info_label.text() == '⚠ 데이터 로드 중'
    assert '로드 중' in window.statusBar().currentMessage()

    _finish_scan(window)
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.filter_info_label.text().startswith('✅')
    assert window.sum_table.columnCount() == 1


def test_scan_failure_does_not_stay_on_loading(window, scan):
    scan(['R'])
    window._loader_thread.error.emit('RuntimeError: 읽기 실패', 'Traceback: 원문')

    assert window.filter_info_label.text() == '⚠ 스캔 실패'
    assert window.filter_info_label.toolTip() == 'RuntimeError: 읽기 실패'
    logged = str(window.logger.export_rows())
    assert 'RuntimeError: 읽기 실패' in logged and 'Traceback: 원문' in logged
    assert window.statusBar().currentMessage() == '스캔 실패'

    window._select_step(0)
    assert window.filter_info_label.text() == '⚠ 스캔 결과가 없습니다'
    assert '로드 중' not in window.statusBar().currentMessage()


@pytest.mark.parametrize('names', [['Z'], []])
def test_cancelled_scan_keeps_previous_recipes_and_results(window, scan, names):
    scan(['R'])
    _finish_scan(window)
    before = list(window.recipes)

    scan(names)     # Z는 폴더명 불일치, []는 Recipe 없음

    assert window.recipes == before
    assert len(window.recipe_results) == 1
    assert window.card_x.badge.text() == '✅ PASS'
    window._on_die_filter_changed(None)     # 취소 뒤에도 예외가 없어야 한다


def test_tooltip_keeps_hint_and_warnings_when_all_dies_are_unchecked(window):
    rows = _table(repeats=range(1, 6))
    _show(window, rows)
    for box in window._die_checkboxes.values():
        box.blockSignals(True)
        box.setChecked(False)
        box.blockSignals(False)
    label = _show(window, rows)
    assert label.text().startswith('⚠ 분석할 수 있는 행이 없습니다')
    assert '모든 Die를 해제했습니다' in label.toolTip()
    assert '21 Die × 5 Repeat' in label.toolTip()


def test_numpy_values_still_give_plain_fail():
    import numpy as np
    dev = {'die_labels': ['a', 'b'], 'repeat_labels': ['c', 'd'],
           'overall_range': np.float64(9.0), 'overall_stddev': np.float64(0.1)}
    assert evaluate_deviation_pass({'count': 5}, dev, 2.0, 1.0) is False


def test_scan_start_failure_recovers_like_a_scan_error(window, scan, monkeypatch):
    def boom():
        raise RuntimeError('화면 오류')
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)

    scan(['R'])

    assert window.btn_scan.isEnabled()
    assert window.btn_browse.isEnabled()
    assert window._loader_thread is None    # 시작하지 않은 스레드를 남기지 않는다
    assert window.filter_info_label.text() == '⚠ 스캔 실패'
    assert window.statusBar().currentMessage() == '스캔 실패'


def _dialogs(monkeypatch):
    import ui.controllers.scan_controller as controller
    shown = []
    monkeypatch.setattr(controller.QMessageBox, 'critical',
                        lambda _parent, _title, text: shown.append(text))
    return shown


def test_load_thread_message_is_shown_as_it_came(window, scan, monkeypatch):
    shown = _dialogs(monkeypatch)
    scan(['R'])
    window._loader_thread.error.emit('RuntimeError: ', '')
    assert shown == ['RuntimeError: ']


def test_scan_start_failure_clears_previous_cards(window, scan, monkeypatch):
    import ui.controllers.scan_controller as controller
    rows = _table()
    _show(window, rows)
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.card_x.lbl_cpk.text() not in ('—', 'N/A')
    at_dialog = []

    def dialog(_parent, _title, text):  # 대화상자는 모달이므로 떠 있는 동안의 카드를 본다
        at_dialog.append((text, window.card_x.badge.text(), window.card_y.badge.text(),
                          window.card_x.lbl_cpk.text(), window.card_y.lbl_cpk.text(),
                          window.btn_scan.isEnabled(), window._loader_thread is None))
    monkeypatch.setattr(controller.QMessageBox, 'critical', dialog)

    def boom(_idx):     # 실제 _reset_results_for_scan이 화면을 다시 그리다 실패한다
        raise RuntimeError('화면 오류')
    monkeypatch.setattr(window, '_select_step', boom)

    scan(['R'])

    for card in (window.card_x, window.card_y):
        assert card.badge.text() == '—'
        assert card.lbl_cpk.text() == '—'
        assert card.lbl_avg.text() == '—'
        assert card.lbl_rng.text() == '—'
        assert card.lbl_std.text() == '—'
        assert card.lbl_rng_spec.text() == ''
        assert card.lbl_std_spec.text() == ''
        assert card.lbl_basis.text() == ''
    assert at_dialog == [('화면 오류', '—', '—', '—', '—', True, True)]
    logged = str(window.logger.export_rows())
    assert '화면 오류' in logged and 'Traceback' in logged

    _show(window, rows)     # 다음 표시에서는 정상으로 돌아온다
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.card_x.lbl_basis.text() == '21 Die × 10 Repeat'


def test_scan_start_recovery_survives_card_clear_failure(window, scan, monkeypatch):
    _show(window, _table())
    shown = _dialogs(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError('화면 오류')
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)
    monkeypatch.setattr(window.card_x, 'update_stats', boom)

    scan(['R'])

    assert shown == ['화면 오류']
    assert window.btn_scan.isEnabled()
    assert window._loader_thread is None
    assert window.filter_info_label.text() == '⚠ 스캔 실패'
    assert window.card_y.badge.text() == '—'    # 한 카드의 실패가 다른 카드를 막지 않는다


def test_scan_start_recovery_survives_log_failure(window, scan, monkeypatch):
    shown = _dialogs(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError('화면 오류')
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)
    monkeypatch.setattr(window.card_x, 'clear_stats', boom)
    monkeypatch.setattr(window.logger, 'error', boom)

    scan(['R'])

    assert shown == ['화면 오류']
    assert window._loader_thread is None
    assert window.btn_scan.isEnabled()


def test_scan_start_recovery_survives_label_and_unprintable_card_error(window, scan, monkeypatch):
    class BadError(Exception):
        def __str__(self):
            raise ValueError('str 실패')
    shown = _dialogs(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError('화면 오류')

    def bad_clear():
        raise BadError()
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)
    monkeypatch.setattr(window.card_x, 'clear_stats', bad_clear)
    monkeypatch.setattr(window.filter_info_label, 'setText', boom)

    scan(['R'])

    assert shown == ['화면 오류']
    assert window._loader_thread is None
    assert window.btn_scan.isEnabled()
    assert window.card_y.badge.text() == '—'


def test_exception_whose_str_fails_still_recovers(window, scan, monkeypatch):
    class BadError(Exception):
        def __str__(self):
            raise ValueError('str 실패')
    shown = _dialogs(monkeypatch)

    def boom():
        raise BadError()
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)

    scan(['R'])

    assert shown == ['BadError']
    assert window._loader_thread is None
    assert window.btn_scan.isEnabled()


@pytest.mark.parametrize('error', [RuntimeError(), RuntimeError('  ')])
def test_exception_without_message_still_fills_the_dialog(window, scan, monkeypatch, error):
    shown = _dialogs(monkeypatch)

    def boom():
        raise error
    monkeypatch.setattr(window, '_reset_results_for_scan', boom)

    scan(['R'])

    assert shown == ['RuntimeError']
    assert window.filter_info_label.toolTip() == 'RuntimeError'


# ── 사용자 결정: 경고가 있는 PASS 배지, 판정이 없을 때의 Cpk ─────────────────

def _excel_rows(result):
    from openpyxl import Workbook
    from core.exporter import write_checklist_sheets
    wb = Workbook()
    write_checklist_sheets(wb, [result], SPEC_LIM, SPEC_DEV)
    rows = list(wb['Checklist'].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def _pdf_rows(result, tmp_path, monkeypatch):
    import matplotlib.axes
    from core.pdf_generator import generate_pdf_report
    from core.recipe_scanner import compare_recipes
    tables = []
    original = matplotlib.axes.Axes.table

    def spy(self, *args, **kwargs):
        tables.append(kwargs)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(matplotlib.axes.Axes, 'table', spy)
    generate_pdf_report(str(tmp_path / 'r.pdf'), 'root', [result], compare_recipes([result]),
                        SPEC_LIM, SPEC_DEV)
    table = next(t for t in tables if 'Cpk' in (t.get('colLabels') or []))
    return [dict(zip(table['colLabels'], row)) for row in table['cellText']]


def test_cpk_is_dash_everywhere_when_not_judged(window, tmp_path, monkeypatch):
    result = _partial_result()

    assert {r['axis']: r['cpk'] for r in build_checklist([result], SPEC_LIM, SPEC_DEV)} == {
        'X': None, 'Y': None}
    assert [r['Cpk'] for r in _excel_rows(result)] == ['—', '—']
    assert [r['Cpk'] for r in _pdf_rows(result, tmp_path, monkeypatch)] == ['—', '—']
    _show(window, result['raw_data'])
    assert window.card_x.lbl_cpk.text() == '—'
    assert window.card_y.lbl_cpk.text() == '—'


def test_cpk_stays_a_number_for_normal_data(window, tmp_path, monkeypatch):
    result = {**_partial_result(), 'raw_data': _table()}
    metric = {r['axis']: r['cpk'] for r in build_checklist([result], SPEC_LIM, SPEC_DEV)}
    assert metric['X'] == pytest.approx(5 / (3 * compute_statistics(
        filter_by_method(filter_stabilization_die(result['raw_data']), 'X'))['stdev']), abs=1e-3)

    assert [r['Cpk'] for r in _excel_rows(result)] == [metric['X'], metric['Y']]
    pdf = _pdf_rows(result, tmp_path, monkeypatch)
    assert [r['Cpk'] for r in pdf] == [f"{metric['X']:.2f}", f"{metric['Y']:.2f}"]
    _show(window, result['raw_data'])
    assert window.card_x.lbl_cpk.text() == f"{metric['X']:.2f}"


def test_cpk_is_not_applicable_when_judged_but_not_computable(window, tmp_path, monkeypatch):
    rows = [dict(r, value=4000.0) for r in _table()]    # σ = 0, 판정은 PASS
    assert _checks(rows)['X']['result'] is True
    cpk = _checks(rows)['X']['cpk']
    assert cpk != cpk
    result = {**_partial_result(), 'raw_data': rows}
    assert _excel_rows(result)[0]['Cpk'] == 'N/A'
    assert _pdf_rows(result, tmp_path, monkeypatch)[0]['Cpk'] == 'N/A'
    _show(window, rows)
    assert window.card_x.lbl_cpk.text() == 'N/A'


def test_cpk_is_dash_when_verdict_is_dash_because_spec_is_missing():
    rows = _table()
    result = {'short_name': 'R', 'round': '1st', 'raw_data': rows}
    row = build_checklist([result], SPEC_LIM, {})[0]
    assert row['result'] is None
    assert row['cpk'] is None


def test_badge_warns_on_pass_with_warning_and_keeps_verdict(window, tmp_path, monkeypatch):
    rows = [r for r in _table() if not (r['site_id'].startswith('0005')
                                        and r['lot_name'] == 'Lot003')]
    result = {**_partial_result(), 'raw_data': rows}

    _show(window, rows)

    assert window.card_x.badge.text() == '⚠ PASS'
    assert window.card_y.badge.text() == '⚠ PASS'
    window._compute_all_step_pass_states()
    assert window.step_pass_states[0] is True
    assert [r['Result'] for r in _excel_rows(result)] == ['PASS', 'PASS']
    assert [r['Result'] for r in _pdf_rows(result, tmp_path, monkeypatch)] == ['PASS', 'PASS']


@pytest.mark.parametrize('extra', [
    {'load_errors': ['Lot9 건너뜀']},
    {'failed_measurements': {'count': 1, 'text': '측정 실패 1건'}},
])
def test_badge_warns_for_every_kind_of_warning(window, extra):
    _show(window, _table(), **extra)
    assert window.card_x.badge.text() == '⚠ PASS'


def test_badge_warns_for_csv_warning(window):
    rows = _table()
    result = dict(recipe='R', short_name='R', round='1st', raw_data=rows, statistics={},
                  data_warnings=['U+FFFD'])
    window.recipe_results = [result]
    window._display_result(result, {'name': 'R', 'short_name': 'R', 'index': 1})
    assert window.card_x.badge.text() == '⚠ PASS'


def test_badge_is_plain_pass_without_warning_and_returns_after_warning(window):
    from ui.theme import GREEN, ORANGE
    _show(window, _table(), load_errors=['Lot9 건너뜀'])
    assert window.card_x.badge.text() == '⚠ PASS'
    assert f'background: {ORANGE}' in window.card_x.badge.styleSheet()
    _show(window, _table())
    assert window.card_x.badge.text() == '✅ PASS'
    assert f'background: {GREEN}' in window.card_x.badge.styleSheet()


def test_custom_filter_keeps_warned_badge_and_shows_the_reason(window):
    rows = _table()
    _show(window, rows, load_errors=['Lot9 건너뜀'])
    box = window._die_checkboxes[5]
    box.blockSignals(True)
    box.setChecked(False)
    box.blockSignals(False)

    label = _show(window, rows, load_errors=['Lot9 건너뜀'])

    assert label.text().startswith('⚠ Custom')
    assert window.card_x.badge.text() == '⚠ PASS'
    assert 'Lot9 건너뜀' in label.toolTip()
    assert '제외: ' in label.toolTip()


def test_badge_fail_and_dash_do_not_change_with_warning(window):
    rows = _table()
    next(r for r in rows if r['method'] == 'X' and r['site_id'].startswith('0005'))['value'] += 9000
    _show(window, rows, load_errors=['Lot9 건너뜀'])
    assert window.card_x.badge.text() == '❌ FAIL'
    assert window.card_y.badge.text() == '⚠ PASS'
    _show(window, _table(range(1, 3), range(1, 11)))
    assert window.card_x.badge.text() == '—'
