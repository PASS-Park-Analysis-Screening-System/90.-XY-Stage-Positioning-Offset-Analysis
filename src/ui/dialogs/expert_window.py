"""
expert_window.py — XY 전문 분석 (QC 엔지니어) 창

작업자 화면에서 뺀 심화 분석을 담는 비모달 창이다. 메인 창이 만든 위젯을 그대로 옮겨 담는다
(분석 코드를 따로 두지 않는다):
- 'Advanced': 메인 창 mixin이 만든 Advanced QTabWidget (Pareto, Correlation, 3D Surface, Positioning Log,
  Diagnosis (트러블슈팅)) — 메인 창의 _inner_tabs['Advanced']가 그대로 가리킨다
- '도구': AI 분석용 Markdown, 이미지 2장 이동량 분석, 3D 트러블슈팅 보고서, 사례 라이브러리, 스캐너 회전 시험 분석
- '도움말': expert_guide.EXPERT_GUIDE
- 아래쪽 '전문 분석 기록': 진단·Positioning Log·보고서·JSON·LLM·차트 오류 메시지 (작업자 System Log와 따로 —
  System Log는 Excel·Quality Report의 로그 시트로 나간다)
- 위쪽 띠: 메인 창이 데이터를 읽는 동안 '메인 창에서 데이터를 읽는 중 — 끝나면 갱신됩니다'

부모가 없는 보통 창이다 (자기 작업 표시줄 단추, 메인 창 위에 붙어 있지 않음). 이 창이 열려 있어도 메인 창을
닫으면 앱이 끝난다 (WA_QuitOnClose 끔, 메인 창 closeEvent가 닫는다). 닫으면 숨기기만 한다 (읽은 Log·진단·도구 창은
그대로) — 숨길 때 떠 있던 도구 창(회전 시험·이미지 이동량 등)도 감추고, 다시 열 때 함께 보인다.
여는 방법·보임 판단·위치 기억은 ui/controllers/expert_controller.py (docs/EXPERT_MODE.md).

도구의 대화상자와 3D 보고서·사례 라이브러리 메시지는 이 창을 부모로 연다 (이 창 위에 쌓인다).
그 기능들이 부모 창에서 찾는 값(settings, _xy_diagnosis, _rotation_test_result, set_rotation_test)은 메인 창의 것을
그대로 넘긴다 — 진단·회전 시험 결과는 메인 창 하나에만 있다. logger는 이 창의 기록이다.
"""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QDialog, QLabel, QMainWindow, QSplitter, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from ui.theme import BG, ORANGE
from ui.widgets.system_logger import SystemLogger

EXPERT_TITLE = 'XY 전문 분석 (QC 엔지니어)'
ADVANCED_TAB = 'Advanced'
TOOLS_TAB = '도구'
HELP_TAB = '도움말'
BUSY_TEXT = '메인 창에서 데이터를 읽는 중 — 끝나면 갱신됩니다'


class ExpertWindow(QMainWindow):
    # 숨김·최소화였다가 보이게 됐다 (처음·다시 열기, 최소화에서 돌아옴). 활성화만으로는 내지 않는다.
    # 미뤄 둔 Log 읽기·진단·차트를 시작하고, 읽기 오류가 난 회차는 다시 읽는다
    became_visible = Signal()

    def __init__(self, app, advanced, tools, guide):
        super().__init__(None)
        self._app = app
        self._needs_flush = True            # 보이게 되는 전환에서만 became_visible
        self._hidden_tools = []             # 이 창을 숨길 때 함께 감춘 도구 창
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowTitle(EXPERT_TITLE)
        self.setMinimumSize(900, 600)

        self.busy_banner = QLabel(BUSY_TEXT)
        self.busy_banner.setAlignment(Qt.AlignCenter)
        self.busy_banner.setStyleSheet(f"background: {ORANGE}; color: {BG}; font-weight: bold; padding: 4px;")
        self.busy_banner.hide()

        self.tabs = QTabWidget()
        self.tabs.addTab(advanced, ADVANCED_TAB)
        self.tabs.addTab(tools, TOOLS_TAB)
        self.tabs.addTab(guide, HELP_TAB)

        log_view = QTextEdit()
        log_view.setPlaceholderText('전문 분석 기록 (진단·Positioning Log·보고서·내보내기)')
        log_view.setMinimumHeight(40)
        self.log = SystemLogger(log_view)
        self.log_view = log_view
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(log_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 90])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.busy_banner)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

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
