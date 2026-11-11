"""
image_shift_dialog.py — 이미지 2장 이동량 분석 (카메라 축 각도·배율)

Positioning Panel "Capture…"로 받은 원본 이미지 두 장(이상)을 골라 이미지가 얼마나
움직였는지 sub-pixel로 재고, 지시한 스테이지 이동과 비교해 각도 φ·배율·잔차를 보인다.
계산은 core.image_shift가 하고, 이 창은 고르기·ROI 지정·결과 표시·복사·CSV 저장만 한다.

이미지 읽기·미리보기 만들기·분석은 화면 스레드 밖(QThread)에서 한다 (1600×1200 한 쌍 약 0.3~3초,
네트워크 경로는 더 걸린다). 픽셀 크기·지시 이동·축 대응만 바꾸면 이미 잰 이동량으로 바로 다시 계산한다.
이미지·ROI·방법이 바뀌면 진행 중인 분석은 다음 쌍에서 멈추고(취소 깃발) 결과를 버린다. 앱을 끝낼 때도
멈추고 기다리며, 그래도 남으면 main()이 threads_alive()로 보고 os._exit로 끝낸다.
"""

import math
import os

from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import image_shift as ims
from ui.theme import ACCENT, BG, BG2, BG3, FG, FG2, GREEN, ORANGE, RED

ROI_COLOR = QColor('#f9e2af')
MATCH_COLOR = QColor(GREEN)
ARROW_COLOR = QColor(ORANGE)
AUTO_TEMPLATE_COLOR = QColor('#94e2d5')
IMAGE_FILTER = '이미지 (*.bmp *.png *.jpg *.jpeg *.tif *.tiff);;모든 파일 (*)'
TABLE_HEADERS = ['#', '비교 이미지', '방법', 'Δx (px)', 'Δy (px)', 'Δx (µm)', 'Δy (µm)',
                 'φ (°)', 'φ (mrad)', '배율', '품질', '경고']

_last_dir = ''          # 파일 대화상자 시작 폴더 (실행 중에만 기억, 설정 파일에 쓰지 않는다)
_live_threads = set()   # 끝나기 전에 Python 객체가 사라지지 않게 잡아 둔다


def open_image_shift_dialog(parent=None):
    """창을 하나만 띄운다. 닫았다 다시 열면 고른 이미지·입력값이 그대로 남아 있다."""
    dlg = getattr(parent, '_image_shift_dialog', None) if parent is not None else None
    if dlg is None:
        dlg = ImageShiftDialog(parent)
        if parent is not None:
            parent._image_shift_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def threads_alive() -> bool:
    """이 창이 띄운 작업 스레드 중 아직 도는 것이 있나 (main()의 os._exit 판단용)."""
    return any(t.isRunning() for t in list(_live_threads))


def _wait_live_threads(timeout_ms=5000, cancel=True):
    """작업 스레드가 끝나기를 기다린다. cancel이면 먼저 모두 멈추라고 알린다 (앱 종료 aboutToQuit).

    테스트·화면 캡처는 cancel=False로 결과까지 받는다.
    """
    if cancel:
        for t in list(_live_threads):
            t.cancelled = True
    for t in list(_live_threads):
        t.wait(timeout_ms)


class _Task(QThread):
    """함수 하나를 작업 스레드에서 돌린다. fn(progress) → 결과.

    progress(끝난 수, 전체)는 cancelled 깃발이 서 있으면 ims.AnalysisCancelled를 던져 멈춘다.
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
            raise ims.AnalysisCancelled()
        self.progress.emit(self.job, done, total)

    def run(self):
        try:
            result = self.fn(self._progress)
        except ims.AnalysisCancelled:
            return
        except Exception as e:      # 화면에 문구로 보이고, 창은 계속 쓸 수 있어야 한다
            self.failed.emit(self.job, str(e) or type(e).__name__)
        else:
            if not self.cancelled:
                self.done.emit(self.job, result)


def _qimage(u8):
    h, w = u8.shape
    return QImage(u8.data, w, h, u8.strides[0], QImage.Format.Format_Grayscale8).copy()


class _ImageView(QWidget):
    """이미지 한 장 + 겹쳐 그리기(ROI, 찾은 자리, 이동 화살표). editable이면 끌어서 ROI를 정한다."""
    roiChanged = Signal(object)         # (x, y, w, h) 이미지 좌표 또는 None

    def __init__(self, title, editable=False, parent=None):
        super().__init__(parent)
        self.title = title
        self.editable = editable
        self.caption = ''
        self.image = None               # QImage
        self.roi = None                 # 이미지 1에서 사용자가 정한 ROI
        self.boxes = []                 # [(rect(x, y, w, h), QColor, 점선 여부, 이름)]
        self.arrow = None               # (x0, y0, dx, dy) 이미지 좌표
        self.message = ''
        self._drag = None
        self.setMinimumSize(360, 270)
        if editable:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setToolTip('끌어서 ROI(무늬가 있는 곳)를 지정합니다. 짧게 클릭하면 ROI를 지웁니다.')

    def set_image(self, qimage, caption='', message=''):
        self.image = qimage
        self.caption = caption
        self.message = message
        self.update()

    def set_overlay(self, boxes=(), arrow=None):
        self.boxes = list(boxes)
        self.arrow = arrow
        self.update()

    def _target(self):
        if self.image is None or self.image.isNull():
            return None
        iw, ih = self.image.width(), self.image.height()
        avail = QRectF(self.rect()).adjusted(4, 22, -4, -4)
        s = min(avail.width() / iw, avail.height() / ih)
        w, h = iw * s, ih * s
        return QRectF(avail.x() + (avail.width() - w) / 2, avail.y() + (avail.height() - h) / 2, w, h), s

    def _to_image(self, pos):
        t = self._target()
        if t is None:
            return None
        rect, s = t
        x = (pos.x() - rect.x()) / s
        y = (pos.y() - rect.y()) / s
        return (min(max(x, 0.0), self.image.width()), min(max(y, 0.0), self.image.height()))

    # 끌어서 ROI
    def mousePressEvent(self, e):
        if self.editable and e.button() == Qt.MouseButton.LeftButton and self.image is not None:
            self._drag = (self._to_image(e.position()), None)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._drag = (self._drag[0], self._to_image(e.position()))
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            start, _ = self._drag
            end = self._to_image(e.position())
            self._drag = None
            if start is None or end is None:
                return
            x0, x1 = sorted((start[0], end[0]))
            y0, y1 = sorted((start[1], end[1]))
            if x1 - x0 < ims.MIN_ROI or y1 - y0 < ims.MIN_ROI:
                self.roiChanged.emit(None)        # 짧은 클릭 = ROI 지우기
            else:
                self.roiChanged.emit((int(round(x0)), int(round(y0)),
                                      int(round(x1 - x0)), int(round(y1 - y0))))
            self.update()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG2))
        p.setPen(QColor(FG))
        head = self.title + (f'  ·  {self.caption}' if self.caption else '')
        p.drawText(QRectF(6, 2, self.width() - 12, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(head, Qt.TextElideMode.ElideMiddle, self.width() - 12))
        t = self._target()
        if t is None:
            p.setPen(QColor(FG2))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or '이미지 없음')
            p.end()
            return
        rect, s = t
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(rect, self.image)

        def to_view(x, y):
            return QPointF(rect.x() + x * s, rect.y() + y * s)

        def draw_box(r, color, dashed, label=''):
            pen = QPen(color, 2)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            tl = to_view(r[0], r[1])
            p.drawRect(QRectF(tl.x(), tl.y(), r[2] * s, r[3] * s))
            if label:
                p.drawText(QPointF(tl.x() + 3, tl.y() - 3 if tl.y() > rect.y() + 14 else tl.y() + 13), label)

        for r, color, dashed, label in self.boxes:
            draw_box(r, color, dashed, label)
        if self.roi is not None:
            draw_box(self.roi, ROI_COLOR, False, 'ROI')
        if self.arrow is not None:
            self._draw_arrow(p, to_view, s)
        if self._drag is not None and self._drag[0] is not None and self._drag[1] is not None:
            (x0, y0), (x1, y1) = self._drag
            draw_box((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)), ROI_COLOR, True)
        p.end()

    def _draw_arrow(self, p, to_view, s):
        x0, y0, dx, dy = self.arrow
        length = math.hypot(dx, dy) * s
        if length < 1e-6:
            return
        # 몇 px 이동은 화면에서 안 보이므로 화살표만 늘려 그리고 배율을 적는다
        k = 1 if length >= 40 else int(math.ceil(40.0 / length))
        start = to_view(x0, y0)
        end = to_view(x0 + dx * k, y0 + dy * k)
        p.setPen(QPen(ARROW_COLOR, 2.5))
        p.drawLine(start, end)
        ang = math.atan2(end.y() - start.y(), end.x() - start.x())
        head = QPolygonF([end,
                          QPointF(end.x() - 10 * math.cos(ang - 0.4), end.y() - 10 * math.sin(ang - 0.4)),
                          QPointF(end.x() - 10 * math.cos(ang + 0.4), end.y() - 10 * math.sin(ang + 0.4))])
        p.setBrush(ARROW_COLOR)
        p.drawPolygon(head)
        if k > 1:
            p.drawText(QPointF(end.x() + 6, end.y() + 4), f'화살표 ×{k}')


class ImageShiftDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('이미지 2장 이동량 분석 — 카메라 축 각도·배율')
        self.setMinimumSize(1100, 780)
        # 대화상자 자신에만 — 단추 등은 앱 공통 다크 테마(DARK_STYLE)를 그대로 쓴다
        self.setStyleSheet(f'QDialog {{ background: {BG}; color: {FG}; }}')
        self.setAcceptDrops(True)
        self.roi = None
        self.batch = None               # 마지막 분석 결과 (ims.BatchResult)
        self._job = 0                   # 분석 작업 번호 (늦게 온 이전 결과는 버린다)
        self._task = None               # 결과를 기다리는 분석 작업
        self._analysis_tasks = set()    # 아직 도는 분석 작업 (멈추라고 한 것 포함) — 끝나야 새로 시작
        self._previews = {}             # (경로, 수정 시각, 크기) → QImage (8비트 미리보기)
        self._preview_jobs = {}         # 작업 번호 → (경로, 수정 시각, 크기)
        self._preview_errors = {}       # (경로, 수정 시각, 크기) → 문구
        self._next_preview_job = 1_000_000
        self._bulk = False              # 목록을 한꺼번에 바꾸는 중 (행 신호마다 다시 그리지 않는다)
        self._ref_path = None           # 이미지 1 경로 — 바뀌면 ROI를 지운다
        self._build_ui()
        self._refresh_views()

    # ── 화면 구성 ────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        title = QLabel('이미지 2장 이동량 분석')
        title.setStyleSheet(f'font-size: 12pt; font-weight: bold; color: {ACCENT};')
        root.addWidget(title)
        intro = QLabel('Positioning Panel의 Capture 이미지(이동 전·후)를 고르면 이미지 이동량을 0.01 px 단위로 재고, '
                       '지시한 스테이지 이동과 비교해 카메라 축과 스테이지 축 사이 각도(φ)와 배율을 계산합니다. '
                       '첫 이미지가 기준이고 나머지는 각각 첫 이미지와 비교합니다(여러 장이면 φ 평균·표준편차). '
                       '무늬가 화면의 1/4 넘게 움직이면 이미지 1에서 무늬를 끌어 ROI로 지정하세요.')
        intro.setWordWrap(True)
        intro.setStyleSheet(f'color: {FG2}; font-size: 9pt;')
        root.addWidget(intro)
        raw_hint = QLabel('원본 *_PR.png(1600×1200)를 쓰세요. *_Result.bmp처럼 좌표 글자·십자선·상자가 그려진 이미지는 '
                          '그 오버레이가 화면에 고정돼 이동량을 0 쪽으로 끕니다 (감지하면 빼고 계산하고 경고합니다).')
        raw_hint.setWordWrap(True)
        raw_hint.setStyleSheet(f'color: {ORANGE}; font-size: 9pt;')
        root.addWidget(raw_hint)

        # 이미지 목록
        files = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(96)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setToolTip('끌어서 순서를 바꿉니다. 맨 위가 기준(이미지 1)입니다. 탐색기에서 파일을 끌어 놓아도 됩니다.')
        self.file_list.setStyleSheet(f'background: {BG2}; border: 1px solid {BG3};')
        model = self.file_list.model()
        for sig in (model.rowsMoved, model.rowsInserted, model.rowsRemoved):
            sig.connect(self._on_list_changed)
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        files.addWidget(self.file_list, 1)
        buttons = QVBoxLayout()
        self.btn_add = QPushButton('이미지 추가…')
        self.btn_add.clicked.connect(self._add_files)
        self.btn_remove = QPushButton('선택 제거')
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear = QPushButton('모두 지우기')
        self.btn_clear.clicked.connect(self._clear_files)
        for b in (self.btn_add, self.btn_remove, self.btn_clear):
            buttons.addWidget(b)
        buttons.addStretch()
        files.addLayout(buttons)
        root.addLayout(files)

        # 미리보기
        split = QSplitter(Qt.Orientation.Horizontal)
        self.view1 = _ImageView('이미지 1 (기준)', editable=True)
        self.view1.roiChanged.connect(self._set_roi)
        self.view2 = _ImageView('비교 이미지')
        split.addWidget(self.view1)
        split.addWidget(self.view2)
        root.addWidget(split, 3)

        # 입력
        inputs = QHBoxLayout()
        inputs.addWidget(QLabel('픽셀 크기'))
        self.pixel_size = self._spin(0.0001, 100.0, 5, ims.DEFAULT_PIXEL_SIZE_UM, ' µm/px')
        inputs.addWidget(self.pixel_size)
        inputs.addSpacing(12)
        inputs.addWidget(QLabel('지시 스테이지 이동 ΔX'))
        self.stage_dx = self._spin(-1e6, 1e6, 3, 0.0, ' µm')
        inputs.addWidget(self.stage_dx)
        inputs.addWidget(QLabel('ΔY'))
        self.stage_dy = self._spin(-1e6, 1e6, 3, 0.0, ' µm')
        inputs.addWidget(self.stage_dy)
        inputs.addWidget(QLabel('축 대응'))
        self.mapping = QComboBox()
        self.mapping.addItem('자동', ims.MAPPING_AUTO)
        for m in ims.MAPPING_CHOICES:
            self.mapping.addItem(ims.mapping_label(m), m)
        self.mapping.setToolTip('스테이지 +X·+Y가 화면에서 어느 쪽으로 가는지\n'
                                '자동: 측정 방향에 가장 가까운 대응 — 한 축만 움직였거나 후보가 10° 이상 떨어질 때만 정함\n'
                                'X·Y를 함께 움직였는데 정하지 못하면 여기서 직접 고르세요 (다시 재지 않고 바로 계산)')
        self.mapping.currentIndexChanged.connect(self._recompute_motion)
        inputs.addWidget(self.mapping)
        inputs.addSpacing(12)
        inputs.addWidget(QLabel('방법'))
        self.method = QComboBox()
        for key in (ims.METHOD_AUTO, ims.METHOD_PHASE, ims.METHOD_TEMPLATE):
            self.method.addItem(ims.METHOD_LABELS[key], key)
        self.method.setToolTip('자동: 위상 상관이 믿을 만하면 쓰고, 이동이 크거나 불안정하면 템플릿 추적\n'
                               '위상 상관: 이동이 분석 영역의 1/4보다 작을 때 가장 정확\n'
                               '템플릿 추적: ROI(없으면 무늬가 뚜렷한 곳)를 이미지 2 전체에서 찾음 — 큰 이동')
        self.method.currentIndexChanged.connect(self._invalidate)
        inputs.addWidget(self.method)
        inputs.addSpacing(12)
        self.roi_label = QLabel()
        self.roi_label.setStyleSheet(f'color: {FG2};')
        inputs.addWidget(self.roi_label)
        self.btn_roi_clear = QPushButton('ROI 지우기')
        self.btn_roi_clear.clicked.connect(lambda: self._set_roi(None))
        inputs.addWidget(self.btn_roi_clear)
        inputs.addStretch()
        self.busy = QProgressBar()
        self.busy.setFixedWidth(140)
        self.busy.setTextVisible(False)
        self.busy.setStyleSheet(f'QProgressBar {{ background: {BG3}; border: none; border-radius: 4px; height: 10px; }}'
                                f'QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}')
        self.busy.hide()
        inputs.addWidget(self.busy)
        self.btn_run = QPushButton('분석')
        self.btn_run.setProperty('accent', True)
        self.btn_run.setMinimumWidth(90)
        self.btn_run.clicked.connect(self._run)
        inputs.addWidget(self.btn_run)
        root.addLayout(inputs)
        self.pixel_size.valueChanged.connect(self._recompute_motion)
        self.stage_dx.valueChanged.connect(self._recompute_motion)
        self.stage_dy.valueChanged.connect(self._recompute_motion)

        sign = QLabel('부호: Δx 화면 오른쪽 +, Δy 화면 아래 + · φ = 기대 방향에서 측정 방향으로 화면에서 반시계면 + · '
                      '배율 = 측정 거리 / 지시 거리 · 기대 방향은 축 대응으로 정합니다 · 여러 장 평균·표준편차는 '
                      '경고 없는 쌍만 씁니다')
        sign.setWordWrap(True)
        sign.setStyleSheet(f'color: {FG2}; font-size: 8pt;')
        root.addWidget(sign)

        # 결과
        self.status = QLabel('이미지를 2장 이상 추가하세요')
        self.status.setStyleSheet(f'color: {FG2};')
        root.addWidget(self.status)
        self.interpretation = QLabel()
        self.interpretation.setWordWrap(True)
        self.interpretation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.interpretation.setStyleSheet(f'font-size: 11pt; font-weight: bold; color: {FG};')
        root.addWidget(self.interpretation)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail.setStyleSheet(f'color: {FG2}; font-size: 9pt;')
        root.addWidget(self.detail)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f'color: {ORANGE}; font-size: 9pt;')
        root.addWidget(self.warn)

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(TABLE_HEADERS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(110)
        self.table.currentCellChanged.connect(lambda row, *_: self._show_pair(row))
        root.addWidget(self.table, 1)
        self.stats = QLabel()
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stats.setStyleSheet(f'color: {ACCENT}; font-size: 10pt; font-weight: bold;')
        root.addWidget(self.stats)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_copy = QPushButton('결과 복사')
        self.btn_copy.clicked.connect(self._copy)
        self.btn_csv = QPushButton('CSV로 저장…')
        self.btn_csv.clicked.connect(self._save_csv)
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        for b in (self.btn_copy, self.btn_csv, close):
            bottom.addWidget(b)
        root.addLayout(bottom)
        self._update_buttons()

    @staticmethod
    def _spin(lo, hi, decimals, value, suffix):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setValue(value)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        s.setMinimumWidth(110)
        return s

    # ── 이미지 목록 ──────────────────────────────────────

    def paths(self):
        return [self.file_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.file_list.count())]

    def set_paths(self, paths):
        """목록을 통째로 바꾼다."""
        self._bulk = True
        self.file_list.clear()
        self._bulk = False
        self.add_paths(paths)

    def add_paths(self, paths):
        known = set(self.paths())
        self._bulk = True
        try:
            for p in paths:
                p = os.path.normpath(str(p))
                if p in known:
                    continue
                known.add(p)
                item = QListWidgetItem(os.path.basename(p))
                item.setData(Qt.ItemDataRole.UserRole, p)
                item.setToolTip(p)
                self.file_list.addItem(item)
        finally:
            self._bulk = False
        self._on_list_changed()

    def _add_files(self):
        global _last_dir
        files, _ = QFileDialog.getOpenFileNames(self, '이미지 고르기', _last_dir, IMAGE_FILTER)
        if files:
            _last_dir = os.path.dirname(files[0])
            self.add_paths(files)

    def _remove_selected(self):
        self._bulk = True
        try:
            for item in self.file_list.selectedItems():
                self.file_list.takeItem(self.file_list.row(item))
        finally:
            self._bulk = False
        self._on_list_changed()

    def _clear_files(self):
        self.set_paths([])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.lower().endswith(ims.IMAGE_EXTENSIONS)]
        if paths:
            self.add_paths(paths)
            e.acceptProposedAction()

    def _on_list_changed(self, *_args):
        if self._bulk:
            return
        paths = self.paths()
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            name = os.path.basename(item.data(Qt.ItemDataRole.UserRole))
            item.setText(f'{i + 1}. {name}' + ('  (기준)' if i == 0 else ''))
        ref = paths[0] if paths else None
        if ref != self._ref_path:
            self._ref_path = ref
            self.roi = None             # 다른 이미지 1에서 그린 ROI는 맞지 않는다
        self._prune_previews(paths)
        self._invalidate()
        self._refresh_views()

    def _on_file_selected(self, row):
        if row >= 1 and self.batch is None:
            self._refresh_views(compare_row=row)

    # ── 미리보기 ─────────────────────────────────────────

    @staticmethod
    def _preview_key(path):
        """(경로, 수정 시각, 크기) — 같은 이름으로 다시 저장한 파일은 새로 읽는다."""
        try:
            st = os.stat(path)
        except OSError:
            return (path, None, None)
        return (path, st.st_mtime_ns, st.st_size)

    def _prune_previews(self, paths):
        """목록에서 빠진 파일의 미리보기·오류는 버린다."""
        keep = set(paths)
        for cache in (self._previews, self._preview_errors):
            for key in [k for k in cache if k[0] not in keep]:
                del cache[key]

    def _preview(self, path):
        """(QImage 또는 None, 안내 문구). 없으면 작업 스레드에서 읽기 시작한다."""
        key = self._preview_key(path)
        if key in self._previews:
            return self._previews[key], ''
        if key in self._preview_errors:
            return None, self._preview_errors[key]
        if key not in self._preview_jobs.values():
            job = self._next_preview_job
            self._next_preview_job += 1
            self._preview_jobs[job] = key
            task = _Task(job, lambda _progress, p=path: ims.to_uint8(ims.load_gray(p)))
            task.done.connect(self._on_preview_done)
            task.failed.connect(self._on_preview_failed)
            self._start(task)
        return None, '읽는 중…'

    def _on_preview_done(self, job, u8):
        key = self._preview_jobs.pop(job, None)
        if key is not None and key[0] in self.paths():
            self._previews[key] = _qimage(u8)
            self._refresh_views()

    def _on_preview_failed(self, job, msg):
        key = self._preview_jobs.pop(job, None)
        if key is not None and key[0] in self.paths():
            self._preview_errors[key] = msg
            self._refresh_views()

    def _refresh_views(self, compare_row=None):
        paths = self.paths()
        if paths:
            img, msg = self._preview(paths[0])
            self.view1.set_image(img, os.path.basename(paths[0]), msg)
        else:
            self.view1.set_image(None, '', '이미지 추가… 또는 파일을 끌어 놓으세요')
        self.view1.roi = self.roi
        pair = self._current_pair()
        if pair is not None:
            k = pair.index - 1
        else:
            current = self.file_list.currentRow()
            k = compare_row if compare_row is not None else (current if current >= 1 else 1)
        if 0 < k < len(paths):
            img, msg = self._preview(paths[k])
            self.view2.title = f'비교 이미지 {k + 1}'
            self.view2.set_image(img, os.path.basename(paths[k]), msg)
        else:
            self.view2.title = '비교 이미지'
            self.view2.set_image(None, '', '두 번째 이미지를 추가하세요')
        self._update_overlays(pair)
        self.roi_label.setText('ROI: ' + ims.roi_text(self.roi))
        self.btn_roi_clear.setEnabled(self.roi is not None)

    def _update_overlays(self, pair):
        boxes1, boxes2, arrow2 = [], [], None
        if pair is not None:
            s = pair.shift
            if s.roi is not None and self.roi is None:
                boxes1.append((s.roi, AUTO_TEMPLATE_COLOR, True, '자동 템플릿'))
            ref = s.roi or self.roi
            if ref is not None:
                x, y, w, h = ref
                boxes2.append(((x, y, w, h), ROI_COLOR, True, ''))
                boxes2.append(((x + s.dx_px, y + s.dy_px, w, h), MATCH_COLOR, False, '찾은 자리'))
                cx, cy = x + w / 2, y + h / 2
            else:
                img = self.view2.image
                cx, cy = (img.width() / 2, img.height() / 2) if img is not None else (0, 0)
            arrow2 = (cx, cy, s.dx_px, s.dy_px)
        self.view1.set_overlay(boxes1)
        self.view2.set_overlay(boxes2, arrow2)

    # ── 입력 변경 ────────────────────────────────────────

    def _set_roi(self, roi):
        self.roi = roi
        self._invalidate()
        self._refresh_views()

    def _invalidate(self, *_args):
        """이미지·ROI·방법이 바뀌면 이전 결과는 맞지 않으므로 지우고, 진행 중인 분석은 멈추게 한다."""
        if self._task is not None:
            self._task.cancelled = True     # 다음 쌍으로 넘어갈 때 멈춘다
            self._job += 1                  # 그 사이 도착한 결과도 버린다
            self._set_busy(False)
            if self._analysis_tasks:
                self.status.setText('이전 분석을 멈추는 중…')
        if self.batch is not None:
            self.batch = None
            self._show_results()
        self._update_buttons()

    def _recompute_motion(self, *_args):
        """픽셀 크기·지시 이동·축 대응만 바뀌었으면 이미 잰 이동량으로 각도·배율을 바로 다시 계산한다.

        interpret_batch는 모든 쌍을 계산한 뒤에야 batch를 바꾸므로, 실패하면 batch는 그대로다.
        그래도 화면 입력과 결과가 갈라지지 않게 결과를 지우고 이유를 보인다.
        """
        if self.batch is None:
            return
        row = self.table.currentRow()
        try:
            ims.interpret_batch(self.batch, self.pixel_size.value(),
                                (self.stage_dx.value(), self.stage_dy.value()), self._mapping_choice())
        except Exception as e:      # 계산 실패가 창을 막지 않게
            self.batch = None
            self._show_results()
            self.status.setText(f'다시 계산 실패 — 분석을 다시 누르세요: {e}')
            self.status.setStyleSheet(f'color: {RED};')
            return
        self._show_results(keep_row=row)

    def _mapping_choice(self):
        """축 대응 콤보 값: ims.MAPPING_AUTO 또는 (swap, gx, gy) — Qt가 튜플을 목록으로 돌려줘도 튜플로."""
        data = self.mapping.currentData()
        return tuple(data) if isinstance(data, (list, tuple)) else data

    # ── 분석 ─────────────────────────────────────────────

    def _run(self):
        paths = self.paths()
        if len(paths) < 2:
            self.status.setText('이미지를 2장 이상 추가하세요')
            return
        if self._analysis_tasks:
            self.status.setText('이전 분석을 멈추는 중… 잠시 뒤 다시 누르세요')
            return
        self._job += 1
        job = self._job
        ps = self.pixel_size.value()
        stage = (self.stage_dx.value(), self.stage_dy.value())
        method = self.method.currentData()
        mapping = self._mapping_choice()
        roi = self.roi
        task = _Task(job, lambda progress: ims.analyze_images(paths, ps, stage, method, roi, progress=progress,
                                                              mapping=mapping))
        task.done.connect(self._on_done)
        task.failed.connect(self._on_failed)
        task.progress.connect(self._on_progress)
        self._task = task
        self._analysis_tasks.add(task)
        self.batch = None
        self._show_results()
        self._set_busy(True, f'분석 중… (0/{len(paths) - 1})')
        self._start(task)

    def _start(self, task):
        app = QCoreApplication.instance()
        if app is not None and not getattr(app, '_image_shift_quit_hooked', False):
            app.aboutToQuit.connect(_wait_live_threads)
            app._image_shift_quit_hooked = True
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
        if not self._analysis_tasks and self._task is None and self.status.text().startswith('이전 분석을 멈추는 중'):
            self._show_results()
        self._update_buttons()

    def run_and_wait(self, timeout_ms=60000):
        """분석을 시작하고 끝날 때까지 기다린다 (테스트·화면 캡처용)."""
        self._run()
        task = self._task
        if task is not None:
            task.wait(timeout_ms)
            QCoreApplication.processEvents()
            QCoreApplication.processEvents()

    def _set_busy(self, on, text=''):
        if on:
            self.busy.setRange(0, 0)
            self.busy.show()
            self.status.setText(text)
        else:
            self.busy.hide()
            self._task = None
        self._update_buttons()

    def _on_progress(self, job, done, total):
        if job != self._job:
            return
        self.busy.setRange(0, total)
        self.busy.setValue(done)
        self.status.setText(f'분석 중… ({done}/{total})')

    def _on_failed(self, job, msg):
        if job != self._job:
            return
        self._set_busy(False)
        self.status.setText(f'분석 실패: {msg}')
        self.status.setStyleSheet(f'color: {RED};')

    def _on_done(self, job, batch):
        if job != self._job:
            return
        self._set_busy(False)
        self.batch = batch
        self._show_results()
        # 분석 중에 바꾼 픽셀 크기·지시 이동·축 대응을 결과에 반영한다
        self._recompute_motion()

    # ── 결과 표시 ────────────────────────────────────────

    def _current_pair(self):
        if self.batch is None or not self.batch.pairs:
            return None
        row = self.table.currentRow()
        return self.batch.pairs[row] if 0 <= row < len(self.batch.pairs) else self.batch.pairs[0]

    def _show_results(self, keep_row=0):
        batch = self.batch
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.status.setStyleSheet(f'color: {FG2};')
        if batch is None:
            self.table.blockSignals(False)
            self.interpretation.clear()
            self.detail.clear()
            self.warn.clear()
            self.stats.clear()
            n = len(self.paths())
            self.status.setText('이미지를 2장 이상 추가하세요' if n < 2 else f'이미지 {n}장 — 분석을 누르세요')
            self._refresh_views()
            self._update_buttons()
            return
        for p in batch.pairs:
            self._add_row(p)
        self.table.blockSignals(False)
        ok, bad = len(batch.pairs), len(batch.errors)
        self.status.setText(f'분석 완료 — {ok}쌍' + (f', 실패 {bad}장' if bad else ''))
        if not batch.pairs:
            self.status.setStyleSheet(f'color: {RED};')
        if batch.pairs and any(batch.stage_um):
            self.status.setText(self.status.text() + f' · 축 대응 {ims.mapping_text(batch)}')
        self.stats.setText(ims.stats_summary(batch))
        if batch.pairs:
            self.table.selectRow(min(max(keep_row, 0), len(batch.pairs) - 1))
        self._show_pair(self.table.currentRow())
        self._update_buttons()

    def _add_row(self, p):
        s, m = p.shift, p.motion
        row = self.table.rowCount()
        self.table.insertRow(row)
        warns = p.warnings
        values = [
            str(p.index), p.name2, ims.METHOD_LABELS.get(s.method, s.method),
            f'{s.dx_px:+.3f}', f'{s.dy_px:+.3f}', f'{m["dx_um"]:+.3f}', f'{m["dy_um"]:+.3f}',
            '' if m['phi_deg'] is None else f'{m["phi_deg"]:+.4f}',
            '' if m['phi_mrad'] is None else f'{m["phi_mrad"]:+.3f}',
            '' if m['scale_ratio'] is None else f'{m["scale_ratio"]:.5f}',
            f'NCC {s.ncc:.3f}' if math.isfinite(s.ncc) else '',
            f'⚠ {len(warns)}' if warns else '없음',
        ]
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            if col >= 3:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col == 10:
                item.setToolTip(ims.quality_text(s))
            if col == 11 and warns:
                item.setForeground(QColor(ORANGE))
                item.setToolTip('\n'.join(warns))
            self.table.setItem(row, col, item)

    def _show_pair(self, row):
        batch = self.batch
        pair = None
        if batch is not None and batch.pairs:
            pair = batch.pairs[row] if 0 <= row < len(batch.pairs) else batch.pairs[0]
        if pair is None:
            self.interpretation.clear()
            self.detail.clear()
            errs = [f'❌ #{i} {name}: {msg}' for i, name, msg in (batch.errors if batch else [])]
            self.warn.setText('\n'.join(errs))
            self._refresh_views()
            return
        s, m = pair.shift, pair.motion
        self.interpretation.setText(m['interpretation'])
        parts = [f'#{pair.index}: Δx {s.dx_px:+.3f} px ({m["dx_um"]:+.3f} µm), Δy {s.dy_px:+.3f} px ({m["dy_um"]:+.3f} µm), '
                 f'거리 {m["length_px"]:.3f} px ({m["length_um"]:.3f} µm)']
        if m['phi_deg'] is not None:
            rx, ry = m['residual_um']
            parts.append(f'φ {m["phi_deg"]:+.4f}° ({m["phi_mrad"]:+.3f} mrad) · 배율 {m["scale_ratio"]:.5f} · '
                         f'잔차 ({rx:+.3f}, {ry:+.3f}) µm · {m["mapping"]}')
        parts.append(f'{ims.METHOD_LABELS.get(s.method, s.method)} · {ims.quality_text(s)}'
                     + (f' · 템플릿 {ims.roi_text(s.roi)}' if s.method == ims.METHOD_TEMPLATE else ''))
        parts.extend(f'참고: {n}' for n in s.notes)
        self.detail.setText('\n'.join(parts))
        lines = [f'⚠ {w}' for w in pair.warnings]
        lines += [f'⚠ (여러 장) {w}' for w in batch.warnings if w not in pair.warnings]
        lines += [f'❌ #{i} {name}: {msg}' for i, name, msg in batch.errors]
        self.warn.setText('\n'.join(lines))
        self._refresh_views()

    def _update_buttons(self):
        has = self.batch is not None and bool(self.batch.pairs)
        self.btn_copy.setEnabled(has)
        self.btn_csv.setEnabled(has)
        # 멈추라고 한 이전 분석이 끝나기 전에는 새로 시작하지 않는다 (작업이 쌓이지 않게)
        self.btn_run.setEnabled(self._task is None and not self._analysis_tasks and len(self.paths()) >= 2)

    # ── 복사·저장 ────────────────────────────────────────

    def _copy(self):
        if self.batch is None:
            return
        QGuiApplication.clipboard().setText(ims.format_report(self.batch))
        self.status.setText('결과를 클립보드에 복사했습니다')

    def _save_csv(self):
        global _last_dir
        if self.batch is None:
            return
        paths = self.paths()
        base = os.path.splitext(os.path.basename(paths[0]))[0] if paths else 'image'
        start = os.path.join(_last_dir or (os.path.dirname(paths[0]) if paths else ''), f'{base}_이동량.csv')
        path, _ = QFileDialog.getSaveFileName(self, 'CSV로 저장', start, 'CSV (*.csv)')
        if not path:
            return
        try:
            ims.write_csv(path, self.batch)
        except OSError as e:
            QMessageBox.critical(self, '오류', f'CSV 저장 실패\n{e}')
            return
        _last_dir = os.path.dirname(path)
        self.status.setText(f'CSV 저장: {path}')
