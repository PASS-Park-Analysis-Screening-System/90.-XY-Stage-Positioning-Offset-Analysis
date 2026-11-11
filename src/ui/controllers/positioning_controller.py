"""
positioning_controller.py — 'Positioning Log' 탭: 비전 보정 로그 대조

선택한 Step·회차의 SamplePositioning/TipPositioning 로그를 최종 착지(HZ1_O)와 대조한다.
- 단계별 평면 계수·Range·HZ1_O 상관 표, 판정 (파생)
- 단계별 X 오차 대 Die Y 차트 (탭이 보일 때 그린다)
- Tip Positioning 표
로그는 로더 스레드가 화면에 보일 회차만 읽는다. 다른 회차로 바꾸면 이 탭이 보일 때 작업 스레드로
읽는다 (탭을 보지 않으면 네트워크 폴더를 더 읽지 않는다).
"""
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHeaderView, QLabel, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core.positioning_log import COEF_NAMES, COEF_UNIT, NO_LOG_TEXT, analyze_result
from ui.theme import FG2, ORANGE, RED
from ui.widgets.chart_widget import ChartWidget

POSITIONING_TAB = 'Positioning Log'
STAGE_HEADERS = ['단계', '축', '∂/∂x', 't', '∂/∂y', 't', 'Range (µm)', 'HZ1_O 상관 r', 'Die']
TIP_HEADERS = ['Lot', '위치', 'Tip X (µm)', 'Tip Y (µm)', '출처', 'Fine Score (%)', '스테이지 Y (mm)']


class _PositioningBridge(QObject):
    """작업 스레드의 결과를 화면 스레드로 넘긴다 (queued 시그널)."""
    done = Signal(object, object, int)      # (회차 결과 dict, 분석 dict, 결과 세대)


def read_positioning(result, bridge, generation):
    """[작업 스레드] 회차 결과 하나의 로그를 읽어 대조하고 bridge로 알린다.

    파이썬 스레드라 창이 먼저 닫혀도 안전하다 (닫힌 뒤의 알림은 버린다).
    """
    try:
        analysis = analyze_result(result)
    except Exception as e:
        analysis = {'status': 'error', 'message': f'로그 대조 실패 ({type(e).__name__}: {e})',
                    'lots': [], 'mean': None, 'verdict': None, 'tips': [], 'tip_summary': None}
    try:
        bridge.done.emit(result, analysis, generation)
    except RuntimeError:
        pass        # 창이 이미 닫혔다


def _num(value, fmt):
    return '—' if value is None else format(value, fmt)


def _item(text, tooltip=''):
    item = QTableWidgetItem(str(text))
    item.setTextAlignment(Qt.AlignCenter)
    if tooltip:
        item.setToolTip(tooltip)
    return item


class PositioningMixin:
    _poslog_result = None
    _poslog_chart_pending = False
    _poslog_reading = None      # 지금 읽는 회차 결과 (한 번에 하나)
    _poslog_bridge = None

    def _build_positioning_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        self.poslog_label = QLabel(NO_LOG_TEXT)
        self.poslog_label.setWordWrap(True)
        self.poslog_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
        self.poslog_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.poslog_label)

        splitter = QSplitter(Qt.Vertical)
        self.poslog_table = QTableWidget(0, len(STAGE_HEADERS))
        self.poslog_table.setHorizontalHeaderLabels(STAGE_HEADERS)
        self.poslog_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.poslog_table.verticalHeader().setVisible(False)
        self.poslog_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.poslog_table.setToolTip(
            "X축 ∂/∂x = Sx (ppm), ∂/∂y = θx (µrad) / Y축 ∂/∂x = θy (µrad), ∂/∂y = Sy (ppm). "
            "Lot이 여럿이면 Die별 Lot 평균으로 피팅한다.")
        splitter.addWidget(self.poslog_table)
        self.chart_widgets['Positioning Stages'] = ChartWidget()
        splitter.addWidget(self.chart_widgets['Positioning Stages'])
        self.poslog_tip_table = QTableWidget(0, len(TIP_HEADERS))
        self.poslog_tip_table.setHorizontalHeaderLabels(TIP_HEADERS)
        self.poslog_tip_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.poslog_tip_table.verticalHeader().setVisible(False)
        self.poslog_tip_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        splitter.addWidget(self.poslog_tip_table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        # 표의 크기 힌트(16행)가 차트를 눌러 버리지 않게 처음 나눔을 정한다 (사용자가 바꿀 수 있다)
        splitter.setSizes([320, 420, 130])
        self.chart_widgets['Positioning Stages'].setMinimumHeight(260)
        layout.addWidget(splitter, 1)
        self.poslog_tip_label = QLabel('')
        self.poslog_tip_label.setWordWrap(True)
        self.poslog_tip_label.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        layout.addWidget(self.poslog_tip_label)
        return widget

    # ── 데이터 ─────────────────────────────────────────

    def _update_positioning_log(self, result):
        """Step·회차를 보일 때 부른다. 분석이 없으면 작업 스레드로 읽는다."""
        self._poslog_result = result
        analysis = (result or {}).get('positioning_log')
        if not (result or {}).get('raw_data'):
            analysis = {'status': 'no_data', 'message': '분석할 데이터가 없음'}
        elif analysis is None and result.get('round_path'):
            self._render_positioning(None, reading=True)
            self._start_positioning_read()
            return
        self._render_positioning(analysis)

    def _start_positioning_read(self):
        """보이는 회차의 로그를 작업 스레드로 읽는다. 탭이 보일 때만, 한 번에 하나만."""
        result = self._poslog_result
        if (not result or result.get('positioning_log') is not None
                or self._poslog_reading is not None or not self._positioning_tab_visible()):
            return
        if self._poslog_bridge is None:
            self._poslog_bridge = _PositioningBridge(self)
            self._poslog_bridge.done.connect(self._on_positioning_done)
        self._poslog_reading = result
        threading.Thread(target=read_positioning, name='poslog-read', daemon=True,
                         args=(result, self._poslog_bridge,
                               getattr(self, '_data_generation', 0))).start()

    def _on_positioning_done(self, result, analysis, generation):
        self._poslog_reading = None
        # 그사이 다시 스캔했으면(결과 세대가 바뀜) 버린다
        if generation != getattr(self, '_data_generation', 0):
            return
        result['positioning_log'] = analysis
        # 회차 전환 화면(run_view)은 사본이다. 원래 회차 항목에도 담아 다시 읽지 않게 한다
        for recipe_result in getattr(self, 'recipe_results', []) or []:
            for run in recipe_result.get('run_results') or []:
                if (run.get('round') == result.get('round')
                        and run.get('round_path') == result.get('round_path')):
                    run['positioning_log'] = analysis
        if result is self._poslog_result:
            self._render_positioning(analysis)
        else:
            self._start_positioning_read()      # 그사이 다른 회차로 바꿨다

    # ── 화면 ─────────────────────────────────────────

    def _render_positioning(self, analysis, reading=False):
        table, tips = self.poslog_table, self.poslog_tip_table
        table.setRowCount(0)
        tips.setRowCount(0)
        self.poslog_tip_label.setText('')
        if reading:
            self.poslog_label.setText('비전 보정 로그 읽는 중…' if self._positioning_tab_visible()
                                      else '이 회차의 비전 보정 로그는 이 탭을 열 때 읽습니다')
            self.chart_widgets['Positioning Stages'].clear()
            return
        if not analysis or analysis.get('status') in ('no_logs', 'no_data', 'error', 'mismatch') \
                and not analysis.get('mean'):
            message = (analysis or {}).get('message') or NO_LOG_TEXT
            self.poslog_label.setText(message)
            self.poslog_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
            self.chart_widgets['Positioning Stages'].clear()
            self._fill_tip_table(analysis or {})
            return
        verdict = analysis.get('verdict') or {}
        lots = analysis.get('lots') or []
        mapping = [f"{lot['lot']}: {', '.join(lot['problems'][:1])}" for lot in lots if lot['problems']]
        rotations = [lot['mapping']['rotation_deg'] for lot in lots
                     if lot.get('mapping') and lot['mapping'].get('rotation_deg') is not None]
        head = (f"{analysis.get('message', '')}"
                + (f" · 정렬 회전 {sum(rotations) / len(rotations):+.3f}°" if rotations else ''))
        lines = [head] + [f"판정 (파생): {line}" for line in verdict.get('lines', [])]
        if mapping:
            lines.append('제외한 Lot: ' + '; '.join(mapping[:3])
                         + (f' 외 {len(mapping) - 3}개' if len(mapping) > 3 else ''))
        self.poslog_label.setText('\n'.join(lines))
        self.poslog_label.setToolTip(verdict.get('rule', ''))
        self.poslog_label.setStyleSheet(f"color: {ORANGE}; font-size: 10pt;")
        carriers = {(t['axis'], t['term']) for t in verdict.get('terms', [])}
        rows = (analysis.get('mean') or {}).get('stages') or []
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            names = COEF_NAMES[row['axis']]
            cells = [row['label'], row['axis']]
            for name in names:
                entry = row['coef'].get(name)
                cells += ([f"{name} {entry['value']:+.2f} {COEF_UNIT[name]}", _num(entry['t'], '+.1f')]
                          if entry else ['—', '—'])
            cells += [_num(row['range'], '.3f'), _num(row['r'], '+.2f'), str(row['k'])]
            for col, text in enumerate(cells):
                item = _item(text)
                if row['stage'] == 'final' and col in (2, 4) and \
                        (row['axis'], names[(col - 2) // 2]) in carriers:
                    item.setForeground(QColor(RED))
                table.setItem(i, col, item)
        self._fill_tip_table(analysis)
        self._poslog_chart_pending = True
        self._flush_positioning_chart()

    def _fill_tip_table(self, analysis):
        tips = analysis.get('tips') or []
        table = self.poslog_tip_table
        table.setRowCount(len(tips))
        for i, t in enumerate(tips):
            cells = [t.get('lot', ''), t.get('location', ''), _num(t.get('tip_x'), '+.2f'),
                     _num(t.get('tip_y'), '+.2f'), t.get('source', ''),
                     _num(t.get('fine_score'), '.2f'), _num(t.get('stage_y'), '+.1f')]
            for col, text in enumerate(cells):
                table.setItem(i, col, _item(text))
        summary = analysis.get('tip_summary') or {}
        self.poslog_tip_label.setText('\n'.join(summary.get('lines') or []))

    def _positioning_tab_visible(self):
        tabs = getattr(self, '_inner_tabs', {}).get('Advanced')
        return (tabs is not None and self.chart_category_tabs.currentWidget() is tabs
                and tabs.tabText(tabs.currentIndex()) == POSITIONING_TAB)

    def _flush_positioning_chart(self, *_args):
        """탭이 보일 때: 읽지 않은 회차의 로그를 읽기 시작하고, 미뤄 둔 차트를 그린다."""
        self._start_positioning_read()
        if not self._poslog_chart_pending or not self._positioning_tab_visible():
            return
        self._poslog_chart_pending = False
        analysis = (self._poslog_result or {}).get('positioning_log')
        try:
            from charts.positioning import plot_positioning_stages
            self.chart_widgets['Positioning Stages'].set_figure(plot_positioning_stages(analysis, 'X'))
        except Exception as e:
            self.logger.error(f"Positioning Log 차트 오류: {type(e).__name__}: {e}")
