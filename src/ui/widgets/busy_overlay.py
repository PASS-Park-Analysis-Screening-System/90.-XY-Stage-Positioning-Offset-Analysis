"""
busy_overlay.py — 폴더 확인·데이터 읽기·화면 그리기 동안 결과 영역을 덮는 진행 표시

상태 표시줄 문구만으로는 작업 중인지 알아채기 어렵다(현장 피드백). 결과 영역(카드·차트·탭)
위에 반투명 막을 덮고 단계 제목, 큰 진행 막대(n/m, %), 지금 읽는 항목, 경과 초,
'중지 (Esc)' 버튼을 보인다. 창 이동·크기 조절은 막지 않는다(창 안의 자식 위젯이다).

target(덮을 위젯)의 위치를 따라간다. QSplitter의 자식은 분할 칸으로 들어가므로 막은
host(가운데 위젯)의 자식으로 두고 target의 위치를 host 좌표로 옮겨 그 위에 올린다.
"""

import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ui.theme import ACCENT, BG, BG2, BG3, FG, FG2


class BusyOverlay(QWidget):
    def __init__(self, target: QWidget, host: QWidget = None, on_cancel=None):
        super().__init__(host if host is not None else target.parentWidget())
        self._target = target
        self._started = 0.0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.hide()

        card = QFrame(self)
        card.setObjectName('busyCard')
        card.setStyleSheet(f"""
            QFrame#busyCard {{ background: {BG2}; border: 1px solid {BG3}; border-radius: 10px; }}
            QLabel {{ background: transparent; }}
        """)
        card.setFixedWidth(460)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(8)

        self.title = QLabel()
        self.title.setStyleSheet(f"color: {ACCENT}; font-size: 13pt; font-weight: bold;")
        lay.addWidget(self.title)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(16)
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: {BG3}; border: none; border-radius: 7px; }}
            QProgressBar::chunk {{ background: {ACCENT}; border-radius: 7px; }}
        """)
        lay.addWidget(self.bar)

        self.count = QLabel()
        self.count.setStyleSheet(f"color: {FG}; font-size: 10pt; font-weight: bold;")
        lay.addWidget(self.count)

        self.item = QLabel()
        self.item.setWordWrap(True)
        self.item.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        lay.addWidget(self.item)

        bottom = QHBoxLayout()
        self.elapsed = QLabel()
        self.elapsed.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        bottom.addWidget(self.elapsed)
        bottom.addStretch()
        self.cancel_btn = QPushButton("중지 (Esc)")
        self.cancel_btn.setToolTip("진행 중인 작업을 멈춥니다. 이전 결과는 그대로입니다")
        if on_cancel is not None:
            self.cancel_btn.clicked.connect(on_cancel)
        bottom.addWidget(self.cancel_btn)
        lay.addLayout(bottom)
        self._card = card

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_elapsed)
        self._follower = _GeometryFollower(self)
        target.installEventFilter(self._follower)
        # 덮는 칸이 분할기 안에 있으면 분할기가 움직일 때(위 줄 높이가 바뀔 때)도 따라간다
        holder = target.parentWidget()
        if holder is not None and holder is not self.parentWidget():
            holder.installEventFilter(self._follower)

    # ── 표시 ──────────────────────────────────────────

    def start(self, title: str, cancellable: bool = True):
        """단계를 시작한다 (진행 수를 모르면 바쁨 표시). 이미 보이면 경과 시간은 이어 간다."""
        if not self.isVisible():
            self._started = time.monotonic()
            self._tick.start()
        self.title.setText(title)
        self.bar.setRange(0, 0)
        self.count.setText('')
        self.item.setText('')
        self.cancel_btn.setVisible(cancellable)
        self._update_elapsed()
        self.follow()
        self.show()
        self.raise_()
        if cancellable:
            self.cancel_btn.setFocus()      # Enter·Space로도 중지할 수 있게

    def set_progress(self, done: int, total: int, item: str = ''):
        """n/m을 안다면 막대를 채운다. total이 0이면 바쁨 표시."""
        if total and total > 0:
            done = max(0, min(done, total))
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.count.setText(f"{done}/{total} ({done * 100 // total}%)")
        else:
            self.bar.setRange(0, 0)
            self.count.setText('')
        if item:
            self.item.setText(item)

    def set_item(self, item: str):
        self.item.setText(item)

    def set_busy_count(self, text: str):
        """전체를 모르는 단계(폴더 확인): 막대는 바쁨 표시, 확인한 수만 적는다."""
        self.bar.setRange(0, 0)
        self.count.setText(text)

    def finish(self):
        self._tick.stop()
        self.hide()

    def _update_elapsed(self):
        self.elapsed.setText(f"경과 {time.monotonic() - self._started:.0f}초")

    # ── 위치 ──────────────────────────────────────────

    def follow(self):
        top_left = self._target.mapTo(self.parentWidget(), QPoint(0, 0))
        self.setGeometry(QRect(top_left, self._target.size()))
        self._card.adjustSize()
        self._card.move((self.width() - self._card.width()) // 2,
                        max(0, (self.height() - self._card.height()) // 3))

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(BG)
        color.setAlpha(190)     # 반투명: 뒤의 화면이 비친다
        painter.fillRect(self.rect(), color)


class _GeometryFollower(QObject):
    """덮는 위젯이 움직이거나 크기가 바뀌면 막도 따라간다."""

    def __init__(self, overlay):
        super().__init__(overlay)
        self._overlay = overlay

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Move) and self._overlay.isVisible():
            self._overlay.follow()
        return False
