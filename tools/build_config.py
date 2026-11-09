"""
build_config.py — 빌드 설정의 단일 출처(single source of truth).

PyInstaller spec, 빌드 검증 스크립트, 빌드 매니저 입력값 출력이 **모두 이 파일 하나**를
참조한다. 같은 정보를 spec/build.bat/외부 빌드 매니저 DB에 각각 적어 두면 반드시
갈라지고, 실제로 그랬다:

    build.bat                  assets 대상이 src/assets  (틀림)
    xy_stage_offset.spec       datas=[] hiddenimports=[] (비어 있음)
    xy_stage_positioning.spec  assets 대상이 assets      (맞음, 그러나 미추적)
    빌드 매니저 필드            비어 있음                 (틀림)

그 결과 배포된 EXE에서 체크리스트 가이드 이미지가 뜨지 않았다.

값을 바꿀 때는 여기만 고치고, `python tools/verify_build.py` 로 결과를 확인한다.
"""

import json
import os
import re

# 프로젝트 루트 (이 파일의 상위 폴더)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY_SCRIPT = os.path.join('src', 'main.py')
EXE_NAME = 'XYStageOffset'

# ──────────────────────────────────────────────────────────────────────────
# 번들 리소스: (저장소 내 상대 경로, EXE 내부 대상 폴더)
#
# 대상 폴더는 **런타임 조회 경로와 반드시 일치**해야 한다. onefile에서는
# sys._MEIPASS 아래에 이 경로 그대로 풀린다. 틀려도 빌드는 성공하고 실행 중에
# 조용히 실패하므로, verify_build.py가 빌드 후 실제 아카이브를 검사한다.
# ──────────────────────────────────────────────────────────────────────────
DATAS = [
    # ui/controllers/ui_builder_mixin.py 가 __file__ 기준 ..\..\assets 로 찾는다
    #   → _MEIPASS/assets/checklist_guide.png
    (os.path.join('src', 'assets'), 'assets'),

    # core/settings.py 가 os.path.join(sys._MEIPASS, 'core', 'settings.json') 로 찾는다
    (os.path.join('src', 'core', 'settings.json'), 'core'),
]

# 빌드 후 EXE 아카이브에 존재해야 하는 파일 (verify_build.py가 검사)
REQUIRED_IN_BUNDLE = [
    os.path.join('assets', 'checklist_guide.png'),
    os.path.join('core', 'settings.json'),
]

# ──────────────────────────────────────────────────────────────────────────
# hidden imports
#   main.py가 sys.path에 src를 넣고 쓰므로 런타임 패키지명은 core/ui/charts 다.
#   (src.core 같은 이름은 존재하지 않아 무효였다)
# ──────────────────────────────────────────────────────────────────────────
HIDDEN_IMPORTS = ['core', 'ui', 'charts']

# --collect-all 대상. pspylib은 사내 wheel이며 없으면 TIFF 보기만 비활성된다.
COLLECT_ALL = ['pspylib']


# ──────────────────────────────────────────────────────────────────────────
# 번들 설정 검사: 빌드한 PC의 개인·고객 경로가 EXE에 들어가면 빌드를 실패시킨다.
# ──────────────────────────────────────────────────────────────────────────
SETTINGS_SOURCE = os.path.join('src', 'core', 'settings.json')
VERSION_SOURCE = os.path.join('src', 'core', 'version.py')
_MUST_BE_EMPTY = ('last_folder', 'recent_folders', 'workflow_folder_map')
_PATH_LIKE = re.compile(
    r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]'     # 드라이브 경로 (문자열 중간 포함)
    r'|[\\/]{2}[^\\/\s]'                 # UNC, URI
    r'|[\\/](users|home|mnt)[\\/]',       # 사용자·마운트 경로
    re.IGNORECASE)


def _strings_in(value, where):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            path = f'{where}.{key}' if where else str(key)
            if isinstance(key, str) and _PATH_LIKE.search(key):
                yield f'{where}.<키>' if where else '<키>', key
            yield from _strings_in(item, '<경로 키>' if _PATH_LIKE.search(str(key)) else path)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings_in(item, f'{where}[{i}]')


def settings_privacy_errors(settings: dict) -> list:
    """번들에 넣으면 안 되는 값의 위치 목록. 값 자체는 로그에 남기지 않는다."""
    errors = [f'{key}: 비어 있어야 합니다' for key in _MUST_BE_EMPTY if settings.get(key)]
    errors += [f'{where}: 경로처럼 보이는 값이 있습니다'
               for where, text in _strings_in(settings, '')
               if _PATH_LIKE.search(text)]
    return errors


def settings_source_errors(path: str = None) -> list:
    """번들 원본 settings.json을 검사한다."""
    with open(path or os.path.join(ROOT, SETTINGS_SOURCE), encoding='utf-8') as f:
        return settings_privacy_errors(json.load(f))


def app_version(path: str = None) -> str:
    """src/core/version.py의 APP_VERSION. 앱을 import하지 않고 읽는다."""
    with open(path or os.path.join(ROOT, VERSION_SOURCE), encoding='utf-8') as f:
        match = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", f.read(), re.MULTILINE)
    if not match:
        raise ValueError('APP_VERSION을 찾을 수 없습니다')
    return match.group(1)


def module_version_error(module_json_path: str, version: str = None) -> str:
    """Core module.json의 version과 앱 버전이 다르면 오류 문장, 같으면 빈 문자열."""
    with open(module_json_path, encoding='utf-8') as f:
        declared = json.load(f).get('version')
    version = version or app_version()
    if declared == version:
        return ''
    return f'module.json 버전 {declared}이 앱 버전 {version}과 다릅니다'


def abs_datas():
    """PyInstaller spec용 절대경로 datas 목록."""
    return [(os.path.join(ROOT, src), dest) for src, dest in DATAS]


def missing_sources():
    """저장소에 실제로 존재하지 않는 번들 원본 목록 (빌드 전 점검용)."""
    return [src for src, _ in DATAS if not os.path.exists(os.path.join(ROOT, src))]
