"""전문 분석 창 (XY 전문 분석 (QC 엔지니어)) — 작업자 화면에서 심화 분석을 숨긴다.

사용자 결정 (2026-11-13): Advanced 카테고리 전체(Pareto, Correlation, 3D Surface, Positioning Log, Diagnosis)와
원인 분석 도구 5개(AI 분석용 Markdown, 이미지 2장 이동량 분석, 3D 트러블슈팅 보고서, 사례 라이브러리,
스캐너 회전 시험 분석)는 메인 창에서 빼고 비모달 전문 분석 창에 둔다. 입구는 상태 표시줄 버전 글자를
3초 안에 5번 클릭 (비밀번호·툴팁·커서 변화·메뉴 없음). 창은 하나, 닫으면 숨기기만, 위치는 사용자 설정.

리뷰 반영 (9613465): 활성화만으로는 다시 읽지 않음(읽기 오류 회차는 탭 변경·다시 열기 때만), 부모 없는 창
(작업 표시줄·보통 창 순서, 처음엔 메인 창 옆), saveGeometry, 숨길 때 도구 창도 숨김, 메인 창 최소화 = 닫힘,
읽는 중 안내 띠, 전문 기능 메시지는 전문 분석 기록에, 창이 닫혀 있으면 Pareto·Correlation·3D도 미룸, F11·Esc.
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
# 작업자 화면(메인 창)·System Log에 나오면 안 되는 말
EXPERT_WORDS = ('Advanced', 'Pareto', 'Correlation', '3D Surface', 'Positioning Log', 'Diagnosis',
                '트러블슈팅 진단', 'AI 분석용', 'Markdown', '이미지 2장', '3D 트러블슈팅', '3D 보고서',
                '사례 라이브러리', '회전 시험', '전문 분석', 'QC 엔지니어', '진단 JSON')


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
    win._expert_window.close()
    win.deleteLater()
    app.processEvents()
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


def _send(window, kind, button=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    button = button or Qt.LeftButton
    event = QMouseEvent(kind, QPointF(3, 3), QPointF(3, 3), button, button, Qt.NoModifier)
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


def _counting_reads(monkeypatch, fail=False):
    """진단 Log·Positioning Log 읽기 횟수 (fail이면 읽기 오류 — 네트워크·잠김)."""
    from ui.controllers import diagnosis_controller, positioning_controller
    calls = {'diag': 0, 'poslog': 0}
    real_logs, real_analyze = diagnosis_controller.read_run_logs, positioning_controller.analyze_result

    def logs(*a, **k):
        calls['diag'] += 1
        if fail:
            raise OSError('네트워크 경로를 찾을 수 없음')
        return real_logs(*a, **k)

    def analyze(*a, **k):
        calls['poslog'] += 1
        if fail:
            raise OSError('네트워크 경로를 찾을 수 없음')
        return real_analyze(*a, **k)
    monkeypatch.setattr(diagnosis_controller, 'read_run_logs', logs)
    monkeypatch.setattr(positioning_controller, 'analyze_result', analyze)
    return calls


def _log_text(logger):
    return '\n'.join(row['message'] for row in logger.export_rows())


def _widget_strings(root):
    """창 안 위젯의 보이는 글자 전부 (글자·툴팁·탭·표·목록·콤보·글 상자)."""
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget,
                                   QTabBar, QTableWidget, QTextEdit, QWidget)
    out = [root.windowTitle(), root.statusBar().currentMessage()]
    for w in [root] + root.findChildren(QWidget):
        if w.window() is not root:
            continue
        out += [w.toolTip(), w.statusTip(), w.whatsThis(), w.accessibleName()]
        if isinstance(w, (QLabel, QAbstractButton, QLineEdit)):
            out.append(w.text())
        if isinstance(w, QLineEdit):
            out.append(w.placeholderText())
        if isinstance(w, QGroupBox):
            out.append(w.title())
        if isinstance(w, QTextEdit):
            out += [w.toPlainText(), w.placeholderText()]
        if isinstance(w, QTabBar):
            out += [w.tabText(i) for i in range(w.count())] + [w.tabToolTip(i) for i in range(w.count())]
        if isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        if isinstance(w, QListWidget):
            out += [w.item(i).text() for i in range(w.count())]
        if isinstance(w, QTableWidget):
            for axis, count in ((w.horizontalHeaderItem, w.columnCount()), (w.verticalHeaderItem, w.rowCount())):
                out += [(axis(i).text() + ' ' + axis(i).toolTip()) for i in range(count) if axis(i)]
            out += [w.item(r, c).text() + ' ' + w.item(r, c).toolTip()
                    for r in range(w.rowCount()) for c in range(w.columnCount()) if w.item(r, c)]
    return [s for s in out if s]


# ── 작업자 화면: Advanced·도구가 없다 ─────────────────────────────────────

def test_main_window_has_no_advanced_category(window):
    from PySide6.QtCore import Qt
    tabs = window.chart_category_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ['Basic Analysis', 'Interactive',
                                                             'Comparison', 'Remark']
    advanced = window._inner_tabs['Advanced']
    assert tabs.indexOf(advanced) == -1 and not window.isAncestorOf(advanced)     # 다른 창에 있다
    expert = window._expert_window
    assert advanced.window() is expert
    # 부모 없는 보통 창: 자기 작업 표시줄 단추, 메인 창 위에 붙지 않음, 열려 있어도 메인 창을 닫으면 앱이 끝남
    assert expert.parent() is None and not expert.testAttribute(Qt.WA_QuitOnClose)
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
    remark = window._inner_tabs['Remark']
    assert _buttons(remark.widget(0)) == ['Excel Export', 'Quality Report', 'CSV Export', 'PDF Report']
    assert remark.tabText(1) == 'Help'
    assert not set(TOOLS) & set(_buttons(window))                   # 메인 창 어디에도 도구 버튼이 없다
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


def test_main_window_strings_have_no_expert_words(app, window, tmp_path):
    """데이터를 읽고 전문 분석 창에서 진단까지 돌린 뒤에도 메인 창의 글자(System Log 포함)에 전문 기능이 없다."""
    _loaded(window, tmp_path)
    show_expert_tab(window, DIAGNOSIS)
    current = window._diag_result
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current and window._xy_diagnosis)
    window._select_chart('Run Comparison')
    window._expert_window.close()
    strings = _widget_strings(window)
    assert len(strings) > 200                                         # 정말 훑었다
    found = {word: [s for s in strings if word in s][:2] for word in EXPERT_WORDS}
    assert not {w: s for w, s in found.items() if s}


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
    from PySide6.QtCore import QEvent, Qt
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
    for _ in range(5):                               # 오른쪽 더블클릭도
        clock.now += 0.1
        _send(window, QEvent.MouseButtonDblClick, Qt.RightButton)
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
    from PySide6.QtWidgets import QApplication
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
    mine = [w for w in QApplication.topLevelWidgets() if isinstance(w, ExpertWindow) and w._app is window]
    assert mine == [expert]


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


def test_advanced_charts_wait_for_the_open_window(app, window, tmp_path):
    """창이 닫혀 있으면 Pareto·Correlation·3D를 그리지 않고 미룬다 (마지막 것만 열 때 그린다)."""
    _loaded(window, tmp_path)
    assert window.chart_widgets['Pareto'].get_widget() is None
    assert window.chart_widgets['3D X'].get_widget() is None
    assert window._advanced_charts_pending is not None
    window._select_step(1)                           # 닫혀 있는 동안 Step을 바꿔도 그리지 않는다
    assert window.chart_widgets['Pareto'].get_widget() is None
    window._expert_window.tabs.setCurrentIndex(2)    # 도움말 탭으로 열어도 창이 열리면 그린다
    window._open_expert_window()
    assert window._advanced_charts_pending is None
    assert window.chart_widgets['3D X'].get_widget().title.startswith('Global Align')
    assert window.chart_widgets['Pareto'].get_widget() is not None


# ── 보임: 창이 닫혀 있거나 최소화돼 있으면 Log를 읽지 않는다 ─────────────────────

def test_lazy_reads_wait_for_the_open_expert_window(app, window, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    calls = _counting_reads(monkeypatch)
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


def test_main_window_minimised_counts_as_closed(app, window, tmp_path, monkeypatch):
    calls = _counting_reads(monkeypatch)
    window.show()
    show_expert_tab(window, DIAGNOSIS)
    window.showMinimized()
    app.processEvents()
    assert not window._expert_window_open() and not window._diagnosis_tab_visible()
    _loaded(window, tmp_path)                        # 메인 창이 최소화돼 있는 동안: 읽지도 그리지도 않는다
    app.processEvents()
    assert calls == {'diag': 0, 'poslog': 0} and window._advanced_charts_pending is not None
    window.showNormal()                              # 되살리면 이어서 한다
    current = window._diag_result
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current)
    assert calls == {'diag': 1, 'poslog': 1} and window._advanced_charts_pending is None


def test_opening_on_tools_or_help_starts_no_reads(app, window, tmp_path, monkeypatch):
    calls = _counting_reads(monkeypatch)
    window._select_chart(DIAGNOSIS)
    _loaded(window, tmp_path)
    expert = window._expert_window
    for index in (1, 2):                             # 도구 · 도움말
        expert.close()
        expert.tabs.setCurrentIndex(index)
        window._open_expert_window()
        app.processEvents()
        assert calls == {'diag': 0, 'poslog': 0}
    expert.tabs.setCurrentIndex(0)                   # Advanced(Diagnosis)로 가면 읽는다
    current = window._diag_result
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current)
    assert calls == {'diag': 1, 'poslog': 1}


def test_failed_read_is_not_retried_on_focus_changes(app, window, tmp_path, monkeypatch):
    """읽기 오류 뒤 창을 오가도(활성화) 다시 읽지 않는다 — 다시 읽기는 탭 변경·다시 열기 때만 (리뷰 M1)."""
    calls = _counting_reads(monkeypatch, fail=True)
    _loaded(window, tmp_path)
    window.show()
    show_expert_tab(window, DIAGNOSIS)
    key = window._diag_read_key(window._diag_result)
    _wait_for(app, lambda: key in (window._diag_failed or {}) and key in (window._poslog_failed or {}))
    assert calls == {'diag': 1, 'poslog': 1} and window._xy_diagnosis is not None
    expert = window._expert_window
    activations = []
    expert.became_visible.connect(lambda: activations.append(1))
    for _ in range(4):                               # 메인 창 ↔ 전문 분석 창 오가기
        window.activateWindow()
        app.processEvents()
        expert.activateWindow()
        app.processEvents()
    checked = [d for d, cb in window._die_checkboxes.items() if cb.isChecked()]
    window._die_checkboxes[checked[-1]].setChecked(False)          # Die 필터 갱신도 다시 읽지 않는다
    app.processEvents()
    assert calls == {'diag': 1, 'poslog': 1} and activations == []
    assert window._xy_diagnosis is not None                         # 오류 결과로 진단을 그대로 보인다
    assert _log_text(window.expert_logger).count('다시 읽습니다') == 1

    expert.tabs.setCurrentIndex(1)                   # 탭을 바꿨다가 돌아오면 다시 읽는다
    expert.tabs.setCurrentIndex(0)
    _wait_for(app, lambda: calls == {'diag': 2, 'poslog': 2}
              and key in (window._diag_failed or {}) and key in (window._poslog_failed or {}))
    expert.close()                                   # 창을 다시 열어도 다시 읽는다
    window._open_expert_window()
    _wait_for(app, lambda: calls == {'diag': 3, 'poslog': 3}
              and key in (window._diag_failed or {}) and key in (window._poslog_failed or {}))


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
    assert expert.logger is window.expert_logger and expert.logger is not window.logger
    expert._rotation_test_result = {'kappa': 0.1}                  # 회전 시험 대화상자가 하는 일
    assert window._rotation_test_result == {'kappa': 0.1}
    expert.set_rotation_test({'kappa': 0.2})
    assert window._rotation_test_result == {'kappa': 0.2}


def test_hiding_the_window_hides_its_tool_dialogs_and_reopen_shows_them(app, window):
    from PySide6.QtWidgets import QPushButton
    window._open_expert_window()
    expert = window._expert_window
    buttons = {b.text(): b for b in expert.tabs.widget(1).findChildren(QPushButton)}
    buttons['이미지 2장 이동량 분석'].click()
    buttons['스캐너 회전 시험 분석'].click()
    dialogs = [expert._image_shift_dialog, expert._scanner_rotation_dialog]
    assert all(d.isVisible() and d.parent() is expert for d in dialogs)
    expert.close()
    assert not any(d.isVisible() for d in dialogs)
    window._open_expert_window()
    assert all(d.isVisible() for d in dialogs)
    for d in dialogs:
        d.close()
    expert.close()
    window._open_expert_window()                     # 사용자가 닫은 도구 창은 다시 열지 않는다
    assert not any(d.isVisible() for d in dialogs)


# ── 기록·읽는 중: 작업자 System Log에 전문 기능 메시지를 남기지 않는다 ────────────────

def test_operator_log_has_no_expert_messages(app, window, tmp_path, monkeypatch):
    import charts
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QFileDialog
    from ui.controllers import diagnosis_controller

    def broken(*_a, **_k):
        raise RuntimeError('3D 그리기 실패')
    monkeypatch.setattr(charts, 'create_3d_surface_widget', broken)   # 차트 오류도 전문 분석 기록으로
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    monkeypatch.setattr(QDesktopServices, 'openUrl', staticmethod(lambda url: True))
    _loaded(window, tmp_path)
    show_expert_tab(window, DIAGNOSIS)
    current = window._diag_result
    _wait_for(app, lambda: 'xy_logs' in current and 'positioning_log' in current and window._xy_diagnosis)
    window.diag_report_btn.click()                   # 3D 보고서
    target = tmp_path / 'diag.json'
    monkeypatch.setattr(diagnosis_controller.QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(target), '')))
    window.diag_save_btn.click()                     # 진단 JSON
    out = tmp_path / 'report.md'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: (str(out), '')))
    window._export_llm_markdown(parent=window._expert_window)
    _wait_for(app, lambda: 'AI 분석용 Markdown 저장' in _log_text(window.expert_logger), timeout=60)
    expert_log = _log_text(window.expert_logger)
    for message in ('3D 트러블슈팅 보고서 저장', '진단 JSON 저장', 'AI 분석용 Markdown 저장', '셀 값 CSV 저장',
                    '3D X Surface 오류'):
        assert message in expert_log, message
    operator_log = _log_text(window.logger)
    assert operator_log                                             # 로드 메시지는 남는다
    assert [w for w in EXPERT_WORDS + ('3D X', 'Surface') if w in operator_log] == []


def test_busy_banner_and_buttons_while_the_main_window_reads(window):
    expert = window._expert_window
    window._diag_obj = object()                      # 진단이 있다
    window._sync_diag_buttons()
    assert window.diag_save_btn.isEnabled() and window.diag_report_btn.isEnabled()
    window._busy_begin('데이터 읽는 중')
    assert not expert.busy_banner.isHidden()
    assert expert.busy_banner.text() == '메인 창에서 데이터를 읽는 중 — 끝나면 갱신됩니다'
    assert not any(b.isEnabled() for b in window._expert_tool_buttons) and len(window._expert_tool_buttons) == 5
    assert not window.diag_save_btn.isEnabled() and not window.diag_report_btn.isEnabled()
    window._busy_end()
    assert expert.busy_banner.isHidden() and all(b.isEnabled() for b in window._expert_tool_buttons)
    assert window.diag_save_btn.isEnabled() and window.diag_report_btn.isEnabled()
    window._diag_obj = None
    window._sync_diag_buttons()
    assert not window.diag_save_btn.isEnabled()


def test_f11_and_esc_work_in_the_expert_window(window):
    from PySide6.QtGui import QKeySequence, QShortcut
    expert = window._expert_window
    keys = {s.key().toString(): s for s in expert.findChildren(QShortcut)}
    assert set(keys) == {'F11', 'Esc'}
    window._main_splitter.setSizes([500, 500])
    keys['F11'].activated.emit()                     # 메인 창 왼쪽 칸 접기
    assert window._main_splitter.sizes()[0] == 0
    assert not keys['Esc'].isEnabled()               # 진행 중일 때만 (대화상자의 Esc를 빼앗지 않게)
    window._set_scan_controls_enabled(False)
    assert keys['Esc'].isEnabled() and keys['Esc'].key() == QKeySequence('Esc')
    window._set_scan_controls_enabled(True)
    assert not keys['Esc'].isEnabled()


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


def _small(window, monkeypatch, default=(400, 300)):
    """offscreen 화면(800×800)에서 최대화로 빠지지 않게 최소 크기·처음 크기를 줄인다."""
    from ui.controllers import expert_controller
    window.setMinimumSize(200, 150)
    window._expert_window.setMinimumSize(300, 200)
    monkeypatch.setattr(expert_controller, 'DEFAULT_SIZE', default)


def test_first_open_is_placed_beside_the_main_window(app, window, monkeypatch):
    _small(window, monkeypatch)
    window.setGeometry(2, 2, 300, 300)
    window.show()
    app.processEvents()
    window._open_expert_window()
    app.processEvents()
    main, expert = window.frameGeometry(), window._expert_window.frameGeometry()
    assert not window._expert_window.isMaximized()
    assert expert.left() > main.right() and not expert.intersects(main)          # 오른쪽 옆
    assert expert.top() == main.top()
    assert app.primaryScreen().availableGeometry().contains(expert)


def test_first_open_cascades_when_there_is_no_room_beside(app, window, monkeypatch):
    _small(window, monkeypatch)
    window.setGeometry(2, 2, 796, 796)               # 화면을 다 차지한다
    window.show()
    app.processEvents()
    window._open_expert_window()
    app.processEvents()
    main, expert = window.frameGeometry(), window._expert_window.frameGeometry()
    assert expert.topLeft() == main.topLeft() + type(main.topLeft())(48, 48)      # 비켜 겹친다
    assert app.primaryScreen().availableGeometry().contains(expert)


def test_geometry_is_remembered_with_qt_and_restored_with_maximised_state(app, window, monkeypatch):
    from core.settings import parse_geometry_string
    _small(window, monkeypatch)
    expert = window._expert_window
    window._open_expert_window()
    expert.setGeometry(60, 70, 420, 310)
    app.processEvents()
    expert.close()
    saved = window.settings['expert_window_geometry']
    assert parse_geometry_string(saved) is None and saved.isascii()          # saveGeometry base64
    window._expert_geometry_restored = False         # 다음 실행처럼 처음 열기
    expert.setGeometry(200, 200, 300, 200)
    window._open_expert_window()
    g = expert.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (60, 70, 420, 310)
    expert.showMaximized()
    app.processEvents()
    expert.close()
    window._expert_geometry_restored = False
    window._open_expert_window()
    app.processEvents()
    assert expert.isMaximized()                      # 최대화도 기억한다 (L8)


def test_offscreen_saved_geometry_is_clamped(app, window, monkeypatch):
    from PySide6.QtWidgets import QMainWindow
    _small(window, monkeypatch)
    expert = window._expert_window
    avail = app.primaryScreen().availableGeometry()
    window.settings['expert_window_geometry'] = '400x300+5000+5000'      # 예전 형식도 읽는다
    window._open_expert_window()
    app.processEvents()
    assert avail.contains(expert.geometry())
    expert.close()
    other = QMainWindow()                            # 다른 모니터에서 저장한 위치 (지금은 화면 밖)
    other.setMinimumSize(300, 200)
    other.setGeometry(5000, 5000, 400, 300)
    window.settings['expert_window_geometry'] = bytes(other.saveGeometry().toBase64()).decode('ascii')
    other.deleteLater()
    window._expert_geometry_restored = False
    window._open_expert_window()
    app.processEvents()
    assert avail.contains(expert.geometry())


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
    loaded['expert_window_geometry'] = 'AdnQywADAAAAAAAy'
    settings.save_settings(loaded)                   # 소스 실행: settings.local.json
    assert json.loads(local.read_text(encoding='utf-8')) == {'expert_window_geometry': 'AdnQywADAAAAAAAy'}
    assert bundled.read_bytes() == before
    assert settings.load_settings()['expert_window_geometry'] == 'AdnQywADAAAAAAAy'

    fake = _FakeWinreg()                             # 빌드본: 레지스트리
    monkeypatch.setattr(settings, 'winreg', fake)
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    settings.save_settings({'expert_window_geometry': 'AdnQywADAAAAAAAy', 'spec_deviation': {}})
    assert fake.values['expert_window_geometry'] == ('AdnQywADAAAAAAAy', fake.REG_SZ)
    assert settings._read_registry_settings() == {'expert_window_geometry': 'AdnQywADAAAAAAAy'}
