"""작업 중임이 눈에 보여야 한다 — 결과 영역 막, 상태 표시줄 막대, 화면 그리기 단계, 가려진 탭.

배경(현장): '응답 없음'은 사라졌지만 파일을 읽는 동안 화면에 아무 변화가 없어 작업 중인지
알 수 없었다(상태 표시줄 문구는 눈에 띄지 않는다). 스캔 완료 화면 갱신은 2.2초였다.
"""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.csv_loader import ScanSession  # noqa: E402
from core.recipe_scanner import compare_recipes, load_all_recipes, recipes_from_mapping  # noqa: E402
from test_run_comparison import _make_runs  # noqa: E402
from ui.workers.progress_throttle import Throttle  # noqa: E402

SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 1.0},
            'Global Align': {'spec_range': 2.0, 'spec_stddev': 1.0}}
SPECS = list(SPEC_DEV)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    import main
    import ui.controllers.scan_controller as controller
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=dict(SPEC_DEV)))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    monkeypatch.setattr(controller, 'add_recent_folder', lambda s, _f: s)
    from PySide6.QtWidgets import QMessageBox
    for name in ('warning', 'information', 'critical'):     # 모달 대화상자가 테스트를 멈추지 않게
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    win = main.DataAnalyzerApp()
    monkeypatch.setattr(win, '_save_settings', lambda: None)
    yield win
    for thread in win._background_threads():
        if hasattr(thread, 'cancel'):
            thread.cancel()
        thread.wait(10000)
    win._loader_thread = None
    win.deleteLater()
    app.processEvents()


@pytest.fixture
def fake_loader(monkeypatch):
    from PySide6.QtCore import QObject, Signal
    import ui.controllers.scan_controller as controller
    started = []

    class FakeThread(QObject):
        finished = Signal(list, list, float)
        error = Signal(str, str)
        progress = Signal(str)
        cancelled = Signal()
        count = Signal(int, int)
        total_lots = 30

        def __init__(self, folder, recipes=None, session=None):
            super().__init__()
            self.folder, self.recipes, self.session = folder, recipes, session
            self.running = True

        def start(self):
            started.append(self)

        def isRunning(self):
            return self.running

        def cancel(self):
            self.running = False

        def wait(self, *_a):
            return True

    monkeypatch.setattr(controller, 'DataLoaderThread', FakeThread)
    monkeypatch.setattr(controller.FolderScanThread, 'start', lambda self: self.run())
    return started


def _wait_for(app, condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, '기다리던 상태가 오지 않았습니다'
        app.processEvents()
        time.sleep(0.01)


def _busy(win):
    return not win._busy_overlay.isHidden(), not win.status_progress.isHidden()


def _start_load(win, tmp_path):
    _make_runs(tmp_path / 'data' / '1. Vision Pattern', ['1', '2'], lots=2)
    win.path_edit.setText(str(tmp_path / 'data'))
    win._scan_folder()


# ── 막과 상태 표시줄 막대: 시작·끝·중지·오류 ─────────────────────────────

def test_overlay_shows_during_load_and_hides_on_done(window, fake_loader, tmp_path):
    _start_load(window, tmp_path)
    loader = fake_loader[-1]
    assert _busy(window) == (True, True)
    assert window._busy_overlay.title.text() == '데이터 읽는 중'
    assert window._busy_overlay.bar.maximum() == 30      # 전체 Lot 수를 처음부터 안다

    loader.progress.emit('데이터 로드 중 1/1: Vision Pattern 회차 1/2')
    loader.count.emit(12, 30)
    overlay = window._busy_overlay
    assert overlay.count.text() == '12/30 (40%)' and 'Vision Pattern 회차 1/2' in overlay.item.text()
    assert (window.status_progress.value(), window.status_progress.maximum()) == (12, 30)
    assert not overlay.cancel_btn.isHidden()

    loader.finished.emit([], [], 0.5)
    assert _busy(window) == (False, False)


def test_overlay_hides_on_cancel_and_on_error(window, fake_loader, tmp_path):
    _start_load(window, tmp_path)
    window._cancel_scan()
    assert _busy(window) == (False, False)

    window._scan_folder()
    assert _busy(window) == (True, True)
    fake_loader[-1].error.emit('OSError: 끊김', '')
    assert _busy(window) == (False, False)


def test_overlay_during_folder_check_and_cancel(app, window, tmp_path, monkeypatch):
    import ui.controllers.scan_controller as controller
    release = threading.Event()

    def slow_detect(folder, spec_keys, remembered, session):
        session.found(140)
        session.checked(os.path.join(folder, 'Vision Pattern', '7', 'Sample2202 (9)'))
        release.wait(5)
        return {'action': 'none'}

    monkeypatch.setattr(controller, 'detect_scan_target', slow_detect)
    window.path_edit.setText(str(tmp_path))
    window._scan_folder()
    overlay = window._busy_overlay
    assert overlay.title.text() == '폴더 확인 중' and _busy(window) == (True, True)
    _wait_for(app, lambda: overlay.count.text() == '1/140 (0%)')
    assert overlay.item.text() == '폴더 확인 중 1/140 — Vision Pattern/7'

    window._cancel_scan()
    assert _busy(window) == (False, False)
    release.set()


def test_overlay_follows_the_results_area(window):
    window.resize(1400, 900)
    window._busy_begin('데이터 읽는 중')
    overlay = window._busy_overlay
    assert overlay.geometry() == window._main_splitter.geometry()
    assert overlay.parentWidget() is window.centralWidget()     # 창 이동·크기 조절은 막지 않는다
    window._busy_end()


# ── 진행 수: 미리 알고, 줄지 않고, 끝에 전체에 닿는다 ───────────────────────

def _loader_for(tmp_path, runs, lots):
    from ui.workers.data_loader_thread import DataLoaderThread
    vp = _make_runs(tmp_path / 'vp', runs, lots=lots)
    session = ScanSession()
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], SPECS, session=session)
    return DataLoaderThread('', recipes=recipes, session=session)


def _run_thread(app, thread):
    """실제 스레드로 돌리고 화면 스레드로 넘어온 시그널을 모두 받는다."""
    thread.start()
    assert thread.wait(30000)
    for _ in range(5):
        app.processEvents()


def test_load_progress_is_monotonic_and_reaches_total(app, tmp_path):
    thread = _loader_for(tmp_path, ['1', '2', '3'], lots=11)    # 회차마다 첫 리핏 제외 대상
    thread._throttle = Throttle(0)      # 모든 값을 받는다
    seen = []
    thread.count.connect(lambda done, total: seen.append((done, total)))
    assert thread.total_lots == 33

    _run_thread(app, thread)

    assert len(seen) > 10 and all(total == 33 for _done, total in seen)
    values = [done for done, _total in seen]
    assert values == sorted(values) and values[-1] == 33


def test_load_progress_is_throttled(app, tmp_path):
    thread = _loader_for(tmp_path, ['1', '2'], lots=6)
    clock = [0.0]
    thread._throttle = Throttle(0.1, clock=lambda: clock[0])   # 시간이 흐르지 않는다
    seen = []
    thread.count.connect(lambda done, total: seen.append(done))

    _run_thread(app, thread)

    assert len(seen) == 2 and seen[-1] == 12        # 처음 한 번 + 끝


def test_throttle_allows_about_ten_per_second():
    clock = [0.0]
    throttle = Throttle(0.1, clock=lambda: clock[0])
    passed = 0
    for step in range(100):         # 1초 동안 100번
        clock[0] = step * 0.01
        passed += throttle.ready()
    assert passed == 10


def test_folder_check_progress_is_throttled(tmp_path):
    from ui.workers.folder_scan_thread import FolderScanThread
    clock = [0.0]

    def job(session):
        session.found(50)
        for k in range(50):
            session.checked(str(tmp_path / 'run' / f'lot{k}'))
        return {}

    thread = FolderScanThread(job, ScanSession())
    thread._throttle = Throttle(0.1, clock=lambda: clock[0])
    seen = []
    thread.count.connect(lambda done, total: seen.append((done, total)))
    thread.run()
    assert seen == [(1, 50)]


# ── 화면 그리기 단계와 Step·회차 전환 ──────────────────────────────────

def _loaded(window, tmp_path, workflows=(('Vision Pattern', ['1', '2', '3']),)):
    mapping = [(name, str(_make_runs(tmp_path / name.replace(' ', '_'), runs, lots=10)))
               for name, runs in workflows]
    recipes, _ = recipes_from_mapping(mapping, SPECS)
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    window._loader_thread = type('Done', (), {'folder': str(tmp_path)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    return results


def test_render_phase_is_shown_before_drawing(window, tmp_path, monkeypatch):
    seen = {}
    real = window._update_summary_table

    def spy(*args, **kwargs):
        seen['overlay'] = (not window._busy_overlay.isHidden(), window._busy_overlay.title.text())
        seen['status'] = window.statusBar().currentMessage()
        seen['cancellable'] = not window._busy_overlay.cancel_btn.isHidden()
        return real(*args, **kwargs)

    monkeypatch.setattr(window, '_update_summary_table', spy)
    _loaded(window, tmp_path)

    assert seen['overlay'] == (True, '화면 그리는 중…')
    assert seen['status'].startswith('화면 그리는 중…') and seen['cancellable'] is False
    assert _busy(window) == (False, False)
    assert '⏱ 화면 갱신' in str(window.logger.export_rows())


def test_runs_summary_computed_by_loader_is_reused(window, tmp_path, monkeypatch):
    import ui.controllers.scan_controller as controller
    from core.checklist import summarize_runs
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], SPECS)
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    specs = (window.settings.get('spec_limits', {}), window.settings.get('spec_deviation', {}))
    results[0]['runs'] = summarize_runs(results[0], *specs)      # 로드 스레드가 한 일
    calls = []
    monkeypatch.setattr(controller, 'summarize_runs', lambda *a: calls.append(a) or [])
    window._loader_thread = type('Done', (), {'folder': str(vp), 'run_specs': specs})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    assert calls == [] and len(window.recipe_results[0]['runs']) == 2


def test_step_and_run_switch_show_wait_cursor_and_message(window, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    _loaded(window, tmp_path, (('Vision Pattern', ['1', '2']), ('Global Align', ['1', '2'])))
    seen = []
    real = window._show_step

    def spy(idx):
        cursor = QApplication.overrideCursor()
        seen.append((cursor is not None and cursor.shape() == Qt.WaitCursor,
                     window.statusBar().currentMessage()))
        return real(idx)

    real_switch = window._switch_run

    def spy_run(*args):
        cursor = QApplication.overrideCursor()
        seen.append(('run', cursor is not None and cursor.shape() == Qt.WaitCursor,
                     window.statusBar().currentMessage()))
        return real_switch(*args)

    monkeypatch.setattr(window, '_show_step', spy)
    monkeypatch.setattr(window, '_switch_run', spy_run)
    window._select_step(1)
    window.run_combo.setCurrentIndex(0)

    assert seen[0] == (True, 'Step 전환 중… Global Align')
    assert seen[1] == ('run', True, '회차 전환 중… 1')
    assert QApplication.overrideCursor() is None     # 끝나면 되돌린다


# ── 가려진 탭은 처음 보일 때 그리고, 같으면 다시 그리지 않는다 ─────────────────

def test_hidden_comparison_tabs_render_on_first_show_only(window, tmp_path, monkeypatch):
    import charts
    calls = {'box': 0, 'metrics': 0}
    real_box, real_metrics = charts.plot_recipe_comparison_boxplot, charts.plot_run_comparison_metrics

    def box(rr):
        calls['box'] += 1
        return real_box(rr)

    def metrics(*a):
        calls['metrics'] += 1
        return real_metrics(*a)

    monkeypatch.setattr(charts, 'plot_recipe_comparison_boxplot', box)
    monkeypatch.setattr(charts, 'plot_run_comparison_metrics', metrics)
    _loaded(window, tmp_path, (('Vision Pattern', ['1', '2']), ('Global Align', ['1', '2'])))
    assert calls == {'box': 0, 'metrics': 0}        # Comparison 탭은 가려져 있다

    window._select_chart('Boxplot')
    window._select_chart('Contour X')
    window._select_chart('Boxplot')
    assert calls['box'] == 1
    assert '📊 Recipe Boxplot 비교 차트 생성 완료' in str(window.logger.export_rows())

    window._select_chart('회차 비교')
    window._select_chart('Boxplot')
    window._select_chart('회차 비교')
    assert calls['metrics'] == 1

    window._select_chart('Contour X')
    window.run_combo.setCurrentIndex(0)             # 가려진 동안 회차를 바꾼다
    assert calls['box'] == 1
    window._select_chart('Boxplot')
    assert calls['box'] == 2


# ── Workflow별 대화상자: 확인 중인 행의 막대와 전체 진행 ───────────────────

def test_dialog_rows_show_busy_bar_and_overall_progress(app, tmp_path, monkeypatch):
    import ui.dialogs.workflow_folder_dialog as module
    from ui.dialogs.workflow_folder_dialog import WorkflowFolderDialog
    release = threading.Event()

    def describe(path, session):
        release.wait(5)
        return True, '✅ Lot 10개 · (root)'

    monkeypatch.setattr(module, 'describe_folder', describe)
    empty = {key: {'path': '', 'enabled': False, 'note': ''} for key in SPECS}
    dlg = WorkflowFolderDialog(None, SPECS, '', {}, '', session=ScanSession(), prefill=empty)
    try:
        dlg._rows['Vision Pattern'][1].setText(str(tmp_path / 'vp'))
        dlg._update_status('Vision Pattern')
        assert not dlg._row_bars['Vision Pattern'].isHidden()
        assert dlg._row_bars['Global Align'].isHidden()
        assert dlg.progress_label.text() == '폴더 확인 완료 0/1'

        release.set()
        _wait_for(app, lambda: not dlg._pending)
        assert dlg._row_bars['Vision Pattern'].isHidden()
        assert dlg.progress_label.text() == '폴더 확인 완료 1/1'
    finally:
        release.set()
        dlg.done(0)
        dlg.deleteLater()
