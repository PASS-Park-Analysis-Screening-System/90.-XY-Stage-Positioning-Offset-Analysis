"""
scanner_rotation_dialog.py — 스캐너 회전 시험 분석 (폐루프 2지점 스윕 → 회전 κ µrad/µm)

출하 검사·스캐너 교체·선형화 후에 하는 스캐너 회전 시험의 XEPi 로그(지점 A·B)를 붙여 넣거나 불러와
지점별 이득·직각 기울기·선형 잔차와 κ를 계산하고 잠정 규격(설정 rotation_spec)으로 판정한다.
PQC Non-linearity 분석 시트(xlsx, 개루프)를 불러오면 검출기 지점별 이득, Y1↔Y2·X1↔X2 이득 차와
Det Stroke를 가설 ④(Y1/Y2 상대 교정) 참고값으로 보인다 (판정 없음).
계산은 core.scanner_rotation_test가 하고, 이 창은 입력·표시·복사·CSV 저장·진단 연결만 한다.

파일 읽기와 로그·시트 해석은 작업 스레드(QThread)에서 한다 (네트워크 경로). 해석한 측정값은 축마다 기억해 두고
픽셀 크기·지점 좌표·시험 단계만 바뀌면 바로 다시 계산한다. 로그 글·명령 목록·xlsx·모드가 바뀌면 진행 중인
해석은 멈추고(취소 깃발) 결과를 버린다. 앱을 끝낼 때도 멈추고 기다리며, 그래도 남으면 main()이
threads_alive()로 보고 os._exit로 끝낸다.

Y 스윕(본 시험)과 X 스윕(대조)을 차례로 분석하면 Y 결과에 X 결과를 '대조'로 붙인다. X 스윕만으로는 판정하지
않는다(참고). 지점 좌표는 폐루프·xlsx 모드와 축마다 따로 기억한다.
"진단에 추가"(폐루프 Y 스윕만)는 복사본을 메인 창 _rotation_test_result에 두고, 메인 창에 set_rotation_test가
있으면 다른 복사본으로 부른다 (진단 쪽 갱신은 워크스트림 A가 한다 — _xy_diagnosis는 직접 고치지 않는다).
"""

import copy
import os
import platform
import re

import matplotlib
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QCoreApplication, Qt, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import scanner_rotation_test as sr
from ui.theme import ACCENT, BG, BG2, BG3, FG, FG2, GREEN, ORANGE, PURPLE, RED

# 한글 글꼴 — charts 모듈과 같은 설정 (이 창만 먼저 열려도 글자가 깨지지 않게)
matplotlib.rcParams['font.family'] = {'Windows': 'Malgun Gothic', 'Darwin': 'AppleGothic'}.get(
    platform.system(), 'NanumGothic')
matplotlib.rcParams['axes.unicode_minus'] = False

TEXT_FILTER = 'XEPi 로그·CSV (*.txt *.log *.csv);;모든 파일 (*)'
XLSX_FILTER = 'Excel (*.xlsx *.xlsm);;모든 파일 (*)'
CLOSED_HEADERS = ['구분', '지점', '좌표 (mm)', '이득', '이득 σ', '직각 (mrad)', 'n', '잔차 p-p (µm)', '비선형 (%)',
                  '최저 Score', '빠진·뺀 단계 (µm)']
OPEN_HEADERS = ['시트', '검출기', '이득 (비전/명령)', '검출기/명령', '비전/검출기', '직각 (mrad)', '잔차 p-p (µm)',
                'Det Stroke 전 → 후', 'n']
CHIP_COLORS = {sr.STATUS_OK: GREEN, sr.STATUS_WARN: ORANGE, sr.STATUS_FAIL: RED, sr.STATUS_NA: BG3}
SERIES_COLORS = (ACCENT, ORANGE, GREEN, PURPLE)
MODE_CLOSED = 'closed'
MODE_XLSX = 'xlsx'

_last_dir = ''          # 파일 대화상자 시작 폴더 (실행 중에만 기억, 설정 파일에 쓰지 않는다)
_live_threads = set()   # 끝나기 전에 Python 객체가 사라지지 않게 잡아 둔다


def open_scanner_rotation_dialog(parent=None):
    """창을 하나만 띄운다. 닫았다 다시 열면 입력값·결과가 그대로 남아 있다."""
    dlg = getattr(parent, '_scanner_rotation_dialog', None) if parent is not None else None
    if dlg is None:
        dlg = ScannerRotationDialog(parent)
        if parent is not None:
            parent._scanner_rotation_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def threads_alive() -> bool:
    """이 창이 띄운 작업 스레드 중 아직 도는 것이 있나 (main()의 os._exit 판단용)."""
    return any(t.isRunning() for t in list(_live_threads))


def _wait_live_threads(timeout_ms=5000, cancel=True):
    """작업 스레드가 끝나기를 기다린다. cancel이면 먼저 모두 멈추라고 알린다 (앱 종료 aboutToQuit)."""
    if cancel:
        for t in list(_live_threads):
            t.cancelled = True
    for t in list(_live_threads):
        t.wait(timeout_ms)


class _Task(QThread):
    """함수 하나를 작업 스레드에서 돌린다. fn(progress) → 결과.

    progress(끝난 수, 전체)는 cancelled 깃발이 서 있으면 sr.AnalysisCancelled를 던져 멈춘다.
    """
    done = Signal(int, object)          # (작업 번호, 결과)
    failed = Signal(int, str)           # (작업 번호, 문구)
    progress = Signal(int, int, int)    # (작업 번호, 끝난 수, 전체)

    def __init__(self, job, fn):
        super().__init__()
        self.job = job
        self.fn = fn
        self.cancelled = False

    def _progress(self, done, total):
        if self.cancelled:
            raise sr.AnalysisCancelled()
        self.progress.emit(self.job, done, total)

    def run(self):
        try:
            result = self.fn(self._progress)
        except sr.AnalysisCancelled:
            return
        except Exception as e:      # 화면에 문구로 보이고, 창은 계속 쓸 수 있어야 한다
            self.failed.emit(self.job, str(e) or type(e).__name__)
        else:
            if not self.cancelled:
                self.done.emit(self.job, result)


def _floats(values):
    return np.asarray([np.nan if v is None else v for v in values or []], float)


class _SpotInput(QGroupBox):
    """지점 하나: 스테이지 좌표 + 로그·CSV 글 (붙여 넣기 또는 불러오기)."""

    def __init__(self, name, coord_mm, parent=None):
        super().__init__(f'지점 {name}', parent)
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.coord_label = QLabel('스테이지 X')
        row.addWidget(self.coord_label)
        self.coord = QDoubleSpinBox()
        self.coord.setRange(-1000.0, 1000.0)
        self.coord.setDecimals(3)
        self.coord.setValue(coord_mm)
        self.coord.setSuffix(' mm')
        self.coord.setKeyboardTracking(False)
        self.coord.setMinimumWidth(110)
        row.addWidget(self.coord)
        row.addStretch()
        self.btn_load = QPushButton('불러오기…')
        self.btn_clear = QPushButton('지우기')
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('XEPi 로그를 붙여 넣으세요 — 한 단계에 한 줄\n'
                                     '15:22:33 Match 1 : (1369.01, 1066.92),Angle = 0.02,Score = 0.97\n'
                                     '빠진 단계는 그 자리에 "누락" 한 줄, 다시 잰 단계는 이전 줄을 지우기\n'
                                     '(또는 줄마다 앞에 명령 값: "-30<탭>Match 1 : …")\n\n'
                                     '또는 CSV: command_um, x_px, y_px[, angle, score]')
        self.text.setStyleSheet(f'background: {BG2}; border: 1px solid {BG3};')
        self.text.setFont(QFont('Consolas', 9))
        lay.addWidget(self.text, 1)
        self.source = QLabel('')
        self.source.setStyleSheet(f'color: {FG2}; font-size: 8pt;')
        lay.addWidget(self.source)
        self.btn_clear.clicked.connect(self._clear)

    def _clear(self):
        self.text.clear()
        self.source.clear()


class ScannerRotationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('스캐너 회전 시험 분석 — 폐루프 2지점 스윕 (κ µrad/µm)')
        self.setMinimumSize(1150, 820)
        # 대화상자 자신에만 — 단추 등은 앱 공통 다크 테마(DARK_STYLE)를 그대로 쓴다
        self.setStyleSheet(f'QDialog {{ background: {BG}; color: {FG}; }}')
        self.result = None              # 표시 중인 rotation_test dict
        self._axis = 'Y'
        # 지점 좌표는 모드·축마다 따로 (폐루프 지점과 xlsx 검출기 지점은 다른 자리다)
        self._coords = {(mode, axis): list(sr.DEFAULT_SPOT_MM) for mode in (MODE_CLOSED, MODE_XLSX)
                        for axis in sr.AXES}
        self._parsed = {}               # 축 → {'a': Sweep, 'b': Sweep, 'date': 분석 시각}
        self._xlsx_path = None
        self._open_parsed = None        # parse_nonlinearity_xlsx 결과
        self._open_date = None
        self._error = ''
        self._job = 0                   # 해석 작업 번호 (늦게 온 이전 결과는 버린다)
        self._task = None               # 결과를 기다리는 해석 작업
        self._task_kind = None          # 그 작업이 폐루프 로그(MODE_CLOSED)인지 xlsx(MODE_XLSX)인지
        self._analysis_tasks = set()    # 아직 도는 해석 작업 (멈추라고 한 것 포함) — 끝나야 새로 시작
        self._load_jobs = {}            # 작업 번호 → (지점 입력, 경로)
        self._latest_load = {}          # 지점 이름 → 마지막으로 시작한 불러오기 작업 번호 (늦게 온 이전 것은 버린다)
        self._applying_load = False     # 불러온 글을 넣는 중 (사용자가 고친 것으로 보지 않는다)
        self._next_load_job = 1_000_000
        self._build_ui()
        self._sync_coord_inputs()
        self._on_mode_changed()

    # ── 화면 구성 ────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        title = QLabel('스캐너 회전 시험 분석')
        title.setStyleSheet(f'font-size: 12pt; font-weight: bold; color: {ACCENT};')
        root.addWidget(title)
        intro = QLabel('servo ON 상태에서 스캐너를 −70 → +70 µm(10 µm 간격) 구동하며 멀리 떨어진 두 지점(예: X −120, '
                       '+120 mm Die)에서 GRAB and RUN 한 XEPi 로그를 넣으면, 지점별 이득 g(비전 변위/지시 µm)와 '
                       'κ = (g_B − g_A) / L[mm] × 1000 µrad/µm를 계산해 잠정 규격으로 판정합니다. '
                       'Y 스윕이 본 시험이고, X 스윕(Y ±120 mm 지점)은 대조입니다.')
        intro.setWordWrap(True)
        intro.setStyleSheet(f'color: {FG2}; font-size: 9pt;')
        root.addWidget(intro)

        # 공통 입력
        top = QHBoxLayout()
        top.addWidget(QLabel('시험 단계'))
        self.stage_combo = QComboBox()
        self.stage_combo.addItems(sr.STAGES)
        top.addWidget(self.stage_combo)
        top.addSpacing(10)
        top.addWidget(QLabel('구동 축'))
        self.axis_combo = QComboBox()
        self.axis_combo.addItem('Y (본 시험)', 'Y')
        self.axis_combo.addItem('X (대조)', 'X')
        top.addWidget(self.axis_combo)
        top.addSpacing(10)
        top.addWidget(QLabel('명령 목록'))
        self.commands_edit = QLineEdit(sr.DEFAULT_COMMANDS_TEXT)
        self.commands_edit.setToolTip('시작:끝:간격 (끝 포함, 예: -70:70:10) 또는 쉼표 목록 (-70, -60, …, 70) — µm\n'
                                      '로그 줄 순서대로 짝짓습니다')
        self.commands_edit.setMinimumWidth(150)
        top.addWidget(self.commands_edit)
        top.addSpacing(10)
        top.addWidget(QLabel('픽셀 크기'))
        self.pixel_size = QDoubleSpinBox()
        self.pixel_size.setRange(0.0001, 100.0)
        self.pixel_size.setDecimals(5)
        self.pixel_size.setValue(round(sr.DEFAULT_PIXEL_SIZE_UM, 5))
        self.pixel_size.setSuffix(' µm/px')
        self.pixel_size.setKeyboardTracking(False)
        self.pixel_size.setToolTip(f'기본 {sr.DEFAULT_PIXEL_SIZE_UM:.5f} µm/px = FOV {sr.FOV_UM:.5f} µm / {sr.FOV_PX} px\n'
                                   'FOV 보정 값이 다르면 바꾸세요 (다시 해석하지 않고 바로 계산)')
        top.addWidget(self.pixel_size)
        top.addStretch()
        root.addLayout(top)

        mode = QHBoxLayout()
        self.rb_closed = QRadioButton('폐루프 시험 로그 (지점 A·B)')
        self.rb_xlsx = QRadioButton('개루프 PQC Non-linearity 분석 시트 (xlsx, 참고)')
        self.rb_closed.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.rb_closed)
        group.addButton(self.rb_xlsx)
        mode.addWidget(self.rb_closed)
        mode.addWidget(self.rb_xlsx)
        mode.addStretch()
        root.addLayout(mode)

        # 지점 A·B
        split = QSplitter(Qt.Orientation.Vertical)
        inputs = QWidget()
        in_lay = QVBoxLayout(inputs)
        in_lay.setContentsMargins(0, 0, 0, 0)
        spots = QHBoxLayout()
        self.spot_a = _SpotInput('A', sr.DEFAULT_SPOT_MM[0])
        self.spot_b = _SpotInput('B', sr.DEFAULT_SPOT_MM[1])
        spots.addWidget(self.spot_a)
        spots.addWidget(self.spot_b)
        in_lay.addLayout(spots, 1)

        xlsx = QHBoxLayout()
        self.btn_xlsx = QPushButton('또는 Non-linearity 분석 시트(xlsx) 불러오기…')
        self.btn_xlsx.setToolTip('WI-XY Scanner-01 "XY Non-linearity error Analysis sheet" (servo OFF)\n'
                                 '검출기 지점별 이득·Y1↔Y2 이득 차·Det Stroke — 가설 ④ 참고값 (판정 없음)')
        xlsx.addWidget(self.btn_xlsx)
        self.xlsx_label = QLabel('xlsx 없음')
        self.xlsx_label.setStyleSheet(f'color: {FG2};')
        xlsx.addWidget(self.xlsx_label, 1)
        self.chk_xlsx_coords = QCheckBox('지점 A·B 좌표를 1·2번 검출기 지점(Y1·Y2 / X1·X2)에 쓰기')
        self.chk_xlsx_coords.setToolTip('켜면 개루프 비틀림 κ도 계산합니다 (폐루프 회전이 아니므로 판정하지 않음)')
        xlsx.addWidget(self.chk_xlsx_coords)
        in_lay.addLayout(xlsx)

        run = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        run.addWidget(self.status, 1)
        self.busy = QProgressBar()
        self.busy.setFixedWidth(140)
        self.busy.setTextVisible(False)
        self.busy.setStyleSheet(f'QProgressBar {{ background: {BG3}; border: none; border-radius: 4px; height: 10px; }}'
                                f'QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}')
        self.busy.hide()
        run.addWidget(self.busy)
        self.btn_run = QPushButton('분석')
        self.btn_run.setProperty('accent', True)
        self.btn_run.setMinimumWidth(90)
        run.addWidget(self.btn_run)
        in_lay.addLayout(run)
        split.addWidget(inputs)

        # 결과
        results = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        chip_row = QHBoxLayout()
        self.chip = QLabel('—')
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chip.setMinimumWidth(170)
        chip_row.addWidget(self.chip)
        chip_text = QVBoxLayout()
        self.spec_label = QLabel()
        self.spec_label.setWordWrap(True)
        self.spec_label.setStyleSheet(f'color: {ORANGE}; font-size: 9pt; font-weight: bold;')
        chip_text.addWidget(self.spec_label)
        self.kappa_label = QLabel()
        self.kappa_label.setWordWrap(True)
        self.kappa_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.kappa_label.setStyleSheet(f'color: {FG}; font-size: 12pt; font-weight: bold;')
        chip_text.addWidget(self.kappa_label)
        chip_row.addLayout(chip_text, 1)
        lv.addLayout(chip_row)

        self.table = QTableWidget(0, len(CLOSED_HEADERS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(100)
        lv.addWidget(self.table, 1)
        self.reasons = QPlainTextEdit()
        self.reasons.setReadOnly(True)
        self.reasons.setStyleSheet(f'background: {BG2}; border: 1px solid {BG3}; color: {FG};')
        self.reasons.setMinimumHeight(120)
        lv.addWidget(self.reasons, 1)
        results.addWidget(left)

        self.figure = Figure(figsize=(5.5, 4.5))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumWidth(420)
        results.addWidget(self.canvas)
        results.setSizes([620, 480])
        split.addWidget(results)
        split.setSizes([330, 470])
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_copy = QPushButton('결과 복사')
        self.btn_csv = QPushButton('CSV로 저장…')
        self.btn_add = QPushButton('진단에 추가')
        self.btn_add.setToolTip('결과를 메인 창에 두고, 진단 결과가 있으면 그 rotation_test에 넣습니다')
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        for b in (self.btn_copy, self.btn_csv, self.btn_add, close):
            bottom.addWidget(b)
        root.addLayout(bottom)

        # 신호
        self.stage_combo.currentIndexChanged.connect(self._recompute)
        self.axis_combo.currentIndexChanged.connect(self._on_axis_changed)
        self.commands_edit.textChanged.connect(self._on_inputs_changed)
        self.pixel_size.valueChanged.connect(self._recompute)
        self.rb_closed.toggled.connect(self._on_mode_changed)
        self.chk_xlsx_coords.toggled.connect(self._recompute)
        self.btn_xlsx.clicked.connect(self._choose_xlsx)
        self.btn_run.clicked.connect(self._run)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_csv.clicked.connect(self._save_csv)
        self.btn_add.clicked.connect(self._add_to_diagnosis)
        for spot in (self.spot_a, self.spot_b):
            spot.text.textChanged.connect(lambda s=spot: self._on_spot_text_changed(s))
            spot.coord.valueChanged.connect(self._on_coord_changed)
            spot.btn_load.clicked.connect(lambda _=False, s=spot: self._choose_spot_file(s))

    # ── 입력 ─────────────────────────────────────────────

    def _mode(self):
        return MODE_XLSX if self.rb_xlsx.isChecked() else MODE_CLOSED

    def set_mode(self, mode):
        (self.rb_xlsx if mode == MODE_XLSX else self.rb_closed).setChecked(True)

    def set_axis(self, axis):
        self.axis_combo.setCurrentIndex(self.axis_combo.findData(axis))

    def _sync_coord_inputs(self):
        """지점 좌표 칸을 지금 모드·축의 값·이름으로 (Y 스윕 지점은 X로, X 스윕 지점은 Y로 떨어져 있다)."""
        coord_axis = 'X' if self._axis == 'Y' else 'Y'
        for spot, value in zip((self.spot_a, self.spot_b), self._coords[(self._mode(), self._axis)]):
            spot.coord_label.setText(f'스테이지 {coord_axis}')
            spot.coord.blockSignals(True)
            spot.coord.setValue(value)
            spot.coord.blockSignals(False)

    def _on_axis_changed(self, *_):
        axis = self.axis_combo.currentData()
        if axis == self._axis:
            return
        self._axis = axis
        self._sync_coord_inputs()
        self._recompute()

    def _on_coord_changed(self, *_):
        self._coords[(self._mode(), self._axis)] = [self.spot_a.coord.value(), self.spot_b.coord.value()]
        self._recompute()

    def _on_spot_text_changed(self, spot):
        if not self._applying_load:
            self._latest_load.pop(spot.name, None)      # 직접 고친 글을 늦게 끝난 불러오기가 덮지 않게
        self._on_inputs_changed()

    def _on_inputs_changed(self, *_):
        """로그 글·명령 목록이 바뀌면 지금 축의 해석 결과는 맞지 않으므로 (모드와 상관없이) 버린다."""
        self._cancel_running(MODE_CLOSED)
        self._parsed.pop(self._axis, None)
        self._recompute()

    def _on_mode_changed(self, *_):
        closed = self._mode() == MODE_CLOSED
        for w in (self.spot_a.text, self.spot_b.text, self.spot_a.btn_load, self.spot_b.btn_load,
                  self.spot_a.btn_clear, self.spot_b.btn_clear, self.commands_edit, self.pixel_size):
            w.setEnabled(closed)
        self.chk_xlsx_coords.setEnabled(not closed)
        self._cancel_running()
        self._sync_coord_inputs()
        self._recompute()

    def _choose_spot_file(self, spot):
        global _last_dir
        path, _ = QFileDialog.getOpenFileName(self, f'지점 {spot.name} 로그·CSV 고르기', _last_dir, TEXT_FILTER)
        if path:
            _last_dir = os.path.dirname(path)
            self.load_spot_file(spot, path)

    def load_spot_file(self, spot, path):
        """파일을 작업 스레드에서 읽어 지점 글 칸에 넣는다. 지점마다 마지막으로 고른 파일만 넣는다."""
        job = self._next_load_job
        self._next_load_job += 1
        self._load_jobs[job] = (spot, path)
        self._latest_load[spot.name] = job
        spot.source.setText(f'읽는 중… {path}')
        task = _Task(job, lambda _progress, p=path: sr.read_text_file(p))
        task.done.connect(self._on_load_done)
        task.failed.connect(self._on_load_failed)
        self._start(task)

    def _on_load_done(self, job, text):
        spot, path = self._load_jobs.pop(job, (None, None))
        if spot is None or self._latest_load.get(spot.name) != job:
            return                                  # 그 사이 다른 파일을 골랐거나 글을 직접 고쳤다
        del self._latest_load[spot.name]
        self._applying_load = True
        try:
            spot.text.setPlainText(text)            # → 입력이 바뀌었으니 이전 결과는 지운다
        finally:
            self._applying_load = False
        spot.source.setText(path)

    def _on_load_failed(self, job, msg):
        spot, _path = self._load_jobs.pop(job, (None, None))
        if spot is not None and self._latest_load.get(spot.name) == job:
            del self._latest_load[spot.name]
            spot.source.setText(f'읽기 실패: {msg}')

    def _choose_xlsx(self):
        global _last_dir
        path, _ = QFileDialog.getOpenFileName(self, 'Non-linearity 분석 시트 고르기', _last_dir, XLSX_FILTER)
        if path:
            _last_dir = os.path.dirname(path)
            self.set_xlsx_path(path)
            self._run()

    def set_xlsx_path(self, path):
        self._cancel_running(MODE_XLSX)
        self._xlsx_path = os.path.normpath(str(path))
        self._open_parsed = None
        self.xlsx_label.setText(self._xlsx_path)
        self.xlsx_label.setToolTip(self._xlsx_path)
        self.set_mode(MODE_XLSX)
        self._recompute()

    def _settings(self, key):
        settings = getattr(self.parent(), 'settings', None)
        return settings.get(key) if isinstance(settings, dict) else None

    def _spec(self):
        return sr.resolve_spec(self._settings('rotation_spec'))

    def _gate(self):
        return sr.resolve_gate(self._settings('rotation_test_gate'))

    # ── 해석 (작업 스레드) ───────────────────────────────

    def _cancel_running(self, kind=None):
        """진행 중인 해석(kind를 주면 그 종류만)은 다음 단계에서 멈추게 하고, 그 사이 도착한 결과도 버린다."""
        if self._task is not None and (kind is None or self._task_kind == kind):
            self._task.cancelled = True
            self._job += 1
            self._set_busy(False)

    def _run(self):
        if self._analysis_tasks:
            self._set_status('이전 분석을 멈추는 중… 잠시 뒤 다시 누르세요', FG2)
            return
        if self._mode() == MODE_XLSX:
            if not self._xlsx_path:
                self._set_status('Non-linearity 분석 시트(xlsx)를 먼저 불러오세요', RED)
                return
            path = self._xlsx_path

            def fn(progress):
                return 'open', None, sr.parse_nonlinearity_xlsx(path, progress)
        else:
            try:
                commands = sr.parse_commands(self.commands_edit.text())
            except sr.RotationTestError as e:
                self._set_status(str(e), RED)
                return
            texts = (self.spot_a.text.toPlainText(), self.spot_b.text.toPlainText())
            axis, ps = self._axis, self.pixel_size.value()
            coords, gate = list(self._coords[(MODE_CLOSED, axis)]), self._gate()

            def fn(progress):
                progress(0, 2)
                a = sr.parse_spot_text(texts[0], commands, 'A')
                progress(1, 2)
                b = sr.parse_spot_text(texts[1], commands, 'B')
                sr.analyze_rotation_test(a, b, axis, ps, coords, gate=gate)     # 계산할 수 있는지 미리 본다
                progress(2, 2)
                return 'closed', axis, {'a': a, 'b': b}
        self._job += 1
        task = _Task(self._job, fn)
        self._task_kind = self._mode()
        task.done.connect(self._on_done)
        task.failed.connect(self._on_failed)
        task.progress.connect(self._on_progress)
        self._task = task
        self._analysis_tasks.add(task)
        self._set_busy(True, '분석 중…')
        self._start(task)

    def _start(self, task):
        app = QCoreApplication.instance()
        if app is not None and not getattr(app, '_scanner_rotation_quit_hooked', False):
            app.aboutToQuit.connect(_wait_live_threads)
            app._scanner_rotation_quit_hooked = True
        _live_threads.add(task)
        task.finished.connect(self._on_task_finished)     # 화면 스레드에서 받는다 (queued)
        task.start()

    def _on_task_finished(self):
        """끝난 작업을 놓는다. 보낸 스레드를 wait()로 완전히 끝낸 뒤 목록에서 뺀다."""
        task = self.sender()
        finished = [task] if isinstance(task, _Task) else [t for t in _live_threads if t.isFinished()]
        for t in finished:
            t.wait()
            _live_threads.discard(t)
            self._analysis_tasks.discard(t)
        if self._task is None and self.status.text().startswith(('이전 분석을 멈추는 중', '분석 중')):
            self._show_results()                    # 멈춘 작업의 '분석 중…'이 남지 않게
        self._update_buttons()

    def run_and_wait(self, timeout_ms=60000):
        """분석을 시작하고 끝날 때까지 기다린다 (테스트·화면 캡처용)."""
        self._run()
        task = self._task
        if task is not None:
            task.wait(timeout_ms)
            for _ in range(3):
                QCoreApplication.processEvents()

    def _set_busy(self, on, text=''):
        if on:
            self.busy.setRange(0, 0)
            self.busy.show()
            self._set_status(text, FG2)
        else:
            self.busy.hide()
            self._task = None
        self._update_buttons()

    def _on_progress(self, job, done, total):
        if job == self._job:
            self.busy.setRange(0, total)
            self.busy.setValue(done)

    def _on_failed(self, job, msg):
        if job != self._job:
            return
        self._set_busy(False)
        self._error = f'분석 실패: {msg}'
        self._show_results()

    def _on_done(self, job, payload):
        if job != self._job:
            return
        kind, axis, data = payload
        date = sr.date_text(None)
        if kind == 'open':
            self._open_parsed, self._open_date = data, date
        else:
            self._parsed[axis] = dict(data, date=date)
        self._set_busy(False)
        self._recompute()

    # ── 계산·표시 ────────────────────────────────────────

    def _compute(self):
        stage, spec, gate = self.stage_combo.currentText(), self._spec(), self._gate()
        if self._mode() == MODE_XLSX:
            if self._open_parsed is None:
                return None
            positions = tuple(self._coords[(MODE_XLSX, self._axis)]) if self.chk_xlsx_coords.isChecked() else None
            return sr.analyze_open_loop(self._open_parsed, axis=self._axis, positions_mm=positions,
                                        stage=stage, spec=spec, date=self._open_date, gate=gate)
        results = {axis: sr.analyze_rotation_test(p['a'], p['b'], axis, self.pixel_size.value(),
                                                  self._coords[(MODE_CLOSED, axis)], stage, spec, p['date'], gate)
                   for axis, p in self._parsed.items()}
        if 'Y' in results:
            return sr.combine_control(results['Y'], results.get('X'))
        return results.get('X')

    def _recompute(self, *_):
        """해석해 둔 측정값으로 다시 계산한다 (픽셀 크기·좌표·단계·축·모드가 바뀌었을 때)."""
        self._error = ''
        try:
            self.result = self._compute()
        except Exception as e:      # 계산 실패가 창을 막지 않게
            self.result = None
            self._error = str(e) if isinstance(e, sr.RotationTestError) else f'계산 실패: {e}'
        self._show_results()

    def _set_status(self, text, color):
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {color};')

    def _show_results(self):
        res = self.result
        if self._task is None:
            if self._error:
                self._set_status(self._error, RED)
            elif res is None:
                hint = ('Non-linearity 분석 시트(xlsx)를 불러오세요' if self._mode() == MODE_XLSX
                        else f'{self._axis} 스윕 지점 A·B 로그를 넣고 분석을 누르세요')
                self._set_status(hint, FG2)
            else:
                text = (f'분석 완료 — {res["axis"]} 스윕' if res['loop'] == sr.LOOP_CLOSED
                        else '분석 완료 — 개루프 PQC Non-linearity 시트 (참고값)')
                if res['loop'] == sr.LOOP_CLOSED and res['axis'] != 'Y':
                    text += ' (대조, 참고 — 판정하려면 Y 스윕도 분석하세요)'
                if res.get('control'):
                    text += f' + 대조 {res["control"]["axis"]} 스윕'
                if res['loop'] == sr.LOOP_CLOSED and self._axis not in self._parsed:
                    text += f' · {self._axis} 스윕은 아직 분석하지 않았습니다'
                self._set_status(text, GREEN)
        self._show_chip(res)
        self._fill_table(res)
        self.reasons.setPlainText('\n'.join(f'• {r}' for r in (res or {}).get('reasons') or []))
        self._draw_chart(res)
        self._update_buttons()

    def _show_chip(self, res):
        if res is None:
            status, text = sr.STATUS_NA, '—'
            self.spec_label.setText(sr.spec_text(self._spec()) + ' — 장비 데이터(평균 + 3σ)로 확정 예정')
            self.kappa_label.clear()
        elif res['loop'] == sr.LOOP_OPEN:
            status, text = sr.STATUS_NA, '참고\n개루프'
            self.spec_label.setText('개루프(servo OFF) 참고값 — 잠정 규격으로 판정하지 않습니다 (가설 ④ Y1/Y2 상대 교정)')
            self.kappa_label.setText(sr.kappa_text(res))
        elif res.get('gate'):
            status, text = sr.STATUS_NA, '판정\n보류'
            self.spec_label.setText('입력이 그럴듯하지 않아 판정하지 않습니다 — 이유 맨 앞 [판정 보류] 항목을 확인하세요')
            self.kappa_label.setText(sr.kappa_text(res))
        elif res['axis'] != 'Y':
            status, text = sr.STATUS_NA, '참고\nX 대조'
            self.spec_label.setText('X 스윕은 대조(참고) — 판정하지 않습니다. 본 시험은 Y 스윕입니다')
            self.kappa_label.setText(sr.kappa_text(res))
        else:
            status = res['status']
            text = sr.verdict_label(res) + ('\n불합격' if status == sr.STATUS_FAIL else '')
            self.spec_label.setText(sr.spec_text(res.get('spec')) + ' — 장비 데이터(평균 + 3σ)로 확정 예정')
            self.kappa_label.setText(sr.kappa_text(res))
        color = CHIP_COLORS[status]
        fg = FG if status == sr.STATUS_NA else BG
        self.chip.setText(text)
        self.chip.setStyleSheet(f'background: {color}; color: {fg}; font-size: 18pt; font-weight: bold; '
                                f'border-radius: 10px; padding: 10px 18px;')

    def _fill_table(self, res):
        rows = []
        if res is not None and res['loop'] == sr.LOOP_OPEN:
            headers = OPEN_HEADERS
            for ch in res['open_loop']['channels']:
                rows.append([ch['sheet'], ch['channel'], sr.fmt_num(ch['gain'], '.4f'), sr.fmt_num(ch['detector_gain'], '.4f'),
                             sr.fmt_num(ch['vision_per_detector'], '.4f'), sr.fmt_num(ch['perp_mrad'], '+.2f'),
                             sr.fmt_num(ch['residual_pp_um'], '.3f'),
                             f'{sr.fmt_num(ch["stroke_before"], ".2f")} → {sr.fmt_num(ch["stroke_after"], ".2f")}',
                             str(ch['n'])])
        else:
            headers = CLOSED_HEADERS
            groups = [] if res is None else [(f'{res["axis"]} 스윕', res)]
            if res is not None and res.get('control'):
                groups.append((f'대조 {res["control"]["axis"]} 스윕', res['control']))
            for label, g in groups:
                for loc in g.get('locations') or []:
                    ax, c = sr.location_coord(loc)
                    rows.append([label, loc['name'], f'{ax} {c:+g}' if c is not None else '—',
                                 sr.fmt_num(loc.get('gain'), '.5f'), sr.fmt_num(loc.get('gain_se'), '.5f'),
                                 sr.fmt_num(loc.get('perp_mrad'), '+.2f'), f'{loc.get("n")}/{loc.get("n_total")}',
                                 sr.fmt_num(loc.get('residual_pp_um'), '.3f'), sr.fmt_num(loc.get('nonlinearity_pct'), '.2f'),
                                 sr.fmt_num(loc.get('min_score'), '.2f'), sr.excluded_text(loc)])
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

    def _chart_series(self, res):
        """[(이름, 명령, 축 방향 변위, 잔차)] — 지금 고른 축의 것."""
        if res is None:
            return '', []
        if res['loop'] == sr.LOOP_OPEN:
            chans = [ch for ch in res['open_loop']['channels'] if ch['axis'] == self._axis]
            return (f'개루프 {self._axis} 검출기 지점 (PQC Non-linearity)',
                    [(f'{ch["sheet"]} 이득 {ch["gain"]:.3f}', ch['points']) for ch in chans])
        group = res
        ctrl = res.get('control')
        if res['axis'] != self._axis and ctrl and ctrl.get('axis') == self._axis:
            group = ctrl
        series = []
        for loc in group.get('locations') or []:
            ax, c = sr.location_coord(loc)
            where = f' ({ax} {c:+g} mm)' if c is not None else ''
            series.append((f'{loc["name"]}{where} 이득 {sr.fmt_num(loc.get("gain"), ".4f")}', loc['points']))
        return f'{group["axis"]} 스윕 · {sr.LOOP_LABELS[sr.LOOP_CLOSED]}', series

    def _draw_chart(self, res):
        fig = self.figure
        fig.clear()
        fig.set_facecolor(BG)
        ax1 = fig.add_subplot(2, 1, 1)
        ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
        for ax in (ax1, ax2):
            ax.set_facecolor(BG2)
            ax.tick_params(colors=FG2, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(BG3)
            ax.grid(True, color=BG3, linewidth=0.6)
        title, series = self._chart_series(res)
        if not series:
            ax1.text(0.5, 0.5, '결과 없음', color=FG2, ha='center', va='center', transform=ax1.transAxes)
        for i, (label, pts) in enumerate(series):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            cmd, along, resid = _floats(pts.get('command_um')), _floats(pts.get('along_um')), _floats(pts.get('residual_um'))
            ax1.plot(cmd, along, 'o', color=color, ms=4, label=label)
            ok = np.isfinite(cmd) & np.isfinite(along) & np.isfinite(resid)
            order = np.argsort(cmd[ok])
            ax1.plot(cmd[ok][order], (along - resid)[ok][order], '-', color=color, lw=1)
            ax2.plot(cmd[ok][order], resid[ok][order], 'o-', color=color, ms=3, lw=1)
        if series:
            ax1.legend(fontsize=8, facecolor=BG2, edgecolor=BG3, labelcolor=FG)
        ax2.axhline(0.0, color=FG2, lw=0.8)
        ax1.set_title(title, color=FG, fontsize=9)
        ax1.set_ylabel('비전 변위 (µm)', color=FG2, fontsize=8)
        ax2.set_ylabel('직선 맞춤 잔차 (µm)', color=FG2, fontsize=8)
        ax2.set_xlabel('지시 (µm)', color=FG2, fontsize=8)
        fig.tight_layout()
        self.canvas.draw_idle()

    def _update_buttons(self):
        has = self.result is not None
        for b in (self.btn_copy, self.btn_csv):
            b.setEnabled(has)
        # 진단에는 폐루프 Y 스윕 결과만 판정으로 넣는다 (X 대조·개루프는 참고값)
        addable = sr.can_add_to_diagnosis(self.result)
        self.btn_add.setEnabled(addable)
        self.btn_add.setToolTip('결과를 진단(rotation_test)에 넣습니다' if addable or not has else
                                'X 스윕 대조·개루프 시트는 참고값이라 진단에 넣지 않습니다 — 폐루프 Y 스윕을 분석하세요')
        # 멈추라고 한 이전 해석이 끝나기 전에는 새로 시작하지 않는다 (작업이 쌓이지 않게)
        self.btn_run.setEnabled(self._task is None and not self._analysis_tasks)

    # ── 복사·저장·진단 ───────────────────────────────────

    def _copy(self):
        if self.result is None:
            return
        QGuiApplication.clipboard().setText(sr.format_report(self.result))
        self._set_status('결과를 클립보드에 복사했습니다', GREEN)

    def _save_csv(self):
        global _last_dir
        if self.result is None:
            return
        stamp = re.sub(r'\D', '', str(self.result.get('date', '')))[:12]
        stage = re.sub(r'[\\/:*?"<>|·\s]+', '_', str(self.result.get('stage', '')))
        start = os.path.join(_last_dir, f'스캐너회전_{stage}_{stamp}.csv')
        path, _ = QFileDialog.getSaveFileName(self, 'CSV로 저장', start, 'CSV (*.csv)')
        if not path:
            return
        try:
            sr.write_csv(path, self.result)
        except OSError as e:
            QMessageBox.critical(self, '오류', f'CSV 저장 실패\n{e}')
            return
        _last_dir = os.path.dirname(path)
        self._set_status(f'CSV 저장: {path}', GREEN)

    def _add_to_diagnosis(self):
        """결과를 메인 창에 넘긴다 (워크스트림 A와 맞춘 약속).

        - 메인 창 _rotation_test_result에 복사본을 둔다 (진단을 실행하면 A가 읽는다).
        - 메인 창에 set_rotation_test가 있으면 다른 복사본으로 부른다 (A가 저장된 진단을 고친다).
        - _xy_diagnosis는 직접 고치지 않는다.
        """
        window = self.parent()
        if window is None or not sr.can_add_to_diagnosis(self.result):
            return
        window._rotation_test_result = copy.deepcopy(self.result)
        hook = getattr(window, 'set_rotation_test', None)
        if not callable(hook):
            self._set_status('메인 창에 두었습니다 — 진단 실행 시 포함됩니다', GREEN)
            return
        try:
            hook(copy.deepcopy(self.result))
        except Exception as e:      # A 쪽 오류가 창을 막지 않게 — 사용자에게 보인다
            self._set_status(f'진단 저장 실패: {e} (결과는 메인 창에 두었고 진단 실행 시 포함됩니다)', RED)
            return
        self._set_status('진단에 저장됨 (rotation_test)', GREEN)
