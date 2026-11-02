"""경계 조건 회귀 테스트: 비정상 측정 행과 폴더 구성에서 값이 말없이 달라지지 않는지 본다."""
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import basis_warnings, build_checklist, compute_axis_metrics
from core.csv_loader import batch_load
from core.die_analysis import (compute_affine_transform, compute_deviation_matrix,
                               extract_die_positions, filter_stabilization_die,
                               stabilization_die)
from core.exporter import export_excel_report, export_quality_report
from core.recipe_scanner import load_recipe_data
from core.statistics import compute_repeatability, compute_statistics, compute_trend

from PySide6.QtWidgets import QApplication

# 참조를 잡아 두어야 한다. 버리면 QApplication이 해제되어 위젯 생성에서 프로세스가 죽는다.
_APP = QApplication.instance() or QApplication([])
# 앱의 메인 창처럼 프로세스가 끝날 때까지 위젯을 유지한다 (타이머의 부모가 먼저 해제되지 않게)
_LIVE_WIDGETS = []

SPEC_DEVIATION = {'R': {'spec_range': 2.0, 'spec_stddev': 0.4}}
SPEC_LIMITS = {'R': {'X': {'lsl': -5000.0, 'usl': 5000.0},
                     'Y': {'lsl': -5000.0, 'usl': 5000.0}}}
HEADER = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
          'HZ1_O (nm),HZ1_O_Valid')


def _row(die, rep=2, axis='Y', value=None, valid=True, lot=None):
    return {'lot_name': lot or f'L{rep:02d}', 'site_id': f'{die:04d}_X000_Y000', 'method': axis,
            'value': 100.0 + die + rep if value is None else value, 'valid': valid,
            'x_um': 1000.0 * die, 'y_um': 500.0 * die + 7.0 * (die % 3)}


def _table(dies=range(1, 23), reps=range(2, 12), axes='XY'):
    return [_row(d, r, a) for r in reps for a in axes for d in dies]


def _write_lot(root, name, repeat_no=None, minute=None, axes='XY', dies=(1, 2, 3)):
    lot = root / name
    lot.mkdir(parents=True)
    meta = []
    if repeat_no is not None:
        meta.append(f'Repeat No,{repeat_no}')
    if minute is not None:
        meta.append(f'Start Time,2026.06.11 10:{minute:02d}:00')
    for axis in axes:
        rows = [f'{d:04d}_X000_Y000,0,0,1,{d},{d},{axis},COMPLETED,TRUE,{float(d)},TRUE'
                for d in dies]
        (lot / f'S_{axis}_UL.csv').write_text(
            '\n'.join(['Lot ID,L'] + meta + ['', HEADER] + rows) + '\n', encoding='utf-8')


def _write_summary_only(root, name):
    lot = root / name
    lot.mkdir(parents=True)
    (lot / 'Summary.csv').write_text(
        f'Lot ID,L\nRepeat No,1\n\n{HEADER}\n', encoding='utf-8')


def _lots(rows):
    seen = []
    for r in rows:
        if r['lot_name'] not in seen:
            seen.append(r['lot_name'])
    return seen


# ── 안정화 Die는 행 순서가 아니라 Site 0001이다 ──────────────────────────

def test_missing_site_0001_in_first_lot_does_not_drop_another_die():
    rows = _table()
    rows = [r for r in rows if not (r['lot_name'] == 'L02' and r['site_id'].startswith('0001'))]

    assert stabilization_die(rows) == 0
    analysis = filter_stabilization_die(rows)
    assert not [r for r in analysis if r['site_id'].startswith('0001')]
    assert [r for r in analysis if r['site_id'].startswith('0002')]


def test_recipe_without_site_0001_keeps_all_dies_and_warns():
    rows = _table(dies=range(2, 23))

    assert stabilization_die(rows) is None
    assert filter_stabilization_die(rows) == rows
    assert any('Site 0001' in m for m in basis_warnings({'raw_data': rows}))


def test_standard_set_has_no_basis_warning():
    assert basis_warnings({'raw_data': _table()}) == []


# ── Valid=TRUE + NaN ──────────────────────────────────────────────────────

def test_valid_nan_row_is_dropped_from_every_aggregate():
    rows = _table(axes='X')
    clean = compute_axis_metrics(rows[1:], 'X', 'R', SPEC_LIMITS, SPEC_DEVIATION)
    rows[0].update(value=math.nan)      # Valid=TRUE인데 값이 NaN

    metrics = compute_axis_metrics(rows, 'X', 'R', SPEC_LIMITS, SPEC_DEVIATION)

    assert metrics['avg'] == clean['avg']
    assert metrics['range'] == clean['range']
    assert metrics['stddev'] == clean['stddev']
    assert metrics['result'] is clean['result'] is True
    assert compute_statistics(rows)['count'] == len(rows) - 1
    assert all(t['mean'] == t['mean'] for t in compute_trend(rows))
    assert compute_repeatability(rows)['overall']['mean'] == pytest.approx(clean['avg'], abs=1e-2)


def test_all_failed_lot_is_not_counted_as_zero_mean():
    rows = _table(axes='X')
    for r in rows:
        if r['lot_name'] == 'L02':
            r.update(valid=False, value=math.nan)

    lot_variation = compute_repeatability(rows)['lot_variation']

    assert lot_variation['count'] == 9
    assert lot_variation['range_of_means'] < 20     # 평균 0이 섞이면 100 이상이 된다


def test_failed_row_coordinates_do_not_break_affine():
    rows = _table()
    bad = next(r for r in rows if r['site_id'].startswith('0005'))
    bad.update(valid=False, value=math.nan, x_um=math.nan, y_um=math.nan)
    analysis = filter_stabilization_die(rows)

    positions = extract_die_positions(analysis)

    assert all(math.isfinite(c) for pos in positions.values() for c in pos)
    dx = compute_deviation_matrix(analysis, 'X')
    dy = compute_deviation_matrix(analysis, 'Y')
    assert compute_affine_transform(dx['die_stats'], dy['die_stats'], positions)['n_dies'] == 21


# ── 폴더 판정과 순서 ──────────────────────────────────────────────────────

def test_summary_only_folder_is_not_counted_as_repeat(tmp_path):
    for rep in range(1, 11):
        _write_lot(tmp_path, f'S{rep:02d}', rep, minute=rep)
    _write_summary_only(tmp_path, 'S99')
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_lots(rows)) == 10
    assert 'first_repeat_excluded' not in info
    assert info['no_measurement_folders'] == ['S99']


def test_summary_only_folder_does_not_break_measurement_order(tmp_path):
    names = [f'Lot6 ({n})' for n in range(2, 11)] + ['Lot6']
    for i, name in enumerate(names):
        _write_lot(tmp_path, name, i + 2, minute=i)
    _write_summary_only(tmp_path, 'Lot6 (99)')

    assert _lots(batch_load(str(tmp_path))) == names


def test_equal_start_time_falls_back_to_repeat_no(tmp_path):
    names = ['Lot6'] + [f'Lot6 ({n})' for n in range(2, 12)]
    # 시각이 모두 같고, 괄호 없는 Lot6이 Repeat 11, Lot6 (2)가 Repeat 1
    for name, rep in zip(names, [11] + list(range(1, 11))):
        _write_lot(tmp_path, name, rep, minute=5)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert info['first_repeat_excluded'] == 'Lot6 (2)'
    assert len(_lots(rows)) == 10


def test_lot_index_is_the_same_for_ten_and_eleven_folder_entries(tmp_path):
    for rep in range(1, 12):
        _write_lot(tmp_path / 'a', f'S{rep:02d}', rep, minute=rep)
    for rep in range(2, 12):
        _write_lot(tmp_path / 'b', f'S{rep:02d}', rep, minute=rep)

    def index_map(name):
        return {r['lot_name']: r['lot_index'] for r in batch_load(str(tmp_path / name))}

    assert index_map('a') == index_map('b')
    assert min(index_map('a').values()) == 1


@pytest.mark.parametrize('total, repeats_left', [(12, 11), (22, 21)])
def test_unexpected_repeat_count_is_flagged_after_exclusion(tmp_path, total, repeats_left):
    for n in range(total):
        rep = n % 11 + 1
        _write_lot(tmp_path, f'S{n:02d}', rep, minute=n, dies=range(1, 23))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '1st', 'path': str(tmp_path)}]})

    assert len(_lots(result['raw_data'])) == repeats_left
    assert any(f'{repeats_left} Repeat' in m for m in basis_warnings(result))


# ── 축 누락 ───────────────────────────────────────────────────────────────

def test_missing_axis_is_not_reported_as_zero(tmp_path):
    from openpyxl import load_workbook

    for rep in range(2, 12):
        _write_lot(tmp_path / 'd', f'S{rep:02d}', rep, minute=rep, axes='Y', dies=range(1, 23))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '1st', 'path': str(tmp_path / 'd')}]})

    x_row = build_checklist([result], SPEC_LIMITS, SPEC_DEVIATION)[0]
    assert x_row['axis'] == 'X'
    assert x_row['range'] is None and x_row['stddev'] is None and x_row['avg'] is None
    assert x_row['result'] is None

    path = str(tmp_path / 'r.xlsx')
    export_excel_report(result['raw_data'], result['repeatability'], result['trend'], path,
                        recipe_results=[result], spec_limits=SPEC_LIMITS,
                        spec_deviation=SPEC_DEVIATION)
    wb = load_workbook(path)
    rows = list(wb['Checklist'].iter_rows(values_only=True))
    x = dict(zip(rows[0], rows[1]))
    assert x['Dev Range (µm)'] == '—'
    assert x['Result'] == '—'
    basis = list(wb['Analysis Basis'].iter_rows(values_only=True))
    assert 'X축 측정값이 없습니다' in dict(zip(basis[0], basis[1]))['기준 확인']


def test_cpk_is_not_invented_without_spec():
    metrics = compute_axis_metrics(_table(), 'Y', 'R', {}, SPEC_DEVIATION)
    assert metrics['cpk'] != metrics['cpk']     # NaN → 화면과 파일에서 N/A


def test_quality_report_carries_basis_sheet(tmp_path):
    from openpyxl import load_workbook

    for rep in range(1, 12):
        _write_lot(tmp_path / 'd', f'S{rep:02d}', rep, minute=rep, dies=range(1, 23))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '1st', 'path': str(tmp_path / 'd')}]})
    path = str(tmp_path / 'q.xlsx')
    export_quality_report([result], SPEC_LIMITS, [], path)

    basis = list(load_workbook(path)['Analysis Basis'].iter_rows(values_only=True))
    row = dict(zip(basis[0], basis[1]))
    assert row['제외 리핏 폴더'] == 'S01'
    assert row['기준 확인'] == 'OK'


# ── PDF ───────────────────────────────────────────────────────────────────

def test_pdf_notes_state_what_really_happened(tmp_path):
    from core.pdf_generator import _basis_notes

    for rep in range(2, 13):
        _write_lot(tmp_path / 'u', f'S{rep:02d}', rep, minute=rep, dies=range(1, 23))
    for rep in range(1, 12):
        _write_lot(tmp_path / 'e', f'S{rep:02d}', rep, minute=rep, dies=range(1, 23))

    def load(name):
        return load_recipe_data({'name': name, 'short_name': name,
                                 'rounds': [{'name': '1st', 'path': str(tmp_path / name)}]})

    notes = _basis_notes([load('u'), load('e')])

    assert any(n.startswith('e: first repeat folder S01 excluded') for n in notes)
    assert any(n.startswith('u: ') and 'Repeat No 1' in n for n in notes)
    assert not any(n.startswith('u: first repeat') for n in notes)


def test_pdf_runs_with_summary_table_and_missing_axis(tmp_path):
    from core.pdf_generator import generate_pdf_report
    from core.recipe_scanner import compare_recipes

    for rep in range(2, 12):
        _write_lot(tmp_path / 'd', f'S{rep:02d}', rep, minute=rep, axes='Y', dies=range(1, 23))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '1st', 'path': str(tmp_path / 'd')}]})
    path = tmp_path / 'r.pdf'

    generate_pdf_report(str(path), 'root', [result], compare_recipes([result]),
                        SPEC_LIMITS, SPEC_DEVIATION)

    assert path.read_bytes().startswith(b'%PDF')


def test_pdf_export_uses_screen_results_not_a_second_load(monkeypatch, tmp_path):
    """경로 입력란이 바뀌어도 PDF는 스캔한 결과로 나가야 한다."""
    import time
    from PySide6.QtWidgets import QFileDialog, QWidget

    import core.pdf_generator as pdf_generator
    from ui.controllers import export_controller
    from ui.controllers.export_controller import ExportMixin

    app = _APP
    seen = {}

    def fake_generate(path, root, results, comparison, *rest):
        seen.update(path=path, root=root, results=results)
        open(path, 'wb').close()

    monkeypatch.setattr(pdf_generator, 'generate_pdf_report', fake_generate)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(tmp_path / 'r.pdf'), '')))
    monkeypatch.setattr(export_controller.os, 'startfile', lambda p: None, raising=False)

    class Log:
        done = False

        def info(self, msg):
            pass

        def ok(self, msg):
            self.done = True

        def error(self, msg):
            self.done = True

    class Host(QWidget, ExportMixin):
        pass

    host = Host()
    _LIVE_WIDGETS.append(host)
    host.recipe_results = [{'short_name': 'R', 'raw_data': []}]
    host.settings = {}
    host.logger = Log()
    host.path_edit = type('E', (), {'text': lambda self: 'scanned-root'})()
    host._export_pdf()
    deadline = time.time() + 10
    while not host.logger.done and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert seen['results'] is host.recipe_results
    assert seen['root'] == 'scanned-root'
    assert (tmp_path / 'r.pdf').exists()
    assert not (tmp_path / 'r.pdf.part').exists()


# ── 화면 표시 ─────────────────────────────────────────────────────────────

def test_stat_card_shows_basis_and_missing_values():
    from ui.widgets.stat_card import StatCard

    card = StatCard('Y Offset')

    card.set_basis(21, 10)
    assert card.lbl_basis.text() == '21 Die × 10 Repeat'
    assert 'Custom' not in card.lbl_basis.text()

    card.set_basis(22, 10, custom=True)
    assert '22 Die' in card.lbl_basis.text() and 'Custom' in card.lbl_basis.text()
    assert 'Summary' in card.lbl_basis.toolTip()

    card.update_stats(None, None, None, float('nan'), None, spec_r=2.0, spec_s=0.4)
    assert card.lbl_rng.text() == '—'
    assert card.lbl_cpk.text() == 'N/A'


def test_filter_label_and_custom_flag_follow_checkbox():
    from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget
    from ui.controllers.step_controller import StepMixin

    class Host(StepMixin):
        def __init__(self):
            self._a, self._b = QWidget(), QWidget()
            self._die_cb_flow = QHBoxLayout(self._a)
            self._die_grid_layout = QGridLayout(self._b)
            self._die_checkboxes, self._die_grid_checkboxes = {}, {}
            self._die_filter_expanded = False
            self._die_filter_reset_pending = False
            self.filter_info_label = QLabel()
            self.step_buttons, self.step_pass_states = [], {}

        def _on_die_filter_changed(self, *_):
            pass

        def __getattr__(self, name):
            if name.startswith('_update_') or name in ('_render_die_position',
                                                       '_refresh_step_buttons'):
                return lambda *a, **k: None
            if name == 'statusBar':
                return lambda: type('S', (), {'showMessage': lambda self, *_: None})()
            raise AttributeError(name)

    host = Host()
    raw = _table()
    recipe = {'index': 1, 'short_name': 'R'}

    host._display_result({'raw_data': raw}, recipe)
    assert host._die_filter_is_custom is False
    assert 'Analysis basis' in host.filter_info_label.text()

    host._die_checkboxes[0].setChecked(True)
    host._display_result({'raw_data': raw}, recipe)
    assert host._die_filter_is_custom is True
    assert 'Custom' in host.filter_info_label.text()

    host._die_checkboxes[0].setChecked(False)
    host._die_checkboxes[5].setChecked(False)
    host._display_result({'raw_data': raw}, recipe)
    assert host._die_filter_is_custom is True


def test_lot_filter_lists_lots_from_both_axes_without_mutating_trend():
    from ui.controllers.lot_filter_controller import LotFilterMixin

    trend_x = [{'lot_name': 'A', 'lot_index': 1}]
    trend_y = [{'lot_name': 'A', 'lot_index': 1}, {'lot_name': 'B', 'lot_index': 2}]

    class Flow:
        def count(self):
            return 0

        def addWidget(self, w):
            pass

    from PySide6.QtWidgets import QLabel
    class Chart:
        def set_widget(self, w):
            pass

    class Host(LotFilterMixin):
        _lot_checkboxes = {}
        _lot_cb_flow = Flow()
        _lot_filter_updating = False
        _lot_filter_info = QLabel()
        _lot_trend_chart = Chart()

        def _on_lot_filter_changed(self, *_):
            pass

    import ui.controllers.lot_filter_controller as m
    m.viz_pg.create_dual_trend_widget, saved = (lambda *a, **k: None), m.viz_pg.create_dual_trend_widget
    try:
        host = Host()
        host._update_lot_trend(trend_x, trend_y)
    finally:
        m.viz_pg.create_dual_trend_widget = saved

    assert list(host._lot_checkboxes) == ['A', 'B']
    assert 'excluded' not in host._lot_filter_info.text()
    assert trend_y[1]['lot_index'] == 2     # 비교 차트와 공유하는 원본은 그대로


def test_comparison_heatmap_excludes_stabilization_die(monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    from charts import comparison

    rows = _table()
    for r in rows:
        r.update(site_x=int(r['site_id'][:4]), site_y=0)
    seen = []
    real = comparison._extract_site_data

    def spy(data, key, method):
        seen.extend(r['site_id'] for r in data)
        return real(data, key, method)

    monkeypatch.setattr(comparison, '_extract_site_data', spy)
    comparison.plot_recipe_comparison_heatmap([{'short_name': 'R', 'raw_data': rows}])

    assert seen and not [s for s in seen if s.startswith('0001')]


def test_comparison_trend_uses_analysis_trend(tmp_path):
    import matplotlib
    matplotlib.use('Agg')
    from charts import comparison

    for rep in range(2, 12):
        _write_lot(tmp_path / 'd', f'S{rep:02d}', rep, minute=rep, dies=range(1, 23))
    result = load_recipe_data({'name': 'R', 'short_name': 'R',
                               'rounds': [{'name': '1st', 'path': str(tmp_path / 'd')}]})

    fig = comparison.plot_recipe_comparison_trend([result, result])

    assert fig is not None
    assert all(t['count'] == 21 for t in result['trend_x'])
