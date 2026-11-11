"""화면과 내보내기 파일이 같은 분석 기준(21 Die × 10 Repeat)을 쓰는지 검사한다.

안정화 Die에 큰 오차를 넣어, 어느 경로든 22 Die로 계산하면 값이 달라지게 만든다.
"""
import csv
import math
import os
import statistics

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import build_checklist, compute_axis_metrics
from core.die_analysis import (DIE_PITCH_MM, DIE_POSITIONS, compute_affine_transform,
                               compute_deviation_matrix, extract_die_positions,
                               filter_stabilization_die, get_die_position)
from core.exporter import (collect_anomalies, export_combined_csv,
                           export_excel_report, export_quality_report)
from core.recipe_scanner import load_recipe_data

SITES = ['0001_X000_Y000', '0002_X000_Y000', '0003_X002_Y000', '0004_X004_Y000',
         '0005_X006_Y000', '0006_X002_Y002', '0007_X004_Y004', '0008_X000_Y002',
         '0009_X000_Y004', '0010_X000_Y006', '0011_X-002_Y002', '0012_X-004_Y004',
         '0013_X-002_Y000', '0014_X-004_Y000', '0015_X-006_Y000', '0016_X-002_Y-002',
         '0017_X-004_Y-004', '0018_X000_Y-002', '0019_X000_Y-004', '0020_X000_Y-006',
         '0021_X002_Y-002', '0022_X004_Y-004']
SPIKE_NM = 5000.0
SPEC_DEVIATION = {'R': {'spec_range': 2.0, 'spec_stddev': 0.4}}
SPEC_LIMITS = {'R': {'X': {'lsl': -5000.0, 'usl': 5000.0},
                     'Y': {'lsl': -5000.0, 'usl': 5000.0}}}


def _site_xy(site_id):
    _, x, y = site_id.split('_')
    return int(x[1:]), int(y[1:])


def _write_lots(root, repeats=range(2, 12)):
    for rep in repeats:
        lot = root / f'Sample10{rep:02d}'
        lot.mkdir(parents=True)
        for axis in ('X', 'Y'):
            lines = [f'Repeat No,{rep}', f'Start Time,2026.02.21 10:{rep:02d}:00', '',
                     'Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,'
                     'Valid,HZ1_O (nm),HZ1_O_Valid']
            for i, site in enumerate(SITES):
                sx, sy = _site_xy(site)
                value = 100.0 + 13.0 * i + 7.0 * rep + (SPIKE_NM if i == 0 else 0.0)
                lines.append(f'{site},{sx},{sy},1,{sx * 10000 + 4305},{sy * 10000 + 5725},{axis},'
                             f'COMPLETED,TRUE,{value},TRUE')
            (lot / f'Sample10_{axis}_UL.csv').write_text('\n'.join(lines) + '\n',
                                                         encoding='utf-8')


@pytest.fixture
def result(tmp_path):
    _write_lots(tmp_path / 'data')
    return load_recipe_data({'name': 'R', 'short_name': 'R',
                             'rounds': [{'name': '2nd', 'path': str(tmp_path / 'data')}]})


def _sheet_rows(ws):
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def _expected(raw, axis):
    """분석 시트 수식을 그대로 계산한 값 (21 Die, MAX-MIN, STDEV)."""
    values = [r['value'] for r in raw
              if r['method'] == axis and not r['site_id'].startswith('0001')]
    assert len(values) == 21 * 10
    mean = sum(values) / len(values)
    deviations = [(v - mean) / 1000.0 for v in values]
    return mean, max(deviations) - min(deviations), statistics.stdev(deviations)


def test_recipe_aggregates_use_analysis_rows(result):
    analysis_values = [r['value'] for r in result['raw_data']
                       if not r['site_id'].startswith('0001')]
    assert len(result['raw_data']) == 440
    assert result['statistics']['count'] == 420
    assert result['statistics']['max'] == pytest.approx(max(analysis_values), abs=1e-3)
    assert result['repeatability']['overall']['count'] == 420
    assert all(t['count'] == 42 for t in result['trend'])
    assert all(t['count'] == 21 for t in result['trend_x'])


def test_stabilization_die_is_never_flagged_as_outlier(result):
    flagged = [r for r in result['raw_data'] if r.get('is_outlier')]
    assert not [r for r in flagged if r['site_id'].startswith('0001')]
    assert result['outlier_count'] == len(flagged)


def test_excel_checklist_matches_sheet_formula_and_screen(result, tmp_path):
    from openpyxl import load_workbook

    path = str(tmp_path / 'report.xlsx')
    export_excel_report(result['raw_data'], result['repeatability'], result['trend'], path,
                        recipe_results=[result], spec_limits=SPEC_LIMITS,
                        spec_deviation=SPEC_DEVIATION)
    wb = load_workbook(path)

    assert wb.sheetnames[:2] == ['Checklist', 'Analysis Basis']
    rows = {r['Axis']: r for r in _sheet_rows(wb['Checklist'])}
    analysis = filter_stabilization_die(result['raw_data'])
    for axis in ('X', 'Y'):
        mean, rng, std = _expected(result['raw_data'], axis)
        row = rows[axis]
        assert (row['Die'], row['Repeat']) == (21, 10)
        assert row['Avg (nm)'] == pytest.approx(mean, abs=1e-3)
        assert row['Dev Range (µm)'] == pytest.approx(rng, abs=1e-3)
        assert row['Dev StdDev (µm)'] == pytest.approx(std, abs=1e-3)
        assert row['Result'] == 'PASS'
        # 카드가 쓰는 함수와도 값이 같다
        card = compute_axis_metrics(analysis, axis, 'R', SPEC_LIMITS, SPEC_DEVIATION)
        assert row['Cpk'] == card['cpk']
        assert row['Dev Range (µm)'] == card['range']

    assert wb['Statistics']['B2'].value == result['repeatability']['overall']['count'] == 420
    assert all(r['Count'] == 42 for r in _sheet_rows(wb['Trend']))


def test_excel_raw_sheet_keeps_22_dies_and_marks_excluded_rows(result, tmp_path):
    from openpyxl import load_workbook

    path = str(tmp_path / 'report.xlsx')
    export_excel_report(result['raw_data'], result['repeatability'], result['trend'], path,
                        recipe_results=[result], spec_limits=SPEC_LIMITS,
                        spec_deviation=SPEC_DEVIATION)
    rows = _sheet_rows(load_workbook(path)['Raw Data'])

    assert len(rows) == 440
    excluded = [r for r in rows if r['분석 대상'] == 'N']
    assert len(excluded) == 20
    assert {r['Site ID'] for r in excluded} == {SITES[0]}


def test_excel_basis_sheet_names_excluded_die_and_folder(tmp_path):
    from openpyxl import load_workbook

    _write_lots(tmp_path / 'data', repeats=range(1, 12))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '2nd', 'path': str(tmp_path / 'data')}]})
    path = str(tmp_path / 'report.xlsx')
    export_excel_report(result['raw_data'], result['repeatability'], result['trend'], path,
                        recipe_results=[result], spec_limits=SPEC_LIMITS,
                        spec_deviation=SPEC_DEVIATION)
    ws = load_workbook(path)['Analysis Basis']
    header = [c.value for c in ws[1]]
    row = dict(zip(header, [c.value for c in ws[2]]))

    assert row['분석 Die'] == 21
    assert row['분석 Repeat'] == 10
    assert row['제외 Die'] == 'Die 1'
    assert row['제외 리핏 폴더'] == 'Sample1001'
    assert (row['분석 행'], row['전체 행']) == (420, 440)


def test_csv_keeps_sheet_columns_and_marks_excluded_rows(result, tmp_path):
    path = tmp_path / 'Analysis.txt'
    export_combined_csv(result['raw_data'], str(path))
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f, delimiter='\t'))

    assert rows[0][:13] == ['Foldername', 'Filename', 'Site ID', 'Site X', 'Site Y',
                            'Point No', 'X (um)', 'Y (um)', 'Method ID', 'State',
                            'Valid', 'HZ1_O (nm)', 'HZ1_O_Valid']
    assert rows[0][13] == 'Analysis Target'
    assert len(rows) - 1 == 440
    assert {r[2] for r in rows[1:] if r[13] == 'N'} == {SITES[0]}


def test_quality_report_marks_scope_and_uses_analysis_reference(result, tmp_path):
    from openpyxl import load_workbook

    bad = result['raw_data'][0]       # 안정화 Die 측정 실패
    bad.update(valid=False, value=math.nan)
    good = next(r for r in result['raw_data']
                if r['site_id'] == SITES[5] and r['method'] == 'X')
    good.update(valid=False, value=math.nan)

    path = str(tmp_path / 'quality.xlsx')
    export_quality_report([result], SPEC_LIMITS, [], path)
    wb = load_workbook(path)

    summary = _sheet_rows(wb['Summary'])[0]
    assert summary['총 측정'] == 440
    assert summary['분석 대상 측정'] == 420
    assert summary['측정 실패'] == 2
    assert summary['실패 (분석 대상)'] == 1
    flags = {r['Site ID']: r['분석 대상'] for r in _sheet_rows(wb['Anomalies'])}
    assert flags == {SITES[0]: 'N', SITES[5]: 'Y'}
    by_die = {r['Site ID']: r['분석 대상'] for r in _sheet_rows(wb['By Die'])}
    assert by_die == flags

    analysis_x = [r['value'] for r in filter_stabilization_die(result['raw_data'])
                  if r['method'] == 'X' and r.get('valid')]
    ref = next(a['ref_mean'] for a in collect_anomalies([result], SPEC_LIMITS)
               if a['axis'] == 'X' and a['in_analysis'] == 'Y')
    assert ref == pytest.approx(sum(analysis_x) / len(analysis_x), abs=1e-3)


def test_pdf_uses_analysis_rows_and_real_round(result, tmp_path, monkeypatch):
    from core import pdf_generator

    captured = {}
    real_title = pdf_generator._add_title_page
    real_titles = []

    def spy_title(pdf, root, comparison, checklist=None, notes=None):
        captured['checklist'] = checklist
        return real_title(pdf, root, comparison, checklist, notes)

    real_extract = pdf_generator._extract_site_data

    def spy_extract(rows, key, method):
        captured.setdefault('contour_sites', set()).update(r['site_id'] for r in rows)
        return real_extract(rows, key, method)

    real_suptitle = pdf_generator.Figure.suptitle

    def spy_suptitle(self, text, **kwargs):
        real_titles.append(text)
        return real_suptitle(self, text, **kwargs)

    monkeypatch.setattr(pdf_generator, '_add_title_page', spy_title)
    monkeypatch.setattr(pdf_generator, '_extract_site_data', spy_extract)
    monkeypatch.setattr(pdf_generator.Figure, 'suptitle', spy_suptitle)

    path = tmp_path / 'report.pdf'
    pdf_generator.generate_pdf_report(str(path), 'root', [result], [], SPEC_LIMITS,
                                      SPEC_DEVIATION)

    assert path.stat().st_size > 0
    assert SITES[0] not in captured['contour_sites']
    assert len(captured['contour_sites']) == 21
    expected = build_checklist([result], SPEC_LIMITS, SPEC_DEVIATION)
    assert [r['range'] for r in captured['checklist']] == [r['range'] for r in expected]
    assert real_titles == ['Step: R (2nd Round)']


def test_comparison_charts_exclude_stabilization_die(result, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    from charts import comparison

    fig = comparison.plot_recipe_comparison_boxplot([result, result])
    title = fig.axes[0].get_title()
    values = [r['value'] for r in filter_stabilization_die(result['raw_data'])
              if r['method'] == 'X']
    assert f'μ={sum(values) / len(values):.0f}' in title


# ── 다이 좌표 ─────────────────────────────────────────────────────────────

def test_die_labels_map_to_real_site_coordinates():
    """라벨 번호는 Site 번호다. Site ID의 좌표와 표준 좌표표가 모두 일치해야 한다."""
    assert len(DIE_POSITIONS) == 21
    for site in SITES:
        label = f'Die{int(site[:4])}'
        assert get_die_position(label) == _site_xy(site), label
    assert get_die_position('Die23') is None


def _affine_inputs(tx, ty, sx, sy, theta):
    """스테이지 좌표(µm)에 알려진 Affine 오차를 입힌 Die 통계와 실측 좌표."""
    positions, x_stats, y_stats = {}, [], []
    for site in SITES[1:]:
        die = int(site[:4])
        ix, iy = _site_xy(site)
        x_mm, y_mm = ix * DIE_PITCH_MM + 4.3, iy * DIE_PITCH_MM + 5.7   # 실데이터 격자 간격
        x_um, y_um = x_mm * 1000.0, y_mm * 1000.0
        positions[die - 1] = (x_mm, y_mm)
        x_stats.append({'die': f'Die{die}', 'avg': tx + sx * x_um - theta * y_um})
        y_stats.append({'die': f'Die{die}', 'avg': ty + sy * y_um + theta * x_um})
    return x_stats, y_stats, positions


def test_affine_recovers_known_parameters_in_real_units():
    x_stats, y_stats, positions = _affine_inputs(
        tx=0.05, ty=-0.03, sx=2e-6, sy=-3e-6, theta=4e-6)

    af = compute_affine_transform(x_stats, y_stats, positions)

    assert af['n_dies'] == 21
    assert af['degenerate'] is False
    assert af['tx'] == pytest.approx(0.05, abs=1e-4)
    assert af['ty'] == pytest.approx(-0.03, abs=1e-4)
    assert af['sx_ppm'] == pytest.approx(2.0, abs=0.01)
    assert af['sy_ppm'] == pytest.approx(-3.0, abs=0.01)
    assert af['theta_urad'] == pytest.approx(4.0, abs=0.01)
    assert af['residual_x'] == pytest.approx(0.0, abs=1e-3)


def test_affine_without_positions_uses_table_and_keeps_die22():
    x_stats, y_stats, _ = _affine_inputs(0.0, 0.0, 2e-6, 0.0, 0.0)

    af = compute_affine_transform(x_stats, y_stats)

    assert af['n_dies'] == 21     # 수정 전에는 Die22 좌표가 없어 20개였다
    assert af['sx_ppm'] == pytest.approx(2.0, abs=0.01)


def test_extract_positions_feed_affine_for_all_analysis_dies(result):
    data = filter_stabilization_die(result['raw_data'])
    dx = compute_deviation_matrix(data, 'X')
    dy = compute_deviation_matrix(data, 'Y')

    af = compute_affine_transform(dx['die_stats'], dy['die_stats'],
                                  extract_die_positions(data))

    assert af['n_dies'] == 21


# ── 카드와 Step 버튼의 기준 ───────────────────────────────────────────────

class _Card:
    def update_stats(self, *args, **kwargs):
        self.stats = (args, kwargs)

    def set_basis(self, die_count, repeat_count, custom=False):
        self.basis = (die_count, repeat_count, custom)


def test_card_shows_basis_and_filter_change_keeps_step_verdict(result):
    from ui.controllers.card_controller import CardMixin

    class Host(CardMixin):
        settings = {'spec_limits': SPEC_LIMITS, 'spec_deviation': SPEC_DEVIATION}
        current_recipe_idx = 0
        step_pass_states = {0: True}
        card_x, card_y = _Card(), _Card()
        _die_filter_is_custom = True

        def _refresh_step_buttons(self):
            pass

    host = Host()
    raw = result['raw_data']
    host._update_cards(raw, {'short_name': 'R'})     # Die 1을 다시 체크한 상태

    assert host.card_y.basis == (22, 10, True)
    # 22 Die 카드는 Spec을 넘지만 Step 버튼의 체크리스트 판정은 그대로다
    assert compute_axis_metrics(raw, 'Y', 'R', SPEC_LIMITS, SPEC_DEVIATION)['result'] is False
    assert host.step_pass_states[0] is True


# 앱의 메인 창처럼 프로세스가 끝날 때까지 위젯을 유지한다 (타이머의 부모가 먼저 해제되지 않게)
_LIVE_WIDGETS = []


# ── PDF 결과 알림 ─────────────────────────────────────────────────────────

class _Log:
    def __init__(self):
        self.ok_msgs, self.errors = [], []

    def info(self, msg):
        pass

    def ok(self, msg):
        self.ok_msgs.append(msg)

    def error(self, msg):
        self.errors.append(msg)


def _run_pdf_export(monkeypatch, tmp_path, generate):
    """_export_pdf를 실제 이벤트 루프로 돌려 메인 스레드에 알림이 오는지 본다."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

    import core.pdf_generator as pdf_generator
    from ui.controllers import export_controller
    from ui.controllers.export_controller import ExportMixin

    app = QApplication.instance() or QApplication([])
    dialogs, opened = [], []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(tmp_path / 'r.pdf'), '')))
    monkeypatch.setattr(QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: dialogs.append(a[2])))
    monkeypatch.setattr(export_controller.os, 'startfile',
                        lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(pdf_generator, 'generate_pdf_report', generate)

    class Host(QWidget, ExportMixin):
        pass

    host = Host()
    _LIVE_WIDGETS.append(host)
    host.recipe_results = [{'short_name': 'R', 'raw_data': []}]
    host.settings = {}
    host.logger = _Log()
    host.path_edit = type('E', (), {'text': lambda self: 'root'})()
    host._export_pdf()

    import time
    deadline = time.time() + 10
    while not (host.logger.ok_msgs or host.logger.errors) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return host.logger, dialogs, opened


def test_pdf_success_is_reported_on_main_thread(monkeypatch, tmp_path):
    log, dialogs, opened = _run_pdf_export(
        monkeypatch, tmp_path, lambda path, *a, **k: open(path, 'wb').close())

    assert log.ok_msgs and 'PDF 저장 완료' in log.ok_msgs[0]
    assert opened == [str(tmp_path / 'r.pdf')]
    assert dialogs == []


def test_pdf_failure_is_shown_to_user(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError('boom')

    log, dialogs, opened = _run_pdf_export(monkeypatch, tmp_path, boom)

    assert log.errors and 'boom' in log.errors[0]
    assert dialogs and 'boom' in dialogs[0]
    assert opened == []
