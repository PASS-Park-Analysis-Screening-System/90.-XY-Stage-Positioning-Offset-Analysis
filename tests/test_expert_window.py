"""전문 분석 창 (XY 전문 분석 (QC 엔지니어)) — 작업자 화면에서 심화 분석을 숨긴다.

사용자 결정 (2026-11-13): Advanced 카테고리 전체(Pareto, Correlation, 3D Surface, Positioning Log, Diagnosis)와
원인 분석 도구 5개(AI 분석용 Markdown, 이미지 2장 이동량 분석, 3D 트러블슈팅 보고서, 사례 라이브러리,
스캐너 회전 시험 분석)는 메인 창에서 빼고 비모달 전문 분석 창에 둔다. 입구는 상태 표시줄 버전 글자를
3초 안에 5번 클릭 (비밀번호·툴팁·커서 변화·메뉴 없음). 창은 하나, 닫으면 숨기기만, 위치는 사용자 설정.
"""
import json
import os
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import show_expert_tab  # noqa: E402
from core.recipe_scanner import compare_recipes, load_all_recipes, recipes_from_mapping  # noqa: E402
from test_run_comparison import _make_runs  # noqa: E402

SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 1.0},
            'Global Align': {'spec_range': 2.0, 'spec_stddev': 1.0}}
TOOLS = ['AI 분석용 Markdown', '이미지 2장 이동량 분석', '3D 트러블슈팅 보고서', '사례 라이브러리',
         '스캐너 회전 시험 분석']
ADVANCED = ['Pareto', 'Correlation', '3D Surface', 'Positioning Log', 'Diagnosis (트러블슈팅)']
DIAGNOSIS = 'Diagnosis (트러블슈팅)'


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    import main
    import ui.controllers.scan_controller as controller
    from PySide6.QtWidgets import QMessageBox
    saved = []
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=dict(SPEC_DEV)))
    monkeypatch.setattr(main, 'save_settings', lambda s: saved.append(dict(s)))
    monkeypatch.setattr(controller, 'add_recent_folder', lambda s, _f: s)
    for name in ('warning', 'information', 'critical'):     # 모달 대화상자가 테스트를 멈추지 않게
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    win = main.DataAnalyzerApp()
    win._test_saved = saved
    yield win
    for thread in win._background_threads():
        if hasattr(thread, 'cancel'):
            thread.cancel()
        thread.wait(10000)
    win._loader_thread = None
    win.deleteLater()
    app.processEvents()


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from ui.controllers import expert_controller
    fake = Clock()
    monkeypatch.setattr(expert_controller, 'time', types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


def _click(window, button=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(window.version_status_label, button or Qt.LeftButton)


def _send(window, kind):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    event = QMouseEvent(kind, QPointF(3, 3), QPointF(3, 3), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window.version_status_label, event)


def _wait_for(app, condition, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, '기다리던 상태가 오지 않았습니다'
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _loaded(window, tmp_path, workflows=(('Vision Pattern', ['1', '2']), ('Global Align', ['1', '2']))):
    mapping = [(name, str(_make_runs(tmp_path / name.replace(' ', '_'), runs, lots=3)))
               for name, runs in workflows]
    recipes, _ = recipes_from_mapping(mapping, list(SPEC_DEV))
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    window._loader_thread = type('Done', (), {'folder': str(tmp_path)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    return results


def _buttons(widget):
    from PySide6.QtWidgets import QPushButton
    return [b.text() for b in widget.findChildren(QPushButton)]


# ── 작업자 화면: Advanced·도구가 없다 ─────────────────────────────────────

def test_main_window_has_no_advanced_category(window):
    from PySide6.QtCore import Qt
    tabs = window.chart_category_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ['Basic Analysis', 'Interactive',
                                                             'Comparison', 'Remark']
    advanced = window._inner_tabs['Advanced']
    assert tabs.indexOf(advanced) == -1 and not window.isAncestorOf(advanced)     # 다른 창에 있다
    expert = window._expert_window
    assert advanced.window() is expert and expert.parent() is window
    assert expert.windowTitle() == 'XY 전문 분석 (QC 엔지니어)'
    assert [expert.tabs.tabText(i) for i in range(expert.tabs.count())] == ['Advanced', '도구', '도움말']
    assert [advanced.tabText(i) for i in range(advanced.count())] == ADVANCED
    assert not expert.isVisible()                                    # 처음에는 숨어 있다
    # 숨은 입구: 버전 글자에 툴팁·커서 변화가 없고, 메뉴도 없다
    label = window.version_status_label
    assert label.toolTip() == '' and not label.testAttribute(Qt.WA_SetCursor)
    assert label.cursor().shape() == Qt.ArrowCursor
    assert not [a for a in window.menuBar().actions()]


def test_export_list_has_exactly_the_four_operator_buttons(window):
    from PySide6.QtWidgets import QPushButton
    remark = window._inner_tabs['Remark']
    assert _buttons(remark.widget(0)) == ['Excel Export', 'Quality Report', 'CSV Export', 'PDF Report']
    assert remark.tabText(1) == 'Help'
    # 메인 창(전문 분석 창 밖) 어디에도 도구 버튼이 없다
    operator = [b.text() for b in window.findChildren(QPushButton) if b.window() is window]
    assert not set(TOOLS) & set(operator)
    assert _buttons(window._expert_window.tabs.widget(1)) == TOOLS


def test_operator_guide_does_not_mention_expert_features(window):
    from ui.dialogs.expert_guide import EXPERT_GUIDE
    from ui.dialogs.guide_dialog import GuideDialog
    dlg = GuideDialog(window)
    text = ' '.join(dlg.contents) + ' '.join(dlg.contents.values())
    for word in ('Advanced', 'Positioning Log', 'Diagnosis', '트러블슈팅 진단', 'AI 분석용', 'Markdown', '이미지 2장',
                 '3D 트러블슈팅', '3D 보고서', '회전 시험', '사례 라이브러리', 'Correlation', '전문 분석'):
        assert word not in text, word
    assert dlg.nav_list.count() == len(dlg.contents)
    dlg.deleteLater()
    guide = window.expert_guide                                       # 전문 분석 창 '도움말'
    assert list(guide.contents) == list(EXPERT_GUIDE)
    expert_text = ' '.join(EXPERT_GUIDE.values())
    for word in ('3초 안에 5번', 'Positioning Log', 'Diagnosis', 'AI 분석용 Markdown', '이미지 2장',
                 '3D 트러블슈팅 보고서', '스캐너 회전 시험', 'Correlation'):
        assert word in expert_text, word


# ── 숨은 입구: 3초 안에 5번 ────────────────────────────────────────────

def test_five_clicks_within_three_seconds_open_the_window(window, clock):
    expert = window._expert_window
    for t in (0.0, 0.5, 1.0, 2.0):
        clock.now = 1000.0 + t
        _click(window)
    assert not expert.isVisible()                    # 4번은 열지 않는다
    clock.now = 1003.0                               # 첫 클릭에서 3.0초
    _click(window)
    assert expert.isVisible() and not expert.isMinimized()


def test_four_clicks_or_five_spread_over_more_than_three_seconds_do_not_open(window, clock):
    from PySide6.QtCore import Qt
    expert = window._expert_window
    for t in (0.0, 0.1, 0.2, 0.3):
        clock.now = 1000.0 + t
        _click(window)
    assert not expert.isVisible()
    clock.now = 1010.0                               # 지난 클릭은 3초가 지나 잊는다
    for t in (0.0, 0.8, 1.6, 2.4, 3.2):              # 5번이지만 3.2초에 걸침
        clock.now = 1010.0 + t
        _click(window)
    assert not expert.isVisible()
    clock.now = 1020.0
    for _ in range(5):                               # 오른쪽 클릭은 세지 않는다
        _click(window, Qt.RightButton)
    assert not expert.isVisible()


def test_fast_clicks_arriving_as_double_clicks_are_counted(window, clock):
    """빠른 두 번째 클릭은 Press 대신 DblClick으로 온다 (Press, Release, DblClick, Release)."""
    from PySide6.QtCore import QEvent
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                 QEvent.MouseButtonRelease) * 2 + (QEvent.MouseButtonPress,):
        clock.now += 0.1
        _send(window, kind)
    assert window._expert_window.isVisible()


def test_reentering_raises_the_single_existing_window(window, clock):
    from ui.dialogs.expert_window import ExpertWindow
    expert = window._expert_window

    def five():
        for _ in range(5):
            clock.now += 0.2
            _click(window)
    five()
    assert expert.isVisible()
    expert.showMinimized()
    five()
    assert window._expert_window is expert and expert.isVisible() and not expert.isMinimized()
    expert.close()
    assert not expert.isVisible()                    # 닫기는 숨기기만
    five()
    assert window._expert_window is expert and expert.isVisible()
    assert len(window.findChildren(ExpertWindow)) == 1


# ── 데이터: 메인 창의 Step·회차·Die 필터를 따른다 ───────────────────────────

def test_expert_window_shows_and_follows_the_main_step_run_and_die_filter(app, window, tmp_path):
    _loaded(window, tmp_path)
    show_expert_tab(window, DIAGNOSIS)
    _wait_for(app, lambda: window._xy_diagnosis is not None)
    assert window.diag_label.text().startswith('Vision Pattern 회차 2')
    assert window.chart_widgets['3D X'].get_widget().title.startswith('Vision Pattern')

    window._select_step(1)                           # 메인 창에서 Step을 바꾼다
    _wait_for(app, lambda: (window._xy_diagnosis or {}).get('source', {}).get('workflow') == 'Global Align')
    assert window.diag_label.text().startswith('Global Align 회차 2')
    assert window.chart_widgets['3D X'].get_widget().title.startswith('Global Align')
    assert window._diag_result['raw_data'] is window.recipe_results[1]['raw_data']

    window.run_combo.setCurrentIndex(0)              # 회차를 바꾼다
    _wait_for(app, lambda: (window._xy_diagnosis or {}).get('source', {}).get('run') == '1')
    assert window.diag_label.text().startswith('Global Align 회차 1')

    dies = len(window.chart_widgets['3D X'].get_widget().die_stats)
    checked = [d for d, cb in window._die_checkboxes.items() if cb.isChecked()]
    window._die_checkboxes[checked[-1]].setChecked(False)          # Die 필터
    assert len(window.chart_widgets['3D X'].get_widget().die_stats) == dies - 1
    current = window._diag_result                    # 읽기 스레드가 끝난 뒤 닫는다
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current)


# ── 보임: 창이 닫혀 있거나 최소화돼 있으면 Log를 읽지 않는다 ─────────────────────

def test_lazy_reads_wait_for_the_open_expert_window(app, window, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from ui.controllers import diagnosis_controller, positioning_controller
    calls = {'diag': 0, 'poslog': 0}
    real_logs, real_analyze = diagnosis_controller.read_run_logs, positioning_controller.analyze_result

    def logs(*a, **k):
        calls['diag'] += 1
        return real_logs(*a, **k)

    def analyze(*a, **k):
        calls['poslog'] += 1
        return real_analyze(*a, **k)
    monkeypatch.setattr(diagnosis_controller, 'read_run_logs', logs)
    monkeypatch.setattr(positioning_controller, 'analyze_result', analyze)

    window._select_chart(DIAGNOSIS)                  # 창 안에서는 Diagnosis가 앞이지만 창이 닫혀 있다
    assert window._inner_tabs['Advanced'].tabText(window._inner_tabs['Advanced'].currentIndex()) == DIAGNOSIS
    _loaded(window, tmp_path)
    window._flush_advanced_tabs()
    app.processEvents()
    assert calls == {'diag': 0, 'poslog': 0}
    assert not window._diagnosis_tab_visible() and not window._positioning_wanted()
    assert window.diag_label.text() == '이 회차의 Log는 이 탭을 열 때 읽습니다'
    assert window._xy_diagnosis is None and 'xy_logs' not in window._diag_result

    expert = window._expert_window                   # 보이지만 최소화: 읽지 않는다
    expert.setWindowState(Qt.WindowMinimized)
    expert.show()
    window._select_step(1)
    app.processEvents()
    assert expert.isMinimized() and calls == {'diag': 0, 'poslog': 0}

    window._open_expert_window()                     # 되살리면 읽는다 (진단이 Positioning Log 읽기도 시작)
    current = window._diag_result
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current)
    assert calls == {'diag': 1, 'poslog': 1} and window._xy_diagnosis is not None

    expert.close()                                   # 닫으면 다른 회차를 읽지 않는다
    window.run_combo.setCurrentIndex(0)
    app.processEvents()
    other = window._diag_result
    assert other is not current and calls == {'diag': 1, 'poslog': 1} and 'xy_logs' not in other
    assert window._xy_diagnosis is None
    window._open_expert_window()
    _wait_for(app, lambda: 'xy_logs' in other and 'positioning_log' in other)
    assert calls == {'diag': 2, 'poslog': 2}


def test_positioning_chart_waits_for_the_open_window(app, window, tmp_path):
    analysis = {'status': 'ok', 'message': 'ok', 'lots': [], 'verdict': {}, 'tips': [],
                'tip_summary': None, 'mean': {'stages': []}}
    window._select_chart('Positioning Log')
    window._update_positioning_log({'raw_data': [{'x': 1}], 'round_path': 'p', 'positioning_log': analysis})
    assert window._poslog_chart_pending                # 창이 닫혀 있어 그리지 않았다
    window._open_expert_window()
    assert not window._poslog_chart_pending


# ── 도구 버튼: 예전과 같은 함수를 부르고, 창은 전문 분석 창 위에 ────────────────────

def test_each_tool_button_calls_the_same_function_as_before(window, monkeypatch):
    from ui.controllers import expert_controller
    from ui.dialogs import scanner_rotation_dialog, troubleshoot_launcher
    calls = []
    monkeypatch.setattr(window, '_export_llm_markdown', lambda parent=None: calls.append(('llm', parent)))
    monkeypatch.setattr(expert_controller, 'open_image_shift_dialog', lambda p: calls.append(('shift', p)))
    monkeypatch.setattr(troubleshoot_launcher, 'open_troubleshoot_report', lambda w: calls.append(('report', w)))
    monkeypatch.setattr(troubleshoot_launcher, 'open_case_library', lambda w: calls.append(('cases', w)))
    monkeypatch.setattr(scanner_rotation_dialog, 'open_scanner_rotation_dialog',
                        lambda p: calls.append(('rotation', p)))
    from PySide6.QtWidgets import QPushButton
    expert = window._expert_window
    buttons = {b.text(): b for b in expert.tabs.widget(1).findChildren(QPushButton)}
    for name in TOOLS:
        buttons[name].click()
    assert calls == [('llm', expert), ('shift', expert), ('report', expert), ('cases', expert),
                     ('rotation', expert)]
    calls.clear()                                    # Diagnosis 탭의 버튼도 같은 함수, 같은 부모
    window.diag_report_btn.setEnabled(True)
    for btn in (window.diag_report_btn, window.diag_rotation_btn, window.diag_cases_btn):
        btn.click()
    assert calls == [('report', expert), ('rotation', expert), ('cases', expert)]
    from PySide6.QtWidgets import QMessageBox                     # 진단 JSON 저장의 메시지도 이 창 위에
    warned = []
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: warned.append(a[0])))
    window._save_diagnosis_json()
    assert warned == [expert]


def test_tool_windows_use_the_main_window_data(window):
    expert = window._expert_window
    window._xy_diagnosis = {'schema': 'x'}
    assert expert._xy_diagnosis is window._xy_diagnosis and expert.settings is window.settings
    assert expert.logger is window.logger
    expert._rotation_test_result = {'kappa': 0.1}                  # 회전 시험 대화상자가 하는 일
    assert window._rotation_test_result == {'kappa': 0.1}
    expert.set_rotation_test({'kappa': 0.2})
    assert window._rotation_test_result == {'kappa': 0.2}


# ── 닫기: 메인 창과 함께, 위치는 사용자 설정에 ──────────────────────────────

def test_closing_the_main_window_closes_the_expert_window(app, window):
    expert = window._expert_window
    window.show()
    window._open_expert_window()
    assert expert.isVisible()
    window.close()
    assert not expert.isVisible()
    saved = window._test_saved[-1]                   # 닫을 때 저장한 설정에 위치가 있다
    assert saved['expert_window_geometry'] == window.settings['expert_window_geometry']


def test_geometry_is_restored_and_remembered_per_user(app, window):
    expert = window._expert_window
    expert.setMinimumSize(300, 200)                  # offscreen 화면(800×600)에서 클램프 경로를 쓴다
    window.settings['expert_window_geometry'] = '500x400+30+40'
    window._open_expert_window()
    g = expert.geometry()
    assert (g.width(), g.height(), g.x(), g.y()) == (500, 400, 30, 40)
    expert.setGeometry(60, 70, 520, 410)
    expert.close()
    assert window.settings['expert_window_geometry'] == '520x410+60+70'
    window._open_expert_window()                     # 다시 열면 그 자리 (다시 복원하지 않는다)
    assert expert.geometry().topLeft().x() == 60


def test_geometry_goes_to_user_settings_not_the_bundle(tmp_path, monkeypatch):
    from core import settings
    from test_settings_persistence import _FakeWinreg
    project = Path(__file__).resolve().parents[1]
    bundled_repo = json.loads((project / 'src' / 'core' / 'settings.json').read_text(encoding='utf-8'))
    assert 'expert_window_geometry' not in bundled_repo
    assert 'expert_window_geometry' in settings._USER_SETTING_KEYS

    bundled, local = tmp_path / 'settings.json', tmp_path / 'settings.local.json'
    bundled.write_text(json.dumps({'spec_deviation': {}}), encoding='utf-8')
    before = bundled.read_bytes()
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, 'LOCAL_SETTINGS_FILE', str(local))
    loaded = settings.load_settings()
    loaded['expert_window_geometry'] = '1200x800+10+20'
    settings.save_settings(loaded)                   # 소스 실행: settings.local.json
    assert json.loads(local.read_text(encoding='utf-8')) == {'expert_window_geometry': '1200x800+10+20'}
    assert bundled.read_bytes() == before
    assert settings.load_settings()['expert_window_geometry'] == '1200x800+10+20'

    fake = _FakeWinreg()                             # 빌드본: 레지스트리
    monkeypatch.setattr(settings, 'winreg', fake)
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    settings.save_settings({'expert_window_geometry': '900x700+0+0', 'spec_deviation': {}})
    assert fake.values['expert_window_geometry'] == ('900x700+0+0', fake.REG_SZ)
    assert settings._read_registry_settings() == {'expert_window_geometry': '900x700+0+0'}
