"""
cli.py — 창 없이 AI 분석용 Markdown을 만드는 명령줄 (Qt를 쓰지 않는다)

    xy_stage_positioning.exe --llm-report <데이터 루트 폴더> --out <출력 .md> [--run <회차>]
    python src/main.py --llm-report <데이터 루트 폴더> --out <출력 .md> [--run <회차>]

화면의 'Scan → AI 분석용 Markdown'(전문 분석 창 도구) 과 같은 경로로 만든다: 폴더 확인(detect_scan_target) →
전체 로드(load_all_recipes) → 회차 요약(summarize_runs) → 쓰기(write_llm_report_files).
Workflow별 폴더 지정이 필요한 폴더는 대화상자를 열 수 없으므로 이름 자동 일치만 쓰고,
일치하지 않으면 묻지 않고 종료 코드 2로 끝낸다.

종료 코드: 0 성공, 2 입력 문제(폴더 없음·데이터 없음·Workflow 지정 필요), 1 예기치 않은 오류.
성공하면 stdout에 쓴 파일 경로(.md, 셀 값 CSV)를 한 줄씩 적는다.
"""

import argparse
import datetime
import os
import sys
import traceback

LLM_REPORT_FLAG = '--llm-report'
EXIT_OK, EXIT_ERROR, EXIT_BAD_INPUT = 0, 1, 2
# 실행 기록 폴더: %LOCALAPPDATA%\xy_stage_positioning\logs (PASS 빌드 이름과 같다)
LOG_APP_NAME = 'xy_stage_positioning'
LOG_FILE_NAME = 'cli.log'
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 256 * 1024


class BadInput(Exception):
    """종료 코드 2로 끝낼 입력 문제 (메시지는 stderr에 그대로 적는다)."""


class _Parser(argparse.ArgumentParser):
    # 창 빌드는 stderr가 없을 수 있다. argparse가 직접 출력·종료하지 않게 예외로 바꾼다
    def error(self, message):
        raise BadInput(f"인자 오류: {message}\n{self.format_usage().strip()}")


def wants_cli(argv) -> bool:
    """명령줄 모드로 실행할 인자인가 (main.py가 Qt를 불러오기 전에 확인한다)."""
    return any(a == LLM_REPORT_FLAG or a.startswith(LLM_REPORT_FLAG + '=') for a in argv)


def _parse(argv):
    parser = _Parser(prog='xy_stage_positioning', add_help=False,
                     description='창 없이 AI 분석용 Markdown과 셀 값 CSV를 만든다.')
    parser.add_argument(LLM_REPORT_FLAG, dest='root', required=True, metavar='ROOT',
                        help='데이터 루트 폴더 (화면의 Root Data 폴더와 같다)')
    parser.add_argument('--out', required=True, metavar='PATH', help='출력 .md 경로')
    parser.add_argument('--run', default=None, metavar='NAME',
                        help='회차 이름 (기본: 화면과 같이 데이터가 있는 마지막 회차)')
    return parser.parse_args(argv)


def _select_run(results, run_name):
    """회차가 여럿인 Workflow마다 run_name 회차를 고른다 (회차가 없는 Workflow 이름 목록을 돌려준다) (화면의 회차 콤보와 같다).
    회차가 하나뿐인 Workflow는 run_results 가 없고 자기 round 만 있다 — 이름이 같으면 찾은 것으로 친다."""
    from core.recipe_scanner import run_view
    found = False
    missing = []
    for i, result in enumerate(results):
        names = [str(r.get('round', '')) for r in result.get('run_results') or []]
        if not names and str(result.get('round', '')) == run_name:
            found = True
        elif run_name in names:
            found = True
            if str(result.get('round', '')) != run_name:
                results[i] = run_view(result, names.index(run_name))
        else:
            missing.append(str(result.get('recipe', i)))
    if not found:
        raise BadInput(f"회차 '{run_name}'이(가) 있는 Workflow가 없습니다.")
    return missing


def load_results(root, settings, run_name=None, warnings=None):
    """화면의 Scan과 같은 결과 목록. 입력 문제는 BadInput."""
    from core.checklist import summarize_runs
    from core.csv_loader import ScanSession
    from core.recipe_scanner import load_all_recipes
    from core.workflow_map import detect_scan_target

    spec_limits = settings.get('spec_limits', {})
    spec_deviation = settings.get('spec_deviation', {})
    session = ScanSession()
    # 명령줄은 지난 Workflow 지정(workflow_folder_map)을 쓰지 않는다. 이름이 맞아야 한다
    target = detect_scan_target(root, list(spec_deviation.keys()), {}, session)
    action = target['action']
    if action == 'invalid':
        raise BadInput(f"폴더를 찾을 수 없습니다: {root}")
    if action == 'none':
        # 화면과 같은 이유 문구가 있으면 같이 적는다 (예: Lot 폴더를 직접 골랐는데 Spec 없음)
        message = target.get('message')
        raise BadInput(f"Recipe 구조를 찾지 못했습니다: {root}"
                       + (f"\n{message}" if message else ''))
    if action == 'dialog':
        raise BadInput(f"{target['reason']}\n명령줄에서는 Workflow별 폴더를 지정할 수 없습니다. "
                       "앱에서 'Workflow별 선택'으로 분석하거나 폴더 이름을 Spec 이름과 맞춰 주세요.")

    results = load_all_recipes(root, round_name='1st', axis='both',
                               recipes=target['recipes'], session=session)
    for result in results:
        if result.get('run_results'):
            result['runs'] = summarize_runs(result, spec_limits, spec_deviation)
    has_data = any(r.get('raw_data') for r in results) or any(
        run.get('raw_data') for r in results for run in r.get('run_results') or [])
    if not has_data:
        raise BadInput(f"읽은 측정 데이터가 없습니다: {root}")
    if run_name is not None:
        missing = _select_run(results, run_name)
        if missing and warnings is not None:
            warnings.append(f"회차 '{run_name}'이(가) 없는 Workflow는 기본 회차를 그대로 썼습니다: "
                            + ', '.join(missing))
    return results


def export_llm_report(root, out, settings, run_name=None, now=None):
    """보고서를 쓰고 쓴 파일 경로 목록과 경고를 돌려준다. 입력 문제는 BadInput."""
    from core.llm_report import raw_csv_path, write_llm_report_files
    from core.settings import rotation_spec
    from core.version import APP_VERSION

    if os.path.isdir(out):
        raise BadInput(f"--out은 파일 경로여야 합니다 (폴더임): {out}")
    warnings = []
    results = load_results(root, settings, run_name, warnings)
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    now = now or datetime.datetime.now()
    state = write_llm_report_files(results, out, settings.get('spec_limits', {}),
                                   settings.get('spec_deviation', {}),
                                   app_version=APP_VERSION,
                                   exported_at=f"{now:%Y-%m-%d %H:%M:%S}",
                                   source_root=root,
                                   # 진단 절: 화면과 같은 회전 규격·장비 기준 폴더(스캔한 폴더).
                                   # 스캐너 회전 시험 결과는 화면에서 그때 불러오는 것이라 없다
                                   rotation_spec=rotation_spec(settings), tool_root=root)
    if state['error'] is not None:
        raise state['error']
    written = [out] + ([raw_csv_path(out)] if state['csv_written'] else [])
    if state['csv_error']:
        warnings.append(f"셀 값 CSV 저장 실패 (Markdown은 저장됨): {state['csv_error']}")
    return written, warnings


def _emit(stream, text):
    """창 빌드(콘솔 없음)는 stdout/stderr가 None이다. 출력 실패가 종료 코드를 바꾸지 않게 한다."""
    if stream is None:
        return
    try:
        stream.write(text + '\n')
        stream.flush()
    except UnicodeEncodeError:
        encoding = getattr(stream, 'encoding', None) or 'ascii'
        try:
            stream.write(text.encode(encoding, 'backslashreplace').decode(encoding) + '\n')
            stream.flush()
        except Exception:
            pass
    except Exception:
        pass


def log_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'),
                                                          'AppData', 'Local')
    return os.path.join(base, LOG_APP_NAME, 'logs')


def _trim_log(path):
    """cli.log가 LOG_MAX_BYTES를 넘으면 마지막 LOG_KEEP_BYTES만 남긴다 (줄 경계, 원자적 교체)."""
    if os.path.getsize(path) <= LOG_MAX_BYTES:
        return
    with open(path, 'rb') as f:
        f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
        tail = f.read()
    tail = tail[tail.find(b'\n') + 1:]
    temp = path + '.tmp'
    with open(temp, 'wb') as f:
        f.write(tail)
    os.replace(temp, path)


def _log(text):
    """실행 기록 한 줄 (창 빌드는 콘솔이 없어 여기서만 결과를 확인할 수 있다). 실패는 무시한다."""
    try:
        folder = log_dir()
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        path = os.path.join(folder, LOG_FILE_NAME)
        if os.path.exists(path):
            _trim_log(path)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f"{stamp} {text}\n")
    except Exception:
        pass


def main(argv=None, stdout=None, stderr=None, settings=None) -> int:
    """명령줄 진입점. 종료 코드를 돌려준다 (sys.exit는 호출하는 쪽이 한다)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = _parse(argv)
        root, out = os.path.abspath(args.root), os.path.abspath(args.out)
        if settings is None:
            from core.settings import load_settings
            settings = load_settings()
        written, warnings = export_llm_report(root, out, settings, args.run)
    except BadInput as e:
        _emit(stderr, str(e))
        _log(f"exit={EXIT_BAD_INPUT} {LLM_REPORT_FLAG} {argv!r}: {e}")
        return EXIT_BAD_INPUT
    except Exception as e:
        _emit(stderr, traceback.format_exc().rstrip())
        _log(f"exit={EXIT_ERROR} {LLM_REPORT_FLAG} {argv!r}: {type(e).__name__}: {e}")
        return EXIT_ERROR
    for text in warnings:
        _emit(stderr, text)
    for path in written:
        _emit(stdout, path)
    _log(f"exit={EXIT_OK} {LLM_REPORT_FLAG} {root} -> {', '.join(written)}"
         + (f" ({'; '.join(warnings)})" if warnings else ''))
    return EXIT_OK
