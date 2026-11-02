"""거짓 판정 회귀 테스트: 읽지 못한 값이 측정값처럼 집계되어 PASS/FAIL이 나오지 않아야 한다."""
import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from core.checklist import basis_warnings, build_checklist
from core.csv_loader import batch_load
from core.die_analysis import evaluate_deviation_pass, extract_die_positions
from core.recipe_scanner import load_recipe_data

_APP = QApplication.instance() or QApplication([])
_LIVE_WIDGETS = []

SPEC_DEVIATION = {'R': {'spec_range': 2.0, 'spec_stddev': 0.4}}
SPEC_LIMITS = {'R': {'X': {'lsl': -5000.0, 'usl': 5000.0},
                     'Y': {'lsl': -5000.0, 'usl': 5000.0}}}


def _write(root, metric='HZ1_O (nm)', site=lambda d: f'{d:04d}_X000_Y000', edit=None):
    for rep in range(2, 12):
        lot = root / f'S{rep:02d}'
        lot.mkdir(parents=True)
        for axis in 'XY':
            rows = []
            for die in range(1, 23):
                cells = [site(die), '0', '0', '1', str(die * 1000.0), str(die * 500.0 + die % 3),
                         axis, 'COMPLETED', 'TRUE', str(4000.0 + die + rep), 'TRUE']
                if edit:
                    edit(cells, die, rep, axis)
                rows.append(','.join(cells))
            head = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
                    f'{metric},{metric.split(" ")[0]}_Valid')
            (lot / f'S_{axis}_UL.csv').write_text(
                '\n'.join([f'Repeat No,{rep}', '', head] + rows) + '\n', encoding='utf-8')


def _load(root):
    return load_recipe_data({'name': 'R', 'short_name': 'R',
                             'rounds': [{'name': '1st', 'path': str(root)}]})


def _rows(result):
    return {r['axis']: r for r in build_checklist([result], SPEC_LIMITS, SPEC_DEVIATION)}


def test_wrong_metric_column_is_no_data_not_a_pass(tmp_path):
    _write(tmp_path, metric='HZ2_O (nm)')
    result = _load(tmp_path)
    rows = _rows(result)

    for axis in 'XY':
        assert rows[axis]['result'] is None
        assert rows[axis]['range'] is None
    assert any('X축 측정값이 없습니다' in m for m in basis_warnings(result))
    assert result['failed_measurements']['count'] == len(result['raw_data'])
    assert any('값을 읽을 수 없는' in w for w in result['data_warnings'])


def test_blank_value_is_dropped_and_reported_not_counted_as_zero(tmp_path):
    def edit(cells, die, rep, axis):
        if (die, rep, axis) == (5, 3, 'X'):
            cells[9] = ''

    _write(tmp_path, edit=edit)
    result = _load(tmp_path)
    rows = _rows(result)

    assert rows['X']['result'] is True      # 0.0으로 들어가면 Range가 4 µm를 넘어 FAIL이었다
    assert rows['X']['range'] < 0.1
    assert result['failed_measurements']['count'] == 1
    assert any('S03 X' in w for w in result['data_warnings'])


def test_replacement_character_value_is_reported(tmp_path):
    def edit(cells, die, rep, axis):
        if (die, rep, axis) == (5, 3, 'Y'):
            cells[9] = '�'

    _write(tmp_path, edit=edit)
    result = _load(tmp_path)

    assert _rows(result)['Y']['result'] is True
    assert any('S03 Y' in w for w in result['data_warnings'])


def test_blank_coordinate_does_not_shift_die_position(tmp_path):
    def edit(cells, die, rep, axis):
        if (die, rep, axis) == (2, 3, 'X'):
            cells[4] = ''

    _write(tmp_path, edit=edit)
    rows = batch_load(str(tmp_path))

    assert extract_die_positions(rows)[1][0] == 2.0     # 0으로 들어가면 평균이 틀어진다
    assert math.isnan(next(r['x_um'] for r in rows
                           if r['site_id'].startswith('0002') and r['lot_name'] == 'S03'
                           and r['method'] == 'X'))


def test_unparseable_site_id_is_not_a_pass(tmp_path):
    _write(tmp_path, site=lambda d: f'P{d:03d}_X000_Y000')
    result = _load(tmp_path)
    rows = _rows(result)

    assert rows['X']['die_count'] == 0
    assert rows['X']['result'] is None
    assert rows['X']['range'] is None
    assert rows['X']['avg'] is None


def test_no_die_means_no_cpk(tmp_path):
    _write(tmp_path, site=lambda d: f'P{d:03d}_X000_Y000')
    cpk = _rows(_load(tmp_path))['X']['cpk']
    assert cpk != cpk


def test_row_with_broken_site_id_is_out_of_avg_as_well():
    good = [{'lot_name': f'L{rep}', 'site_id': f'{die:04d}_X000_Y000', 'method': 'X',
             'value': 100.0, 'valid': True} for rep in range(10) for die in range(1, 23)]
    broken = {'lot_name': 'L0', 'site_id': 'BAD', 'method': 'X', 'value': 9000.0, 'valid': True}
    rows = {r['axis']: r for r in build_checklist(
        [{'short_name': 'R', 'raw_data': good + [broken]}], SPEC_LIMITS, SPEC_DEVIATION)}

    assert rows['X']['avg'] == 100.0


def test_row_with_broken_site_id_does_not_shift_deviation_zero_point():
    from core.die_analysis import compute_deviation_matrix

    good = [{'lot_name': f'L{rep}', 'site_id': f'{die:04d}_X000_Y000', 'method': 'X',
             'value': 100.0, 'valid': True} for rep in range(10) for die in range(1, 23)]
    broken = {'lot_name': 'L0', 'site_id': 'BAD', 'method': 'X', 'value': 99999.0, 'valid': True}

    dev = compute_deviation_matrix(good + [broken], 'X')

    assert dev['average_nm'] == 100.0
    assert all(d['avg'] == 0.0 for d in dev['die_stats'])
    assert any('1행을 제외' in m for m in basis_warnings({'raw_data': good + [broken]}))


def test_excluded_rows_are_stated_in_basis_even_when_shape_is_standard(tmp_path):
    def edit(cells, die, rep, axis):
        if (die, rep, axis) == (5, 3, 'X'):
            cells[9] = ''

    _write(tmp_path, edit=edit)

    assert any('1행을 제외' in m for m in basis_warnings(_load(tmp_path)))


def test_die_without_coordinates_is_not_placed_from_the_table():
    from core.die_analysis import get_die_position

    assert get_die_position('Die6', {0: (1.0, 2.0)}) is None
    assert get_die_position('Die1', {0: (1.0, 2.0)}) == (1.0, 2.0)
    assert get_die_position('Die6') == (2, 2)


def test_evaluate_pass_requires_a_deviation_matrix():
    stats = {'count': 10}
    empty = {'die_labels': [], 'overall_range': 0.0, 'overall_stddev': 0.0}
    full = {'die_labels': ['Die2', 'Die3'], 'repeat_labels': ['Lot1', 'Lot2'],
            'overall_range': 0.0, 'overall_stddev': 0.0}

    assert evaluate_deviation_pass(stats, empty, 2.0, 0.4) is None
    assert evaluate_deviation_pass(stats, full, 2.0, 0.4) is True


def test_standard_data_still_passes_without_warnings(tmp_path):
    _write(tmp_path)
    result = _load(tmp_path)

    assert _rows(result)['X']['result'] is True
    assert basis_warnings(result) == []
    assert result['data_warnings'] == []


def test_only_site_0001_warning_is_truthful():
    rows = [{'lot_name': 'L', 'site_id': '0001_X000_Y000', 'method': 'X', 'value': 1.0}]
    assert not any('Site 0001이 없어' in m for m in basis_warnings({'raw_data': rows}))


def test_pdf_cover_names_the_scanned_folder_not_the_edited_path(monkeypatch, tmp_path):
    import core.pdf_generator as pdf_generator
    from ui.controllers import export_controller
    from ui.controllers.export_controller import ExportMixin

    seen = {}

    def fake(path, root, *rest):
        seen['root'] = root
        open(path, 'wb').close()

    monkeypatch.setattr(pdf_generator, 'generate_pdf_report', fake)
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
    host._scanned_folder = 'scanned-A'
    host.settings = {}
    host.logger = Log()
    host.path_edit = type('E', (), {'text': lambda self: 'edited-B'})()
    host._export_pdf()
    deadline = time.time() + 10
    while not host.logger.done and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)

    assert seen['root'] == 'scanned-A'


def test_many_notes_flow_onto_extra_pages_without_loss(tmp_path):
    from matplotlib.backends.backend_pdf import PdfPages
    from core.pdf_generator import NOTE_LINES_PER_PAGE, _add_notes_pages

    notes = [f'note {i}' for i in range(NOTE_LINES_PER_PAGE * 2 + 1)]
    with PdfPages(tmp_path / 'n.pdf') as pdf:
        _add_notes_pages(pdf, notes)
        assert pdf.get_pagecount() == 3


def test_no_notes_adds_no_page(tmp_path):
    from matplotlib.backends.backend_pdf import PdfPages
    from core.pdf_generator import _add_notes_pages

    with PdfPages(tmp_path / 'n.pdf') as pdf:
        _add_notes_pages(pdf, [])
        assert pdf.get_pagecount() == 0
