"""Site ID를 읽지 못한 행이 행렬 0점과 다른 값을 만들지 않는지 확인한다."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import basis_warnings, build_checklist
from core.die_analysis import (compute_deviation_matrix, filter_stabilization_die,
                               select_display_rows, stabilization_die)
from core.exporter import _analysis_flag, build_quality_summary, collect_anomalies
from core.recipe_scanner import load_recipe_data
from core.statistics import axis_reference_means, compute_statistics, detect_outliers

DIES = range(1, 23)
REPEATS = range(1, 11)
SPEC_DEV = {'R': {'spec_range': 2.0, 'spec_stddev': 1.0}}
SPEC_LIM = {'R': {'X': {'lsl': -5000, 'usl': 5000}, 'Y': {'lsl': -5000, 'usl': 5000}}}


def _row(die, rep, axis, value, valid=True, site=None):
    return {'lot_name': f'Lot{rep:03d}', 'site_id': site or f'{die:04d}_X000_Y000',
            'method': axis, 'value': value, 'valid': valid, 'is_outlier': False,
            'filename': f'{rep}.csv', 'x_um': 1.0, 'y_um': 1.0}


def _rows():
    return [_row(d, r, axis, 4000.0 + d + r / 10)
            for d in DIES for r in REPEATS for axis in ('X', 'Y')]


def _with_broken_row(rows):
    """X축 정상 행 하나를 Site ID 불량 + 터무니없는 값으로 바꾼다."""
    target = next(r for r in rows if r['method'] == 'X' and r['site_id'].startswith('0005')
                  and r['lot_name'] == 'Lot003')
    target['site_id'] = 'BAD'
    target['value'] = 99999.0
    return target


def _result(rows):
    analysis = filter_stabilization_die(rows)
    detect_outliers(analysis, method='iqr')
    return {'recipe': 'R', 'short_name': 'R', 'round': '1st', 'raw_data': rows,
            'statistics': compute_statistics(analysis), 'data_warnings': []}


def test_filter_drops_unreadable_and_stabilization_rows():
    rows = _rows()
    bad = _with_broken_row(rows)

    analysis = filter_stabilization_die(rows)

    assert bad not in analysis
    assert not any(r['site_id'].startswith('0001') for r in analysis)
    assert len(analysis) == 21 * 10 * 2 - 1


def test_filter_drops_unreadable_row_even_without_stabilization_die():
    rows = [r for r in _rows() if not r['site_id'].startswith('0001')]
    bad = _with_broken_row(rows)

    assert stabilization_die(rows) is None
    assert bad not in filter_stabilization_die(rows)


def test_reference_mean_equals_matrix_zero_point():
    rows = _rows()
    _with_broken_row(rows)
    analysis = filter_stabilization_die(rows)

    means = axis_reference_means(analysis)

    for axis in ('X', 'Y'):
        assert means[axis] == pytest.approx(compute_deviation_matrix(analysis, axis)['average_nm'])


def test_quality_sheet_uses_matrix_zero_point_and_marks_row_out_of_analysis():
    rows = _rows()
    bad = _with_broken_row(rows)
    failed = next(r for r in rows if r['method'] == 'X' and r['site_id'].startswith('0007'))
    failed['valid'] = False
    invalid_bad = next(r for r in rows if r['method'] == 'Y' and r['site_id'].startswith('0009'))
    invalid_bad['site_id'] = 'BAD2'
    invalid_bad['valid'] = False

    anomalies = collect_anomalies([_result(rows)])

    zero = compute_deviation_matrix(filter_stabilization_die(rows), 'X')['average_nm']
    row = next(a for a in anomalies if a['site_id'] == failed['site_id'] and a['axis'] == 'X')
    assert row['ref_mean'] == pytest.approx(zero, abs=0.001)
    assert row['in_analysis'] == 'Y'
    # 값 99999인 불량 ID 행은 분석 밖이므로 이상치로 올라오지 않는다
    assert not bad['is_outlier']
    assert not any(a['site_id'] == 'BAD' for a in anomalies)
    # 실패 행으로는 남되 분석 대상이 아니라고 표시한다
    assert next(a for a in anomalies if a['site_id'] == 'BAD2')['in_analysis'] == 'N'
    assert _analysis_flag(bad, stabilization_die(rows)) == 'N'


def test_statistics_and_analysis_total_exclude_unreadable_row():
    clean = _rows()
    rows = _rows()
    _with_broken_row(rows)

    stats = _result(rows)['statistics']

    assert stats['count'] == len(filter_stabilization_die(clean)) - 1
    assert stats['max'] < 5000
    assert stats['stdev'] == pytest.approx(_result(clean)['statistics']['stdev'], abs=0.05)
    summary = build_quality_summary([_result(rows)], [])[0]
    assert summary['analysis_total'] == 21 * 10 * 2 - 1


def test_verdict_range_and_stddev_are_unchanged_by_unreadable_row():
    """분석 행이 달라지지 않으면 행렬 결과도 그대로다."""
    rows = _rows()
    _with_broken_row(rows)
    without = [r for r in rows if r['site_id'] != 'BAD']

    got = build_checklist([_result(rows)], SPEC_LIM, SPEC_DEV)
    want = build_checklist([_result(without)], SPEC_LIM, SPEC_DEV)

    assert len(got) == 2
    for a, b in zip(got, want):
        for key in ('range', 'stddev', 'avg', 'cpk', 'result', 'die_count', 'repeat_count'):
            assert a[key] == b[key], key


def test_excluded_row_count_still_counts_unreadable_rows():
    rows = _rows()
    _with_broken_row(rows)
    failed = next(r for r in rows if r['method'] == 'X' and r['site_id'].startswith('0007'))
    failed['valid'] = False
    invalid_bad = next(r for r in rows if r['method'] == 'Y' and r['site_id'].startswith('0009'))
    invalid_bad['site_id'] = 'BAD2'
    invalid_bad['valid'] = False

    # BAD(유효), 실패 행, BAD2(실패)를 각각 한 번씩 센다
    assert any('3행을 제외' in m for m in basis_warnings(_result(rows)))


def test_clean_data_is_not_affected():
    rows = _rows()
    analysis = filter_stabilization_die(rows)

    assert len(analysis) == 420
    assert basis_warnings(_result(rows)) == []
    assert all(_analysis_flag(r, stabilization_die(rows)) == ('N' if r['site_id'].startswith('0001') else 'Y')
               for r in rows)


def test_clean_data_values_are_pinned():
    """값 4000 + Die + Repeat/10, Die 2~22, Repeat 1~10을 손으로 계산한 기대값."""
    rows = build_checklist([_result(_rows())], SPEC_LIM, SPEC_DEV)

    assert len(rows) == 2
    for row in rows:
        assert row['avg'] == pytest.approx(4012.55)        # 4000 + 12 + 0.55
        assert row['range'] == pytest.approx(0.021)        # (20 + 0.9) / 1000
        assert row['result'] is True
        assert (row['die_count'], row['repeat_count']) == (21, 10)


def test_display_rows_drop_unreadable_rows_with_or_without_excluded_die():
    rows = _rows()
    bad = _with_broken_row(rows)

    assert bad not in select_display_rows(rows, {0})
    assert bad not in select_display_rows(rows, set())
    assert len(select_display_rows(rows, {0})) == len(filter_stabilization_die(rows))


def _write_csv(root, broken_rows=False, all_broken=False):
    """실제 로더를 거치도록 Lot 폴더와 CSV를 만든다."""
    head = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
            'HZ1_O (nm),HZ1_O_Valid')
    for rep in range(2, 12):
        lot = root / f'S{rep:02d}'
        lot.mkdir(parents=True)
        for axis in 'XY':
            lines = []
            for die in DIES:
                site, value = f'{die:04d}_X000_Y000', 4000.0 + die + rep / 10
                if all_broken or (broken_rows and axis == 'X' and die == 5 and rep == 3):
                    site, value = 'BAD', 99999.0
                lines.append(f'{site},0,0,1,{die * 1000.0},{die * 500.0},{axis},COMPLETED,TRUE,{value},TRUE')
            (lot / f'S_{axis}_UL.csv').write_text(
                '\n'.join([f'Repeat No,{rep}', '', head] + lines) + '\n', encoding='utf-8')


def _load(root):
    return load_recipe_data({'name': 'R', 'short_name': 'R',
                             'rounds': [{'name': '1st', 'path': str(root)}]})


def test_real_loader_keeps_statistics_and_outliers_off_unreadable_row(tmp_path):
    _write_csv(tmp_path, broken_rows=True)

    result = _load(tmp_path)

    assert result['statistics']['max'] < 5000
    assert not any(r['is_outlier'] for r in result['raw_data'] if r['site_id'] == 'BAD')


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


def _show(window, result):
    window.recipe_results = [result]
    window.raw_data = result['raw_data']
    window._display_result(result, {'name': 'R', 'short_name': 'R', 'index': 1})
    return window.filter_info_label.text()


def test_screen_label_counts_same_rows_as_analysis(tmp_path, window):
    _write_csv(tmp_path, broken_rows=True)
    result = _load(tmp_path)

    label = _show(window, result)

    assert 'Analyzing 419 of 440' in label
    assert label.startswith('⚠')    # 제외한 행이 있으면 경고가 있으므로 ✅가 아니다
    assert '1행을 제외' in window.filter_info_label.toolTip()


def test_screen_does_not_claim_analysis_basis_when_nothing_is_readable(tmp_path, window):
    _write_csv(tmp_path, all_broken=True)
    result = _load(tmp_path)

    label = _show(window, result)

    assert 'Analyzing 0 of' in label
    assert not label.startswith('✅')


def test_summary_table_shows_dash_when_recipe_has_no_analysis_rows(tmp_path, window):
    from core.recipe_scanner import compare_recipes
    _write_csv(tmp_path, all_broken=True)
    result = _load(tmp_path)

    window._update_summary_table(compare_recipes([result]), [result])

    texts = [window.sum_table.item(r, 0).text()
             for r in range(window.sum_table.rowCount())
             if window.sum_table.item(r, 0)]
    assert '—' in texts
    assert '0.0' not in texts
