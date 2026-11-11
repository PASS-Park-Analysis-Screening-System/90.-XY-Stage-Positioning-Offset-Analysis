"""
positioning_controller.py — 'Positioning Log' 탭: 비전 보정 로그 대조

선택한 Step·회차의 SamplePositioning/TipPositioning 로그를 최종 착지(HZ1_O)와 대조한다.
- 단계별 평면 계수·Range·HZ1_O 상관 표, 판정 (파생)
- 단계별 X 오차 대 Die Y 차트 (탭이 보일 때 그린다)
- Tip Positioning 표
로그는 이 탭이 보일 때만 작업 스레드(파이썬 스레드 하나)로 읽는다. 데이터 로드는 로그를 읽지 않는다
(네트워크 폴더에서 Lot마다 왕복이 늘지 않게). 읽은 분석은 그 회차 결과에 담아 다시 읽지 않는다
(읽기 오류가 섞였으면 담지 않고 다음에 탭을 열 때 다시 읽는다).
"""
import itertools
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHeaderView, QLabel, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core.positioning_log import (COEF_NAMES, COEF_UNIT, NO_LOG_TEXT, analyze_result, run_key,
                                  store_analysis)
from ui.theme import FG2, ORANGE, RED
from ui.widgets.chart_widget import ChartWidget

POSITIONING_TAB = 'Positioning Log'
STAGE_HEADERS = ['단계', '축', '∂/∂x', 't', '∂/∂y', 't', 'Range (µm)', 'r 전체',
                 'r (∂/∂x 항)', 'r (∂/∂y 항)', 'Die']
TIP_HEADERS = ['Lot', '위치', 'Tip X (µm)', 'Tip Y (µm)', '출처', 'Fine Score (%)', '스테이지 Y (mm)']
READING_TEXT = '비전 보정 로그 읽는 중…'
WAITING_TEXT = '이 회차의 비전 보정 로그는 이 탭을 열 때 읽습니다'
NO_DATA = {'status': 'no_data', 'message': '분석할 데이터가 없음'}
NO_PATH = {'status': 'no_logs', 'message': NO_LOG_TEXT}


class _PositioningBridge(QObject):
    """작업 스레드의 결과를 화면 스레드로 넘긴다 (queued 시그널)."""
    done = Signal(object, object, object)      # (회차 결과 dict, 분석 dict, (결과 세대, 읽기 번호))


def read_positioning(result, bridge, meta):
    """[작업 스레드] 회차 결과 하나의 로그를 읽어 대조하고 bridge로 알린다.

    파이썬 스레드라 창이 먼저 닫혀도 안전하다 (닫힌 뒤의 알림은 버린다).
    """
    try:
        analysis = analyze_result(result)
    except Exception as e:
        analysis = {'status': 'error', 'message': f'로그 대조 실패 ({type(e).__name__}: {e})',
                    'lots': [], 'mean': None, 'verdict': None, 'tips': [], 'tip_summary': None}
    try:
        bridge.done.emit(result, analysis, meta)
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
    _poslog_result = None       # 지금 보이는 회차 결과
    _poslog_analysis = None     # 지금 그려 둔 분석 (같은 분석이면 다시 그리지 않는다)
    _poslog_chart_pending = False
    _poslog_reading = None      # 읽는 중인 (회차 키, 결과 세대, 읽기 번호) — 한 세대에 하나
    _poslog_bridge = None
    _poslog_tokens = itertools.count(1)

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
            "Lot이 여럿이면 Die별 Lot 평균으로 피팅한다. r 전체 = 값끼리의 상관, r (항) = 그 계수만 "
            "남긴 부분 잔차끼리의 상관 (판정에 쓴다). — = 정할 수 없음 (좌표 퍼짐 부족·공선).")
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
        """Step·회차를 보일 때 부른다 (Die 필터를 바꿔도 불린다).

        분석이 바뀌지 않았으면 표·차트를 다시 그리지 않는다. 분석이 없으면 탭이 보일 때 읽는다.
        """
        self._poslog_result = result
        if not (result or {}).get('raw_data'):
            self._show_positioning(NO_DATA)
            return
        analysis = result.get('positioning_log')
        if analysis is not None or not result.get('round_path'):
            self._show_positioning(analysis if analysis is not None else NO_PATH)
            return
        self._show_pending()
        self._start_positioning_read()

    def _start_positioning_read(self):
        """보이는 회차의 로그를 작업 스레드로 읽는다. 탭이 보일 때만, 한 세대에 하나만."""
        result = self._poslog_result
        if (not result or not result.get('raw_data') or not result.get('round_path')
                or result.get('positioning_log') is not None
                or not self._positioning_tab_visible()):
            return
        generation = getattr(self, '_data_generation', 0)
        reading = self._poslog_reading
        if reading is not None and reading[1] == generation:
            return      # 같은 세대에서 읽는 중이다. 끝나면 보이는 회차를 다시 본다
        if self._poslog_bridge is None:
            self._poslog_bridge = _PositioningBridge(self)
            self._poslog_bridge.done.connect(self._on_positioning_done)
        token = next(self._poslog_tokens)
        self._poslog_reading = (run_key(result), generation, token)
        self.poslog_label.setText(READING_TEXT)
        threading.Thread(target=read_positioning, name='poslog-read', daemon=True,
                         args=(result, self._poslog_bridge, (generation, token))).start()

    def _on_positioning_done(self, result, analysis, meta):
        generation, token = meta
        if self._poslog_reading is not None and self._poslog_reading[2] == token:
            self._poslog_reading = None
        if generation != getattr(self, '_data_generation', 0):
            # 그사이 다시 스캔했다. 이 결과는 버리고 지금 보이는 회차를 읽는다
            self._start_positioning_read()
            return
        key = run_key(result)
        stored = store_analysis(result, analysis)
        if stored:
            # 회차 전환 화면(run_view)은 사본이다. 같은 회차의 다른 사본에도 담아 다시 읽지 않는다
            for recipe_result in getattr(self, 'recipe_results', []) or []:
                for item in [recipe_result] + list(recipe_result.get('run_results') or []):
                    if run_key(item) == key:
                        item['positioning_log'] = analysis
        current = self._poslog_result
        if current is not None and run_key(current) == key:
            if stored:
                current['positioning_log'] = analysis
            self._show_positioning(analysis)
        else:
            self._start_positioning_read()      # 그사이 다른 회차로 바꿨다

    # ── 화면 ─────────────────────────────────────────

    def _show_pending(self):
        self._poslog_analysis = None
        self._poslog_chart_pending = False
        self.poslog_table.setRowCount(0)
        self.poslog_tip_table.setRowCount(0)
        self.poslog_tip_label.setText('')
        self.poslog_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
        self.poslog_label.setText(READING_TEXT if self._positioning_tab_visible() else WAITING_TEXT)
        self.chart_widgets['Positioning Stages'].clear()

    def _show_positioning(self, analysis):
        if analysis is self._poslog_analysis:
            return      # 같은 분석이다 (Step·Die 필터 갱신마다 다시 그리지 않는다)
        self._poslog_analysis = analysis
        self._render_positioning(analysis)

    def _render_positioning(self, analysis):
        table, tips = self.poslog_table, self.poslog_tip_table
        table.setRowCount(0)
        tips.setRowCount(0)
        self.poslog_tip_label.setText('')
        if not analysis or not analysis.get('mean'):
            message = (analysis or {}).get('message') or NO_LOG_TEXT
            self.poslog_label.setText(message)
            self.poslog_label.setToolTip('')
            self.poslog_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
            self._poslog_chart_pending = False
            self.chart_widgets['Positioning Stages'].clear()
            self._fill_tip_table(analysis or {})
            return
        verdict = analysis.get('verdict') or {}
        lots = analysis.get('lots') or []
        excluded = [f"{lot['lot']}: {', '.join(lot['problems'][:1])}" for lot in lots
                    if lot['status'] != 'ok' and lot['problems']]
        rotations = [lot['mapping']['rotation_deg'] for lot in lots
                     if lot['status'] == 'ok' and lot.get('mapping')
                     and lot['mapping'].get('rotation_deg') is not None]
        head = (f"{analysis.get('message', '')}"
                + (f" · 정렬 회전 {sum(rotations) / len(rotations):+.3f}°" if rotations else ''))
        lines = [head] + [f"판정 (파생): {line}" for line in verdict.get('lines', [])]
        if excluded:
            lines.append('제외한 Lot: ' + '; '.join(excluded[:3])
                         + (f' 외 {len(excluded) - 3}개' if len(excluded) > 3 else ''))
        self.poslog_label.setText('\n'.join(lines))
        self.poslog_label.setToolTip(verdict.get('rule', ''))
        self.poslog_label.setStyleSheet(f"color: {ORANGE}; font-size: 10pt;")
        significant = {(t['axis'], t['term']) for t in verdict.get('terms', [])}
        rows = (analysis.get('mean') or {}).get('stages') or []
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            names = COEF_NAMES[row['axis']]
            cells = [row['label'], row['axis']]
            for name in names:
                entry = row['coef'].get(name)
                cells += ([f"{name} {entry['value']:+.2f} {COEF_UNIT[name]}", _num(entry['t'], '+.1f')]
                          if entry else ['—', '—'])
            r_terms = row.get('r_terms') or {}
            cells += [_num(row['range'], '.3f'), _num(row['r'], '+.2f'),
                      _num(r_terms.get(names[0]), '+.2f'), _num(r_terms.get(names[1]), '+.2f'),
                      str(row['k'])]
            for col, text in enumerate(cells):
                item = _item(text)
                if row['stage'] == 'final' and col in (2, 4) and \
                        (row['axis'], names[(col - 2) // 2]) in significant:
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
        try:
            from charts.positioning import plot_positioning_stages
            self.chart_widgets['Positioning Stages'].set_figure(
                plot_positioning_stages(self._poslog_analysis, 'X'))
        except Exception as e:
            self.logger.error(f"Positioning Log 차트 오류: {type(e).__name__}: {e}")
