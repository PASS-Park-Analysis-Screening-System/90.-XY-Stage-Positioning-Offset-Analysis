"""
settings.py — settings.json 기반 설정 저장/로드

빌드 시 src/core/settings.json을 onefile EXE에 포함하고, 해당 파일의 스펙을
항상 기준으로 사용한다. 빌드본의 사용자 환경설정은 Windows 레지스트리에
저장하므로 외부 settings.json 파일은 생성하지 않는다.

소스 실행의 사용자 환경설정은 settings.local.json(git 제외, 번들 제외)에 저장한다.
settings.json에 저장하면 개인·고객 경로가 그대로 빌드에 들어간다.
"""

import json
import os
import sys

try:
    import winreg
except ImportError:  # Windows 이외의 개발/테스트 환경
    winreg = None

IS_FROZEN = getattr(sys, 'frozen', False)


def _resolve_bundled_settings_file() -> str:
    """소스 또는 PyInstaller onefile 내부의 settings.json 경로를 반환한다."""
    if IS_FROZEN and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'core', 'settings.json')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')


BUNDLED_SETTINGS_FILE = _resolve_bundled_settings_file()
LOCAL_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'settings.local.json')

_REGISTRY_PATH = r'Software\XYStageOffset'
_USER_SETTING_KEYS = {
    'last_folder',
    'window_geometry',
    'expert_window_geometry',   # 전문 분석 창 (window_geometry와 같은 "WxH+X+Y")
    'recent_folders',
    'wafer_size',
    'workflow_folder_map',
}
# 빌드본은 번들에 값이 섞여 들어와도 이 항목을 쓰지 않는다. 빌드한 PC의 경로다.
_MACHINE_SPECIFIC_DEFAULTS = {'last_folder': '', 'recent_folders': [],
                              'workflow_folder_map': {}}
# 값을 JSON 문자열로 레지스트리에 저장하는 항목
_JSON_REGISTRY_KEYS = ('recent_folders', 'workflow_folder_map')

# 회전 진단 잠정 규격 (트러블슈팅 진단 탭). 장비 데이터(평균 + 3σ)로 확정 전까지 잠정이다.
#   theta: X 라인 |θx| (Y 라인은 |θy|), µrad — ±120 mm Die에서 Range 기여 = θ × 0.24 µm,
#          1.0 µm Spec의 25 %를 배정하면 1.0 µrad
#   kappa: 스캐너 구동 1 µm당 회전, µrad/µm
#   offset_max: 측정 라인에 걸리는 스캐너 오프셋(보정 포함) 한도, µm
DEFAULT_ROTATION_SPEC = {'theta_max': 1.0, 'theta_warn': 0.7,
                         'kappa_max': 0.10, 'kappa_warn': 0.07,
                         'offset_max': 10.0, 'provisional': True}


def rotation_spec(settings: dict = None) -> dict:
    """설정의 회전 잠정 규격. 번들 settings.json 값이 우선이고, 없거나 숫자가 아니거나 음수인 항목은
    항목마다 코드 기본값을 쓴다. 주의값이 한도보다 크면 한도로 맞춘다 (warn ≤ max)."""
    spec = (settings or {}).get('rotation_spec')
    spec = spec if isinstance(spec, dict) else {}
    out = {}
    for key, default in DEFAULT_ROTATION_SPEC.items():
        value = spec.get(key, default)
        if key == 'provisional':
            out[key] = value if isinstance(value, bool) else default
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = float(default)
        if isinstance(spec.get(key), bool) or value != value or value in (float('inf'), float('-inf')) \
                or value < 0:
            value = float(default)
        out[key] = value
    for warn, limit in (('theta_warn', 'theta_max'), ('kappa_warn', 'kappa_max')):
        out[warn] = min(out[warn], out[limit])
    return out


def _read_settings_file(file_path: str) -> dict:
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def _write_settings_file(file_path: str, settings: dict):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _is_valid_user_value(name: str, value) -> bool:
    if name == 'recent_folders':
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if name == 'wafer_size':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'workflow_folder_map':
        # {Spec 이름: {'path': 폴더, 'enabled': 사용 여부}}
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, dict)
            and isinstance(v.get('path'), str) and isinstance(v.get('enabled'), bool)
            for k, v in value.items())
    return isinstance(value, str)


def _read_local_settings() -> dict:
    """소스 실행의 사용자 환경설정을 읽는다. 없거나 손상되면 빈 dict.

    손으로 고친 파일이 앱 시작을 막으면 안 되므로, 인코딩이 틀리거나(ValueError)
    형식이 맞지 않는 값은 버린다.
    """
    try:
        local = _read_settings_file(LOCAL_SETTINGS_FILE)
    except (ValueError, OSError):
        return {}
    if not isinstance(local, dict):
        return {}
    return {key: value for key, value in local.items()
            if key in _USER_SETTING_KEYS and _is_valid_user_value(key, value)}


def _read_registry_settings() -> dict:
    """HKCU에서 빌드본 사용자 환경설정을 읽는다."""
    if winreg is None:
        return {}

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH)
    except OSError:
        return {}

    settings = {}
    with key:
        for name in _USER_SETTING_KEYS:
            try:
                value, _ = winreg.QueryValueEx(key, name)
            except OSError:
                continue

            if name in _JSON_REGISTRY_KEYS:
                try:
                    value = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not _is_valid_user_value(name, value):
                    continue
            elif name == 'wafer_size':
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
            settings[name] = value
    return settings


def _write_registry_settings(settings: dict):
    """허용된 사용자 환경설정만 HKCU에 저장한다."""
    if winreg is None:
        return

    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH)
        with key:
            for name in _USER_SETTING_KEYS:
                if name not in settings:
                    continue
                value = settings[name]
                if name in _JSON_REGISTRY_KEYS:
                    value = json.dumps(value, ensure_ascii=False)
                    value_type = winreg.REG_SZ
                elif name == 'wafer_size':
                    value = int(value)
                    value_type = winreg.REG_DWORD
                else:
                    value = str(value)
                    value_type = winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, value_type, value)
    except (OSError, TypeError, ValueError):
        pass


def load_settings() -> dict:
    """내장 settings.json을 기준으로 사용자 환경설정을 병합한다.

    소스 실행은 settings.local.json의 사용자 설정을 병합한다. onefile 빌드본은
    EXE에 포함된 JSON의 스펙을 사용하고, Windows 레지스트리의 사용자 설정만
    제한적으로 병합한다.
    """
    try:
        bundled = _read_settings_file(BUNDLED_SETTINGS_FILE)
    except (json.JSONDecodeError, OSError):
        return {}

    # 규격은 다른 Spec 항목처럼 번들에서 온다 (사용자 설정으로 바꾸지 않는다). 빠진 항목은 코드 기본값
    bundled['rotation_spec'] = rotation_spec(bundled)
    if not IS_FROZEN:
        return {**bundled, **_read_local_settings()}

    settings = {**bundled, **_MACHINE_SPECIFIC_DEFAULTS}
    for key, value in _read_registry_settings().items():
        if key in _USER_SETTING_KEYS:
            settings[key] = value
    return settings


def save_settings(settings: dict):
    """사용자 환경설정만 저장한다. 번들 대상 settings.json은 바꾸지 않는다."""
    user_settings = {
        key: value for key, value in settings.items()
        if key in _USER_SETTING_KEYS
    }
    if IS_FROZEN:
        _write_registry_settings(user_settings)
        return

    try:
        _write_settings_file(LOCAL_SETTINGS_FILE, user_settings)
    except OSError:
        pass


def parse_geometry_string(geo: str):
    """저장된 창 geometry 문자열 "WxH+X+Y" → (x, y, w, h) 튜플.

    빈 문자열이거나 형식이 손상된 경우 None을 반환한다(호출부는 None이면 최대화 등
    폴백 동작을 수행). 음수 좌표("...+-5+50")도 split('+')로 3토막이 유지되어 처리된다.
    """
    if not geo:
        return None
    try:
        size_part, x_str, y_str = geo.split('+')
        w_str, h_str = size_part.split('x')
        return int(x_str), int(y_str), int(w_str), int(h_str)
    except (ValueError, AttributeError):
        return None


def add_recent_folder(settings: dict, folder_path: str) -> dict:
    """최근 폴더 추가 (최대 5개, 중복 제거)"""
    recents = settings.get('recent_folders', [])
    # 이미 존재하면 제거 후 맨 앞에 추가
    if folder_path in recents:
        recents.remove(folder_path)
    recents.insert(0, folder_path)
    settings['recent_folders'] = recents[:5]
    return settings
