"""
expert_controller.py — XY 전문 분석 (QC 엔지니어) 창: 숨은 입구, 한 창, 보임 판단, 위치 기억

작업자 화면(메인 창)에는 Basic Analysis·Interactive·Comparison·Remark만 둔다. Advanced(Pareto, Correlation,
3D Surface, Positioning Log, Diagnosis)와 원인 분석 도구는 전문 분석 창(ui/dialogs/expert_window.py)의 왼쪽 메뉴 쪽이다.
쪽의 위젯은 지금처럼 같은 mixin이 만들고(_build_ui의 차트, _build_positioning_widget, _build_diagnosis_pages) 이 창에
옮겨 담을 뿐이라 chart_widgets 등은 그대로다. 그래서 Step·회차·Die 필터를 바꾸면 예전과 같은 경로
(_display_result → _update_charts·_update_positioning_log·_update_diagnosis)로 이 창의 내용이 바뀐다.

- 입구: 상태 표시줄의 버전 글자를 3초 안에 5번 클릭 (비밀번호·툴팁·커서 변화·메뉴 없음, System Log에도 남기지 않음)
- 한 창: 다시 들어오면 있는 창을 앞으로 가져온다. 닫으면 숨기기만 한다 (데이터·캐시는 그대로)
- 보임: 전문 분석 창이 '열려 있다' = 창이 보이고 최소화되지 않았고 메인 창도 최소화되지 않았다
  (_expert_window_open). 쪽 하나가 '보인다' = 창이 열려 있고 왼쪽 메뉴에서 그 쪽이 골라져 있다 (_advanced_tab_visible;
  'Diagnosis (트러블슈팅)'은 진단 쪽 넷 중 어느 것이든). Positioning Log·Diagnosis의 Log 읽기, 진단 계산, 미뤄 둔 차트, 진단이 시작하는
  Positioning Log 읽기가 모두 이것으로 판단하고, Pareto·Correlation·3D는 창이 열려 있을 때 그린다.
  창이 닫혀 있거나 최소화돼 있으면 새 Log 읽기·진단 계산·차트 그리기를 시작하지 않는다 (이미 시작한 읽기는 끝난다).
- 다시 시작: 창이 숨김·최소화에서 보이게 되거나(활성화만으로는 아님) 메뉴에서 다른 쪽을 고르면 _retry_advanced_reads.
  읽기 오류가 난 회차(회차 키, 결과 세대)는 이때만 다시 읽는다 — Step·Die 필터 갱신이나 창 전환으로는 다시 읽지 않는다.
- 위치: 사용자 설정 expert_window_geometry에 saveGeometry()를 base64로 (최대화·화면 밖 위치는 Qt가 처리, 예전
  "WxH+X+Y"도 읽는다). 소스 실행은 settings.local.json, 빌드본은 레지스트리, 번들 settings.json에는 넣지 않는다.
  저장값이 없으면 처음에 메인 창이 있는 화면에서 메인 창 옆에, 자리가 없으면 비켜 겹쳐 둔다.
- 메인 창이 데이터를 읽는 동안(_busy_begin~_busy_end) 창 위쪽에 안내 띠를 보이고 도구 버튼·진단 저장/보고서 버튼을 끈다.
- 전문 기능의 메시지는 이 창의 기록(self.expert_logger)에 남긴다. 작업자 System Log는 Excel·Quality Report로 나간다.
"""
import time

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from core.settings import parse_geometry_string
from ui.dialogs.expert_guide import EXPERT_GUIDE
from ui.controllers.diagnosis_controller import DIAGNOSIS_TAB
from ui.controllers.positioning_controller import POSITIONING_TAB
from ui.dialogs.expert_window import HELP_PAGE, LOG_PAGE, TOOLS_PAGE, ExpertWindow
from ui.dialogs.guide_dialog import GUIDE_STYLE, GuideBrowser
from ui.dialogs.image_shift_dialog import open_image_shift_dialog
from ui.theme import ACCENT, GREEN, ORANGE, PURPLE

GROUP_ANALYSIS = '분석'
GROUP_TRACE = '원인 추적'
GROUP_TOOLS = '도구 · 기록'
EXPERT_GEOMETRY_KEY = 'expert_window_geometry'
UNLOCK_CLICKS = 5
UNLOCK_SECONDS = 3.0
DEFAULT_SIZE = (1400, 900)                  # 처음 열 때 (화면에 맞춰 줄인다)
CASCADE = 48                                # 메인 창 옆에 자리가 없으면 이만큼 비켜 겹친다


class ExpertMixin:
    _expert_window = None
    _expert_geometry_restored = False
    _expert_clicks = ()         # 버전 글자 클릭 시각 (time.monotonic, 지난 3초만)
    _expert_busy = False        # 메인 창이 데이터를 읽는 중
    _expert_tool_buttons = ()
    _advanced_charts_pending = None     # 창이 닫혀 있는 동안 미룬 Pareto·Correlation·3D 그리기

    def _build_expert_window(self):
        """_build_ui 끝에서 부른다: 왼쪽 메뉴 + 쪽(분석 차트, Positioning Log, 진단 넷, 도구, 도움말, 기록)을 담은
        창을 숨긴 채 만든다. 분석 차트는 _build_ui가 만든 것(self._expert_chart_pages)을 옮겨 담는다."""
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
        ], columns=3)
        self._expert_tool_buttons = tools.findChildren(QPushButton)
        guide = QWidget()
        guide.setStyleSheet(GUIDE_STYLE)
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(16, 16, 16, 16)
        self.expert_guide = GuideBrowser(EXPERT_GUIDE)
        guide_layout.addWidget(self.expert_guide)

        expert = ExpertWindow(self)
        self.expert_logger = expert.log
        expert.add_group(GROUP_ANALYSIS)
        for name, widget in self._expert_chart_pages:
            expert.add_page(name, widget, GROUP_ANALYSIS)
        expert.add_group(GROUP_TRACE)
        expert.add_page(POSITIONING_TAB, self._build_positioning_widget(), GROUP_TRACE)
        diag_bar, diag_pages = self._build_diagnosis_pages()
        expert.add_group(DIAGNOSIS_TAB, parent=GROUP_TRACE)
        for name, widget in diag_pages:
            expert.add_page(name, widget, DIAGNOSIS_TAB, section=DIAGNOSIS_TAB)
        expert.set_section_bar(DIAGNOSIS_TAB, diag_bar)
        expert.add_group(GROUP_TOOLS)
        expert.add_page(TOOLS_PAGE, tools, GROUP_TOOLS)
        expert.add_page(HELP_PAGE, guide, GROUP_TOOLS)
        expert.add_page(LOG_PAGE, expert.log_view, GROUP_TOOLS)
        expert.select_page(self._expert_chart_pages[0][0])
        expert.became_visible.connect(self._retry_advanced_reads)
        expert.page_changed.connect(self._retry_advanced_reads)
        # 메인 창 단축키를 이 창에서도: F11 왼쪽 칸 접기, Esc 진행 중 중지 (켜고 끄기는 _set_scan_controls_enabled)
        QShortcut(QKeySequence('F11'), expert).activated.connect(self._toggle_left_panel)
        self._expert_cancel_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), expert)
        self._expert_cancel_shortcut.activated.connect(self._cancel_scan)
        self._expert_cancel_shortcut.setEnabled(False)
        # 부모가 없는 창이라 메인 창이 지워질 때 함께 지운다 (테스트 등에서 메인 창만 지울 때)
        self.destroyed.connect(expert.deleteLater)
        self._expert_window = expert

    # ── 입구 ─────────────────────────────────────────

    def _on_version_label_event(self, event):
        """상태 표시줄 버전 글자의 마우스 이벤트 (main.eventFilter가 넘긴다). 3초 안에 5번 클릭하면 연다.

        빠른 두 번째 클릭은 Press 대신 DblClick으로 오므로 둘 다 센다 (왼쪽 단추만)."""
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
        """전문 분석 창을 연다. 이미 있으면 그 창을 (최소화였으면 되살려) 앞으로 가져온다 — 창은 하나.
        숨길 때 함께 감춘 도구 창도 다시 보인다."""
        expert = self._expert_window
        if not self._expert_geometry_restored:
            self._expert_geometry_restored = True
            self._restore_expert_geometry()
        if expert.isMinimized():
            expert.setWindowState(expert.windowState() & ~Qt.WindowMinimized)
        expert.show()
        expert.raise_()
        expert.activateWindow()
        expert.show_hidden_tools()

    def _close_expert_window(self):
        """메인 창을 닫을 때 (위치를 기억하고 숨긴다. 함께 감춘 도구 창도)."""
        if self._expert_window is not None:
            self._expert_window.close()

    # ── 위치 ─────────────────────────────────────────

    def _remember_expert_geometry(self):
        """전문 분석 창 위치·크기·최대화를 사용자 설정에 둔다 (저장은 메인 창의 _save_settings)."""
        expert = self._expert_window
        if expert is not None:
            self.settings[EXPERT_GEOMETRY_KEY] = bytes(expert.saveGeometry().toBase64()).decode('ascii')

    def _restore_expert_geometry(self):
        """처음 열 때: 저장한 geometry(base64, 예전 "WxH+X+Y"도), 없거나 못 쓰면 메인 창 옆."""
        expert = self._expert_window
        saved = self.settings.get(EXPERT_GEOMETRY_KEY) or ''
        if parse_geometry_string(saved) is not None:
            self._restore_geometry(expert, EXPERT_GEOMETRY_KEY)     # 예전 형식: 화면 안으로 클램프
            return
        if saved:
            try:
                data = QByteArray.fromBase64(saved.encode('ascii'))
            except UnicodeEncodeError:
                data = QByteArray()
            if not data.isEmpty() and expert.restoreGeometry(data):
                return
        self._place_expert_beside_main()

    def _place_expert_beside_main(self):
        """메인 창이 있는 화면에서 메인 창 오른쪽(또는 왼쪽)에. 자리가 없으면 메인 창에서 비켜 겹친다."""
        expert = self._expert_window
        screen = (QGuiApplication.screenAt(self.frameGeometry().center()) or self.screen()
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return
        avail = screen.availableGeometry()
        main = self.frameGeometry()
        # 창 테두리·제목 줄 두께는 메인 창 것으로 어림한다 (아래 W·H는 테두리를 포함한 바깥 크기)
        fw, fh = max(0, main.width() - self.width()), max(0, main.height() - self.height())
        min_w, min_h = expert.minimumWidth() + fw, expert.minimumHeight() + fh
        if avail.width() < min_w or avail.height() < min_h:
            expert.setWindowState(Qt.WindowMaximized)
            return
        w = max(min_w, min(DEFAULT_SIZE[0] + fw, avail.width()))
        h = max(min_h, min(max(main.height(), DEFAULT_SIZE[1] + fh), avail.height()))
        y = max(avail.top(), min(main.top(), avail.top() + avail.height() - h))
        if main.right() + 1 + w <= avail.left() + avail.width():
            x = main.right() + 1
        elif main.left() - w >= avail.left():
            x = main.left() - w
        else:
            h = max(min_h, min(h, avail.height() - CASCADE))
            x = max(avail.left(), min(main.left() + CASCADE, avail.left() + avail.width() - w))
            y = max(avail.top(), min(main.top() + CASCADE, avail.top() + avail.height() - h))
        expert.resize(w - fw, h - fh)
        expert.move(QPoint(x, y))

    # ── 메인 창이 데이터를 읽는 중 ─────────────────────────

    def _set_expert_busy(self, busy):
        """_busy_begin·_busy_end가 부른다: 안내 띠, 도구 버튼·진단 저장/보고서 버튼 끄기·켜기."""
        self._expert_busy = bool(busy)
        if self._expert_window is not None:
            self._expert_window.set_busy(busy)
        for btn in self._expert_tool_buttons:
            btn.setEnabled(not busy)
        sync = getattr(self, '_sync_diag_buttons', None)
        if sync is not None:
            sync()

    def _expert_dialog_host(self):
        """도구·진단 버튼이 여는 대화상자·메시지의 부모: 전문 분석 창 (그 위에 쌓인다)."""
        return self._expert_window if self._expert_window is not None else self

    # ── 보임 ─────────────────────────────────────────

    def _expert_window_open(self):
        """전문 분석 창이 보이고 최소화되지 않았고, 메인 창도 최소화되지 않았다."""
        expert = self._expert_window
        return (expert is not None and expert.isVisible() and not expert.isMinimized()
                and not self.isMinimized())

    def _advanced_tab_visible(self, name):
        """쪽 name이 지금 보이는가 — 창이 열려 있고(_expert_window_open) 왼쪽 메뉴에서 그 쪽이 골라져 있다.
        name이 묶음(Diagnosis (트러블슈팅))이면 그 묶음의 쪽 중 하나. Log 읽기·진단 계산·미뤄 둔 차트는 모두 이것으로 판단한다."""
        if not self._expert_window_open():
            return False
        current = self._expert_window.current_page()
        return current == name or self._expert_window.section_of(current) == name

    def _set_expert_page_status(self, name, status):
        """메뉴 쪽의 주의·이상 점 (diagnosis_controller가 진단을 그릴 때)."""
        if self._expert_window is not None:
            self._expert_window.set_page_status(name, status)

    def _retry_advanced_reads(self, *_args):
        """창이 다시 보이게 됐거나 메뉴에서 쪽을 바꿨다 (사용자의 명시적 동작): 읽기 오류가 난 회차도 다시 읽고 flush."""
        for name in ('_poslog_failed', '_diag_failed'):
            failed = self.__dict__.get(name)
            if failed:
                failed.clear()
        self._flush_advanced_tabs()

    def _flush_advanced_tabs(self, *_args):
        """보이는 탭의 미뤄 둔 차트·Log 읽기·진단을 시작한다 (창이 열려 있을 때만 무엇이든 한다)."""
        self._flush_advanced_charts()
        self._flush_positioning_chart()
        self._flush_diagnosis()

    def _flush_advanced_charts(self):
        draw = self._advanced_charts_pending
        if draw is not None and self._expert_window_open():
            self._advanced_charts_pending = None
            draw()
