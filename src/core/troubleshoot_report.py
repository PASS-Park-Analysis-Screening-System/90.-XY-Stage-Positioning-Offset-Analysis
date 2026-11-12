"""
troubleshoot_report.py — 진단 결과(pass.xy.diagnosis/1)로 3D 트러블슈팅 HTML 보고서 만들기

보고서는 파일 하나다. 틀(resources/troubleshoot/report_template.html)의
`/*__PASS_DIAG__*/null` 자리에 진단 dict를 JSON으로 넣는다. 그림·3D·표는 모두 틀 안의
스크립트가 그 데이터로 그리며 네트워크·외부 글꼴·스크립트를 쓰지 않는다.

사례 라이브러리(resources/cases/index.html)는 끝난 조사의 HTML 페이지 모음이다.

리소스 위치: 소스 실행은 src/resources, 빌드본(onefile)은 sys._MEIPASS/resources
(tools/build_config.py의 DATAS 대상과 같아야 한다).

Qt를 쓰지 않는다 (화면·테스트가 같이 쓴다).
"""

import datetime
import json
import math
import os
import re
import shutil
import sys

DIAG_MARKER = '/*__PASS_DIAG__*/null'
REPORT_PREFIX = 'XY_troubleshoot'
# 파일 이름·URL에서 문제 되는 문자 (#·%·&·; 는 file:// URL에서 잘린다)
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#%&;\'\x00-\x1f\s]+')
MAX_NAME_TRIES = 1000


def _is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False)) and hasattr(sys, '_MEIPASS')


def resource_dir() -> str:
    """번들 리소스 폴더 (소스: src/resources, 빌드본: _MEIPASS/resources)."""
    if _is_frozen():
        return os.path.join(sys._MEIPASS, 'resources')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'resources')


def template_path() -> str:
    return os.path.join(resource_dir(), 'troubleshoot', 'report_template.html')


def case_library_path() -> str:
    """번들에 든 사례 라이브러리 목록 페이지(index.html) 경로."""
    return os.path.join(resource_dir(), 'cases', 'index.html')


def default_report_dir() -> str:
    """%LOCALAPPDATA%\\PASS\\XYStage\\reports (없으면 사용자 폴더 아래)."""
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'PASS', 'XYStage', 'reports')


def browsable_case_library(dest_root: str = None) -> str:
    """브라우저로 열 사례 라이브러리 index.html.

    소스 실행은 번들 경로를 그대로 쓴다. 빌드본(onefile)의 _MEIPASS는 앱을 끝내면
    지워지므로, 앱이 꺼진 뒤에도 페이지 사이 링크가 살아 있게 사용자 폴더
    (%LOCALAPPDATA%\\PASS\\XYStage\\cases)로 복사해서 연다.
    번들이 바뀌면(새 버전에서 페이지가 빠지거나 이름이 바뀌면) 옛 사본이 남지 않게 먼저 지운다
    (브라우저가 잡고 있어 못 지운 파일은 덮어쓴다).
    """
    src = case_library_path()
    if not _is_frozen():
        return src
    dest = dest_root or os.path.join(os.path.dirname(default_report_dir()), 'cases')
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(os.path.dirname(src), dest, dirs_exist_ok=True)
    return os.path.join(dest, 'index.html')


def _json_default(value):
    """numpy 수 같은 JSON 밖의 값: 파이썬 값으로 바꾸고, 안 되면 문자열."""
    for name in ('item', 'tolist'):
        method = getattr(value, name, None)
        if callable(method):
            try:
                return _clean(method())
            except (TypeError, ValueError):
                pass
    return str(value)


def _clean(value):
    """NaN·무한대는 null로 (스키마: 모르는 값은 null). UTF-8로 쓸 수 없는 문자(짝 없는 서러게이트)는 ?로."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value.encode('utf-8', 'replace').decode('utf-8')
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def diag_script_json(diag) -> str:
    """<script> 안에 그대로 넣어도 안전한 JSON 문자열.

    `</`는 `<\\/`로 바꿔 데이터 속 `</script>`가 태그를 닫지 못하게 하고, `<!--`도 막는다
    (스크립트 안의 `<!--<script`는 이어지는 `</script>`를 무력화한다).
    U+2028·U+2029는 오래된 JS 엔진에서 줄바꿈으로 읽히므로 이스케이프한다.
    """
    text = json.dumps(_clean(diag), ensure_ascii=False, default=_json_default, allow_nan=False)
    text = text.replace('</', '<\\/').replace('<!--', '<\\u0021--')
    return text.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def build_report_html(diag: dict) -> str:
    """진단 dict를 넣은 보고서 HTML 전체."""
    with open(template_path(), encoding='utf-8') as f:
        template = f.read()
    if template.count(DIAG_MARKER) != 1:
        raise ValueError('보고서 틀에 데이터 자리(/*__PASS_DIAG__*/null)가 정확히 한 번 있어야 합니다')
    return template.replace(DIAG_MARKER, diag_script_json(diag))


def _sample_label(diag) -> str:
    source = diag.get('source') if isinstance(diag, dict) else None
    sample = source.get('sample') if isinstance(source, dict) else None
    label = _UNSAFE_NAME.sub('_', str(sample or '')).strip('._')[:60]
    return label or 'unknown'


def write_report(diag, out_dir: str = None) -> str:
    """보고서 HTML과 진단 JSON을 나란히 쓰고 HTML 경로를 돌려준다.

    이름: XY_troubleshoot_<YYYYmmdd_HHMMSS>_<시료>.html / .json (같은 초에 또 만들면 _2, _3 …)
    """
    # 내용을 모두 만든 뒤에 파일을 연다 (만들다 실패하면 파일이 남지 않는다)
    html = build_report_html(diag).encode('utf-8')
    data = json.dumps(_clean(diag), ensure_ascii=False, indent=2, default=_json_default,
                      allow_nan=False).encode('utf-8')
    folder = out_dir or default_report_dir()
    os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, f'{REPORT_PREFIX}_{datetime.datetime.now():%Y%m%d_%H%M%S}_{_sample_label(diag)}')
    for n in range(1, MAX_NAME_TRIES + 1):
        base = stem if n == 1 else f'{stem}_{n}'
        written = []
        try:
            # 'x': 이미 있으면 FileExistsError — 확인과 만들기 사이에 다른 창이 끼어들 수 없다
            for ext, payload in (('.html', html), ('.json', data)):
                with open(base + ext, 'xb') as f:
                    written.append(base + ext)
                    f.write(payload)
        except FileExistsError:
            _remove(written)
            continue
        except BaseException:
            _remove(written)     # 반쯤 쓴 파일을 남기지 않는다
            raise
        return base + '.html'
    raise FileExistsError(f'보고서 이름을 정하지 못했습니다: {stem}')


def _remove(paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass
