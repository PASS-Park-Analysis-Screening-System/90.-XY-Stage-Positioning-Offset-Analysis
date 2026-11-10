"""명령줄 --llm-report (core.cli) — 창 없이 화면 내보내기와 같은 Markdown·CSV를 쓴다."""
import io
import json
import os
import subprocess
import sys

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import cli
from core.llm_report import REPORT_SCHEMA
from test_llm_report import HEADER, SPEC_DEV, SPEC_LIM, _make_runs

SETTINGS = {'spec_deviation': SPEC_DEV, 'spec_limits': SPEC_LIM}
SRC_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'main.py')


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    # 실행 기록이 실제 %LOCALAPPDATA%에 쌓이지 않게 한다
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))


def _root(tmp_path):
    """자동 스캔 루트: 회차 2개인 Vision Pattern + 회차 없는 Global Align."""
    root = tmp_path / 'root'
    _make_runs(root / '1. Vision Pattern', ['1', '2'])
    _make_runs(root / '4. Global Align', [None])
    return root


def _run(argv, settings=SETTINGS):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main([str(a) for a in argv], stdout=out, stderr=err, settings=settings)
    return code, out.getvalue(), err.getvalue()


def _front(md):
    assert md.startswith('---\n')
    return yaml.safe_load(md.split('---\n')[1])


def _log_text(tmp_path):
    return (tmp_path / 'appdata' / cli.LOG_APP_NAME / 'logs' / cli.LOG_FILE_NAME).read_text(
        encoding='utf-8')


def test_writes_md_and_csv(tmp_path):
    root, out = _root(tmp_path), tmp_path / 'out' / 'report.md'
    code, stdout, stderr = _run(['--llm-report', root, '--out', out])
    assert code == 0, stderr
    csv_path = tmp_path / 'out' / 'report_raw.csv'
    assert stdout.splitlines() == [str(out), str(csv_path)]
    md = out.read_text(encoding='utf-8')
    front = _front(md)
    assert front['report_schema'] == REPORT_SCHEMA == 'xy-stage-llm-report/1'
    assert front['data_source'] == {'mode': 'auto', 'root': str(root)}
    assert [w['name'] for w in front['workflows']] == ['Vision Pattern', 'Global Align']
    assert front['workflows'][0]['selected_run'] == '2'     # 화면 기본: 데이터가 있는 마지막 회차
    assert 'raw_csv: "report_raw.csv"' in md
    assert csv_path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert b'\r\n' not in out.read_bytes()
    assert not list((tmp_path / 'out').glob('*.part'))
    assert 'exit=0' in _log_text(tmp_path)


def test_same_files_as_gui_export(tmp_path, monkeypatch):
    """화면 Scan → AI 분석용 Markdown과 같은 바이트 (exported_at 줄만 다르다)."""
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    import main
    from core.checklist import summarize_runs
    from core.csv_loader import ScanSession
    from core.workflow_map import detect_scan_target
    from ui.controllers import export_controller
    from ui.workers.data_loader_thread import DataLoaderThread

    root = _root(tmp_path)
    assert _run(['--llm-report', root, '--out', tmp_path / 'cli.md'])[0] == 0

    # 화면 경로: 스캔 스레드의 판별 → 로드 스레드 → 로드 완료 처리(회차 요약)
    session = ScanSession()
    target = detect_scan_target(str(root), list(SPEC_DEV), {}, session)
    loader = DataLoaderThread(str(root), recipes=target['recipes'], session=session)
    loaded = []
    loader.finished.connect(lambda results, comparison, elapsed: loaded.append(results))
    loader.run()
    results = loaded[0]
    for result in results:
        if result.get('run_results'):
            result['runs'] = summarize_runs(result, SPEC_LIM, SPEC_DEV)

    monkeypatch.setattr(main, 'load_settings', lambda: dict(SETTINGS))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    try:
        win.recipe_results = results
        win._scanned_folder = str(root)
        gui_md = tmp_path / 'gui.md'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                            staticmethod(lambda *a, **k: (str(gui_md), '')))
        started = []
        monkeypatch.setattr(export_controller.threading, 'Thread',
                            lambda target, daemon: type('T', (), {
                                'start': lambda self: started.append(target)})())
        win._export_llm_markdown()
        started[0]()
    finally:
        win.deleteLater()
        app.processEvents()

    def body(path):
        return [line for line in path.read_text(encoding='utf-8').splitlines()
                if not line.startswith('exported_at:')]
    cli_lines, gui_lines = body(tmp_path / 'cli.md'), body(gui_md)
    assert len(cli_lines) > 100
    # 1.1.5의 진단 절도 화면과 같이 들어간다
    assert '## 트러블슈팅 진단 (Diagnosis)' in cli_lines
    assert '## 비전 보정 로그 대조 (Positioning Log)' in cli_lines
    # CSV 이름 줄만 다르다 (cli_raw.csv / gui_raw.csv)
    differ = [(a, b) for a, b in zip(cli_lines, gui_lines) if a != b]
    assert len(cli_lines) == len(gui_lines)
    assert all('cli_raw.csv' in a and 'gui_raw.csv' in b for a, b in differ)
    assert (tmp_path / 'cli_raw.csv').read_bytes() == (tmp_path / 'gui_raw.csv').read_bytes()


def test_run_option_selects_run(tmp_path):
    root, out = _root(tmp_path), tmp_path / 'r.md'
    code, _, stderr = _run(['--llm-report', root, '--out', out, '--run', '1'])
    assert code == 0, stderr
    workflows = _front(out.read_text(encoding='utf-8'))['workflows']
    assert workflows[0]['selected_run'] == '1'
    assert workflows[1]['selected_run'] == '(root)'      # 회차가 없는 Workflow는 그대로
    code, _, stderr = _run(['--llm-report', root, '--out', out, '--run', '9'])
    assert code == 2 and "회차 '9'" in stderr


def test_run_option_accepts_single_run_workflow():
    """회차가 하나뿐인 Workflow(run_results 없음)도 그 round 이름으로 찾는다 (실데이터 '2nd' 하나뿐)."""
    from core.cli import _select_run
    results = [{'recipe': 'A', 'round': '2nd', 'raw_data': [1]},
               {'recipe': 'B', 'round': '2nd', 'raw_data': [1]}]
    _select_run(results, '2nd')
    assert [r['round'] for r in results] == ['2nd', '2nd']


def test_missing_folder_is_bad_input(tmp_path):
    out = tmp_path / 'x.md'
    code, stdout, stderr = _run(['--llm-report', tmp_path / 'nope', '--out', out])
    assert code == 2 and stdout == ''
    assert '폴더를 찾을 수 없습니다' in stderr
    assert not out.exists()
    assert 'exit=2' in _log_text(tmp_path)


def test_folder_without_recipes_is_bad_input(tmp_path):
    (tmp_path / 'empty' / 'misc').mkdir(parents=True)
    code, _, stderr = _run(['--llm-report', tmp_path / 'empty', '--out', tmp_path / 'x.md'])
    assert code == 2 and 'Recipe 구조를 찾지 못했습니다' in stderr


@pytest.mark.parametrize('layout', ['mismatched', 'flat', 'self_lot'])
def test_workflow_mapping_needed_is_bad_input_without_prompt(tmp_path, layout):
    root = tmp_path / 'root'
    if layout == 'mismatched':
        _make_runs(root / 'Pattern Test', [None])    # Spec 이름과 다른 폴더
    elif layout == 'flat':
        _make_runs(root, [None])                     # Lot이 루트 바로 아래
    else:
        _make_runs(tmp_path / 'vp', [None], lots=1)  # Lot 폴더 자체를 고름 (1.1.5)
        root = tmp_path / 'vp' / 'Lot01'
    out = tmp_path / 'x.md'
    code, _, stderr = _run(['--llm-report', root, '--out', out])
    assert code == 2
    assert '명령줄에서는 Workflow별 폴더를 지정할 수 없습니다' in stderr
    assert not out.exists()


def test_self_lot_without_spec_explains_why(tmp_path):
    _make_runs(tmp_path / 'vp', [None], lots=1)
    code, _, stderr = _run(['--llm-report', tmp_path / 'vp' / 'Lot01', '--out', tmp_path / 'x.md'],
                           settings={'spec_deviation': {}, 'spec_limits': {}})
    assert code == 2 and 'Recipe 구조를 찾지 못했습니다' in stderr
    assert 'Lot 폴더입니다' in stderr       # 화면과 같은 이유 문구 (SELF_LOT_NO_SPEC)


def test_bad_arguments_and_out_folder(tmp_path):
    code, _, stderr = _run(['--llm-report', tmp_path])
    assert code == 2 and '--out' in stderr
    code, _, stderr = _run(['--llm-report', _root(tmp_path), '--out', tmp_path])
    assert code == 2 and '폴더임' in stderr


def test_no_measurement_data_is_bad_input(tmp_path):
    lot = tmp_path / 'root' / '1. Vision Pattern' / 'Lot01'
    lot.mkdir(parents=True)
    # 머리글만 있고 측정 행이 없다
    (lot / 'Sample10_X_UL.csv').write_text('Lot ID,Lot1\n\n' + HEADER, encoding='utf-8')
    code, _, stderr = _run(['--llm-report', tmp_path / 'root', '--out', tmp_path / 'x.md'])
    assert code == 2 and '측정 데이터가 없습니다' in stderr
    assert not (tmp_path / 'x.md').exists()


def test_unexpected_error_is_exit_1_with_traceback(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError('뜻밖의 오류')
    monkeypatch.setattr(cli, 'load_results', boom)
    code, _, stderr = _run(['--llm-report', tmp_path, '--out', tmp_path / 'x.md'])
    assert code == 1
    assert 'Traceback' in stderr and 'RuntimeError: 뜻밖의 오류' in stderr
    assert 'exit=1' in _log_text(tmp_path)


def test_no_console_streams(tmp_path, monkeypatch):
    """창 빌드는 stdout/stderr가 None이다. 출력 없이도 같은 종료 코드다."""
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    assert cli.main(['--llm-report', str(_root(tmp_path)), '--out', str(tmp_path / 'a.md')],
                    settings=SETTINGS) == 0
    assert cli.main(['--llm-report', str(tmp_path / 'nope'), '--out', str(tmp_path / 'b.md')],
                    settings=SETTINGS) == 2
    assert (tmp_path / 'a.md').exists()


def test_wants_cli():
    assert cli.wants_cli(['--llm-report', 'D:/x', '--out', 'a.md'])
    assert cli.wants_cli(['--llm-report=D:/x'])
    assert not cli.wants_cli([])
    assert not cli.wants_cli(['D:/--llm-report'])


def test_main_py_runs_without_qt(tmp_path):
    """python src/main.py --llm-report … 는 Qt를 불러오지 않고 끝난다 (창이 뜨지 않는다)."""
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / 'appdata'))
    proc = subprocess.run([sys.executable, '-X', 'importtime', SRC_MAIN, '--llm-report',
                           str(tmp_path / 'nope'), '--out', str(tmp_path / 'x.md')],
                          capture_output=True, timeout=120, env=env)
    assert proc.returncode == 2
    assert b'PySide6' not in proc.stderr and b'matplotlib' not in proc.stderr


def test_launch_status_ready_line(tmp_path, monkeypatch):
    import main
    status = tmp_path / 'status.jsonl'
    status.write_text('{"event": "progress", "pct": 10, "message": "x"}\n', encoding='utf-8')
    monkeypatch.setenv('PASS_LAUNCH_STATUS_FILE', str(status))
    main.report_launch_status('ready')
    lines = status.read_text(encoding='utf-8').splitlines()
    assert json.loads(lines[-1]) == {'event': 'ready'} and len(lines) == 2

    monkeypatch.setenv('PASS_LAUNCH_STATUS_FILE', str(tmp_path))   # 폴더라 쓸 수 없다
    main.report_launch_status('ready')                              # 예외 없이 지나간다
    monkeypatch.delenv('PASS_LAUNCH_STATUS_FILE')
    main.report_launch_status('ready')
