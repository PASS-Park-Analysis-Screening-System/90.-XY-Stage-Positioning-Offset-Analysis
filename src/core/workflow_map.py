"""
workflow_map.py — Workflow(Spec 이름)별 데이터 폴더 지정의 화면 없는 로직

폴더명이 Spec 이름과 달라도 분석할 수 있게, 사용자가 Workflow마다 폴더를 고른다.
대화상자(ui/dialogs/workflow_folder_dialog.py)는 이 함수들로 상태 문구와
초기값, 확인 가능 여부를 정한다. 화면 없이 테스트할 수 있게 Qt를 쓰지 않는다.
"""

import os

from core.recipe_scanner import build_recipe, match_spec_key


def describe_folder(path: str):
    """폴더 상태를 (사용 가능 여부, 표시 문구)로 돌려준다.

    Repeat(Lot) 수는 안내용일 뿐이며 사용 가능 여부에 영향을 주지 않는다.
    """
    path = (path or '').strip()
    if not path:
        return False, '— 폴더 미지정'
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
        return True, f"✅ Lot {rounds[0]['lot_count']}개 · (root)"
    return True, '✅ ' + ', '.join(f"{r['name']} Lot {r['lot_count']}개" for r in rounds)


def _is_usable(path: str) -> bool:
    return describe_folder(path)[0]


def compute_prefill(spec_keys, root: str = '', remembered: dict = None) -> dict:
    """Workflow별 초기 폴더와 사용 여부.

    우선순위: (a) 루트 바로 아래에서 Spec 이름과 같은 폴더(대소문자·앞 번호 무시),
    (b) 지난번에 고른 폴더, (c) 비움. 루트 자체에 Lot이 바로 있고 첫 Workflow가
    비어 있으면 루트를 첫 Workflow에 제안한다. 사용 체크는 폴더가 유효할 때만 켠다.

    Returns:
        {spec_key: {'path': str, 'enabled': bool}, ...} (spec_keys 순서)
    """
    spec_keys = list(spec_keys)
    remembered = remembered if isinstance(remembered, dict) else {}
    root = (root or '').strip()

    matched = {}
    if root and os.path.isdir(root):
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
            prefill[key] = {'path': path, 'enabled': _is_usable(path)}
            continue
        entry = remembered.get(key)
        if isinstance(entry, dict) and isinstance(entry.get('path'), str) and entry['path']:
            path = entry['path']
            prefill[key] = {'path': path,
                            'enabled': bool(entry.get('enabled')) and _is_usable(path)}
            continue
        prefill[key] = {'path': '', 'enabled': False}

    # 루트가 곧 Lot 폴더 묶음인 경우 (예: 1..14 Lot만 있는 Vision Pattern 폴더)
    if spec_keys and root and not prefill[spec_keys[0]]['path'] and _is_usable(root):
        prefill[spec_keys[0]] = {'path': root, 'enabled': True}
    return prefill


def validate_rows(rows):
    """확인 버튼을 누를 수 있는지와 막는 이유.

    Args:
        rows: [(spec_key, enabled, path), ...]

    Returns:
        (ok, reasons) — reasons는 화면에 그대로 보여 줄 문장 목록
    """
    reasons = []
    usable = 0
    for key, enabled, path in rows:
        if not enabled:
            continue
        ok, text = describe_folder(path)
        if ok:
            usable += 1
        else:
            reasons.append(f'{key}: {text.lstrip("❌— ").strip()}')
    if not reasons and usable == 0:
        reasons.append('사용할 Workflow를 하나 이상 선택해 주세요.')
    return not reasons, reasons


def common_source_folder(paths) -> str:
    """여러 Workflow 폴더의 공통 상위 폴더. 드라이브가 달라 구할 수 없으면 첫 폴더."""
    paths = [p for p in paths if p]
    if not paths:
        return ''
    try:
        return os.path.commonpath(paths)
    except ValueError:
        return paths[0]
