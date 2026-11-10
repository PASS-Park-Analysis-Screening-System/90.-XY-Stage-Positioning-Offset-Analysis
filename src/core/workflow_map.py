"""
workflow_map.py — Workflow(Spec 이름)별 데이터 폴더 지정의 화면 없는 로직

폴더명이 Spec 이름과 달라도 분석할 수 있게, 사용자가 Workflow마다 폴더를 고른다.
대화상자(ui/dialogs/workflow_folder_dialog.py)는 이 함수들로 상태 문구와
초기값, 확인 가능 여부를 정한다. 화면 없이 테스트할 수 있게 Qt를 쓰지 않는다.

폴더 확인은 CSV 머리글을 읽어 느리다(네트워크 폴더). 같은 폴더를 다시 읽지 않도록
cache를 넘겨 결과를 재사용한다. cache는 dict이거나 ScanSession이다. ScanSession이면
스캔·대화상자·로드가 Lot 목록까지 함께 쓴다 (대화상자는 화면 스레드 밖에서 부른다).
"""

import os

from core.csv_loader import ScanSession, is_dir, list_dir, scan_self_lot
from core.recipe_scanner import (build_recipe, is_run_style, match_spec_key,
                                 normalize_spec_names, scan_recipes, stray_csv_note,
                                 summary_only_notes)

NOTE_REMEMBERED = '지난번 선택'
NOTE_ROOT = '선택한 폴더 — 어느 Workflow인지 확인 후 체크하세요'
_FLAT_SUFFIX = ' · (root)'   # 폴더 바로 아래에 Lot이 있다는 표시
_SELF_LOT_SUFFIX = ' (선택한 폴더가 Lot 폴더)'   # 폴더 자체가 Lot 하나라는 표시


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
    store = cache.status if isinstance(cache, ScanSession) else cache
    if store is not None and key in store:
        return store[key]
    session = cache if isinstance(cache, ScanSession) else None
    status = _read_folder_status(path, session)
    failed = session.failed_under(path) if session is not None else []
    if failed and status[0]:
        status = (True, f'{status[1]} · ⚠ Lot 폴더 {len(failed)}개 확인 실패')
    # 실패·일부 실패는 담지 않는다. 일시 오류일 수 있어 다시 입력하거나 확인하면 다시 읽는다
    if store is not None and status[0] and not failed:
        store[key] = status
    return status


def cached_status(path: str, cache):
    """화면 스레드용: 파일을 읽지 않고 알 수 있는 상태. 폴더를 읽어야 하면 None.

    빈 경로·상대 경로는 바로 답하고, 이미 확인한 폴더는 cache에서 돌려준다
    (cache에는 사용 가능한 결과만 있다. 실패는 다시 확인한다).
    """
    text = clean_path(path)
    if not text or not os.path.isabs(text):
        return describe_folder(text)
    store = cache.status if isinstance(cache, ScanSession) else cache
    if store is not None:
        return store.get(path_key(text))
    return None


def _read_folder_status(path: str, session=None):
    if not is_dir(path, session):
        return False, '❌ 폴더 없음'
    # 폴더 자체가 Lot 폴더면 Lot 1개다 (로드도 build_recipe(..., allow_self=True)로 같게 본다)
    if scan_self_lot(path, session):
        return True, f'✅ Lot 1개{_SELF_LOT_SUFFIX}'
    try:
        recipe = (build_recipe(path) if session is None
                  else build_recipe(path, session=session))
    except OSError:
        return False, '❌ 폴더를 읽을 수 없음'
    if recipe is None:
        return False, '❌ Lot 데이터 없음'
    rounds = recipe['rounds']
    if len(rounds) == 1 and rounds[0]['name'] == '(root)':
        return True, f"✅ Lot {rounds[0]['lot_count']}개{_FLAT_SUFFIX}"
    names = [r['name'] for r in rounds]
    if len(rounds) >= 2 and is_run_style(names):
        # 회차 폴더(1..14, 날짜)는 목록 대신 개수와 범위로 줄여 보여 준다
        counts = [r['lot_count'] for r in rounds]
        lots = (f'Lot {counts[0]}개씩' if min(counts) == max(counts)
                else f'Lot {min(counts)}~{max(counts)}개')
        return True, f'✅ 회차 {len(rounds)}개 ({names[0]}~{names[-1]}) · {lots}'
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
    session = cache if isinstance(cache, ScanSession) else None
    has_root = bool(root) and is_dir(root, session)

    def usable(path):
        return describe_folder(path, cache)[0]

    matched = {}
    if has_root:
        try:
            entries = list_dir(root, session)
        except OSError:
            entries = []
        for name, folder in entries:
            if not folder:
                continue
            full = os.path.join(root, name)
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

    # 루트가 곧 Lot 폴더 묶음인 경우 (예: 1..14 Lot만 있는 Vision Pattern 폴더)나
    # 루트 자체가 Lot 폴더 하나인 경우. 하위 폴더에 Lot이 있는 보통 루트는 여기에 해당하지 않는다
    ok, text = describe_folder(root, cache) if has_root else (False, '')
    if ok and text.endswith((_FLAT_SUFFIX, _SELF_LOT_SUFFIX)):
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


FLAT_ROOT_REASON = ("선택한 폴더에 Lot 데이터가 바로 들어 있습니다. "
                    "어느 Workflow의 데이터인지 지정해 주세요.")
SELF_LOT_REASON = ("선택한 폴더가 Lot 폴더입니다 (Lot 1개, X/Y 측정 CSV가 바로 있음). "
                   "어느 Workflow의 데이터인지 지정해 주세요.")
SELF_LOT_NO_SPEC = "선택한 폴더는 Lot 폴더입니다 — Spec 설정 후 Workflow별 선택으로 지정하세요"


def detect_scan_target(folder, spec_keys, remembered, session):
    """고른 폴더를 확인해 다음 단계를 정한다 (화면의 스캔 스레드와 명령줄 --llm-report가 쓴다).
    화면은 건드리지 않는다.

    Returns:
        {'action': 'invalid'}                 — 폴더가 없다
        {'action': 'none', 'message': str}    — Recipe 구조가 없다 (message는 이유, 없을 수 있음)
        {'action': 'load', 'recipes': [...]}  — 그대로 로드한다
        {'action': 'dialog', 'reason': str, 'mismatched': [...], 'prefill': {...}}
            — Workflow별 폴더 대화상자를 연다 (초기값도 여기서 계산해 대화상자가 바로 뜬다)
        'notes': [(수준 'warn'|'info', 문구), ...] — System Log에 남길 안내 (없을 수 있음)
    """
    if not is_dir(folder, session):
        return {'action': 'invalid'}
    mismatched = []
    dropped = []
    recipes = scan_recipes(folder, session=session, notes=dropped)
    notes = [('info', text) for text in dropped]
    if not recipes:
        # 고른 폴더 자체가 Lot 폴더면(측정 Lot 하위 폴더가 없을 때만) Lot 1개로 본다.
        # 하위의 Capture, Debug, Info, Log는 Recipe가 아니다 (Info·Debug/Analysis는 메타만 같다)
        if scan_self_lot(folder, session):
            if not spec_keys:
                return {'action': 'none', 'message': SELF_LOT_NO_SPEC}
            return {'action': 'dialog', 'reason': SELF_LOT_REASON, 'mismatched': [],
                    'prefill': compute_prefill(spec_keys, folder, remembered, session)}
        # 고른 폴더 자체가 Lot 묶음이면(예: 1..14 Lot만 있는 Vision Pattern 폴더)
        # Workflow를 지정해 분석할 수 있게 한다
        flat = build_recipe(folder, session=session)
        stray = stray_csv_note(folder, session) if flat is not None else None
        if flat is None:
            notes += [('info', text)
                      for text in summary_only_notes(folder, session, include_subs=False)]
        elif stray:
            notes.append(('warn', stray))
        if not (spec_keys and flat is not None):
            return {'action': 'none', 'notes': notes}
        reason = FLAT_ROOT_REASON
    else:
        stray = stray_csv_note(folder, session)
        if stray:
            notes.append(('warn', stray))
        # ─── 표준 Recipe 이름 검증 (Spec 설정 기준) ───
        # 일치하면 short_name을 정확한 Spec 키로 맞춘다 (판정·Cpk는 키를 정확히 찾는다)
        mismatched = normalize_spec_names(recipes, spec_keys) if spec_keys else []
        if not mismatched:
            return {'action': 'load', 'recipes': recipes, 'notes': notes}
        mis_list = '\n'.join(f"  • {m}" for m in mismatched)
        reason = ("폴더명이 Spec 이름과 달라 Workflow별로 데이터 폴더를 지정해 주세요.\n"
                  f"Spec 이름과 다른 폴더:\n{mis_list}")
    return {'action': 'dialog', 'reason': reason, 'mismatched': mismatched, 'notes': notes,
            'prefill': compute_prefill(spec_keys, folder, remembered, session)}
