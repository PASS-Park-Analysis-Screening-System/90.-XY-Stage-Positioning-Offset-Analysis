"""
expert_controller.py — XY 전문 분석 (QC 엔지니어) 창: 숨은 입구, 한 창, 보임 판단, 위치 기억

작업자 화면(메인 창)에는 Basic Analysis·Interactive·Comparison·Remark만 둔다. Advanced 탭(Pareto, Correlation,
3D Surface, Positioning Log, Diagnosis)과 원인 분석 도구는 전문 분석 창(ui/dialogs/expert_window.py)에 있다.
Advanced 탭은 지금처럼 _build_ui가 같은 mixin으로 만들고, 이 창으로 옮겨 담을 뿐이다
(self._inner_tabs['Advanced']는 그대로 그 QTabWidget). 그래서 Step·회차·Die 필터를 바꾸면 예전과 같은 경로
(_display_step → _update_charts·_update_positioning_log·_update_diagnosis)로 이 창의 내용이 바뀐다.

- 입구: 상태 표시줄의 버전 글자를 3초 안에 5번 클릭 (비밀번호·툴팁·커서 변화·메뉴 없음, System Log에도 남기지 않음)
- 한 창: 다시 들어오면 있는 창을 앞으로 가져온다. 닫으면 숨기기만 한다 (데이터·캐시는 그대로)
- 보임: Advanced 탭 하나가 '보인다' = 전문 분석 창이 열려 있고(보이고 최소화되지 않음) 그 창에서 Advanced가 앞에 있고
  그 안에서 그 탭이 앞에 있다 (_advanced_tab_visible 하나). Positioning Log·Diagnosis의 Log 읽기, 진단 계산,
  미뤄 둔 차트, 진단이 시작하는 Positioning Log 읽기가 모두 이것으로 판단한다. 창이 보이게 되거나(열기·최소화에서
  돌아옴·활성화) 바깥·안쪽 탭을 바꾸면 기존 flush(_flush_positioning_chart, _flush_diagnosis)를 부른다.
  창이 닫혀 있거나 최소화돼 있으면 새 Log 읽기·진단 계산·차트 그리기를 시작하지 않는다.
- 위치: 사용자 설정 expert_window_geometry (window_geometry와 같은 "WxH+X+Y", 소스 실행은 settings.local.json,
  빌드본은 레지스트리). 번들 settings.json에는 넣지 않는다.
"""
import time

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.dialogs.expert_guide import EXPERT_GUIDE
from ui.dialogs.expert_window import ExpertWindow
from ui.dialogs.guide_dialog import GUIDE_STYLE, GuideBrowser
from ui.dialogs.image_shift_dialog import open_image_shift_dialog
from ui.theme import ACCENT, GREEN, ORANGE, PURPLE

EXPERT_CATEGORY = 'Advanced'                # 메인 창 _inner_tabs의 키 — 이 탭 묶음을 전문 분석 창에 담는다
EXPERT_GEOMETRY_KEY = 'expert_window_geometry'
UNLOCK_CLICKS = 5
UNLOCK_SECONDS = 3.0


class ExpertMixin:
    _expert_window = None
    _expert_geometry_restored = False
    _expert_clicks = ()         # 버전 글자 클릭 시각 (time.monotonic, 지난 3초만)

    def _build_expert_window(self):
        """_build_ui 끝에서 부른다: 이미 만든 Advanced 탭 + 도구 + 도움말을 담은 창을 숨긴 채 만든다."""
        tools = self._build_action_page('도구', '원인 분석용 도구입니다. 결과 창·파일 대화상자는 이 창 위에 열립니다.', [
            ('AI 분석용 Markdown', 'Spec 초과 원인 분석을 LLM에 맡기기 위한 Markdown 한 파일\n'
             '요약·원인 단서·회차 비교·Die×Repeat 편차 표 포함',
             lambda: self._export_llm_markdown(parent=self._expert_dialog_host()), PURPLE),
            ('이미지 2장 이동량 분석', 'Capture 이미지 2장(이상)의 이동량을 sub-pixel로 재서 카메라 축 각도·배율 계산 (별도 창)',
             lambda: open_image_shift_dialog(self._expert_dialog_host()), ACCENT),
            ('3D 트러블슈팅 보고서', '진단 결과로 장비 3D·Die 화살표·What-if가 든 HTML 한 파일을 만들어 기본 브라우저로 열기',
             lambda: self._open_diagnosis_report(), GREEN),
            ('사례 라이브러리', '끝난 조사 사례(HTML 페이지 모음)를 기본 브라우저로 열기',
             lambda: self._open_case_library(), PURPLE),
            ('스캐너 회전 시험 분석', '폐루프 2지점 스윕 로그(또는 PQC Non-linearity 시트)로 스캐너 회전 κ(µrad/µm)를 계산해 '
             '잠정 규격으로 판정 (별도 창)', lambda: self._open_rotation_test_dialog(), ORANGE),
        ])
        guide = QWidget()
        guide.setStyleSheet(GUIDE_STYLE)
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(16, 16, 16, 16)
        self.expert_guide = GuideBrowser(EXPERT_GUIDE)
        guide_layout.addWidget(self.expert_guide)

        expert = ExpertWindow(self, self._inner_tabs[EXPERT_CATEGORY], tools, guide)
        expert.became_visible.connect(self._flush_advanced_tabs)
        expert.tabs.currentChanged.connect(self._flush_advanced_tabs)
        self._expert_window = expert

    # ── 입구 ─────────────────────────────────────────

    def _on_version_label_event(self, event):
        """상태 표시줄 버전 글자의 마우스 이벤트 (main.eventFilter가 넘긴다). 3초 안에 5번 클릭하면 연다.

        빠른 두 번째 클릭은 Press 대신 DblClick으로 오므로 둘 다 센다."""
        if event.type() not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) \
                or event.button() != Qt.LeftButton:
            return
        now = time.monotonic()
        clicks = [t for t in self._expert_clicks if now - t <= UNLOCK_SECONDS] + [now]
        if len(clicks) >= UNLOCK_CLICKS:
            clicks = []
            self._open_expert_window()
        self._expert_clicks = clicks

    def _open_expert_window(self):
        """전문 분석 창을 연다. 이미 있으면 그 창을 (최소화였으면 되살려) 앞으로 가져온다 — 창은 하나."""
        expert = self._expert_window
        if not self._expert_geometry_restored:
            self._expert_geometry_restored = True
            self._restore_geometry(expert, EXPERT_GEOMETRY_KEY)
        if expert.isMinimized():
            expert.setWindowState(expert.windowState() & ~Qt.WindowMinimized)
        expert.show()
        expert.raise_()
        expert.activateWindow()

    def _close_expert_window(self):
        """메인 창을 닫을 때 (위치를 기억하고 숨긴다)."""
        if self._expert_window is not None:
            self._expert_window.close()

    def _remember_expert_geometry(self):
        """전문 분석 창 위치·크기를 사용자 설정에 둔다 (저장은 메인 창의 _save_settings)."""
        expert = self._expert_window
        if expert is None:
            return
        rect = (expert.normalGeometry() if expert.isMinimized() or expert.isMaximized()
                else expert.geometry())
        if rect.width() > 0 and rect.height() > 0:
            self.settings[EXPERT_GEOMETRY_KEY] = f"{rect.width()}x{rect.height()}+{rect.x()}+{rect.y()}"

    def _expert_dialog_host(self):
        """도구·진단 버튼이 여는 대화상자·메시지의 부모: 전문 분석 창 (그 위에 쌓인다)."""
        return self._expert_window if self._expert_window is not None else self

    # ── 보임 ─────────────────────────────────────────

    def _advanced_tab_visible(self, name):
        """Advanced 탭 name이 지금 보이는가 — 전문 분석 창이 열려 있고(보이고 최소화 아님) 그 창에서 Advanced가
        앞에 있고 그 안에서 name 탭이 앞에 있다. Log 읽기·진단 계산·미뤄 둔 차트는 모두 이것으로 판단한다."""
        expert = self._expert_window
        tabs = getattr(self, '_inner_tabs', {}).get(EXPERT_CATEGORY)
        return (expert is not None and tabs is not None
                and expert.isVisible() and not expert.isMinimized()
                and expert.tabs.currentWidget() is tabs
                and tabs.tabText(tabs.currentIndex()) == name)

    def _flush_advanced_tabs(self, *_args):
        """전문 분석 창이 보이게 됐거나 탭을 바꿨다: 보이는 탭의 미뤄 둔 Log 읽기·진단·차트를 시작한다."""
        self._flush_positioning_chart()
        self._flush_diagnosis()
