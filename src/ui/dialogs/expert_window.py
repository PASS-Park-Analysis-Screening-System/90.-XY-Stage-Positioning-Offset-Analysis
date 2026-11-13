"""
expert_window.py — XY 전문 분석 (QC 엔지니어) 창

작업자 화면에서 뺀 심화 분석을 담는 비모달 창이다. 메인 창이 만든 위젯을 그대로 옮겨 담는다
(분석 코드를 따로 두지 않는다):
- 'Advanced': 메인 창 mixin이 만든 Advanced QTabWidget (Pareto, Correlation, 3D Surface, Positioning Log,
  Diagnosis (트러블슈팅)) — 메인 창의 _inner_tabs['Advanced']가 그대로 가리킨다
- '도구': AI 분석용 Markdown, 이미지 2장 이동량 분석, 3D 트러블슈팅 보고서, 사례 라이브러리, 스캐너 회전 시험 분석
- '도움말': expert_guide.EXPERT_GUIDE

여는 방법·보임 판단·위치 기억은 ui/controllers/expert_controller.py (docs/EXPERT_MODE.md).
닫으면 숨기기만 한다 (읽은 Log·진단·도구 창은 그대로). 메인 창을 닫으면 함께 닫힌다.

도구의 대화상자(이미지 이동량·회전 시험)와 3D 보고서·사례 라이브러리 메시지는 이 창을 부모로 연다 (이 창 위에 쌓인다).
그 기능들이 부모 창에서 찾는 값(settings, logger, _xy_diagnosis, _rotation_test_result, set_rotation_test)은
메인 창의 것을 그대로 넘긴다 — 진단·회전 시험 결과는 메인 창 하나에만 있다.
"""

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QMainWindow, QTabWidget

EXPERT_TITLE = 'XY 전문 분석 (QC 엔지니어)'
ADVANCED_TAB = 'Advanced'
TOOLS_TAB = '도구'
HELP_TAB = '도움말'


class ExpertWindow(QMainWindow):
    # 보이게 됐다: 처음·다시 열기, 최소화에서 돌아옴, 활성화. 미뤄 둔 Log 읽기·진단·차트를 시작한다
    became_visible = Signal()

    def __init__(self, app, advanced, tools, guide):
        super().__init__(app)       # 메인 창 소유 — 메인 창 위에 뜨고, 메인 창과 함께 사라진다
        self._app = app
        self.setWindowTitle(EXPERT_TITLE)
        self.setMinimumSize(900, 600)
        self.tabs = QTabWidget()
        self.tabs.addTab(advanced, ADVANCED_TAB)
        self.tabs.addTab(tools, TOOLS_TAB)
        self.tabs.addTab(guide, HELP_TAB)
        self.setCentralWidget(self.tabs)

    # ── 보임 ──────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.became_visible.emit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange or (
                event.type() == QEvent.ActivationChange and self.isActiveWindow()):
            if self.isVisible() and not self.isMinimized():
                self.became_visible.emit()

    def closeEvent(self, event):
        if self.isVisible():
            self._app._remember_expert_geometry()
        super().closeEvent(event)       # 숨기기만 한다 (WA_DeleteOnClose 아님)

    # ── 도구가 부모 창에서 찾는 값: 메인 창의 것 ─────────────────

    @property
    def settings(self):
        return self._app.settings

    @property
    def logger(self):
        return self._app.logger

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
