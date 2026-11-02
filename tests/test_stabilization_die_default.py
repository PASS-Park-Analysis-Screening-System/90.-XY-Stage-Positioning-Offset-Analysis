"""안정화 Die 기본 제외 회귀 테스트.

분석 시트와 Summary 체크리스트는 첫 측정 Die(Site 0001)를 뺀 21 Die로 계산하는데,
Step 카드·Die/Deviation 표·Step PASS/FAIL은 22 Die 전체로 계산해 값이 어긋났다.
(In-Die Align Y: 시트·Summary 0.781/0.167 vs 카드 0.811/0.169)
"""
import os
import statistics

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel,
                               QWidget)

from core import (compute_deviation_matrix, extract_die_number,
                  filter_stabilization_die)
from ui.controllers.step_controller import StepMixin

SPIKE_NM = 5000.0  # 안정화 Die에만 넣는 큰 오차


def _rows(n_dies=22, n_repeats=10, spike=True):
    """Die × Repeat 측정 행. 첫 Die의 Y에만 큰 오차를 넣는다."""
    rows = []
    for rep in range(n_repeats):
        lot = f'Sample10{rep + 2:02d}'
        for axis in ('X', 'Y'):
            for die in range(n_dies):
                value = 10.0 * die + 7.0 * rep
                if spike and die == 0 and axis == 'Y':
                    value += SPIKE_NM
                rows.append({'lot_name': lot, 'site_id': f'{die + 1:04d}_X000_Y000',
                             'method': axis, 'value': value, 'valid': True})
    return rows


def _dies(rows):
    return {extract_die_number(r['site_id']) for r in rows}


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


class _StatusBar:
    def showMessage(self, *_):
        pass


class _Host(StepMixin):
    """_display_result가 쓰는 위젯만 갖춘 최소 창. 하위 갱신은 받은 데이터만 기록한다."""

    def __init__(self):
        self._root = QWidget()
        self._grid_root = QWidget()
        self._die_cb_flow = QHBoxLayout(self._root)
        self._die_grid_layout = QGridLayout(self._grid_root)
        self._die_checkboxes = {}
        self._die_grid_checkboxes = {}
        self._die_filter_expanded = False
        self._die_filter_reset_pending = False
        self.filter_info_label = QLabel()
        self.step_buttons = []
        self.step_pass_states = {}
        self.settings = {'spec_deviation': {
            'In-Die Align': {'spec_range': 2.0, 'spec_stddev': 0.4}}}
        self.analyzed = None

    def _on_die_filter_changed(self, *_):
        pass

    def _update_cards(self, data, recipe, warned=False):
        self.analyzed = data

    def _update_die_avg_tables(self):
        pass

    def _update_deviation_tables(self):
        pass

    def _update_raw_table(self):
        pass

    def _update_charts(self, data, result, recipe):
        pass

    def _render_die_position(self):
        pass

    def _refresh_step_buttons(self):
        pass

    def statusBar(self):
        return _StatusBar()

    def show(self, rows):
        self._display_result({'raw_data': rows},
                             {'index': 2, 'short_name': 'In-Die Align'})


def test_first_die_is_excluded_from_analysis_by_default(qapp):
    host = _Host()
    host.show(_rows())

    assert 0 not in _dies(host.analyzed)
    assert len(_dies(host.analyzed)) == 21
    assert not host._die_checkboxes[0].isChecked()
    assert not host._die_grid_checkboxes[0].isChecked()
    assert all(cb.isChecked() for d, cb in host._die_checkboxes.items() if d != 0)
    assert 'Die 1 excluded' in host.filter_info_label.text()


def test_default_analysis_matches_summary_checklist(qapp):
    """카드(_update_cards)가 받는 행과 Summary가 계산하는 행의 결과가 같아야 한다."""
    host = _Host()
    rows = _rows()
    host.show(rows)

    summary = compute_deviation_matrix(filter_stabilization_die(rows), 'Y')
    card = compute_deviation_matrix(host.analyzed, 'Y')

    assert card['overall_range'] == summary['overall_range']
    assert card['overall_stddev'] == summary['overall_stddev']
    assert card['average_nm'] == summary['average_nm']


def test_default_analysis_matches_excel_sheet_formula(qapp):
    """분석 시트 수식(21 Die × 10 Repeat, MAX-MIN, STDEV)과 직접 대조한다."""
    host = _Host()
    rows = _rows()
    host.show(rows)
    dev = compute_deviation_matrix(host.analyzed, 'Y')

    values = [r['value'] for r in rows
              if r['method'] == 'Y' and not r['site_id'].startswith('0001')]
    assert len(values) == 21 * 10
    mean = sum(values) / len(values)
    deviations = [(v - mean) / 1000.0 for v in values]

    assert len(dev['die_labels']) == 21
    assert len(dev['repeat_labels']) == 10
    assert dev['overall_range'] == pytest.approx(max(deviations) - min(deviations), abs=1e-3)
    assert dev['overall_stddev'] == pytest.approx(statistics.stdev(deviations), abs=1e-3)


def test_user_can_recheck_first_die_and_choice_survives_step_change(qapp):
    host = _Host()
    rows = _rows()
    host.show(rows)

    host._die_checkboxes[0].setChecked(True)
    host.show(rows)  # Step 전환과 같은 경로: Die 구성이 같으면 체크 상태를 유지한다

    assert host._die_checkboxes[0].isChecked()
    assert len(_dies(host.analyzed)) == 22


def test_new_scan_restores_default_exclusion(qapp):
    host = _Host()
    rows = _rows()
    host.show(rows)
    host._die_checkboxes[0].setChecked(True)

    host._die_filter_reset_pending = True  # _on_scan_complete가 켜는 값
    host.show(rows)

    assert not host._die_checkboxes[0].isChecked()
    assert len(_dies(host.analyzed)) == 21
    assert host._die_filter_reset_pending is False


def test_single_die_data_is_not_emptied(qapp):
    host = _Host()
    rows = _rows(n_dies=1, spike=False)
    host.show(rows)

    assert host._die_checkboxes[0].isChecked()
    assert host.analyzed == rows


def test_step_pass_state_ignores_stabilization_die(qapp):
    """첫 Die 오차(5 µm)가 Spec 2 µm를 넘어도 21 Die 기준이면 PASS여야 한다."""
    host = _Host()
    rows = _rows(spike=True)
    assert compute_deviation_matrix(rows, 'Y')['overall_range'] > 2.0  # 22 Die면 FAIL

    host.recipe_results = [{'raw_data': rows, 'short_name': 'In-Die Align'}]
    host._compute_all_step_pass_states()

    assert host.step_pass_states[0] is True
