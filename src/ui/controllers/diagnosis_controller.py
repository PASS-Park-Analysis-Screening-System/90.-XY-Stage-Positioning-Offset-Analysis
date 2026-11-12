"""
diagnosis_controller.py — 'Diagnosis (트러블슈팅)' 탭: 원인 후보 자동 진단 (pass.xy.diagnosis/1)

선택한 Step·회차의 측정값과 Lot Log(스캐너 오프셋)로 core.xy_diagnosis 진단을 보여 준다.
- 요약 칩: X/Y 판정, 회전(잠정 규격), 레시피 오프셋 확인, drift, transient
- 스캐너 오프셋 표 (레시피 / 기준 보정 / 미세 보정 / 적용값, X·Y 라인)
- what-if 표 (X 라인 레시피 Y Offset 변경 예측, 현재·권장 행 강조)
- drift 차트 (Lot 평균 대 시각, 탭이 보일 때 그린다)
- 부품 판정과 근거, 다음 조치
- '진단 JSON 저장'

Log는 이 탭이 보일 때만 작업 스레드(파이썬 스레드 하나 + Lot 스레드)로 읽는다. 데이터 로드는 Log를
읽지 않는다. 읽은 Log는 그 회차 결과의 'xy_logs'에 담아 다시 읽지 않는다(읽기 오류가 섞였으면 담지
않고 다음에 탭을 열 때 다시 읽는다). 진단 계산은 화면 스레드에서 한다(Log를 읽은 뒤라 파일을 읽지 않고
10 Lot에 수십 ms). 작업 스레드는 Qt를 만지지 않고 시그널로 결과만 넘긴다.

다른 기능이 붙는 자리: self._xy_diagnosis — 지금 탭에 보이는 진단(XYDiagnosis.to_dict(), 스키마
pass.xy.diagnosis/1). 아직 계산하지 않았거나 데이터가 없으면 None. 회차를 바꾸면 None이 됐다가
탭에서 다시 계산된다. 3D 보고서 열기·스캐너 회전 시험 분석 버튼은 이 dict를 쓴다
(회전 시험 결과는 'rotation_test' 칸).

스캐너 회전 시험: 시험 대화상자가 set_rotation_test(결과 dict, 설계의 rotation_test 형식)를 부른다.
사본을 self._rotation_test_result에, 그때 보이던 진단의 장비(source.tool_id — Log의 Tool ID, 없으면 연 폴더)를
self._rotation_test_tool에 둔다. 회차가 아니라 장비의 것이라 회차를 바꿔도 지우지 않고, 같은 장비의 진단을
계산할 때마다 'rotation_test' 칸에 넣는다 (폐루프·Y 축 warn·fail은 xy_scanner_y의 직접 증거, PART_RULES).
다른 장비의 진단에는 넣지 않고 '다른 장비의 시험 결과 — 적용 안 함'을 보인다. 진단이 없을 때 받은 결과는
다음에 계산하는 진단의 장비에 묶는다.

이 탭이 보이면 그 회차의 Positioning Log 분석(positioning_controller의 읽기)도 시작한다. 끝나면
_refresh_diagnosis로 다시 계산해 vision·xy_stage와 스캐너 판정이 그 분석을 쓴다. AI 분석용 리포트도 같은
분석(담긴 것, 없으면 내보내기 스레드에서 계산)을 쓴다.
"""
import copy
import itertools
import json
import os
import re
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core.csv_loader import ScanSession
from core.positioning_log import run_key
from core.settings import rotation_spec
from core.xy_diagnosis import (NO_LOG_TEXT, OTHER_TOOL_TEXT, PART_RULES, ROTATION_RULE,
                               build_diagnosis, read_run_logs, rotation_test_text, store_logs,
                               tool_identity)
from ui.theme import BG, BG3, FG, FG2, GREEN, ORANGE, RED
from ui.widgets.chart_widget import ChartWidget
from ui.widgets.flow_layout import FlowLayout

DIAGNOSIS_TAB = 'Diagnosis (트러블슈팅)'
READING_TEXT = '스캐너 오프셋 Log 읽는 중…'
WAITING_TEXT = '이 회차의 Log는 이 탭을 열 때 읽습니다'
PENDING_TEXT = '이 탭을 열면 진단을 계산합니다'
NO_DATA_TEXT = '분석할 데이터가 없음'
NO_PATH_LOGS = {'status': 'no_logs', 'message': NO_LOG_TEXT, 'lots': []}
VERDICT_COLOR = {'ok': GREEN, 'warn': ORANGE, 'fail': RED, 'na': BG3}
VERDICT_TEXT = {'ok': 'OK', 'warn': '주의', 'fail': '이상', 'na': '—'}
OFFSET_HEADERS = ['라인', '레시피 X', '레시피 Y', '기준 보정 X', '기준 보정 Y', '미세 보정 X',
                  '미세 보정 Y', '적용 X', '적용 Y', '측정 수']
WHATIF_HEADERS = ['', '레시피 Y (µm)', '스캐너 Y (µm)', 'θx (µrad)', 'Range 평균', 'Range 최대',
                  'StdDev 평균', 'StdDev 최대', 'PASS Lot', '전체 Range', '전체 StdDev']
PART_HEADERS = ['부품', '판정', '근거']


class _DiagnosisBridge(QObject):
    """작업 스레드의 결과를 화면 스레드로 넘긴다 (queued 시그널)."""
    done = Signal(object, object, object)      # (회차 결과 dict, Log dict, (결과 세대, 읽기 번호))


def read_diagnosis_logs(result, bridge, meta, session=None):
    """[작업 스레드] 회차 결과 하나의 Lot Log를 읽고 bridge로 알린다 (Qt 위젯을 만지지 않는다).
    session을 중지하면(창 닫기) 남은 Lot은 읽지 않는다."""
    try:
        logs = read_run_logs(result, session)
    except Exception as e:
        logs = {'status': 'error', 'message': f'Log 읽기 실패 ({type(e).__name__}: {e})', 'lots': []}
    try:
        bridge.done.emit(result, logs, meta)
    except RuntimeError:
        pass        # 창이 이미 닫혔다


def _fmt(value, fmt='.3f'):
    return '—' if not isinstance(value, (int, float)) or isinstance(value, bool) else format(value, fmt)


def _item(text, tooltip=''):
    item = QTableWidgetItem(str(text))
    item.setTextAlignment(Qt.AlignCenter)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _table(headers, stretch_last=False):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_last:
        header.setStretchLastSection(True)
    return table


class DiagnosisMixin:
    _diag_result = None         # 지금 보이는 회차 결과
    _diag_key = None            # 그려 둔 진단의 키 (같으면 다시 계산하지 않는다)
    _diag_obj = None            # 그려 둔 XYDiagnosis
    _diag_logs = None           # 그 진단에 쓴 Log (회전 시험 결과가 오면 다시 계산)
    _rotation_test_result = None    # 스캐너 회전 시험 결과 사본 (장비의 것, 회차와 무관)
    _rotation_test_tool = None      # 그 결과를 받은 장비 (tool_id). None이면 다음 진단의 장비에 묶는다
    _diag_sessions = None           # 읽는 중인 Log 읽기의 ScanSession (창을 닫으면 중지)
    _xy_diagnosis = None        # 지금 탭에 보이는 진단 dict (pass.xy.diagnosis/1) — 모듈 설명 참고
    _diag_chart_pending = False
    _diag_reading = None        # 읽는 중인 (회차 키, 결과 세대, 읽기 번호) — 한 세대에 하나
    _diag_bridge = None
    _diag_tokens = itertools.count(1)

    def _build_diagnosis_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.diag_label = QLabel(WAITING_TEXT)
        self.diag_label.setWordWrap(True)
        self.diag_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
        self.diag_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.diag_label, 1)
        self.diag_save_btn = QPushButton('진단 JSON 저장')
        self.diag_save_btn.setToolTip('지금 보이는 진단을 pass.xy.diagnosis/1 JSON으로 저장')
        self.diag_save_btn.clicked.connect(self._save_diagnosis_json)
        self.diag_save_btn.setEnabled(False)
        top.addWidget(self.diag_save_btn, 0, Qt.AlignTop)
        # 3D 보고서·회전 시험·사례 라이브러리: 같은 기능이 Remark → Export 목록에도 있다.
        # 진단을 본 자리에서 바로 열 수 있게 여기에도 둔다 (지연 import — 탭을 안 열면 안 읽는다).
        self.diag_report_btn = QPushButton('3D 보고서 열기')
        self.diag_report_btn.setToolTip('지금 진단으로 3D 트러블슈팅 HTML을 만들어 기본 브라우저로 연다')
        self.diag_report_btn.clicked.connect(self._open_diagnosis_report)
        self.diag_report_btn.setEnabled(False)
        top.addWidget(self.diag_report_btn, 0, Qt.AlignTop)
        self.diag_rotation_btn = QPushButton('스캐너 회전 시험')
        self.diag_rotation_btn.setToolTip('폐루프 2지점 스윕으로 스캐너 회전 κ를 재고 진단에 넣는다 (별도 창)')
        self.diag_rotation_btn.clicked.connect(self._open_rotation_test_dialog)
        top.addWidget(self.diag_rotation_btn, 0, Qt.AlignTop)
        self.diag_cases_btn = QPushButton('사례 라이브러리')
        self.diag_cases_btn.setToolTip('끝난 조사 사례(HTML)를 기본 브라우저로 연다')
        self.diag_cases_btn.clicked.connect(self._open_case_library)
        top.addWidget(self.diag_cases_btn, 0, Qt.AlignTop)
        layout.addLayout(top)

        chips = QWidget()
        self._diag_chip_layout = FlowLayout(chips, spacing=6)
        self._diag_chips = {}
        for key in ('X', 'Y', 'rotation', 'offset', 'drift', 'transient', 'rotation_test'):
            chip = QLabel('—')
            chip.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._diag_chip_layout.addWidget(chip)
            self._diag_chips[key] = chip
        layout.addWidget(chips)

        splitter = QSplitter(Qt.Vertical)
        tables = QWidget()
        tables_layout = QVBoxLayout(tables)
        tables_layout.setContentsMargins(0, 0, 0, 0)
        tables_layout.addWidget(QLabel('스캐너 오프셋 (µm, Log.csv ImageJob — 적용 = 레시피 + 기준 보정 + 미세 보정)'))
        self.diag_offset_table = _table(OFFSET_HEADERS)
        self.diag_offset_table.setMaximumHeight(90)
        tables_layout.addWidget(self.diag_offset_table)
        self.diag_whatif_label = QLabel('What-if: X 라인 레시피 Y Offset 변경 (선형 가정)')
        self.diag_whatif_label.setWordWrap(True)
        tables_layout.addWidget(self.diag_whatif_label)
        self.diag_whatif_table = _table(WHATIF_HEADERS)
        self.diag_whatif_table.setToolTip(
            '예측 = 이 회차 Lot·Die 값에서 κ_eff × (현재 − 새 오프셋) × Die Y를 뺀 값 (선형 가정). '
            'Range·StdDev 평균·최대는 Lot마다, 전체는 앱 판정과 같은 전체 값. 초록 = 권장, 굵게 = 현재.')
        tables_layout.addWidget(self.diag_whatif_table)
        splitter.addWidget(tables)

        middle = QSplitter(Qt.Horizontal)
        self.chart_widgets['Diagnosis Drift'] = ChartWidget()
        self.chart_widgets['Diagnosis Drift'].setMinimumSize(320, 220)
        middle.addWidget(self.chart_widgets['Diagnosis Drift'])
        self.diag_parts_table = _table(PART_HEADERS, stretch_last=True)
        self.diag_parts_table.setWordWrap(True)
        self.diag_parts_table.setToolTip('\n'.join(PART_RULES))
        # 근거 열이 길어 표의 크기 힌트가 차트를 눌러 버리지 않게 한다 (긴 근거는 줄바꿈·툴팁)
        self.diag_parts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        middle.addWidget(self.diag_parts_table)
        middle.setStretchFactor(0, 2)
        middle.setStretchFactor(1, 3)
        middle.setSizes([440, 660])
        splitter.addWidget(middle)

        self.diag_actions_label = QLabel('')
        self.diag_actions_label.setWordWrap(True)
        self.diag_actions_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.diag_actions_label.setStyleSheet(f"color: {FG}; font-size: 10pt;")
        splitter.addWidget(self.diag_actions_label)
        splitter.setSizes([300, 320, 140])
        layout.addWidget(splitter, 1)
        return widget

    # ── 데이터 ─────────────────────────────────────────

    def _update_diagnosis(self, result):
        """Step·회차를 보일 때 부른다 (Die 필터를 바꿔도 불린다 — 진단은 분석 기준이라 같다)."""
        self._diag_result = result
        self._refresh_diagnosis()

    def _refresh_diagnosis(self):
        result = self._diag_result
        if not (result or {}).get('raw_data'):
            self._show_diag_message(NO_DATA_TEXT)
            return
        logs = result.get('xy_logs')
        if logs is None and result.get('round_path'):
            self._show_diag_message(READING_TEXT if self._diagnosis_tab_visible() else WAITING_TEXT)
            self._start_diagnosis_read()
            return
        logs = logs if logs is not None else NO_PATH_LOGS
        key = self._diagnosis_key(result, logs)
        if key == self._diag_key:
            return      # 같은 진단이다 (Step·Die 필터 갱신마다 다시 계산하지 않는다)
        if not self._diagnosis_tab_visible():
            self._show_diag_message(PENDING_TEXT)
            return
        self._compute_diagnosis(result, logs, key)

    def _diagnosis_key(self, result, logs):
        name = result.get('short_name') or result.get('recipe', '')
        spec = (self.settings.get('spec_deviation') or {}).get(name) or {}
        return (run_key(result), id(logs), id(result.get('positioning_log')),
                json.dumps(spec, sort_keys=True), json.dumps(rotation_spec(self.settings), sort_keys=True),
                getattr(self, '_data_generation', 0))

    def _tool_root(self):
        """Log에 Tool ID가 없을 때 장비를 가리는 폴더: 사용자가 연(스캔한) 폴더."""
        return getattr(self, '_scanned_folder', '') or None

    def _compute_diagnosis(self, result, logs, key):
        from core.version import APP_VERSION
        try:
            if self._rotation_test_result is not None and self._rotation_test_tool is None:
                # 진단이 없을 때 받은 시험 결과: 처음 진단하는 장비에 묶는다
                self._rotation_test_tool = tool_identity(result, logs, self._tool_root())[0]
            diag = build_diagnosis(result, logs, self.settings.get('spec_deviation', {}),
                                   rotation_spec(self.settings), app_version=APP_VERSION,
                                   rotation_test=copy.deepcopy(self._rotation_test_result),
                                   rotation_test_tool=self._rotation_test_tool,
                                   tool_root=self._tool_root())
        except Exception as e:
            self.logger.error(f"트러블슈팅 진단 오류: {type(e).__name__}: {e}")
            self._show_diag_message(f'진단 실패 ({type(e).__name__}: {e})')
            return
        self._diag_key = key
        self._diag_obj = diag
        self._diag_logs = logs
        self._xy_diagnosis = diag.to_dict()
        self._render_diagnosis(self._xy_diagnosis)

    def set_rotation_test(self, data):
        """스캐너 회전 시험 대화상자가 부른다 (data: rotation_test 형식 dict, None이면 지움).

        사본과 그때 보이던 진단의 장비(tool_id)를 담고, 보이는 진단이 있으면 그 결과로 다시 계산해
        (부품 판정 포함) JSON·화면에 넣는다. 회차를 바꿔도 지우지 않는다 — 시험 결과는 장비의 것이다.
        """
        self._rotation_test_result = copy.deepcopy(data) if isinstance(data, dict) else None
        self._rotation_test_tool = ((self._xy_diagnosis or {}).get('source') or {}).get('tool_id')
        if self._diag_obj is not None and self._diag_result is not None:
            self._compute_diagnosis(self._diag_result, self._diag_logs, self._diag_key)

    def _start_diagnosis_read(self):
        """보이는 회차의 Log를 작업 스레드로 읽는다. 탭이 보일 때만, 한 세대에 하나만."""
        result = self._diag_result
        if (not result or not result.get('raw_data') or not result.get('round_path')
                or result.get('xy_logs') is not None or not self._diagnosis_tab_visible()):
            return
        generation = getattr(self, '_data_generation', 0)
        reading = self._diag_reading
        if reading is not None and reading[1] == generation:
            return      # 같은 세대에서 읽는 중이다. 끝나면 보이는 회차를 다시 본다
        if self._diag_bridge is None:
            self._diag_bridge = _DiagnosisBridge(self)
            self._diag_bridge.done.connect(self._on_diagnosis_logs)
        token = next(self._diag_tokens)
        self._diag_reading = (run_key(result), generation, token)
        self.diag_label.setText(READING_TEXT)
        session = ScanSession()
        if self._diag_sessions is None:
            self._diag_sessions = {}
        self._diag_sessions[token] = session
        threading.Thread(target=read_diagnosis_logs, name='xydiag-read', daemon=True,
                         args=(result, self._diag_bridge, (generation, token), session)).start()

    def _cancel_diagnosis_reads(self):
        """창을 닫을 때: 읽는 중인 Log 읽기를 중지한다 (남은 Lot은 읽지 않는다)."""
        for session in list((self._diag_sessions or {}).values()):
            session.cancel()

    def _on_diagnosis_logs(self, result, logs, meta):
        generation, token = meta
        (self._diag_sessions or {}).pop(token, None)
        if self._diag_reading is not None and self._diag_reading[2] == token:
            self._diag_reading = None
        if generation != getattr(self, '_data_generation', 0):
            # 그사이 다시 스캔했다. 이 결과는 버리고 지금 보이는 회차를 읽는다
            self._start_diagnosis_read()
            return
        key = run_key(result)
        if store_logs(result, logs):
            # 회차 전환 화면(run_view)은 사본이다. 같은 회차의 다른 사본에도 담아 다시 읽지 않는다
            for recipe_result in getattr(self, 'recipe_results', []) or []:
                for item in [recipe_result] + list(recipe_result.get('run_results') or []):
                    if run_key(item) == key:
                        item['xy_logs'] = logs
            current = self._diag_result
            if current is not None and run_key(current) == key:
                current['xy_logs'] = logs
        current = self._diag_result
        if current is None or run_key(current) != key:
            self._start_diagnosis_read()        # 그사이 다른 회차로 바꿨다
            return
        if logs.get('status') == 'error':
            self.logger.warn(f"트러블슈팅 진단: {logs.get('message', '')} — 다음에 탭을 열 때 다시 읽습니다")
        # 읽기 오류가 섞였으면 담지 않았다. 이번 결과로 진단하되 다음에 탭을 열 때 다시 읽는다
        # (여기서 _refresh_diagnosis를 부르면 오류가 계속되는 동안 읽기를 되풀이한다)
        self._compute_diagnosis(current, logs, self._diagnosis_key(current, logs))

    # ── 화면 ─────────────────────────────────────────

    def _show_diag_message(self, text):
        self._diag_key = None
        self._diag_obj = None
        self._diag_logs = None
        self._xy_diagnosis = None
        self._diag_chart_pending = False
        self.diag_save_btn.setEnabled(False)
        self.diag_report_btn.setEnabled(False)
        self.diag_label.setText(text)
        self.diag_label.setToolTip('')
        for chip in self._diag_chips.values():
            self._set_chip(chip, '—', 'na', '')
        for table in (self.diag_offset_table, self.diag_whatif_table, self.diag_parts_table):
            table.setRowCount(0)
        self.diag_actions_label.setText('')
        self.chart_widgets['Diagnosis Drift'].clear()

    def _set_chip(self, chip, text, verdict, tooltip):
        color = VERDICT_COLOR.get(verdict, BG3)
        fg = BG if verdict in ('ok', 'warn', 'fail') else FG2
        chip.setText(text)
        chip.setToolTip(tooltip)
        chip.setStyleSheet(f"background: {color}; color: {fg}; border-radius: 4px; padding: 3px 8px;"
                           " font-size: 9pt; font-weight: bold;")

    def _render_diagnosis(self, d):
        source = d.get('source') or {}
        rotation = d.get('rotation') or {}
        spec = d.get('spec') or {}
        head = (f"{source.get('workflow', '')} 회차 {source.get('run', '')} — Lot {source.get('lots', 0)}개, "
                f"Die {source.get('dies', 0)}개"
                + (f", {source['start'].replace('T', ' ')} ~ {str(source.get('end') or '').replace('T', ' ')}"
                   if source.get('start') else '')
                + f" · Log: {source.get('log_message') or source.get('log_status') or '—'}")
        warnings = d.get('warnings') or []
        self.diag_label.setText(head + ('\n⚠ ' + '\n⚠ '.join(warnings[:4]) if warnings else '')
                                + (f' 외 {len(warnings) - 4}개' if len(warnings) > 4 else ''))
        self.diag_label.setToolTip('\n'.join(warnings))
        self.diag_save_btn.setEnabled(True)
        self.diag_report_btn.setEnabled(True)

        for axis in ('X', 'Y'):
            v = (d.get('verdict') or {}).get(axis) or {}
            verdict = {True: 'ok', False: 'fail'}.get(v.get('pass'), 'na')
            text = {True: 'PASS', False: 'FAIL'}.get(v.get('pass'), '—')
            self._set_chip(self._diag_chips[axis],
                           f"{axis} {text} (Range {_fmt(v.get('range'))} / StdDev {_fmt(v.get('stddev'))})",
                           verdict, f"Spec Range {spec.get('range')} / StdDev {spec.get('stddev')} µm")
        theta = rotation.get('theta')
        self._set_chip(self._diag_chips['rotation'],
                       f"회전 θx {_fmt(theta, '+.2f')} µrad, κ {_fmt(rotation.get('kappa_eff'), '.3f')} "
                       f"— {VERDICT_TEXT.get(rotation.get('status'), '—')} (잠정 규격)",
                       rotation.get('status', 'na'),
                       '\n'.join(rotation.get('reasons') or []) + '\n\n' + ROTATION_RULE)
        check = rotation.get('offset_check') or {}
        self._set_chip(self._diag_chips['offset'],
                       f"레시피 오프셋 X 라인 스캐너 Y {_fmt(rotation.get('scanner_y'), '+.1f')} µm "
                       f"— {VERDICT_TEXT.get(check.get('status'), '—')}",
                       check.get('status', 'na'), '\n'.join(check.get('reasons') or []))
        for key, title in (('drift', 'Drift'), ('transient', 'Transient')):
            part = d.get(key) or {}
            self._set_chip(self._diag_chips[key], f"{title} — {VERDICT_TEXT.get(part.get('status'), '—')}",
                           part.get('status', 'na'), '\n'.join(part.get('reasons') or []))
        rt = d.get('rotation_test')
        if not isinstance(rt, dict) and source.get('rotation_test_note'):
            self._set_chip(self._diag_chips['rotation_test'],
                           f"스캐너 회전 시험 — {OTHER_TOOL_TEXT} (잠정 규격)", 'na',
                           f"{rotation_test_text(self._rotation_test_result)}\n시험 장비: "
                           f"{self._rotation_test_tool or '—'}\n이 진단의 장비: {source.get('tool_id') or '—'}")
        elif isinstance(rt, dict):
            kappa = rt.get('kappa')
            text = (f"스캐너 회전 시험 κ {_fmt(kappa, '.3f') if isinstance(kappa, (int, float)) else '—'} "
                    f"µrad/µm — {VERDICT_TEXT.get(rt.get('status'), rt.get('status') or '—')} · "
                    f"{rt.get('stage') or '—'} · {rt.get('date') or '—'} (잠정 규격)")
            tip = '\n'.join([rotation_test_text(rt)] + list(rt.get('reasons') or []))
            self._set_chip(self._diag_chips['rotation_test'], text,
                           rt.get('status') if rt.get('status') in VERDICT_COLOR else 'na', tip)
        else:
            self._set_chip(self._diag_chips['rotation_test'], '스캐너 회전 시험 — 결과 없음 (잠정 규격)', 'na',
                           '스캐너 회전 시험 분석 결과가 없습니다 (폐루프 2지점 스윕)')

        self._fill_offsets(d.get('scanner_offsets') or {})
        self._fill_whatif(d.get('whatif') or {})
        self._fill_parts(d.get('parts') or [])
        actions = d.get('actions') or []
        self.diag_actions_label.setText('다음 조치\n' + '\n'.join(f'{i}. {a}' for i, a in enumerate(actions, 1)))
        self._diag_chart_pending = True
        self._flush_diagnosis_chart()

    def _fill_offsets(self, offsets):
        table = self.diag_offset_table
        table.setRowCount(0)
        rows = [(key, offsets[key]) for key in ('X_line', 'Y_line') if isinstance(offsets.get(key), dict)]
        table.setRowCount(len(rows))
        for i, (key, entry) in enumerate(rows):
            cells = [('X 라인' if key == 'X_line' else 'Y 라인', '')]
            for name in ('recipe', 'ref', 'fine', 'applied'):
                pair = entry.get(name) or [None, None]
                tip = ''
                if name in ('recipe', 'ref') and entry.get('recipe') is None and entry.get('recipe_plus_ref'):
                    tip = (f"레시피 + 기준 보정 = {_fmt(entry['recipe_plus_ref'][0], '+.3f')}, "
                           f"{_fmt(entry['recipe_plus_ref'][1], '+.3f')} µm (기준 스캔 결과가 Log에 없음)")
                cells += [(_fmt(pair[0], '+.3f'), tip), (_fmt(pair[1], '+.3f'), tip)]
            cells.append((str(entry.get('n') or 0), f"기준 보정 값이 있는 측정 {entry.get('n_ref', 0)}개"))
            for col, (text, tip) in enumerate(cells):
                table.setItem(i, col, _item(text, tip))

    def _fill_whatif(self, whatif):
        table = self.diag_whatif_table
        rows = whatif.get('rows') or []
        text = 'What-if: X 라인 레시피 Y Offset 변경 (선형 가정'
        if whatif.get('kappa_eff') is not None:
            text += f", κ_eff {whatif['kappa_eff']:.3f} µrad/µm"
        text += ')'
        if whatif.get('note'):
            text += f" — {whatif['note']}"
        self.diag_whatif_label.setText(text)
        table.setRowCount(len(rows))
        bold = QFont()
        bold.setBold(True)
        for i, row in enumerate(rows):
            mark = ' · '.join(m for m, on in (('현재', row.get('current')), ('권장', row.get('recommended'))) if on)
            passed = ('—' if row.get('pass_lots') is None else f"{row['pass_lots']}/{row.get('lots')}")
            cells = [mark, _fmt(row.get('recipe_y'), '+.1f'), _fmt(row.get('scanner_y'), '+.2f'),
                     _fmt(row.get('theta'), '+.2f'), _fmt(row.get('range_mean')), _fmt(row.get('range_max')),
                     _fmt(row.get('std_mean')), _fmt(row.get('std_max')), passed,
                     _fmt(row.get('range_all')), _fmt(row.get('std_all'))]
            for col, value in enumerate(cells):
                item = _item(value)
                if row.get('current'):
                    item.setFont(bold)
                if row.get('recommended'):
                    item.setBackground(QBrush(QColor(GREEN)))
                    item.setForeground(QBrush(QColor(BG)))
                table.setItem(i, col, item)

    def _fill_parts(self, parts):
        table = self.diag_parts_table
        table.setRowCount(len(parts))
        for i, part in enumerate(parts):
            verdict = part.get('verdict', 'na')
            table.setItem(i, 0, _item(part.get('name') or part.get('id'), part.get('id', '')))
            v = _item(VERDICT_TEXT.get(verdict, verdict), verdict)
            if verdict in ('ok', 'warn', 'fail'):
                v.setBackground(QBrush(QColor(VERDICT_COLOR[verdict])))
                v.setForeground(QBrush(QColor(BG)))
            table.setItem(i, 1, v)
            reason = QTableWidgetItem(part.get('reason', ''))
            reason.setToolTip(part.get('reason', ''))
            table.setItem(i, 2, reason)
        table.resizeRowsToContents()

    def _diagnosis_tab_visible(self):
        tabs = getattr(self, '_inner_tabs', {}).get('Advanced')
        return (tabs is not None and self.chart_category_tabs.currentWidget() is tabs
                and tabs.tabText(tabs.currentIndex()) == DIAGNOSIS_TAB)

    def _flush_diagnosis(self, *_args):
        """탭이 보일 때: 읽지 않은 회차의 Log와 Positioning Log 분석을 읽기 시작하고, 진단을 계산하고,
        미뤄 둔 차트를 그린다. Positioning Log 읽기는 positioning_controller의 것을 그대로 쓴다
        (같은 세대 확인·화면 스레드 넘김, 끝나면 _refresh_diagnosis)."""
        if not self._diagnosis_tab_visible():
            return
        start_positioning = getattr(self, '_start_positioning_read', None)
        if start_positioning is not None:
            start_positioning()
        self._refresh_diagnosis()
        self._flush_diagnosis_chart()

    def _flush_diagnosis_chart(self):
        if not self._diag_chart_pending or not self._diagnosis_tab_visible() or not self._xy_diagnosis:
            return
        self._diag_chart_pending = False
        try:
            from charts.diagnosis import plot_drift
            self.chart_widgets['Diagnosis Drift'].set_figure(plot_drift(self._xy_diagnosis))
        except Exception as e:
            self.logger.error(f"Diagnosis drift 차트 오류: {type(e).__name__}: {e}")

    def _open_diagnosis_report(self):
        """지금 진단으로 3D 트러블슈팅 HTML을 만들어 기본 브라우저로 연다."""
        from ui.dialogs.troubleshoot_launcher import open_troubleshoot_report
        open_troubleshoot_report(self)

    def _open_rotation_test_dialog(self):
        """스캐너 회전 시험 분석 창 (결과는 set_rotation_test로 진단에 들어온다)."""
        from ui.dialogs.scanner_rotation_dialog import open_scanner_rotation_dialog
        open_scanner_rotation_dialog(self)

    def _open_case_library(self):
        """끝난 조사 사례 모음을 기본 브라우저로 연다."""
        from ui.dialogs.troubleshoot_launcher import open_case_library
        open_case_library(self)

    def _save_diagnosis_json(self):
        diag = self._diag_obj
        if diag is None:
            QMessageBox.warning(self, "경고", "저장할 진단이 없습니다.")
            return
        source = diag.source or {}
        label = re.sub(r'[\\/:*?"<>|\s]+', '_', f"{source.get('workflow', '')}_{source.get('run', '')}").strip('_')
        default = f"XY_Diagnosis_{label or 'run'}_{datetime.now():%Y%m%d_%H%M}.json"
        path, _ = QFileDialog.getSaveFileName(self, "진단 JSON 저장", default, "JSON (*.json)")
        if not path:
            return
        temp = path + '.part'
        try:
            with open(temp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(diag.to_json())
            os.replace(temp, path)
            self.logger.ok(f"진단 JSON 저장: {path}")
        except Exception as e:
            self.logger.error(f"진단 JSON 저장 실패: {type(e).__name__}: {e}")
            QMessageBox.critical(self, "오류", f"진단 JSON 저장 실패\n{e}")
        finally:
            if os.path.exists(temp):
                os.remove(temp)
