"""네트워크 폴더에서 창이 '응답 없음'이 되지 않아야 한다.

배경: 공유 폴더(\\\\서버\\...\\Vision Pattern, 회차 14개 × Lot 10개)를 열면 화면 스레드가
폴더 목록·CSV 머리글을 네 번 거듭 읽었다(스캔, 대화상자 열기, 확인 버튼, Recipe 만들기).
실측 약 89초 동안 창이 멈췄고, 데이터 로드는 Lot을 하나씩 읽어 37초가 걸렸다.

여기서는 느린 파일 시스템을 흉내 내 다음을 확인한다.
  - 화면 스레드의 진입점(Scan, Workflow별 선택, 시작 시 자동 스캔, TIFF 더블클릭)은 바로
    돌아오고, 파일 접근은 작업 스레드에서만 일어난다
  - 한 번의 사용자 동작에서 폴더마다 한 번만 확인한다 (대화상자 확인 버튼도 다시 읽지 않는다)
  - 측정 순서용 메타는 머리글만 읽는다
  - Lot 병렬 로드는 순차 로드와 결과가 같다
  - 중지하면 이전 결과가 그대로 남는다
"""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import csv_loader, recipe_scanner  # noqa: E402
from core.csv_loader import ScanSession  # noqa: E402
from core.workflow_map import describe_folder  # noqa: E402

SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 1.0}}
SPECS = ['Vision Pattern', 'In-Die Align', 'LLC Translation', 'Global Align']
HEADER = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
          'HZ1_O (nm),HZ1_O_Valid\r\n')


def _meta(repeat, start):
    return (f"Lot ID,Lot6\r\nRecipe ID,04. XY Stage\\1. Vision Pattern Recognize\r\n"
            f"Sample ID,Sample22\r\nRepeat No,{repeat}\r\n"
            f"Start Time,2026.11.07 {start}\r\nEnd Time,2026.11.07 23:59:59\r\n\r\n\r\n")


def _write_lot(lot, repeat=1, start='10:00:00', extra_rows=0, seed=0):
    """실데이터와 같은 머리글의 Lot 폴더 (X/Y UL CSV, 요약 CSV, TIFF 이름 몇 개)."""
    lot.mkdir(parents=True, exist_ok=True)
    for axis in ('X', 'Y'):
        rows = [f'{d:04d}_X{d:03d}_Y000,{d},0,1,{d * 1000},0,{axis},COMPLETED,TRUE,'
                f'{2000 + 37 * d + 11 * seed + (axis == "Y"):.3f},TRUE\r\n'
                for d in range(1, 23)]
        rows += [f'{9000 + k:04d}_X000_Y000,0,0,1,0,0,{axis},COMPLETED,TRUE,1.0,TRUE\r\n'
                 for k in range(extra_rows)]
        (lot / f'Sample22_{axis}_UL.csv').write_bytes(
            (_meta(repeat, start) + HEADER + ''.join(rows)).encode())
    (lot / 'Sample22.csv').write_bytes(b'Lot ID,Lot6\r\n\r\nX_UL\r\nITEM,HZ1_O (nm),\r\nMEAN,1,\r\n')
    for d in (1, 2):
        (lot / f'Sample22_{d:04d}_X{d:03d}_Y000.tiff').write_bytes(b'')
    return lot


def _make_runs(folder, runs=3, lots=4):
    """회차 폴더(1..runs) × Lot. 폴더 이름 순서와 측정 순서(Start Time)는 반대다."""
    for run in range(1, runs + 1):
        for k in range(lots):
            _write_lot(folder / str(run) / f'Sample{k}', repeat=k + 1,
                       start=f'{10 + lots - k:02d}:00:00', seed=run * 10 + k)
    return folder


# ── 측정 순서 메타는 머리글만 읽는다 ──────────────────────────────────

class _CountingFile:
    def __init__(self, f, counter):
        self._f, self._counter = f, counter

    def readline(self, *a):
        line = self._f.readline(*a)
        self._counter['bytes'] += len(line)
        self._counter['lines'] += 1 if line else 0
        return line

    def read(self, *a):
        data = self._f.read(*a)
        self._counter['bytes'] += len(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()

    def close(self):
        self._f.close()


@pytest.fixture
def counted_open(monkeypatch):
    counter = {'bytes': 0, 'lines': 0, 'opened': []}

    def fake_open(path, mode='r', *a, **k):
        f = open(path, mode, *a, **k)
        if 'b' not in mode:
            return f
        counter['opened'].append(os.path.basename(path))
        return _CountingFile(f, counter)

    monkeypatch.setattr(csv_loader, 'open', fake_open, raising=False)
    return counter


def test_read_order_meta_reads_only_the_header(tmp_path, counted_open):
    lot = _write_lot(tmp_path / 'Sample1', repeat=3, start='16:03:49', extra_rows=5000)
    size = (lot / 'Sample22_X_UL.csv').stat().st_size

    start, repeat = csv_loader._read_order_meta({'path': str(lot), 'x_csv': 'Sample22_X_UL.csv'})

    assert (start.hour, start.minute, start.second, repeat) == (16, 3, 49, 3)
    # Repeat No(4번째 줄)와 Start Time(5번째 줄)을 찾으면 멈춘다 — 파일은 수백 KB다
    assert counted_open['lines'] <= 5
    assert counted_open['bytes'] < 400 < size


def test_read_order_meta_stops_at_data_header(tmp_path, counted_open):
    lot = tmp_path / 'Sample1'
    lot.mkdir()
    (lot / 'S_X_UL.csv').write_bytes(
        b'Lot ID,Lot6\r\n\r\n' + HEADER.encode()
        + b'0001_X000_Y000,0,0,1,0,0,X,COMPLETED,TRUE,1.0,TRUE\r\n' * 5000)

    assert csv_loader._read_order_meta({'path': str(lot), 'x_csv': 'S_X_UL.csv'}) == (None, None)
    assert counted_open['lines'] == 3     # Site ID 머리글에서 멈춘다


def test_lot_scan_opens_each_csv_once_and_reads_only_its_head(tmp_path, counted_open):
    root = tmp_path / 'run'
    for k in range(4):
        _write_lot(root / f'Sample{k}', repeat=k + 1, start=f'{14 - k:02d}:00:00', extra_rows=3000)

    lots = csv_loader.scan_lot_folders(str(root))

    # 측정 순서(Start Time)대로: 폴더 이름과 반대
    assert [lot['lot_name'] for lot in lots] == ['Sample3', 'Sample2', 'Sample1', 'Sample0']
    assert all('head' not in lot for lot in lots)
    # 폴더 판별과 측정 순서가 같은 X CSV의 앞부분을 함께 쓴다 (Lot마다 한 번, 20줄 이하)
    assert counted_open['opened'] == ['Sample22_X_UL.csv'] * 4
    assert counted_open['lines'] <= 4 * 20


# ── 한 번의 사용자 동작에서 폴더마다 한 번만 확인한다 ─────────────────────

@pytest.fixture
def count_folder_checks(monkeypatch):
    calls = []
    real = csv_loader._is_data_folder

    def counting(path, *args):
        calls.append(os.path.normcase(os.path.normpath(path)))
        return real(path, *args)

    monkeypatch.setattr(csv_loader, '_is_data_folder', counting)
    return calls


def test_detection_dialog_and_load_share_one_scan(tmp_path, count_folder_checks):
    from ui.controllers.scan_controller import detect_scan_target
    root = _make_runs(tmp_path / 'Vision Pattern', runs=3, lots=4)
    session = ScanSession()

    # 스캔: 회차 폴더 1..3이 Recipe로 보이고 Spec 이름과 달라 대화상자를 연다
    target = detect_scan_target(str(root), SPECS, {}, session)
    assert target['action'] == 'dialog'
    first_pass = len(count_folder_checks)
    assert first_pass == len(set(count_folder_checks))     # 같은 폴더를 두 번 보지 않았다

    # 대화상자(루트 확인), 확인 버튼(상태 재사용), Recipe 만들기, 데이터 로드
    assert describe_folder(str(root), session) == (True, '✅ 회차 3개 (1~3) · Lot 4개씩')
    recipes, problems = recipe_scanner.recipes_from_mapping(
        [('Vision Pattern', str(root))], SPECS, session=session)
    assert problems == [] and [r['lot_count'] for r in recipes[0]['rounds']] == [4, 4, 4]
    results = recipe_scanner.load_all_recipes('', recipes=recipes, session=session.fork())
    assert len(results[0]['run_results']) == 3

    lot_checks = [p for p in count_folder_checks if os.path.basename(p).startswith('sample')]
    assert len(lot_checks) == 12 == len(set(lot_checks))   # Lot 12개를 한 번씩만

    # 새 동작(새 세션)은 지난 결과를 쓰지 않고 다시 읽는다 — 그사이 생긴 Lot도 보인다
    _write_lot(root / '1' / 'Sample9', repeat=9, start='09:00:00')
    fresh = describe_folder(str(root), ScanSession())
    assert fresh == (True, '✅ 회차 3개 (1~3) · Lot 4~5개')


def test_recipes_from_mapping_with_checked_session_touches_no_files(tmp_path, monkeypatch):
    root = _make_runs(tmp_path / 'vp', runs=2, lots=2)
    session = ScanSession()
    assert describe_folder(str(root), session)[0]

    def boom(*_a, **_k):
        raise AssertionError('대화상자에서 확인한 폴더를 다시 읽었다')
    for name in ('listdir', 'scandir'):
        monkeypatch.setattr(os, name, boom)
    monkeypatch.setattr(os.path, 'isdir', boom)

    recipes, problems = recipe_scanner.recipes_from_mapping(
        [('Vision Pattern', str(root))], SPECS, session=session)
    assert problems == [] and recipes[0]['source_path'] == str(root)


# ── 병렬 로드는 순차 로드와 결과가 같다 ───────────────────────────────

def _load(root, workers, monkeypatch):
    monkeypatch.setattr(csv_loader, 'LOT_LOAD_WORKERS', workers)
    errors, warnings, info = [], [], {}
    rows = csv_loader.batch_load(str(root), errors=errors, warnings=warnings, info=info)
    return rows, errors, warnings, info


def test_parallel_lot_load_equals_sequential(tmp_path, monkeypatch):
    root = tmp_path / 'run'
    for k in range(12):
        _write_lot(root / f'Sample{k:02d}', repeat=k + 1, start=f'{10 + k:02d}:00:00', seed=k)
    (root / 'Sample05' / 'Sample22.csv').write_bytes(
        b'Lot ID,Lot6\r\n\r\nX_UL\r\nITEM,HZ1_O (nm),\r\nMEAN,\xef\xbf\xbd,\r\n')

    sequential = _load(root, 1, monkeypatch)
    parallel = _load(root, 8, monkeypatch)

    assert parallel == sequential
    assert len(sequential[0]) == 11 * 44     # 첫 리핏 제외, Lot당 22 Die × X/Y
    assert sequential[3]['first_repeat_excluded'] == 'Sample00'
    assert sequential[2]    # 요약 CSV 경고도 같은 순서로 남는다


def test_parallel_lot_load_overlaps_network_waits(tmp_path, monkeypatch):
    root = tmp_path / 'run'
    for k in range(8):
        _write_lot(root / f'Sample{k}', repeat=k + 2, start=f'{10 + k:02d}:00:00')
    real = csv_loader.load_lot_data
    active, peak = [0], [0]
    lock = threading.Lock()

    def slow(path):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)    # 네트워크 지연
        with lock:
            active[0] -= 1
        return real(path)

    monkeypatch.setattr(csv_loader, 'load_lot_data', slow)
    t0 = time.perf_counter()
    rows = csv_loader.batch_load(str(root))
    elapsed = time.perf_counter() - t0

    assert len(rows) == 8 * 44
    assert peak[0] >= 4
    assert elapsed < 8 * 0.15 * 0.6


def test_cancel_stops_the_load(tmp_path):
    root = tmp_path / 'run'
    for k in range(3):
        _write_lot(root / f'Sample{k}', repeat=k + 2)
    session = ScanSession()
    session.cancel()
    with pytest.raises(csv_loader.ScanCancelled):
        recipe_scanner.load_all_recipes(
            '', recipes=[recipe_scanner.build_recipe(str(root))], session=session)


# ── 화면: 진입점은 바로 돌아오고 파일 접근은 작업 스레드에서만 ─────────────

def _wait_for(app, condition, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, '작업 스레드가 끝나지 않았습니다'
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def slow_fs(monkeypatch, tmp_path):
    """데이터 폴더(tmp_path 아래)에 대한 파일 시스템 호출마다 지연을 넣고,
    어느 스레드에서 불렸는지 남긴다 (네트워크 폴더 흉내)."""
    state = {'delay': 0.02, 'main_calls': [], 'worker_calls': 0}
    main = threading.main_thread()
    root = os.path.normcase(str(tmp_path))

    def wrap(real, name):
        def slow(*args, **kwargs):
            try:
                target = os.path.normcase(os.fspath(args[0])) if args else ''
            except TypeError:
                target = ''
            if isinstance(target, str) and target.startswith(root):
                if threading.current_thread() is main:
                    state['main_calls'].append((name, target))
                else:
                    state['worker_calls'] += 1
                time.sleep(state['delay'])
            return real(*args, **kwargs)
        return slow

    state['install'] = lambda: [
        monkeypatch.setattr(os, 'listdir', wrap(os.listdir, 'listdir')),
        monkeypatch.setattr(os, 'scandir', wrap(os.scandir, 'scandir')),
        monkeypatch.setattr(os.path, 'isdir', wrap(os.path.isdir, 'isdir')),
        monkeypatch.setattr(csv_loader, 'open', wrap(open, 'open'), raising=False)]
    return state


@pytest.fixture
def window(app, monkeypatch):
    import main
    import ui.controllers.scan_controller as controller
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=SPEC_DEV))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    monkeypatch.setattr(win, '_save_settings', lambda: None)
    monkeypatch.setattr(controller, 'add_recent_folder', lambda s, _f: s)
    yield win
    for thread in win._background_threads():     # 중지했지만 아직 끝나지 않은 스레드 포함
        if hasattr(thread, 'cancel'):
            thread.cancel()
        thread.wait(10000)
    win._loader_thread = None
    win.deleteLater()
    app.processEvents()


@pytest.fixture
def fake_loader(monkeypatch):
    """데이터 로드는 시작만 기록한다 (폴더 확인 단계만 본다)."""
    from PySide6.QtCore import QObject, Signal
    import ui.controllers.scan_controller as controller
    started = []

    class FakeThread(QObject):
        finished = Signal(list, list, float)
        error = Signal(str, str)
        progress = Signal(str)
        cancelled = Signal()
        count = Signal(int, int)

        def __init__(self, folder, recipes=None, session=None):
            super().__init__()
            self.folder, self.recipes, self.session = folder, recipes, session

        def start(self):
            started.append(self)

        def isRunning(self):
            return False

    monkeypatch.setattr(controller, 'DataLoaderThread', FakeThread)
    return started


def _logged(win):
    return '\n'.join(r['message'] for r in win.logger.export_rows())


def test_scan_button_returns_immediately_and_reads_in_worker(app, window, fake_loader,
                                                             slow_fs, tmp_path):
    root = tmp_path / 'data'
    _make_runs(root / '1. Vision Pattern', runs=2, lots=3)
    window.path_edit.setText(str(root))
    slow_fs['install']()

    t0 = time.perf_counter()
    window._scan_folder()
    returned = time.perf_counter() - t0

    assert returned < 0.15        # 확인할 폴더가 수십 개라 화면 스레드에서 했다면 1초가 넘는다
    assert not window.btn_scan.isEnabled() and not window.btn_workflow.isEnabled()
    assert not window.btn_cancel_scan.isHidden()

    _wait_for(app, lambda: window._scan_thread is None)

    assert slow_fs['main_calls'] == []
    assert slow_fs['worker_calls'] > 10
    assert [r['short_name'] for r in fake_loader[-1].recipes] == ['Vision Pattern']
    assert fake_loader[-1].session is not None       # 로드가 확인 결과를 다시 쓴다
    assert '폴더 확인' in _logged(window)


def test_progress_text_names_the_folder_being_checked():
    from ui.workers.folder_scan_thread import progress_text
    path = os.path.join('\\\\srv', 'share', 'Vision Pattern', '7', 'Sample2202 (9)')
    assert progress_text(37, 140, path) == '폴더 확인 중 37개 확인 — Vision Pattern/7'


def test_cancel_during_folder_check_keeps_previous_results(app, window, fake_loader,
                                                          slow_fs, tmp_path):
    root = tmp_path / 'data'
    _make_runs(root / '1. Vision Pattern', runs=3, lots=4)
    before_recipes = [{'name': 'R', 'short_name': 'R', 'index': 1, 'rounds': []}]
    before_results = [{'short_name': 'R', 'raw_data': []}]
    window.recipes, window.recipe_results = before_recipes, before_results
    window.path_edit.setText(str(root))
    slow_fs['delay'] = 0.05
    slow_fs['install']()

    window._scan_folder()
    window._cancel_scan()
    _wait_for(app, lambda: window._scan_thread is None)

    assert fake_loader == []
    assert window.recipes is before_recipes and window.recipe_results is before_results
    assert window.btn_scan.isEnabled() and window.btn_cancel_scan.isHidden()
    assert '중지' in window.statusBar().currentMessage()


def test_cancel_during_load_restores_previous_results(app, window, tmp_path, monkeypatch):
    from core.recipe_scanner import compare_recipes, load_all_recipes, recipes_from_mapping
    old = _make_runs(tmp_path / 'old', runs=1, lots=3) / '1'
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(old))], SPECS)
    results = load_all_recipes('', recipes=recipes)
    window.recipes = recipes
    window._build_nav()     # 로드 시작 때 하는 일
    window._loader_thread = type('Done', (), {'folder': str(old)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    badge = window.card_x.badge.text()
    assert window.step_buttons and window.recipe_results is results

    new_root = tmp_path / 'new'
    _make_runs(new_root / '1. Vision Pattern', runs=2, lots=3)
    gate = threading.Event()
    real = csv_loader.load_lot_data

    def blocked(path):
        gate.wait(10)
        return real(path)
    monkeypatch.setattr(csv_loader, 'load_lot_data', blocked)
    window.path_edit.setText(str(new_root))

    window._scan_folder()
    _wait_for(app, lambda: window._scan_thread is None and window._loader_thread is not None)
    assert window.recipe_results == []           # 로드 중에는 이전 판정을 숨긴다
    window._cancel_scan()
    gate.set()
    _wait_for(app, lambda: window._loader_thread is None)

    assert window.recipe_results is results and window.recipes is recipes
    assert window.folder_path != str(new_root)     # 중지한 로드는 다음 자동 스캔 대상을 바꾸지 않는다
    assert window.settings.get('recent_folders', []) == []
    assert [b.text() for b in window.step_buttons] == ['Step 1: Vision Pattern']
    assert window.card_x.badge.text() == badge
    assert window.btn_scan.isEnabled()
    assert '로드 중지' in window.statusBar().currentMessage()


def test_full_flow_logs_phase_timings(app, window, tmp_path):
    root = tmp_path / 'data'
    _make_runs(root / '1. Vision Pattern', runs=2, lots=3)
    window.path_edit.setText(str(root))

    window._scan_folder()
    _wait_for(app, lambda: window._scan_thread is None and window._loader_thread is None)

    log = _logged(window)
    for phase in ('폴더 확인', '데이터 로드', '화면 갱신'):
        assert f'⏱ {phase} ' in log
    assert window.recipe_results and window.recipe_results[0].get('run_results')


def test_workflow_dialog_opens_immediately_and_checks_in_background(app, window, slow_fs,
                                                                    fake_loader, tmp_path,
                                                                    monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox
    from ui.dialogs.workflow_folder_dialog import CHECKING, WorkflowFolderDialog
    vp = _make_runs(tmp_path / 'vp', runs=2, lots=3)
    window.settings['workflow_folder_map'] = {'Vision Pattern': {'path': str(vp), 'enabled': True}}
    seen = {}
    reads = []
    real_build = recipe_scanner.build_recipe

    def counting_build(path, *a, **k):
        reads.append(path)
        return real_build(path, *a, **k)
    import core.workflow_map as wm
    monkeypatch.setattr(wm, 'build_recipe', counting_build)
    monkeypatch.setattr(recipe_scanner, 'build_recipe', counting_build)

    def user(dlg):
        seen['opened'] = time.perf_counter() - seen['t0']
        seen['first_status'] = dlg._rows['Vision Pattern'][2].text()
        seen['ok_while_checking'] = dlg.buttons.button(QDialogButtonBox.Ok).isEnabled()
        _wait_for(app, lambda: not dlg._prefill_pending and not dlg._pending)
        seen['status'] = dlg._rows['Vision Pattern'][2].text()
        seen['reads_before_ok'] = list(reads)
        dlg._accept_if_valid()
        seen['reads_after_ok'] = list(reads)
        return dlg.result()

    monkeypatch.setattr(WorkflowFolderDialog, 'exec', user)
    slow_fs['install']()

    seen['t0'] = time.perf_counter()
    window._open_workflow_dialog()

    assert seen['opened'] < 0.15
    assert seen['first_status'] == CHECKING and seen['ok_while_checking'] is False
    assert seen['status'].startswith('✅ 회차 2개')
    assert slow_fs['main_calls'] == []
    assert seen['reads_after_ok'] == seen['reads_before_ok']     # 확인 버튼은 다시 읽지 않는다
    _wait_for(app, lambda: bool(fake_loader))      # Recipe 만들기도 스캔 스레드에서 한다
    assert slow_fs['main_calls'] == []
    recipes = fake_loader[-1].recipes
    assert [r['lot_count'] for r in recipes[0]['rounds']] == [3, 3]
    assert len(reads) == len(seen['reads_before_ok']) + 1        # Recipe는 세션에서 꺼낸다


def test_startup_checks_last_folder_off_the_ui_thread(app, monkeypatch, fake_loader,
                                                      slow_fs, tmp_path):
    import main
    import ui.controllers.scan_controller as controller
    root = tmp_path / 'data'
    _make_runs(root / '1. Vision Pattern', runs=1, lots=2)
    monkeypatch.setattr(main, 'load_settings',
                        lambda: dict(spec_deviation=SPEC_DEV, last_folder=str(root)))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    monkeypatch.setattr(controller, 'add_recent_folder', lambda s, _f: s)
    slow_fs['install']()

    win = main.DataAnalyzerApp()
    try:
        monkeypatch.setattr(win, '_save_settings', lambda: None)
        assert win.path_edit.text() == str(root)
        assert slow_fs['main_calls'] == []     # 창을 만드는 동안 폴더를 묻지 않는다
        _wait_for(app, lambda: bool(fake_loader))
        _wait_for(app, lambda: win._scan_thread is None)
        assert slow_fs['main_calls'] == []
        assert slow_fs['worker_calls'] > 0
    finally:
        win.deleteLater()
        app.processEvents()


def test_startup_with_missing_folder_warns_only_in_log(app, monkeypatch, tmp_path):
    import main
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: shown.append(a)))
    missing = str(tmp_path / 'gone')
    monkeypatch.setattr(main, 'load_settings',
                        lambda: dict(spec_deviation=SPEC_DEV, last_folder=missing))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    try:
        _wait_for(app, lambda: '지난 폴더를 찾을 수 없어' in _logged(win))
        _wait_for(app, lambda: win._scan_thread is None)
        assert shown == [] and win.btn_scan.isEnabled()
    finally:
        win.deleteLater()
        app.processEvents()


def test_tiff_double_click_lists_folders_in_worker(app, window, slow_fs, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QTableWidgetItem
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: None))
    run = tmp_path / 'run'
    _write_lot(run / 'Sample2202 (9)')
    window.recipes = [{'name': 'R', 'short_name': 'R', 'index': 1, 'rounds': []}]
    window.recipe_results = [{'short_name': 'R', 'round_path': str(run), 'raw_data': []}]
    window.current_recipe_idx = 0
    window.raw_table.setRowCount(1)
    window.raw_table.setItem(0, 0, QTableWidgetItem('Sample2202 (9)'))
    window.raw_table.setItem(0, 1, QTableWidgetItem('0002_X002_Y000'))
    slow_fs['install']()

    t0 = time.perf_counter()
    window._on_row_double_click(0, 0)
    assert time.perf_counter() - t0 < 0.1
    _wait_for(app, lambda: window._tiff_thread is None)

    assert slow_fs['main_calls'] == []
    assert window.last_tiff_folder == os.path.normpath(str(run / 'Sample2202 (9)'))


def test_find_tiffs_for_row_matches_site_then_number(tmp_path):
    from ui.controllers.tiff_controller import find_tiffs_for_row
    lot = _write_lot(tmp_path / 'run' / 'Sample2202 (9)')
    (lot / 'Capture').mkdir()
    (lot / 'Capture' / 'Sample22_0001_X001_Y000.tiff').write_bytes(b'')

    matched, folder, message = find_tiffs_for_row(str(tmp_path / 'run'), 'Sample2202 (9)',
                                                  '0001_X001_Y000')
    assert message == '' and folder == str(lot)
    assert matched == [os.path.normpath(str(lot / 'Sample22_0001_X001_Y000.tiff'))]
    assert find_tiffs_for_row('', 'x', 'y') == ([], '', 'TIFF 탐색 실패: round_path 없음')


# ── 리뷰 반영: 네트워크 일시 오류, 대화상자 확인 순서, 실패 재확인, 닫기, 늦은 결과 ────────

def _flaky_folder_check(monkeypatch, target, failures):
    """target Lot 폴더 판별이 처음 failures번 OSError를 낸다 (SMB 일시 오류 흉내)."""
    real = csv_loader._is_data_folder
    calls = {'n': 0}

    def flaky(path, *args):
        if os.path.basename(path) == target:
            calls['n'] += 1
            if calls['n'] <= failures:
                raise OSError(64, '지정된 네트워크 이름을 더 이상 사용할 수 없습니다')
        return real(path, *args)

    monkeypatch.setattr(csv_loader, '_is_data_folder', flaky)
    monkeypatch.setattr(csv_loader, 'FOLDER_RETRY_DELAY', 0)
    return calls


def _eleven_lots(root):
    for k in range(11):
        _write_lot(root / f'Sample{k:02d}', repeat=k + 1, start=f'{10 + k:02d}:00:00')
    return root


def test_transient_network_error_is_retried_and_keeps_every_lot(tmp_path, monkeypatch):
    root = _eleven_lots(tmp_path / 'run')
    calls = _flaky_folder_check(monkeypatch, 'Sample05', failures=1)
    session = ScanSession()

    lots = csv_loader.scan_lot_folders(str(root), session)

    assert calls['n'] == 2 and len(lots) == 11
    assert session.errors == {}
    assert csv_loader.session_key(str(root)) in session.lots      # 성공했으니 담는다
    info = {}
    csv_loader.batch_load(str(root), info=info, session=session)
    assert info['first_repeat_excluded'] == 'Sample00'           # 11개라 첫 리핏 제외가 켜진다


def test_persistent_network_error_is_reported_and_not_cached(tmp_path, monkeypatch):
    from core.checklist import analysis_warnings
    root = _eleven_lots(tmp_path / 'vp')
    _flaky_folder_check(monkeypatch, 'Sample05', failures=99)
    session = ScanSession()

    lots = csv_loader.scan_lot_folders(str(root), session)
    assert len(lots) == 10
    failed_path = os.path.join(str(root), 'Sample05')
    assert list(session.errors) == [failed_path]
    assert csv_loader.session_key(str(root)) not in session.lots   # 다음에 다시 확인한다
    ok, text = describe_folder(str(root), session)
    assert ok and '⚠ Lot 폴더 1개 확인 실패' in text
    assert session.status == {} and session.rounds == {}

    errors = []
    csv_loader.batch_load(str(root), errors=errors, session=session)
    assert errors == [csv_loader.folder_check_warning(failed_path, errors[0].split('(', 1)[1][:-1])]
    assert 'Lot 폴더 확인 실패' in errors[0] and '결과에서 빠졌을 수 있음' in errors[0]
    result = recipe_scanner.load_all_recipes(
        '', recipes=[recipe_scanner.build_recipe(str(root), 'Vision Pattern', session=session)],
        session=session)[0]
    assert any('Lot 폴더 확인 실패' in w for w in analysis_warnings(result))   # 카드·내보내기로 간다


def test_failed_folder_check_is_retried_by_the_next_check(tmp_path):
    session = ScanSession()
    folder = tmp_path / 'later'
    assert describe_folder(str(folder), session) == (False, '❌ 폴더 없음')
    assert session.status == {} and session.dirs == {}         # 실패는 담지 않는다
    _write_lot(folder / 'Sample1', repeat=2)
    assert describe_folder(str(folder), session) == (True, '✅ Lot 1개 · (root)')


def _dialog(app, session, monkeypatch, describe=None):
    import ui.dialogs.workflow_folder_dialog as module
    from ui.dialogs.workflow_folder_dialog import WorkflowFolderDialog
    if describe is not None:
        monkeypatch.setattr(module, 'describe_folder', describe)
    empty = {key: {'path': '', 'enabled': False, 'note': ''} for key in SPECS}
    return WorkflowFolderDialog(None, SPECS, '', {}, '', session=session, prefill=empty)


def test_dialog_skips_and_cancels_stale_checks(app, tmp_path, monkeypatch):
    from core.csv_loader import ScanCancelled
    seen, release, started = [], threading.Event(), threading.Event()
    blocker, typed, final = str(tmp_path / 'C'), str(tmp_path / 'A'), str(tmp_path / 'B')

    def describe(path, session):
        seen.append(path)
        if path == blocker:          # 작업 스레드를 잡아 둔다 (느린 공유 폴더 루트)
            started.set()
            release.wait(5)
        if path == typed:
            raise AssertionError('바뀐 경로를 확인했다')
        if path == final + '_slow':
            started.set()
            while not session.cancelled:     # 확인 중에 사용자가 경로를 바꾼다
                time.sleep(0.01)
            raise ScanCancelled()
        return True, f'✅ {os.path.basename(path)}'

    dlg = _dialog(app, ScanSession(), monkeypatch, describe)
    try:
        ida = dlg._rows['In-Die Align'][1]
        ida.setText(blocker)
        dlg._update_status('In-Die Align')
        assert started.wait(5)
        vp = dlg._rows['Vision Pattern'][1]
        vp.setText(typed)
        dlg._update_status('Vision Pattern')     # 대기열에 들어갔지만 아직 돌지 않았다
        vp.setText(final)
        dlg._update_status('Vision Pattern')
        release.set()
        _wait_for(app, lambda: not dlg._pending)
        assert typed not in seen                 # 바뀐 경로의 확인은 건너뛴다
        assert dlg._rows['Vision Pattern'][2].text() == '✅ B'

        # 진행 중인 확인은 경로를 바꾸면 멈춘다 (다음 확인을 막지 않는다)
        started.clear()
        vp.setText(final + '_slow')
        dlg._update_status('Vision Pattern')
        assert started.wait(5)
        vp.setText(final + '_next')
        dlg._update_status('Vision Pattern')
        _wait_for(app, lambda: not dlg._pending)
        assert dlg._rows['Vision Pattern'][2].text() == '✅ B_next'
    finally:
        release.set()
        dlg.done(0)
        dlg.deleteLater()


def test_dialog_rechecks_a_failed_path_when_reentered_or_on_recheck(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox
    session = ScanSession()
    dlg = _dialog(app, session, monkeypatch)
    folder = tmp_path / 'vp'
    try:
        check, edit, status, _timer = dlg._rows['Vision Pattern']
        edit.setText(str(folder))
        dlg._update_status('Vision Pattern')
        _wait_for(app, lambda: not dlg._pending)
        assert status.text() == '❌ 폴더 없음'
        check.setChecked(True)
        assert not dlg.buttons.button(QDialogButtonBox.Ok).isEnabled()

        _write_lot(folder / 'Sample1', repeat=2)
        edit.setText('')                     # 같은 경로를 다시 입력한다
        edit.setText(str(folder))
        dlg._update_status('Vision Pattern')
        _wait_for(app, lambda: not dlg._pending)
        assert status.text() == '✅ Lot 1개 · (root)'
        assert dlg.buttons.button(QDialogButtonBox.Ok).isEnabled()

        # '다시 확인'은 세션 결과를 지우고 다시 읽는다
        _write_lot(folder / 'Sample2', repeat=3)
        dlg.recheck_btn.click()
        _wait_for(app, lambda: not dlg._pending)
        assert status.text() == '✅ Lot 2개 · (root)'
    finally:
        dlg.done(0)
        dlg.deleteLater()


class _StuckThread:
    """네트워크 읽기에 묶여 끝나지 않는 스레드."""

    def __init__(self):
        self.cancelled = False
        self.waits = []

    def isRunning(self):
        return True

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=None):
        self.waits.append(ms)
        return False

    def terminate(self):
        raise AssertionError('terminate()를 부르면 안 된다')


def test_close_cancels_and_waits_briefly_without_terminate(app, window):
    from PySide6.QtGui import QCloseEvent
    scan, loader = _StuckThread(), _StuckThread()
    window._scan_thread, window._loader_thread = scan, loader
    try:
        t0 = time.perf_counter()
        window.closeEvent(QCloseEvent())
        assert time.perf_counter() - t0 < 3
        assert scan.cancelled and loader.cancelled
        assert all(ms is not None and ms <= 1500 for ms in scan.waits + loader.waits)
        assert window.background_busy()          # main()이 프로세스를 끝낸다
    finally:
        window._scan_thread = window._loader_thread = None


def test_late_folder_check_result_after_cancel_is_ignored(app, window, fake_loader, tmp_path,
                                                         monkeypatch):
    import ui.controllers.scan_controller as controller
    release = threading.Event()

    def slow_detect(folder, spec_keys, remembered, session):
        release.wait(5)              # 중지에도 끝까지 읽어 결과를 낸다
        return {'action': 'load', 'recipes': [{'name': 'R', 'short_name': 'R', 'index': 1,
                                               'rounds': []}]}

    monkeypatch.setattr(controller, 'detect_scan_target', slow_detect)
    window.path_edit.setText(str(tmp_path))
    window._scan_folder()
    thread = window._scan_thread
    assert window._cancel_shortcut.isEnabled()

    window._cancel_scan()
    assert window.btn_scan.isEnabled() and window._scan_thread is None     # 바로 되살린다
    assert not window._cancel_shortcut.isEnabled()
    release.set()
    _wait_for(app, lambda: not thread.isRunning())
    _wait_for(app, lambda: thread not in window._retired())

    assert fake_loader == []                     # 늦은 결과로 로드를 시작하지 않는다
    assert window.recipes == []


def test_done_signal_of_a_cancelled_session_takes_the_cancelled_path(app, window, fake_loader,
                                                                    tmp_path, monkeypatch):
    import ui.controllers.scan_controller as controller
    monkeypatch.setattr(controller.FolderScanThread, 'start', lambda self: self.run())

    def detect_then_cancelled(folder, spec_keys, remembered, session):
        session.cancel()             # 다 읽은 뒤 중지 요청이 들어왔다
        return {'action': 'load', 'recipes': [{'name': 'R', 'short_name': 'R', 'index': 1,
                                               'rounds': []}]}

    monkeypatch.setattr(controller, 'detect_scan_target', detect_then_cancelled)
    window.path_edit.setText(str(tmp_path))
    window._scan_folder()

    assert fake_loader == []
    assert '중지' in window.statusBar().currentMessage()


def test_late_tiff_result_from_a_previous_scan_is_dropped(app, window, tmp_path):
    from ui.controllers.tiff_controller import TiffLoadThread
    window.last_tiff_folder = ''
    thread = TiffLoadThread('', 'Lot', 'Site')
    thread.generation = window._scan_generation
    window._tiff_thread = thread
    window._next_generation()            # 그사이 다시 스캔했다
    window._on_tiff_loaded({'lot_name': 'Lot', 'site_id': 'Site', 'matched': ['x.tiff'],
                            'lot_folder': str(tmp_path), 'message': '', 'error': '',
                            'results': [], 'failures': [], 'no_pspylib': False})
    assert window.last_tiff_folder == ''
    assert 'TIFF 결과를 버립니다' in _logged(window)
