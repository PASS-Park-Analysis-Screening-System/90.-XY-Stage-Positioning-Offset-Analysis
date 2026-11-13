"""
expert_window.py — XY 전문 분석 (QC 엔지니어) 창

작업자 화면에서 뺀 심화 분석을 담는 비모달 창이다. 메인 창 mixin이 만든 위젯을 그대로 옮겨 담는다
(분석 코드를 따로 두지 않는다). 왼쪽 메뉴(묶음 머리 + 쪽)로 오른쪽 쪽을 고른다 — 위쪽 탭 줄은 없다:

  분석        Pareto · Correlation · 3D Surface
  원인 추적    Positioning Log · Diagnosis (트러블슈팅) ▸ 진단 요약 · 스캐너 오프셋 · What-if · 드리프트 · 흔들림 · 부위 판정 · 조치
  도구 · 기록  도구 · 도움말 · 전문 분석 기록 (읽지 않은 수)

분석 쪽은 1600×900 창에서 세로 스크롤 없이 들어간다 (도움말·기록만 스크롤). 묶음(section)에 머리줄이 있으면
(Diagnosis: 설명·진단 JSON 저장·3D 보고서·회전 시험·사례 라이브러리) 그 묶음의 쪽마다 위에 보인다.
메뉴의 진단 쪽에는 주의·이상 점이 붙는다. 맨 위 띠: 메인 창이 데이터를 읽는 동안 '메인 창에서 데이터를 읽는 중 — 끝나면 갱신됩니다'.
'전문 분석 기록'은 진단·Positioning Log·보고서·JSON·LLM·차트 오류 메시지다 (작업자 System Log와 따로 —
System Log는 Excel·Quality Report의 로그 시트로 나간다).

부모가 없는 보통 창이다 (자기 작업 표시줄 단추, 메인 창 위에 붙어 있지 않음). 이 창이 열려 있어도 메인 창을
닫으면 앱이 끝난다 (WA_QuitOnClose 끔, 메인 창 closeEvent가 닫는다). 닫으면 숨기기만 한다 (읽은 Log·진단·도구 창은
그대로) — 숨길 때 떠 있던 도구 창(회전 시험·이미지 이동량 등)도 감추고, 다시 열 때 함께 보인다.
여는 방법·보임 판단·위치 기억은 ui/controllers/expert_controller.py (docs/EXPERT_MODE.md).

도구의 대화상자와 3D 보고서·사례 라이브러리 메시지는 이 창을 부모로 연다 (이 창 위에 쌓인다).
그 기능들이 부모 창에서 찾는 값(settings, _xy_diagnosis, _rotation_test_result, set_rotation_test)은 메인 창의 것을
그대로 넘긴다 — 진단·회전 시험 결과는 메인 창 하나에만 있다. logger는 이 창의 기록이다.
"""

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QTextEdit,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ui.theme import ACCENT, BG, BG2, BG3, FG, FG2, ORANGE, RED
from ui.widgets.system_logger import SystemLogger

EXPERT_TITLE = 'XY 전문 분석 (QC 엔지니어)'
TOOLS_PAGE = '도구'
HELP_PAGE = '도움말'
LOG_PAGE = '전문 분석 기록'
BUSY_TEXT = '메인 창에서 데이터를 읽는 중 — 끝나면 갱신됩니다'
STATUS_COLOR = {'warn': ORANGE, 'fail': RED}
NAV_WIDTH = 210


def _dot(color):
    pix = QPixmap(10, 10)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    return QIcon(pix)


class _ExpertLog(SystemLogger):
    """전문 분석 기록 — 새 메시지를 창에 알린다 (메뉴의 읽지 않은 수)."""

    def __init__(self, view, on_message):
        super().__init__(view)
        self._on_message = on_message

    def _append(self, msg, tag='info'):
        super()._append(msg, tag)
        self._on_message(tag)


class ExpertWindow(QMainWindow):
    # 숨김·최소화였다가 보이게 됐다 (처음·다시 열기, 최소화에서 돌아옴). 활성화만으로는 내지 않는다.
    # 미뤄 둔 Log 읽기·진단·차트를 시작하고, 읽기 오류가 난 회차는 다시 읽는다
    became_visible = Signal()
    # 왼쪽 메뉴에서 다른 쪽을 골랐다 (쪽 이름). 명시적 동작이라 읽기 오류가 난 회차도 다시 읽는다
    page_changed = Signal(str)

    def __init__(self, app):
        super().__init__(None)
        self._app = app
        self._needs_flush = True            # 보이게 되는 전환에서만 became_visible
        self._hidden_tools = []             # 이 창을 숨길 때 함께 감춘 도구 창
        self._pages = {}                    # 쪽 이름 → (stack 번호, 메뉴 항목, 묶음 이름 또는 None)
        self._sections = {}                 # 묶음 이름 → 첫 쪽 이름
        self._section_bars = {}             # 묶음 이름 → 머리줄 위젯
        self._groups = {}                   # 메뉴 묶음 머리 이름 → 항목
        self._unread = 0
        self._unread_error = False
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowTitle(EXPERT_TITLE)
        self.setMinimumSize(900, 600)

        self.busy_banner = QLabel(BUSY_TEXT)
        self.busy_banner.setAlignment(Qt.AlignCenter)
        self.busy_banner.setStyleSheet(f"background: {ORANGE}; color: {BG}; font-weight: bold; padding: 4px;")
        self.busy_banner.hide()

        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setRootIsDecorated(False)
        self.nav.setItemsExpandable(False)
        self.nav.setIndentation(14)
        self.nav.setFixedWidth(NAV_WIDTH)
        self.nav.setIconSize(QSize(10, 10))
        self.nav.setStyleSheet(f"""
            QTreeWidget {{ background: {BG2}; border: none; outline: none; padding: 6px 4px; font-size: 10pt; }}
            QTreeWidget::item {{ padding: 5px 4px; border-radius: 4px; color: {FG}; }}
            QTreeWidget::item:selected {{ background: {ACCENT}; color: {BG}; font-weight: bold; }}
            QTreeWidget::item:hover:!selected {{ background: {BG3}; }}
        """)
        self.nav.currentItemChanged.connect(self._on_nav_changed)

        self.stack = QStackedWidget()
        self._bar_host = QWidget()
        bar_layout = QVBoxLayout(self._bar_host)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        self._bar_layout = bar_layout
        self._bar_host.hide()

        content = QWidget()
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(0)
        c_layout.addWidget(self._bar_host)
        c_layout.addWidget(self.stack, 1)

        body = QWidget()
        b_layout = QHBoxLayout(body)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(0)
        b_layout.addWidget(self.nav)
        b_layout.addWidget(content, 1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.busy_banner)
        layout.addWidget(body, 1)
        self.setCentralWidget(central)

        self.log_view = QTextEdit()
        self.log_view.setPlaceholderText('전문 분석 기록 (진단·Positioning Log·보고서·내보내기)')
        self.log = _ExpertLog(self.log_view, self._on_log_message)

    # ── 메뉴·쪽 ──────────────────────────────────────────

    def add_group(self, title, parent=None):
        """메뉴 묶음 머리 (고를 수 없음). parent가 있으면 그 묶음 안의 작은 머리."""
        item = QTreeWidgetItem([title])
        item.setFlags(Qt.ItemIsEnabled)
        font = QFont(self.nav.font())
        font.setBold(parent is None)
        font.setPointSize(9)
        item.setFont(0, font)
        item.setForeground(0, QBrush(QColor(FG2)))
        if parent is None:
            self.nav.addTopLevelItem(item)
        else:
            self._groups[parent].addChild(item)
        item.setExpanded(True)
        self._groups[title] = item
        return item

    def add_page(self, name, widget, group, section=None):
        """쪽 하나 (메뉴 group 아래). section: 같은 묶음의 쪽들 (머리줄·'그 묶음이 보인다' 판단)."""
        index = self.stack.addWidget(widget)
        item = QTreeWidgetItem([name])
        item.setData(0, Qt.UserRole, name)
        self._groups[group].addChild(item)
        self._groups[group].setExpanded(True)
        self._pages[name] = (index, item, section)
        if section is not None:
            self._sections.setdefault(section, name)
        if self.nav.currentItem() is None:
            self.nav.setCurrentItem(item)
        return item

    def set_section_bar(self, section, widget):
        """묶음의 쪽이 보일 때 그 위에 보이는 머리줄."""
        self._section_bars[section] = widget
        self._bar_layout.addWidget(widget)
        self._sync_bar()

    def has_page(self, name):
        return name in self._pages or name in self._sections

    def page_names(self):
        return list(self._pages)

    def page(self, name):
        return self.stack.widget(self._pages[name][0])

    def nav_item(self, name):
        return self._pages[name][1]

    def section_of(self, name):
        entry = self._pages.get(name)
        return entry[2] if entry else None

    def current_page(self):
        item = self.nav.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def select_page(self, name):
        """쪽(또는 묶음 이름 → 그 묶음의 첫 쪽)을 고른다."""
        name = self._sections.get(name, name) if name not in self._pages else name
        self.nav.setCurrentItem(self._pages[name][1])

    def set_page_status(self, name, status):
        """메뉴 항목의 점 (주의 주황·이상 빨강, None이면 지운다)."""
        item = self._pages[name][1]
        color = STATUS_COLOR.get(status)
        item.setIcon(0, _dot(color) if color else QIcon())
        item.setToolTip(0, {'warn': '주의', 'fail': '이상'}.get(status, ''))

    def _on_nav_changed(self, item, _previous):
        name = item.data(0, Qt.UserRole) if item is not None else None
        if name not in self._pages:
            return
        self.stack.setCurrentIndex(self._pages[name][0])
        self._sync_bar()
        if name == LOG_PAGE:
            self._unread, self._unread_error = 0, False
            self._sync_log_item()
        self.page_changed.emit(name)

    def _sync_bar(self):
        section = self.section_of(self.current_page())
        for key, bar in self._section_bars.items():
            bar.setVisible(key == section)
        self._bar_host.setVisible(section in self._section_bars)

    # ── 전문 분석 기록 ────────────────────────────────────

    def _on_log_message(self, tag):
        if self.current_page() == LOG_PAGE and self.isVisible():
            return
        self._unread += 1
        self._unread_error = self._unread_error or tag == 'err'
        self._sync_log_item()

    def _sync_log_item(self):
        if LOG_PAGE not in self._pages:
            return
        item = self._pages[LOG_PAGE][1]
        item.setText(0, f'{LOG_PAGE}  ({self._unread})' if self._unread else LOG_PAGE)
        item.setForeground(0, QBrush(QColor(RED if self._unread_error else ORANGE if self._unread else FG)))

    def unread_count(self):
        return self._unread

    def set_busy(self, busy):
        self.busy_banner.setVisible(bool(busy))

    # ── 보임 ──────────────────────────────────────────

    def _emit_if_newly_visible(self):
        if self._needs_flush and self.isVisible() and not self.isMinimized():
            self._needs_flush = False
            self.became_visible.emit()

    def showEvent(self, event):
        super().showEvent(event)
        self._emit_if_newly_visible()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._needs_flush = True
        if not event.spontaneous():         # 닫기·숨기기 (최소화는 OS가 딸린 창을 함께 감춘다)
            self._hidden_tools = [w for w in self.findChildren(QDialog) if w.isWindow() and w.isVisible()]
            for dialog in self._hidden_tools:
                dialog.hide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self._needs_flush = True
            else:
                self._emit_if_newly_visible()

    def show_hidden_tools(self):
        """숨길 때 함께 감춘 도구 창을 다시 보인다 (expert_controller._open_expert_window)."""
        tools, self._hidden_tools = self._hidden_tools, []
        for dialog in tools:
            try:
                dialog.show()
            except RuntimeError:            # 그사이 지워진 창
                pass

    def closeEvent(self, event):
        if self.isVisible():
            self._app._remember_expert_geometry()
        super().closeEvent(event)           # 숨기기만 한다 (WA_DeleteOnClose 아님)

    # ── 도구가 부모 창에서 찾는 값: 메인 창의 것 (logger는 이 창의 기록) ──────────

    @property
    def settings(self):
        return self._app.settings

    @property
    def logger(self):
        return self.log

    @property
    def _xy_diagnosis(self):
        return self._app._xy_diagnosis

    @property
    def _rotation_test_result(self):
        return self._app._rotation_test_result

    @_rotation_test_result.setter
    def _rotation_test_result(self, value):
        self._app._rotation_test_result = value

    def set_rotation_test(self, data):
        return self._app.set_rotation_test(data)
