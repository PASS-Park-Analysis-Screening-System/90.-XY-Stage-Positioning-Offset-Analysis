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
    detect_outliers_by_axis(rows, method='iqr')
    alone = detect_outliers_by_axis([r for r in copy.deepcopy(rows) if r['method'] == 'Y'],
                                    method='iqr')
    assert [r['is_outlier'] for r in rows if r['method'] == 'Y'] == [r['is_outlier'] for r in alone]


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
    assert '경고' in label.text()
    assert needle in label.toolTip()


def test_card_and_step_button_show_no_verdict_for_partial_data(window):
    _show(window, _table(range(1, 3), range(1, 11)))
    window._compute_all_step_pass_states()
    assert window.step_pass_states[0] is None
    assert window.card_x.badge.text() == '—'
    assert window.card_y.badge.text() == '—'
