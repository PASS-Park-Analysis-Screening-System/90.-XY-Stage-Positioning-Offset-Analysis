"""
positioning_log.py — 비전 보정 로그(SamplePositioning·TipPositioning)와 최종 착지(HZ1_O) 대조

Lot 폴더마다 장비가 남기는 탭 구분 로그가 있다.
    <Lot>/Debug/SamplePositioning/<Sample>_SamplePositioning.txt  — Die(측정 위치)마다 한 행
    <Lot>/Debug/TipPositioning/<Sample>_TipPositioning.txt        — Tip Positioning마다 한 행

SamplePositioning 한 행은 그 Die로 가는 위치 결정 과정이다.
    Stage X/Y        명령한 스테이지 좌표 (샘플 정렬 회전 ~0.05°와 원점 차이가 들어 있다)
    Coarse Match     도착했을 때 카메라가 본 패턴 위치 오차 (도착 오차)
    Coarse Move      거친 보정 이동량
    Fine Match       거친 보정 뒤 다시 본 오차 (정밀 정렬 잔차)
    Fine Move        정밀 보정 이동량
    Optical Shift    광학(배율·초점) 보정값
    Tip Pos          카메라 → Tip 오프셋 (Tip Positioning 결과)
그다음 Tip이 착지해 측정한 값이 HZ1_O(최종 착지 오차)다.

단계마다 Die 값을 Die 좌표에 평면 피팅해(Sx, θx, θy, Sy와 t) 최종 착지의 평면 성분이
어느 단계에 이미 있었는지 본다. 같은 성분인지는 계수의 크기·t와, 그 계수만 남긴 부분 잔차
(예: θx면 v − c − a·x)끼리의 상관과 부호가 맞는지로 판단한다. 비전 로그 어디에도 없고
최종에만 있으면 비전 정렬 이후(카메라 → Tip 착지) 구간에서 생긴 오차다.
판정 문구는 경험 규칙(파생)이며 규칙을 함께 적는다.

Qt를 쓰지 않는다 (화면의 작업 스레드·AI 분석용 리포트·테스트가 같이 쓴다).
"""

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.csv_loader import (LOT_LOAD_WORKERS, _decode_bytes, _open_csv_text, _read_file_bytes,
                             _with_retry, list_dir)
from core.die_analysis import (compute_deviation_matrix, extract_die_number,
                               extract_die_positions, filter_stabilization_die)

AXES = ('X', 'Y')
DERIVED = '(파생)'
SAMPLE_DIR = 'samplepositioning'
TIP_DIR = 'tippositioning'
SAMPLE_SUFFIX = '_samplepositioning.txt'
TIP_SUFFIX = '_tippositioning.txt'

# 행 ↔ Die 확인: 측정 순서로 짝지은 뒤 회전·평행이동을 맞춘 잔차가 이보다 크면 '불일치'
MAP_TOLERANCE_UM = 300.0
# 샘플 정렬 회전은 ~0.05°다. 이보다 크면 Die 배치의 대칭(90°·180°·270°)으로 잘못 맞은 것이다
MAX_ROTATION_DEG = 1.0
# 평면 피팅: 좌표 열의 퍼짐(표준편차)이 이보다 작거나 X·Y가 거의 한 직선이면 그 계수는 정하지 않는다
MIN_SPREAD_MM = 5.0
COLLINEAR_R = 0.95
# 판정 규칙 (파생)
MIN_DIES = 8            # 평면 성분을 판단하는 최소 Die 수 (최종·로그 단계 모두)
MIN_T = 3.0             # 유의한 평면 성분: |t| ≥ 3
CARRY_RATIO = 0.5       # 로그 단계가 같은 성분을 가졌다: |값| ≥ 0.5 × |최종 값|
CARRY_R = 0.5           # 그리고 그 성분의 부분 잔차끼리 |r| ≥ 0.5, 부호가 맞을 것
TIP_VARIATION_UM = 0.5  # Tip Positioning 결과 변동 표시 기준
TIP_MIN_SPAN_MM = 10.0  # Tip X 대 스테이지 Y 기울기를 내는 최소 Y 범위

# (키, 표시 이름, 구간 이름, SamplePositioning 열 이름 (X는 x, Y는 y로 바꿔 찾는다))
STAGES = (
    ('coarse_match', '도착 오차 (Coarse Match)', '스테이지 이동(도착)', 'coarse match {a}'),
    ('coarse_move', '거친 보정 (Coarse Move)', '거친 보정 이동', 'coarse move {a}'),
    ('fine_match', '정밀 잔차 (Fine Match)', '비전 정밀 정렬', 'fine match {a}'),
    ('fine_move', '정밀 보정 (Fine Move)', '정밀 보정 이동', 'fine move {a}'),
    ('vision_total', '비전 보정 합계 (Coarse+Fine Move)', '비전 보정 이동', None),
    ('optical_shift', 'Optical Shift (Fine)', '광학 보정(Optical Shift)', 'fine optical shift {a}'),
    ('tip_pos', 'Tip 위치 (Tip Pos)', 'Tip Positioning', 'tip pos {a}'),
    ('final', '최종 착지 (HZ1_O 편차)', '최종 착지', None),
)
LOG_STAGES = tuple(s[0] for s in STAGES if s[0] != 'final')
STAGE_LABEL = {s[0]: s[1] for s in STAGES}
STAGE_SPAN = {s[0]: s[2] for s in STAGES}
# 평면 계수: 축별 (∂/∂x 이름, ∂/∂y 이름). 단위는 µm/mm × 1000 = ppm 또는 µrad
COEF_NAMES = {'X': ('Sx', 'θx'), 'Y': ('θy', 'Sy')}
COEF_UNIT = {'Sx': 'ppm', 'θx': 'µrad', 'θy': 'µrad', 'Sy': 'ppm'}
TERM_TEXT = {
    'Sx': 'X 위치에 비례하는 X 오차(Sx)',
    'θx': 'Y 위치에 비례하는 X 오차(θx)',
    'θy': 'X 위치에 비례하는 Y 오차(θy)',
    'Sy': 'Y 위치에 비례하는 Y 오차(Sy)',
}
NO_LOG_TEXT = '로그 없음 — Lot 폴더 Debug/SamplePositioning 확인'
READ_FAIL_TEXT = '로그를 읽지 못함'
VERDICT_RULE = (f'규칙 {DERIVED}: Die {MIN_DIES}개 이상에서 최종 착지(HZ1_O)의 평면 계수 중 '
                f'|t| ≥ {MIN_T:g}인 것만 본다. 비전 로그 단계(Die {MIN_DIES}개 이상)가 같은 계수를 '
                f'가졌다고 보는 조건은 |t| ≥ {MIN_T:g}, |값| ≥ {CARRY_RATIO:g} × |최종 값|, 그 계수만 '
                f'남긴 부분 잔차(예: θx면 v − c − Sx·x)끼리의 |r| ≥ {CARRY_R:g}, 그리고 '
                '부호(단계)·부호(최종) = 부호(r) 모두. 같은 부호인지 반대 부호인지 적는다. '
                '어느 단계도 갖지 않으면 비전 정렬 이후(카메라→Tip 착지) 구간, 가진 단계가 있으면 '
                f'그중 가장 앞 단계의 구간으로 본다. 좌표 퍼짐이 {MIN_SPREAD_MM:g} mm 미만이거나 X·Y가 '
                f'한 직선(|r| > {COLLINEAR_R:g})이면 그 계수는 — (판정하지 않음).')


class LogReadError(OSError):
    """로그 파일이 있는데 읽지 못했다 (잠김·권한·해석 실패)."""


# ──────────────────────────────────────────────
# 파일 찾기·읽기
# ──────────────────────────────────────────────

def _norm_header(name: str) -> str:
    """'Coarse Match X (um)' → 'coarse match x' (단위·공백·대소문자 무시)."""
    name = re.sub(r'\([^)]*\)', '', name or '')
    return re.sub(r'\s+', ' ', name).strip().lower()


def _header_priority(raw: str) -> int:
    """같은 이름으로 정규화되는 열이 둘이면 µm 열을 픽셀 열보다 먼저 쓴다."""
    lower = (raw or '').lower()
    if '(um)' in lower or 'µm' in lower or '(μm)' in lower:
        return 2
    if 'pxl' in lower or 'pixel' in lower:
        return 0
    return 1


def _number(text) -> Optional[float]:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _entries(path: str, session=None):
    """폴더 목록. 폴더가 없으면 None. 네트워크 일시 오류는 한 번 더 읽고, 그래도 실패하면 올린다
    ('로그 없음'과 '읽지 못함'을 구분해야 한다)."""
    result = _with_retry(lambda: list_dir(path, session), session)
    if isinstance(result, (FileNotFoundError, NotADirectoryError)):
        return None
    if isinstance(result, OSError):
        raise result
    return result


def _child_folder(path: str, wanted: str, session=None) -> Optional[str]:
    entries = _entries(path, session)
    for name, folder in entries or []:
        if folder and name.lower() == wanted:
            return os.path.join(path, name)
    return None


def _choose_log(folder: str, suffix: str, prefix: str, kind: str, session=None):
    """(경로 또는 None, 문제 문장). CSV 이름 앞부분(prefix)과 같은 로그를 먼저 고른다.

    로그가 하나뿐이면 이름이 달라도 쓴다 (실데이터: CSV 46LKD105SJE6_X_UL.csv, 로그 Sample19_…).
    여럿인데 맞는 것이 없으면 고르지 않는다 (다른 샘플의 로그를 쓰지 않게).
    """
    names = sorted(name for name, is_folder in (_entries(folder, session) or [])
                   if not is_folder and name.lower().endswith(suffix))
    if not names:
        return None, ''
    if prefix:
        same = [n for n in names if n[:-len(suffix)].lower() == prefix]
        if same:
            return os.path.join(folder, same[0]), ''
    if len(names) == 1:
        return os.path.join(folder, names[0]), ''
    return None, (f"{kind} 로그가 {len(names)}개인데 CSV 이름({prefix or '?'})과 맞는 것이 없음: "
                  + ', '.join(names[:4]))


def find_logs(lot_path: str, session=None, csv_prefix: str = '') -> dict:
    """Lot 폴더의 로그 {'sample': 경로|None, 'tip': 경로|None, 'problems': [문장]}.

    폴더·파일이 없으면 None이다. 그 밖의 읽기 오류(네트워크·권한)는 OSError로 올린다.
    """
    found = {'sample': None, 'tip': None, 'problems': []}
    debug = _child_folder(lot_path, 'debug', session) if lot_path else None
    if debug is None:
        return found
    for key, folder, suffix, kind in (('sample', SAMPLE_DIR, SAMPLE_SUFFIX, 'SamplePositioning'),
                                      ('tip', TIP_DIR, TIP_SUFFIX, 'TipPositioning')):
        sub = _child_folder(debug, folder, session)
        if sub is not None:
            found[key], problem = _choose_log(sub, suffix, (csv_prefix or '').lower(), kind,
                                              session)
            if problem:
                found['problems'].append(problem)
    return found


def _read_bytes(path: str) -> bytes:
    raw = _with_retry(lambda: _read_file_bytes(path))
    if isinstance(raw, OSError):
        raise raw
    if not raw:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size:        # 있는데 읽히지 않는다 (DLP 차단 뒤 복사도 실패, 잠김)
            raise LogReadError(f'파일을 열 수 없음 (잠김 또는 권한): {os.path.basename(path)}')
    return raw


def read_table(path: str) -> list:
    """탭 구분 로그를 [{정규화한 열 이름: 문자열}, ...]로. 빈 줄·빈 열·남는 칸은 버린다.

    인코딩은 UTF-16 BOM을 먼저 보고, 아니면 CSV와 같은 순서로 맞춘다. 칸이 머리글보다 적으면
    없는 열은 빠진다. 같은 이름이 되는 열이 둘이면 µm 열을 쓴다. 읽지 못하면 OSError.
    """
    raw = _read_bytes(path)
    if not raw:
        return []
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        try:
            text = raw.decode('utf-16')
        except UnicodeDecodeError as e:
            raise LogReadError(f'UTF-16 해석 실패: {os.path.basename(path)} ({e})') from e
    else:
        text, _ = _decode_bytes(raw, path)
        if text is None:
            raise LogReadError(f'인코딩 해석 실패: {os.path.basename(path)}')
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    raw_headers = lines[0].split('\t')
    best = {}       # 정규화한 이름 → 쓸 열 번호
    for index, raw_header in enumerate(raw_headers):
        name = _norm_header(raw_header)
        if not name:
            continue
        if name not in best or _header_priority(raw_header) > _header_priority(raw_headers[best[name]]):
            best[name] = index
    rows = []
    for line in lines[1:]:
        cells = line.split('\t')
        row = {name: cells[i].strip() for name, i in best.items() if i < len(cells)}
        if any(row.values()):
            rows.append(row)
    return rows


def parse_sample_positioning(path: str) -> list:
    """SamplePositioning 행 [{slot, point, location, stage_x, stage_y, 단계 값...}, ...]."""
    out = []
    for row in read_table(path):
        rec = {'slot': row.get('slot', ''), 'point': _number(row.get('point')),
               'location': row.get('location', ''),
               'stage_x': _number(row.get('stage x')), 'stage_y': _number(row.get('stage y'))}
        for key, _label, _span, column in STAGES:
            if column is None:
                continue
            for axis in AXES:
                value = _number(row.get(column.format(a=axis.lower())))
                if value is None and key == 'optical_shift':
                    value = _number(row.get(f'coarse optical shift {axis.lower()}'))
                rec[f'{key}_{axis.lower()}'] = value
        for axis in AXES:
            a = axis.lower()
            cm, fm = rec.get(f'coarse_move_{a}'), rec.get(f'fine_move_{a}')
            rec[f'vision_total_{a}'] = None if cm is None or fm is None else cm + fm
        out.append(rec)
    return out


def parse_tip_positioning(path: str) -> list:
    """TipPositioning 행. Tip X/Y 칸이 비어 있으면 Fine Match X/Y를 쓴다 (실데이터는 비어 있다)."""
    out = []
    for row in read_table(path):
        tip_x, tip_y = _number(row.get('tip x')), _number(row.get('tip y'))
        source = 'Tip X/Y'
        if tip_x is None and tip_y is None:
            tip_x, tip_y = _number(row.get('fine match x')), _number(row.get('fine match y'))
            source = 'Fine Match X/Y'
        out.append({'slot': row.get('slot', ''), 'location': row.get('location', ''),
                    'coarse_success': row.get('coarse success', ''),
                    'fine_success': row.get('fine success', ''),
                    'coarse_score': _number(row.get('coarse score')),
                    'fine_score': _number(row.get('fine score')),
                    'tip_x': tip_x, 'tip_y': tip_y, 'source': source})
    return out


def _slot_text(value) -> str:
    number = _number(value)
    if number is not None and number == int(number):
        return str(int(number))
    return (str(value or '')).strip()


def csv_slot(lot_path: str, csv_name: str) -> str:
    """측정 CSV 메타의 Slot No (없거나 읽지 못하면 '')."""
    if not lot_path or not csv_name:
        return ''
    lines = _with_retry(lambda: _open_csv_text(os.path.join(lot_path, csv_name), 20))
    if isinstance(lines, OSError):
        return ''
    for line in lines:
        key, _, value = line.partition(',')
        if key.strip().lower() == 'slot no':
            return _slot_text(value.split(',')[0])
        if key.strip().lower() == 'site id':
            break
    return ''


# ──────────────────────────────────────────────
# 행 ↔ Die
# ──────────────────────────────────────────────

def lot_sites(rows: list) -> dict:
    """한 Lot의 측정 행에서 Die 좌표 {die_idx: (x_um, y_um)} (X_UL·Y_UL CSV의 X/Y (um))."""
    sites = {}
    for r in rows:
        die = extract_die_number(r.get('site_id', ''))
        x, y = r.get('x_um'), r.get('y_um')
        if die is None or die in sites or not isinstance(x, (int, float)) \
                or not isinstance(y, (int, float)) or not (math.isfinite(x) and math.isfinite(y)):
            continue
        sites[die] = (float(x), float(y))
    return sites


def _rigid_fit(src, dst):
    """src → dst 2D 회전 + 평행이동 최소제곱. (회전 rad, (tx, ty), 변환 함수)."""
    n = len(src)
    sx = sum(p[0] for p in src) / n
    sy = sum(p[1] for p in src) / n
    dx = sum(q[0] for q in dst) / n
    dy = sum(q[1] for q in dst) / n
    num = sum((p[0] - sx) * (q[1] - dy) - (p[1] - sy) * (q[0] - dx) for p, q in zip(src, dst))
    den = sum((p[0] - sx) * (q[0] - dx) + (p[1] - sy) * (q[1] - dy) for p, q in zip(src, dst))
    theta = math.atan2(num, den) if (num or den) else 0.0
    c, s = math.cos(theta), math.sin(theta)

    def apply(p):
        x, y = p[0] - sx, p[1] - sy
        return (c * x - s * y + dx, s * x + c * y + dy)
    tx, ty = apply((0.0, 0.0))
    return theta, (tx, ty), apply


def map_rows_to_dies(rows: list, sites: dict, tolerance_um: float = MAP_TOLERANCE_UM) -> dict:
    """로그 행을 Die에 짝짓는다: 측정 순서대로 짝지은 뒤 Die 좌표로 확인한다.

    Point 0 행은 샘플 정렬 기준점이라 Die가 아니다 (실데이터: Tip X/Y 0, 웨이퍼 가장자리 좌표).
    명령 좌표에는 샘플 정렬 회전(~0.05°)과 원점 차이가 있어, 짝지은 좌표로 회전·평행이동을
    맞춘 뒤 잔차가 tolerance_um 안이고 가장 가까운 Die가 짝지은 Die와 같은 자리인지 본다.
    맞춘 회전이 MAX_ROTATION_DEG를 넘어도 불일치다 (Die 배치의 4중 대칭으로 90°·180°·270° 회전한
    순서도 잔차가 작게 맞을 수 있다). 하나라도 어긋나면 짝짓지 않고 'mismatch'와 이유를 돌려준다.

    Returns:
        {'status': 'ok'|'mismatch', 'mapping': {die_idx: 행}, 'problems': [...],
         'rows': 행 수, 'dies': Die 수, 'rotation_deg', 'offset_um', 'max_residual_um'}
    """
    used = [r for r in rows if r.get('point') != 0]
    dies = sorted(sites)
    out = {'status': 'mismatch', 'mapping': {}, 'problems': [], 'rows': len(used),
           'dies': len(dies), 'skipped_point0': len(rows) - len(used),
           'rotation_deg': None, 'offset_um': None, 'max_residual_um': None}
    if not dies:
        out['problems'].append('Die 좌표(X/Y UL CSV)가 없어 확인할 수 없음')
        return out
    if len(used) != len(dies):
        out['problems'].append(f'로그 행 {len(used)}개 ≠ Die {len(dies)}개 (Point 0 행 '
                               f'{out["skipped_point0"]}개 제외)')
        return out
    if any(r.get('stage_x') is None or r.get('stage_y') is None for r in used):
        out['problems'].append('Stage X/Y를 읽지 못한 행이 있음')
        return out
    src = [(r['stage_x'], r['stage_y']) for r in used]
    dst = [sites[d] for d in dies]
    theta, offset, apply = _rigid_fit(src, dst)
    out['rotation_deg'] = math.degrees(theta)
    out['offset_um'] = offset
    if abs(out['rotation_deg']) > MAX_ROTATION_DEG:
        out['problems'].append(f"맞춘 회전 {out['rotation_deg']:+.2f}° — {MAX_ROTATION_DEG:g}°를 넘어 "
                               '측정 순서가 Die 배치와 다름 (대칭 배치로 잘못 맞았을 수 있음)')
        return out
    residuals = []
    for k, (p, die) in enumerate(zip(src, dies)):
        q = apply(p)
        residual = math.hypot(q[0] - sites[die][0], q[1] - sites[die][1])
        residuals.append(residual)
        nearest = min(dies, key=lambda d: math.hypot(q[0] - sites[d][0], q[1] - sites[d][1]))
        same_place = math.hypot(sites[nearest][0] - sites[die][0],
                                sites[nearest][1] - sites[die][1]) < 1.0
        if residual > tolerance_um:
            out['problems'].append(f'{k + 1}번째 행 → Die{die + 1}: 회전·원점 보정 후 '
                                   f'{residual:.0f} µm 떨어짐 (허용 {tolerance_um:g} µm)')
        elif not same_place:
            out['problems'].append(f'{k + 1}번째 행이 Die{die + 1}보다 Die{nearest + 1}에 가까움')
    out['max_residual_um'] = max(residuals)
    if not out['problems']:
        out['status'] = 'ok'
        out['mapping'] = dict(zip(dies, used))
    return out


# ──────────────────────────────────────────────
# 평면 피팅·상관
# ──────────────────────────────────────────────

def plane_fit(values: dict, positions: dict) -> Optional[dict]:
    """{die: 값(µm)}을 Die 좌표(mm)에 v = c + a·x + b·y로 피팅. a, b는 ×1000 (ppm·µrad).

    좌표 열의 표준편차가 MIN_SPREAD_MM 미만이면 그 열은 모델에서 빼고 계수는 None이다.
    X·Y가 거의 한 직선(|r| > COLLINEAR_R)이면 두 계수 모두 None이다 (값을 정할 수 없다).

    Returns:
        {'k', 'dx': {'value','se','t'} 또는 None, 'dy': ..., 'raw': {'c','a','b'} (µm, µm/mm)}
        Die가 4개 미만이면 None. SE = sqrt(s²·diag((AᵀA)⁻¹)), s² = 잔차 제곱합/(k−열 수).
        값이 모두 같으면 t는 None.
    """
    import numpy as np
    dies = [d for d in values if d in positions and values[d] is not None]
    k = len(dies)
    if k < 4:
        return None
    xs = np.array([positions[d][0] for d in dies], dtype=float)
    ys = np.array([positions[d][1] for d in dies], dtype=float)
    v = np.array([values[d] for d in dies], dtype=float)
    use_x = float(xs.std()) >= MIN_SPREAD_MM
    use_y = float(ys.std()) >= MIN_SPREAD_MM
    if use_x and use_y and abs(float(np.corrcoef(xs, ys)[0, 1])) > COLLINEAR_R:
        use_x = use_y = False
    out = {'k': k, 'dx': None, 'dy': None, 'dies': dies,
           'raw': {'c': float(v.mean()), 'a': None, 'b': None}}
    columns = [np.ones(k)] + ([xs] if use_x else []) + ([ys] if use_y else [])
    design = np.column_stack(columns)
    p = design.shape[1]
    if p == 1 or k <= p or np.linalg.matrix_rank(design) < p:
        return out
    inv = np.linalg.inv(design.T @ design)
    coef = inv @ design.T @ v
    residual = v - design @ coef
    s2 = float(residual @ residual) / (k - p)
    se = np.sqrt(np.maximum(np.diag(inv) * s2, 0.0))
    out['raw']['c'] = float(coef[0])
    index = 1
    for key, raw_key, used in (('dx', 'a', use_x), ('dy', 'b', use_y)):
        if not used:
            continue
        value, err = float(coef[index]) * 1e3, float(se[index]) * 1e3
        t = value / err if err > 1e-12 * max(1.0, abs(value)) else None
        out[key] = {'value': 0.0 if abs(value) < 1e-9 else value, 'se': err, 't': t}
        out['raw'][raw_key] = float(coef[index])
        index += 1
    return out


def correlation(a: dict, b: dict) -> Optional[float]:
    """같은 Die끼리 짝지은 피어슨 r. 3쌍 미만이거나 한쪽이 상수면 None."""
    keys = [d for d in a if d in b and a[d] is not None and b[d] is not None]
    if len(keys) < 3:
        return None
    xs = [a[d] for d in keys]
    ys = [b[d] for d in keys]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-18 or syy <= 1e-18:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _partial(values: dict, fit: dict, positions: dict, term: str) -> Optional[dict]:
    """한 계수만 남긴 부분 잔차: ∂/∂x 항이면 v − c − b·y, ∂/∂y 항이면 v − c − a·x."""
    if not fit or fit.get(term) is None:
        return None
    raw = fit['raw']
    out = {}
    for d in fit['dies']:
        x, y = positions[d]
        v = values[d] - raw['c']
        if term == 'dx' and raw['b'] is not None:
            v -= raw['b'] * y
        if term == 'dy' and raw['a'] is not None:
            v -= raw['a'] * x
        out[d] = v
    return out


def stage_table(values: dict, positions: dict) -> list:
    """단계·축별 평면 계수, Range, 최종과의 상관.

    values: {stage: {axis: {die_label: µm}}}
    Returns: [{'stage', 'label', 'axis', 'coef': {이름: {'value','se','t'} 또는 None}, 'k',
               'range', 'r', 'r_terms': {이름: 부분 잔차 r 또는 None}}, ...]
      r은 값 그대로의 상관, r_terms는 그 계수만 남긴 부분 잔차끼리의 상관 (판정은 r_terms를 쓴다)
    """
    final_fit, final_vals = {}, {}
    for axis in AXES:
        vals = {d: v for d, v in (values.get('final', {}).get(axis) or {}).items() if v is not None}
        final_vals[axis] = vals
        final_fit[axis] = plane_fit(vals, positions) if vals else None
    rows = []
    for key, label, _span, _col in STAGES:
        for axis in AXES:
            vals = {d: v for d, v in (values.get(key, {}).get(axis) or {}).items() if v is not None}
            if not vals:
                continue
            fit = final_fit[axis] if key == 'final' else plane_fit(vals, positions)
            names = COEF_NAMES[axis]
            coef = {names[0]: fit['dx'], names[1]: fit['dy']} if fit else {}
            r_terms = {}
            if key != 'final':
                for name, term in zip(names, ('dx', 'dy')):
                    mine = _partial(vals, fit, positions, term)
                    theirs = _partial(final_vals[axis], final_fit[axis], positions, term)
                    r_terms[name] = (correlation(mine, theirs)
                                     if mine is not None and theirs is not None else None)
            rows.append({'stage': key, 'label': label, 'axis': axis, 'coef': coef,
                         'k': fit['k'] if fit else len(vals),
                         'range': max(vals.values()) - min(vals.values()),
                         'r': None if key == 'final' else correlation(vals, final_vals[axis]),
                         'r_terms': r_terms})
    return rows


def judge(table: list) -> dict:
    """최종 착지의 유의한 평면 성분마다 그 성분을 이미 가진 비전 로그 단계를 찾는다 (파생).

    Returns: {'lines': [문장], 'terms': [{'axis','term','value','t','carriers':[단계],
              'details': [{'stage','value','r','same_sign'}], 'span'}], 'rule'}
    """
    by = {(r['stage'], r['axis']): r for r in table}
    terms, lines = [], []
    for axis in AXES:
        final = by.get(('final', axis))
        if not final or not final['coef'] or final['k'] < MIN_DIES:
            continue
        for name in COEF_NAMES[axis]:
            entry = final['coef'].get(name)
            if entry is None or entry['t'] is None or abs(entry['t']) < MIN_T:
                continue
            details = []
            for stage in LOG_STAGES:
                row = by.get((stage, axis))
                if not row or not row['coef'] or row['k'] < MIN_DIES:
                    continue
                e = row['coef'].get(name)
                r_term = (row.get('r_terms') or {}).get(name)
                if e is None or e['t'] is None or r_term is None:
                    continue
                if (abs(e['t']) < MIN_T or abs(e['value']) < CARRY_RATIO * abs(entry['value'])
                        or abs(r_term) < CARRY_R or e['value'] == 0 or entry['value'] == 0):
                    continue
                same_sign = (e['value'] > 0) == (entry['value'] > 0)
                # 부분 잔차 상관의 부호가 계수 부호의 곱과 다르면 그 상관은 이 성분에서 온 것이 아니다
                if same_sign != (r_term > 0):
                    continue
                details.append({'stage': stage, 'value': e['value'], 'r': r_term,
                                'same_sign': same_sign})
            unit = COEF_UNIT[name]
            head = (f"{TERM_TEXT[name]} {entry['value']:+.2f} {unit} (t {entry['t']:+.1f})")
            if details:
                span = STAGE_SPAN[details[0]['stage']]
                listed = ', '.join(
                    f"{STAGE_LABEL[d['stage']]} {d['value']:+.2f} {unit}"
                    f"({'같은' if d['same_sign'] else '반대'} 부호, r {d['r']:+.2f})" for d in details)
                if details[0]['same_sign']:
                    tail = f'{span} 구간에서 생겨 보정 뒤에도 남은 것으로 보임'
                else:   # 반대 부호: 같은 오차의 부호 정의가 다르거나 보정이 지나친 것일 수 있다
                    tail = f'{span} 구간과 반대 부호로 연관 (부호 정의·보정 과다 확인 필요)'
                lines.append(f'{head}가 비전 보정 로그의 {listed}에도 있음 → {tail}')
            else:
                span = '비전 정렬 이후(카메라→Tip 착지)'
                lines.append(f'{head}가 비전 보정 로그에는 없고 최종 착지(HZ1_O)에만 있음 '
                             f'→ {span} 구간')
            terms.append({'axis': axis, 'term': name, 'value': entry['value'], 't': entry['t'],
                          'carriers': [d['stage'] for d in details], 'details': details,
                          'span': span})
    if not lines:
        final_k = max((r['k'] for r in table if r['stage'] == 'final'), default=0)
        if final_k < MIN_DIES:
            lines.append(f'Die {final_k}개라 구간을 판단하지 않음 (Die {MIN_DIES}개 이상 필요)')
        else:
            lines.append(f'최종 착지(HZ1_O)에 유의한 평면 성분이 없음 (|t| < {MIN_T:g} 또는 정할 수 '
                         '없는 계수) — 구간 판단 안 함')
    return {'lines': lines, 'terms': terms, 'rule': VERDICT_RULE}


# ──────────────────────────────────────────────
# Tip Positioning
# ──────────────────────────────────────────────

_Y_NAME = re.compile(r'^Y\s*([-+]?\d+(?:\.\d+)?)', re.IGNORECASE)


def _location_y(location: str, sample_rows: list) -> Optional[float]:
    """Tip Positioning 위치의 스테이지 Y (mm): 같은 Location인 측정 행의 Stage Y 평균
    (측정 행의 Location이 여럿일 때). 아니면 'Y+120'처럼 Y로 시작하는 이름의 숫자, 'Center'는 0.
    알 수 없으면 None."""
    name = (location or '').strip()
    ys = [r['stage_y'] for r in sample_rows
          if (r.get('location') or '').lower() == name.lower()
          and r.get('stage_y') is not None and r.get('point') != 0]
    if ys and len({(r.get('location') or '').lower() for r in sample_rows}) > 1:
        return sum(ys) / len(ys) / 1000.0
    match = _Y_NAME.match(name)
    if match:
        return float(match.group(1))
    if name.lower() == 'center':
        return 0.0
    return None


def tip_summary(tips: list, final_coef: dict = None) -> dict:
    """Tip Positioning 결과 변동과 (위치가 둘 이상, Y 범위 10 mm 이상이면) Tip X·Y 대 스테이지 Y 기울기."""
    lines = []
    xs = [t['tip_x'] for t in tips if t.get('tip_x') is not None]
    ys = [t['tip_y'] for t in tips if t.get('tip_y') is not None]
    out = {'count': len(tips), 'range_x': None, 'range_y': None, 'flag': False,
           'slope_x': None, 'slope_y': None, 'locations': [], 'lines': lines}
    if not tips:
        lines.append('Tip Positioning 로그 없음')
        return out
    if xs:
        out['range_x'] = max(xs) - min(xs)
    if ys:
        out['range_y'] = max(ys) - min(ys)
    out['flag'] = any(v is not None and v > TIP_VARIATION_UM
                      for v in (out['range_x'], out['range_y']))
    lines.append(f"Tip Positioning {len(tips)}회: Tip X 변동 {_fmt(out['range_x'])} µm, "
                 f"Tip Y 변동 {_fmt(out['range_y'])} µm"
                 + (f' — ⚠ {TIP_VARIATION_UM:g} µm 초과' if out['flag'] else
                    f' (기준 {TIP_VARIATION_UM:g} µm 이하)'))
    locations = sorted({(t.get('location') or '').strip() for t in tips if t.get('location')},
                       key=str.lower)
    out['locations'] = locations
    points = [(t['stage_y'], t['tip_x'], t['tip_y'], (t.get('location') or '').strip().lower())
              for t in tips if t.get('stage_y') is not None and t.get('tip_x') is not None]
    span = (max(p[0] for p in points) - min(p[0] for p in points)) if points else 0.0
    if len({p[3] for p in points}) >= 2 and span >= TIP_MIN_SPAN_MM:
        sx = _slope([p[0] for p in points], [p[1] for p in points])
        sy = _slope([p[0] for p in points if p[2] is not None],
                    [p[2] for p in points if p[2] is not None])
        out['slope_x'], out['slope_y'] = sx, sy
        text = (f"Tip X 대 스테이지 Y 기울기 {DERIVED}: {_fmt(sx, '+.2f')} µrad, Tip Y 대 "
                f"스테이지 Y: {_fmt(sy, '+.2f')} ppm (위치 {len({p[3] for p in points})}곳, "
                f"Y 범위 {span:.0f} mm, {len(points)}회)")
        theta = ((final_coef or {}).get('θx') or {}).get('value')
        if theta is not None and sx is not None:
            text += f" — 최종 착지 θx {theta:+.2f} µrad와 비교"
        lines.append(text)
    else:
        lines.append(f"Tip Positioning 위치가 {len(locations) or 1}곳"
                     f"({', '.join(locations) or '?'})"
                     + (f', 스테이지 Y 범위 {span:.0f} mm' if len(locations) > 1 else '')
                     + f"이라 Tip X와 스테이지 Y 관계는 계산하지 않음 (위치 2곳 이상, Y 범위 "
                     f"{TIP_MIN_SPAN_MM:g} mm 이상 필요). 제안: Y +120 / 0 / −120 mm 세 곳에서 "
                     "Tip Positioning을 해 Tip X 기울기를 최종 θx와 비교")
    return out


def _slope(xs, ys) -> Optional[float]:
    """최소제곱 기울기 (µm/mm × 1000 = µrad 또는 ppm). 계산할 수 없으면 None."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx * 1e3


def _fmt(value, fmt='.3f'):
    return '—' if value is None else format(value, fmt)


# ──────────────────────────────────────────────
# Lot·회차 분석
# ──────────────────────────────────────────────

def lot_folder(result: dict, lot_name: str) -> str:
    """회차 결과의 Lot 폴더 경로. 고른 폴더 하나가 Lot이면(self_lot) 회차 폴더 자체다."""
    round_path = result.get('round_path') or ''
    if not round_path:
        return ''
    if result.get('self_lot'):
        return round_path
    return os.path.join(round_path, lot_name)


def run_key(result: dict) -> tuple:
    """같은 회차인지 비교하는 키 (회차 이름, 회차 폴더)."""
    return ((result or {}).get('round'), (result or {}).get('round_path'))


def _csv_prefix(csv_name: str) -> str:
    lower = (csv_name or '').lower()
    for suffix in ('_x_ul.csv', '_y_ul.csv'):
        if lower.endswith(suffix):
            return lower[:-len(suffix)]
    return ''


def _read_lot(lot_name: str, path: str, csv_name: str = '', session=None) -> dict:
    """[작업 스레드] Lot 하나의 로그를 찾아 읽는다. 읽기 오류는 'error'에 문장으로 남긴다."""
    out = {'lot': lot_name, 'path': path, 'sample_log': None, 'tip_log': None, 'problems': [],
           'sample_rows': [], 'tips': [], 'error': '', 'csv_slot': ''}
    if session is not None and session.cancelled:
        return out
    try:
        logs = find_logs(path, session, _csv_prefix(csv_name))
        out['sample_log'], out['tip_log'] = logs['sample'], logs['tip']
        out['problems'] = logs['problems']
        if logs['sample']:
            out['sample_rows'] = parse_sample_positioning(logs['sample'])
            out['csv_slot'] = csv_slot(path, csv_name)
        if logs['tip']:
            out['tips'] = parse_tip_positioning(logs['tip'])
    except OSError as e:
        out['error'] = f'{type(e).__name__}: {e}' if not isinstance(e, LogReadError) else str(e)
    return out


def _slot_problem(read: dict) -> str:
    """로그의 Slot과 CSV의 Slot No가 다르면 문장 (둘 중 하나라도 없으면 비교하지 않는다)."""
    slots = {_slot_text(r.get('slot')) for r in read['sample_rows'] if _slot_text(r.get('slot'))}
    if len(slots) > 1:
        return f"로그의 Slot이 여러 개 ({', '.join(sorted(slots))})"
    if slots and read['csv_slot'] and slots != {read['csv_slot']}:
        return f"로그 Slot {next(iter(slots))} ≠ CSV Slot No {read['csv_slot']} (다른 샘플의 로그)"
    return ''


def analyze_result(result: dict, session=None, workers: int = LOT_LOAD_WORKERS) -> dict:
    """회차 결과 하나(선택 회차)의 비전 보정 로그 대조.

    Lot마다 로그를 읽어 Die에 짝짓고, Lot별 단계 표와 Lot 평균(Die별 평균) 단계 표, 판정,
    Tip Positioning 목록을 만든다. 로그가 없거나(no_log) 읽지 못했거나(error) 짝짓지 못한
    (mismatch) Lot은 이유와 함께 남긴다. session을 주면 폴더 목록을 다시 읽지 않는다.
    workers: Lot 로그를 겹쳐 읽을 스레드 수 (1이면 순서대로 읽는다)
    """
    raw = result.get('raw_data') or []
    data = filter_stabilization_die(raw)
    out = {'status': 'no_data', 'message': '분석할 데이터가 없음', 'lots': [], 'mean': None,
           'verdict': None, 'tips': [], 'tip_summary': None, 'round': result.get('round', '')}
    if not data:
        return out
    order, csv_names = {}, {}
    for r in raw:
        name = r.get('lot_name', '')
        order[name] = min(order.get(name, r.get('lot_index', 0)), r.get('lot_index', 0))
        if r.get('filename') and name not in csv_names:
            csv_names[name] = r['filename']
    lots = sorted(order, key=lambda n: (order[n], n))
    if not result.get('round_path'):
        out.update(status='no_logs', message=NO_LOG_TEXT)
        return out
    devs = {axis: compute_deviation_matrix(data, axis) for axis in AXES}
    positions = {f'Die{i + 1}': p for i, p in extract_die_positions(data).items()}
    analysis_dies = {extract_die_number(r.get('site_id', '')) for r in data}

    jobs = [(name, lot_folder(result, name), csv_names.get(name, '')) for name in lots]
    if len(jobs) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs)),
                                thread_name_prefix='poslog') as pool:
            reads = list(pool.map(lambda j: _read_lot(j[0], j[1], j[2], session), jobs))
    else:
        reads = [_read_lot(name, path, csv, session) for name, path, csv in jobs]
    if session is not None:
        session.check_cancelled()

    sums = {}      # stage → axis → die → [값, ...] (Lot 평균용)
    for lot_name, read in zip(lots, reads):
        lot = {'lot': lot_name, 'path': read['path'], 'sample_log': read['sample_log'],
               'tip_log': read['tip_log'], 'status': 'ok', 'problems': [], 'mapping': None,
               'stages': [], 'verdict': None}
        out['lots'].append(lot)
        if read['error']:
            lot.update(status='error', problems=[f"{READ_FAIL_TEXT} ({read['error']})"])
            continue
        for t in read['tips']:
            out['tips'].append(dict(t, lot=lot_name,
                                    stage_y=_location_y(t.get('location', ''), read['sample_rows'])))
        if not read['sample_log']:
            if read['problems']:
                lot.update(status='mismatch', problems=list(read['problems']))
            else:
                lot.update(status='no_log', problems=[NO_LOG_TEXT])
            continue
        slot_problem = _slot_problem(read)
        if slot_problem:
            lot.update(status='mismatch', problems=[slot_problem])
            continue
        lot_rows = [r for r in raw if r.get('lot_name') == lot_name]
        mapping = map_rows_to_dies(read['sample_rows'], lot_sites(lot_rows))
        lot['mapping'] = {k: v for k, v in mapping.items() if k != 'mapping'}
        if mapping['status'] != 'ok':
            lot.update(status='mismatch', problems=list(mapping['problems']))
            continue
        values = {}
        for die, row in mapping['mapping'].items():
            if die not in analysis_dies:
                continue        # 안정화 Die는 판정·통계와 같이 뺀다
            label = f'Die{die + 1}'
            for key in LOG_STAGES:
                for axis in AXES:
                    v = row.get(f'{key}_{axis.lower()}')
                    if v is not None:
                        values.setdefault(key, {}).setdefault(axis, {})[label] = v
            for axis in AXES:
                v = devs[axis]['matrix'].get(lot_name, {}).get(label)
                if v is not None:
                    values.setdefault('final', {}).setdefault(axis, {})[label] = v
        lot['stages'] = stage_table(values, positions)
        lot['verdict'] = judge(lot['stages'])
        lot['values'] = values
        for key, axes in values.items():
            for axis, per_die in axes.items():
                for die, v in per_die.items():
                    sums.setdefault(key, {}).setdefault(axis, {}).setdefault(die, []).append(v)

    counts = {}
    for lot in out['lots']:
        counts[lot['status']] = counts.get(lot['status'], 0) + 1
    excluded = ', '.join(f'{text} {counts[status]}개' for status, text in
                         (('no_log', '로그 없음'), ('mismatch', '불일치'), ('error', '읽기 실패'))
                         if counts.get(status))
    final_coef = {}
    if counts.get('ok'):
        mean_values = {key: {axis: {d: sum(vs) / len(vs) for d, vs in per_die.items()}
                             for axis, per_die in axes.items()}
                       for key, axes in sums.items()}
        table = stage_table(mean_values, positions)
        out['mean'] = {'lots': counts['ok'], 'stages': table, 'values': mean_values,
                       'positions': positions}
        out['verdict'] = judge(table)
        for row in table:
            if row['stage'] == 'final':
                final_coef.update({k: v for k, v in row['coef'].items() if v is not None})
        out['status'] = 'partial' if excluded else 'ok'
        out['message'] = f"Lot {counts['ok']}개 대조" + (f' (제외: {excluded})' if excluded else '')
    elif counts.get('error'):
        out.update(status='error', message=f'{READ_FAIL_TEXT} — ' + '; '.join(
            f"{lot['lot']}: {lot['problems'][0]}" for lot in out['lots']
            if lot['status'] == 'error')[:400])
    elif counts.get('no_log') == len(out['lots']):
        out.update(status='no_logs', message=NO_LOG_TEXT)
    else:
        out.update(status='mismatch',
                   message='로그를 Die에 짝짓지 못함 — ' + '; '.join(
                       f"{lot['lot']}: {', '.join(lot['problems'][:2])}" for lot in out['lots']
                       if lot['problems'])[:400])
    out['tip_summary'] = tip_summary(out['tips'], final_coef)
    return out


def has_read_error(analysis: dict) -> bool:
    """읽기 오류가 섞인 분석인가 (담아 두지 않고 다음에 다시 읽는다)."""
    return (analysis or {}).get('status') == 'error' or any(
        lot.get('status') == 'error' for lot in (analysis or {}).get('lots') or [])


def store_analysis(result: dict, analysis: dict) -> bool:
    """분석을 회차 결과와 같은 회차의 run_results 항목에 담는다. 읽기 오류가 섞였으면 담지 않는다.

    Returns: 담았으면 True
    """
    if has_read_error(analysis):
        return False
    key = run_key(result)
    result['positioning_log'] = analysis
    for run in result.get('run_results') or []:
        if run_key(run) == key:
            run['positioning_log'] = analysis
    return True


def ensure_analysis(result: dict, session=None, workers: int = LOT_LOAD_WORKERS) -> dict:
    """결과에 담긴 분석을 쓰고, 없으면 계산한다 (결과를 바꾸지 않는다)."""
    cached = result.get('positioning_log')
    if isinstance(cached, dict):
        return cached
    return analyze_result(result, session, workers)


def stage_series(analysis: dict, axis: str = 'X', stages=('coarse_match', 'fine_move', 'final')):
    """차트용: 단계별 [(Die Y mm, 값 µm − 단계 평균, Die)] (Lot 평균 기준)."""
    mean = (analysis or {}).get('mean') or {}
    positions = mean.get('positions') or {}
    series = {}
    for stage in stages:
        per_die = ((mean.get('values') or {}).get(stage) or {}).get(axis) or {}
        points = [(positions[d][1], v, d) for d, v in per_die.items() if d in positions]
        if not points:
            continue
        avg = sum(p[1] for p in points) / len(points)
        series[stage] = sorted(((y, v - avg, d) for y, v, d in points), key=lambda p: p[0])
    return series


__all__ = ['find_logs', 'read_table', 'parse_sample_positioning', 'parse_tip_positioning',
           'csv_slot', 'lot_sites', 'map_rows_to_dies', 'plane_fit', 'correlation', 'stage_table',
           'judge', 'tip_summary', 'lot_folder', 'run_key', 'analyze_result', 'has_read_error',
           'store_analysis', 'ensure_analysis', 'stage_series', 'LogReadError',
           'STAGES', 'STAGE_LABEL', 'COEF_NAMES', 'COEF_UNIT', 'NO_LOG_TEXT', 'READ_FAIL_TEXT',
           'VERDICT_RULE']
