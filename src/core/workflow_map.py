"""
workflow_map.py — Workflow(Spec 이름)별 데이터 폴더 지정의 화면 없는 로직

폴더명이 Spec 이름과 달라도 분석할 수 있게, 사용자가 Workflow마다 폴더를 고른다.
대화상자(ui/dialogs/workflow_folder_dialog.py)는 이 함수들로 상태 문구와
초기값, 확인 가능 여부를 정한다. 화면 없이 테스트할 수 있게 Qt를 쓰지 않는다.

폴더 확인은 CSV 머리글을 읽어 느리다(네트워크 폴더). 대화상자가 열려 있는 동안
같은 폴더를 다시 읽지 않도록 cache(dict)를 넘겨 결과를 재사용한다.
"""

import os

from core.recipe_scanner import build_recipe, match_spec_key

NOTE_REMEMBERED = '지난번 선택'
NOTE_ROOT = '선택한 폴더 — 어느 Workflow인지 확인 후 체크하세요'
_FLAT_SUFFIX = ' · (root)'   # 폴더 바로 아래에 Lot이 있다는 표시


def clean_path(text: str) -> str:
    """입력한 경로를 정리한다. 탐색기 '경로로 복사'가 붙이는 양쪽 큰따옴표를 뗀다."""
    text = (text or '').strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def path_key(path: str) -> str:
    """같은 폴더인지 비교·캐시할 때 쓰는 키 (대소문자·구분자 차이 무시)."""
    return os.path.normcase(os.path.normpath(clean_path(path)))


def describe_folder(path: str, cache: dict = None):
    """폴더 상태를 (사용 가능 여부, 표시 문구)로 돌려준다.

    Repeat(Lot) 수는 안내용일 뿐이며 사용 가능 여부에 영향을 주지 않는다.
    cache를 주면 같은 폴더는 한 번만 읽는다.
    """
    path = clean_path(path)
    if not path:
        return False, '— 폴더 미지정'
    if not os.path.isabs(path):
        return False, '❌ 전체 경로를 입력하세요'
    key = path_key(path)
    if cache is not None and key in cache:
        return cache[key]
    status = _read_folder_status(path)
    if cache is not None:
        cache[key] = status
    return status


def _read_folder_status(path: str):
    if not os.path.isdir(path):
        return False, '❌ 폴더 없음'
    try:
        recipe = build_recipe(path)
    except OSError:
        return False, '❌ 폴더를 읽을 수 없음'
    if recipe is None:
        return False, '❌ Lot 데이터 없음'
    rounds = recipe['rounds']
    if len(rounds) == 1 and rounds[0]['name'] == '(root)':
        return True, f"✅ Lot {rounds[0]['lot_count']}개{_FLAT_SUFFIX}"
    return True, '✅ ' + ', '.join(f"{r['name']} Lot {r['lot_count']}개" for r in rounds)


def _is_inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path_key(path), path_key(root)]) == path_key(root)
    except ValueError:  # 드라이브가 다르다
        return False


def compute_prefill(spec_keys, root: str = '', remembered: dict = None,
                    cache: dict = None) -> dict:
    """Workflow별 초기 폴더, 사용 여부, 안내 문구.

    우선순위: (a) 루트 바로 아래에서 Spec 이름과 같은 폴더(대소문자·앞 번호 무시),
    (b) 지난번에 고른 폴더, (c) 비움.
    루트를 준 경우(스캔에서 연 경우) 오늘 고른 폴더가 우선이다.
      - 루트 자체에 Lot이 바로 있으면 (a)로 채워지지 않은 첫 행에 루트를 넣는다.
        어느 Workflow인지는 사용자가 정해야 하므로 체크하지 않는다.
      - 루트 밖의 지난번 폴더는 체크하지 않는다. 체크하면 지난 데이터가 섞인다.
    루트가 없으면(상단 버튼) 지난번 폴더를 지난번 체크 그대로 쓴다(유효할 때만).

    Returns:
        {spec_key: {'path': str, 'enabled': bool, 'note': str}, ...} (spec_keys 순서)
    """
    spec_keys = list(spec_keys)
    remembered = remembered if isinstance(remembered, dict) else {}
    root = clean_path(root)
    has_root = bool(root) and os.path.isdir(root)

    def usable(path):
        return describe_folder(path, cache)[0]

    matched = {}
    if has_root:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for name in names:
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            key = match_spec_key(name, spec_keys)
            if key is not None and key not in matched:
                matched[key] = full

    prefill = {}
    for key in spec_keys:
        if key in matched:
            path = matched[key]
            prefill[key] = {'path': path, 'enabled': usable(path), 'note': ''}
            continue
        entry = remembered.get(key)
        if isinstance(entry, dict) and isinstance(entry.get('path'), str) and entry['path']:
            path = entry['path']
            enabled = bool(entry.get('enabled'))
            if has_root and not _is_inside(path, root):
                enabled = False
            prefill[key] = {'path': path, 'enabled': enabled and usable(path),
                            'note': NOTE_REMEMBERED}
            continue
        prefill[key] = {'path': '', 'enabled': False, 'note': ''}

    # 루트가 곧 Lot 폴더 묶음인 경우 (예: 1..14 Lot만 있는 Vision Pattern 폴더).
    # 하위 폴더에 Lot이 있는 보통 루트는 여기에 해당하지 않는다
    ok, text = describe_folder(root, cache) if has_root else (False, '')
    if ok and text.endswith(_FLAT_SUFFIX):
        for key in spec_keys:
            if key not in matched:
                prefill[key] = {'path': root, 'enabled': False, 'note': NOTE_ROOT}
                break
    return prefill


def duplicate_folders(rows):
    """체크한 Workflow 중 같은 폴더를 가리키는 것들. [[spec_key, ...], ...]"""
    groups = {}
    for key, enabled, path in rows:
        if enabled and clean_path(path):
            groups.setdefault(path_key(path), []).append(key)
    return [keys for keys in groups.values() if len(keys) > 1]


def validate_rows(rows, cache: dict = None):
    """확인 버튼을 누를 수 있는지, 막는 이유, 막지 않는 경고.

    Args:
        rows: [(spec_key, enabled, path), ...]

    Returns:
        (ok, reasons, warnings) — 모두 화면에 그대로 보여 줄 문장
    """
    reasons = []
    usable = 0
    for key, enabled, path in rows:
        if not enabled:
            continue
        ok, text = describe_folder(path, cache)
        if ok:
            usable += 1
        else:
            reasons.append(f'{key}: {text.lstrip("❌— ").strip()}')
    if not reasons and usable == 0:
        reasons.append('사용할 Workflow를 하나 이상 선택해 주세요.')
    warnings = [f"같은 폴더를 여러 Workflow에 지정했습니다: {', '.join(keys)}"
                for keys in duplicate_folders(rows)]
    return not reasons, reasons, warnings


def common_source_folder(paths) -> str:
    """여러 Workflow 폴더의 공통 상위 폴더. 드라이브가 달라 구할 수 없으면 첫 폴더."""
    paths = [p for p in paths if p]
    if not paths:
        return ''
    try:
        return os.path.commonpath(paths)
    except ValueError:
        return paths[0]
