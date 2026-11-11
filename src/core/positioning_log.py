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
어느 단계에 이미 있었는지 본다. 비전 로그 어디에도 없고 최종에만 있으면 비전 정렬 이후
(카메라 → Tip 착지) 구간에서 생긴 오차다. 판정 문구는 경험 규칙(파생)이며 규칙을 함께 적는다.

Qt를 쓰지 않는다 (로더 스레드·AI 분석용 리포트·테스트가 같이 쓴다).
"""

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.csv_loader import (LOT_LOAD_WORKERS, ScanCancelled, _decode_bytes, _read_file_bytes,
                             list_dir)
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
# 판정 규칙 (파생)
MIN_DIES = 8            # 평면 성분을 판단하는 최소 Die 수
MIN_T = 3.0             # 유의한 평면 성분: |t| ≥ 3
CARRY_RATIO = 0.5       # 로그 단계가 같은 성분을 가졌다: |값| ≥ 0.5 × |최종 값|
CARRY_R = 0.5           # 그리고 Die별 값이 최종과 |r| ≥ 0.5
TIP_VARIATION_UM = 0.5  # Tip Positioning 결과 변동 표시 기준

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
VERDICT_RULE = (f'규칙 {DERIVED}: Die {MIN_DIES}개 이상에서 최종 착지(HZ1_O)의 평면 계수 중 '
                f'|t| ≥ {MIN_T:g}인 것만 본다. 비전 로그 단계가 같은 계수를 가졌다고 보는 조건은 '
                f'|t| ≥ {MIN_T:g}, |값| ≥ {CARRY_RATIO:g} × |최종 값|, Die별 값과 최종의 '
                f'|r| ≥ {CARRY_R:g} 세 가지 모두. 어느 단계도 갖지 않으면 비전 정렬 이후'
                '(카메라→Tip 착지) 구간, 가진 단계가 있으면 그중 가장 앞 단계의 구간으로 본다.')


# ──────────────────────────────────────────────
# 파일 찾기·읽기
# ──────────────────────────────────────────────

def _norm_header(name: str) -> str:
    """'Coarse Match X (um)' → 'coarse match x' (단위·공백·대소문자 무시)."""
    name = re.sub(r'\([^)]*\)', '', name or '')
    return re.sub(r'\s+', ' ', name).strip().lower()


def _number(text) -> Optional[float]:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _find_child(path: str, wanted: str, session=None, suffix: bool = False):
    """path 안에서 이름이 wanted인(대소문자 무시) 폴더, 또는 suffix면 그렇게 끝나는 파일."""
    try:
        entries = list_dir(path, session)
    except OSError:
        return None
    for name, folder in entries:
        lower = name.lower()
        if suffix and not folder and lower.endswith(wanted):
            return os.path.join(path, name)
        if not suffix and folder and lower == wanted:
            return os.path.join(path, name)
    return None


def find_logs(lot_path: str, session=None) -> dict:
    """Lot 폴더의 로그 파일 경로 {'sample': 경로 또는 None, 'tip': 경로 또는 None}."""
    found = {'sample': None, 'tip': None}
    debug = _find_child(lot_path, 'debug', session) if lot_path else None
    if debug is None:
        return found
    for key, folder, suffix in (('sample', SAMPLE_DIR, SAMPLE_SUFFIX), ('tip', TIP_DIR, TIP_SUFFIX)):
        sub = _find_child(debug, folder, session)
        if sub is not None:
            found[key] = _find_child(sub, suffix, session, suffix=True)
    return found


def read_table(path: str) -> list:
    """탭 구분 로그를 [{정규화한 열 이름: 문자열}, ...]로. 빈 줄·빈 열·남는 칸은 버린다.

    인코딩은 CSV와 같은 순서로 맞춘다. 칸이 머리글보다 적으면 없는 열은 빠진다.
    """
    raw = _read_file_bytes(path)
    if not raw:
        return []
    text, _ = _decode_bytes(raw, path)
    if text is None:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [_norm_header(h) for h in lines[0].split('\t')]
    rows = []
    for line in lines[1:]:
        cells = line.split('\t')
        row = {h: c.strip() for h, c in zip(header, cells) if h}
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
    하나라도 어긋나면 짝짓지 않고 'mismatch'와 이유를 돌려준다 (추측하지 않는다).

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

    Returns:
        {'k', 'dx': {'value','se','t'}, 'dy': {...}} — Die가 4개 미만이거나 좌표가 공선이면 None.
        SE = sqrt(s²·diag((AᵀA)⁻¹)), s² = 잔차 제곱합/(k−3). 값이 모두 같으면 t는 None.
    """
    import numpy as np
    dies = [d for d in values if d in positions and values[d] is not None]
    k = len(dies)
    if k < 4:
        return None
    design = np.array([[1.0, *positions[d]] for d in dies])
    v = np.array([values[d] for d in dies], dtype=float)
    try:
        inv = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:
        return None
    coef = inv @ design.T @ v
    residual = v - design @ coef
    s2 = float(residual @ residual) / (k - 3)
    se = np.sqrt(np.maximum(np.diag(inv) * s2, 0.0))
    out = {'k': k}
    for key, index in (('dx', 1), ('dy', 2)):
        value, err = float(coef[index]) * 1e3, float(se[index]) * 1e3
        if err > 1e-12 * max(1.0, abs(value)):
            t = value / err
        else:
            t = None
        out[key] = {'value': 0.0 if abs(value) < 1e-9 else value, 'se': err, 't': t}
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


def stage_table(values: dict, positions: dict) -> list:
    """단계·축별 평면 계수, Range, 최종과의 상관.

    values: {stage: {axis: {die_label: µm}}}
    Returns: [{'stage', 'label', 'axis', 'coef': {이름: {'value','se','t'}}, 'k', 'range', 'r'}, ...]
    """
    rows = []
    for key, label, _span, _col in STAGES:
        for axis in AXES:
            vals = {d: v for d, v in (values.get(key, {}).get(axis) or {}).items() if v is not None}
            if not vals:
                continue
            fit = plane_fit(vals, positions)
            names = COEF_NAMES[axis]
            coef = ({names[0]: fit['dx'], names[1]: fit['dy']} if fit else {})
            final = values.get('final', {}).get(axis) or {}
            rows.append({'stage': key, 'label': label, 'axis': axis, 'coef': coef,
                         'k': fit['k'] if fit else len(vals),
                         'range': max(vals.values()) - min(vals.values()),
                         'r': None if key == 'final' else correlation(vals, final)})
    return rows


def judge(table: list) -> dict:
    """최종 착지의 유의한 평면 성분마다 그 성분을 이미 가진 비전 로그 단계를 찾는다 (파생).

    Returns: {'lines': [문장], 'terms': [{'term','value','t','carriers':[단계],'span'}], 'rule'}
    """
    by = {(r['stage'], r['axis']): r for r in table}
    terms, lines = [], []
    for axis in AXES:
        final = by.get(('final', axis))
        if not final or not final['coef'] or final['k'] < MIN_DIES:
            continue
        for name in COEF_NAMES[axis]:
            entry = final['coef'][name]
            if entry['t'] is None or abs(entry['t']) < MIN_T:
                continue
            carriers = []
            for stage in LOG_STAGES:
                row = by.get((stage, axis))
                if not row or not row['coef']:
                    continue
                e = row['coef'][name]
                if (e['t'] is not None and abs(e['t']) >= MIN_T
                        and abs(e['value']) >= CARRY_RATIO * abs(entry['value'])
                        and row['r'] is not None and abs(row['r']) >= CARRY_R):
                    carriers.append(stage)
            unit = COEF_UNIT[name]
            head = (f"{TERM_TEXT[name]} {entry['value']:+.2f} {unit} (t {entry['t']:+.1f})")
            if carriers:
                span = STAGE_SPAN[carriers[0]]
                names = ', '.join(STAGE_LABEL[s] for s in carriers)
                lines.append(f'{head}가 비전 보정 로그의 {names}에도 있음 → {span} 구간에서 '
                             '생겨 보정 뒤에도 남은 것으로 보임')
            else:
                span = '비전 정렬 이후(카메라→Tip 착지)'
                lines.append(f'{head}가 비전 보정 로그에는 없고 최종 착지(HZ1_O)에만 있음 '
                             f'→ {span} 구간')
            terms.append({'axis': axis, 'term': name, 'value': entry['value'], 't': entry['t'],
                          'carriers': carriers, 'span': span})
    if not lines:
        final_k = max((r['k'] for r in table if r['stage'] == 'final'), default=0)
        if final_k < MIN_DIES:
            lines.append(f'Die {final_k}개라 구간을 판단하지 않음 (Die {MIN_DIES}개 이상 필요)')
        else:
            lines.append(f'최종 착지(HZ1_O)에 유의한 평면 성분이 없음 (|t| < {MIN_T:g}) '
                         '— 구간 판단 안 함')
    return {'lines': lines, 'terms': terms, 'rule': VERDICT_RULE}


# ──────────────────────────────────────────────
# Tip Positioning
# ──────────────────────────────────────────────

def _location_y(location: str, sample_rows: list) -> Optional[float]:
    """Tip Positioning 위치의 스테이지 Y (mm): 같은 Location인 측정 행의 Stage Y 평균,
    없으면 위치 이름의 숫자(예: 'Y+120' → 120). 알 수 없으면 None."""
    ys = [r['stage_y'] for r in sample_rows
          if (r.get('location') or '').lower() == (location or '').lower()
          and r.get('stage_y') is not None and r.get('point') != 0]
    if ys and len({(r.get('location') or '').lower() for r in sample_rows}) > 1:
        return sum(ys) / len(ys) / 1000.0
    match = re.search(r'[-+]?\d+(?:\.\d+)?', location or '')
    if match and (location or '').lower() != 'center':
        return float(match.group())
    if (location or '').lower() == 'center':
        return 0.0
    return None


def tip_summary(tips: list, final_coef: dict = None) -> dict:
    """Tip Positioning 결과 변동과 (위치가 여럿이면) Tip X·Y 대 스테이지 Y 기울기."""
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
    points = [(t['stage_y'], t['tip_x'], t['tip_y']) for t in tips
              if t.get('stage_y') is not None and t.get('tip_x') is not None]
    if len({p[0] for p in points}) >= 2:
        sx = _slope([p[0] for p in points], [p[1] for p in points])
        sy = _slope([p[0] for p in points if p[2] is not None],
                    [p[2] for p in points if p[2] is not None])
        out['slope_x'], out['slope_y'] = sx, sy
        text = (f"Tip X 대 스테이지 Y 기울기 {DERIVED}: {_fmt(sx, '+.2f')} µrad, Tip Y 대 "
                f"스테이지 Y: {_fmt(sy, '+.2f')} ppm (위치 {len(locations)}곳, {len(points)}회)")
        theta = ((final_coef or {}).get('θx') or {}).get('value')
        if theta is not None and sx is not None:
            text += f" — 최종 착지 θx {theta:+.2f} µrad와 비교"
        lines.append(text)
    else:
        lines.append(f"Tip Positioning 위치가 {len(locations) or 1}곳"
                     f"({', '.join(locations) or '?'})뿐이라 Tip X와 스테이지 Y 관계는 계산할 수 "
                     "없음. 제안: Y +120 / 0 / −120 mm 세 곳에서 Tip Positioning을 해 Tip X 기울기를 "
                     "최종 θx와 비교")
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


def _read_lot(lot_name: str, path: str, session=None) -> dict:
    """[작업 스레드] Lot 하나의 로그를 찾아 읽는다. 오류는 'error'에 문장으로 남긴다."""
    out = {'lot': lot_name, 'path': path, 'sample_log': None, 'tip_log': None,
           'sample_rows': [], 'tips': [], 'error': ''}
    if session is not None and session.cancelled:
        return out
    try:
        logs = find_logs(path, session)
        out['sample_log'], out['tip_log'] = logs['sample'], logs['tip']
        if logs['sample']:
            out['sample_rows'] = parse_sample_positioning(logs['sample'])
        if logs['tip']:
            out['tips'] = parse_tip_positioning(logs['tip'])
    except OSError as e:
        out['error'] = f'{type(e).__name__}: {e}'
    return out


def analyze_result(result: dict, session=None, workers: int = LOT_LOAD_WORKERS) -> dict:
    """회차 결과 하나(선택 회차)의 비전 보정 로그 대조.

    Lot마다 로그를 읽어 Die에 짝짓고, Lot별 단계 표와 Lot 평균(Die별 평균) 단계 표, 판정,
    Tip Positioning 목록을 만든다. 로그가 없거나 짝짓지 못한 Lot은 이유와 함께 남긴다.
    session을 주면 폴더 목록을 다시 읽지 않고, 중지하면 ScanCancelled를 올린다.
    workers: Lot 로그를 겹쳐 읽을 스레드 수 (1이면 순서대로 읽는다)
    """
    raw = result.get('raw_data') or []
    data = filter_stabilization_die(raw)
    out = {'status': 'no_data', 'message': '분석할 데이터가 없음', 'lots': [], 'mean': None,
           'verdict': None, 'tips': [], 'tip_summary': None, 'round': result.get('round', '')}
    if not data:
        return out
    order = {}
    for r in raw:
        name = r.get('lot_name', '')
        order[name] = min(order.get(name, r.get('lot_index', 0)), r.get('lot_index', 0))
    lots = sorted(order, key=lambda n: (order[n], n))
    if not result.get('round_path'):
        out.update(status='no_logs', message=NO_LOG_TEXT)
        return out
    devs = {axis: compute_deviation_matrix(data, axis) for axis in AXES}
    positions = {f'Die{i + 1}': p for i, p in extract_die_positions(data).items()}
    analysis_dies = {extract_die_number(r.get('site_id', '')) for r in data}

    paths = [(name, lot_folder(result, name)) for name in lots]
    if len(paths) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(paths)),
                                thread_name_prefix='poslog') as pool:
            reads = list(pool.map(lambda p: _read_lot(p[0], p[1], session), paths))
    else:
        reads = [_read_lot(name, path, session) for name, path in paths]
    if session is not None:
        session.check_cancelled()

    sums = {}      # stage → axis → die → [값, ...] (Lot 평균용)
    for lot_name, read in zip(lots, reads):
        lot = {'lot': lot_name, 'path': read['path'], 'sample_log': read['sample_log'],
               'tip_log': read['tip_log'], 'status': 'ok', 'problems': [], 'mapping': None,
               'stages': [], 'verdict': None}
        out['lots'].append(lot)
        for t in read['tips']:
            out['tips'].append(dict(t, lot=lot_name,
                                    stage_y=_location_y(t.get('location', ''), read['sample_rows'])))
        if read['error']:
            lot.update(status='error', problems=[f"로그를 읽지 못함 ({read['error']})"])
            continue
        if not read['sample_log']:
            lot.update(status='no_log', problems=[NO_LOG_TEXT])
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

    used = [lot for lot in out['lots'] if lot['status'] == 'ok']
    final_coef = {}
    if used:
        mean_values = {key: {axis: {d: sum(vs) / len(vs) for d, vs in per_die.items()}
                             for axis, per_die in axes.items()}
                       for key, axes in sums.items()}
        table = stage_table(mean_values, positions)
        out['mean'] = {'lots': len(used), 'stages': table, 'values': mean_values,
                       'positions': positions}
        out['verdict'] = judge(table)
        for row in table:
            if row['stage'] == 'final':
                final_coef.update(row['coef'])
        missing = len(out['lots']) - len(used)
        out['status'] = 'ok' if not missing else 'partial'
        out['message'] = (f'Lot {len(used)}개 대조' + (f' (Lot {missing}개 제외 — 로그 없음 또는 '
                                                      '불일치)' if missing else ''))
    elif all(lot['status'] == 'no_log' for lot in out['lots']):
        out.update(status='no_logs', message=NO_LOG_TEXT)
    else:
        out.update(status='mismatch',
                   message='로그를 Die에 짝짓지 못함 — ' + '; '.join(
                       f"{lot['lot']}: {', '.join(lot['problems'][:2])}" for lot in out['lots']
                       if lot['problems'])[:400])
    out['tip_summary'] = tip_summary(out['tips'], final_coef)
    return out


def attach_analysis(result: dict, session=None) -> dict:
    """[로더 스레드] 화면에 보일 회차 결과에 분석을 담는다. 같은 회차의 run_results 항목에도 담아
    회차를 바꿨다 돌아와도 다시 읽지 않는다. 중지(ScanCancelled) 말고는 예외를 결과로 바꾼다."""
    try:
        analysis = analyze_result(result, session)
    except ScanCancelled:
        raise
    except Exception as e:      # 로그 대조 실패가 데이터 로드를 막으면 안 된다
        import logging
        logging.exception('비전 보정 로그 대조 실패: %s', result.get('short_name', ''))
        analysis = {'status': 'error', 'message': f'로그 대조 실패 ({type(e).__name__}: {e})',
                    'lots': [], 'mean': None, 'verdict': None, 'tips': [], 'tip_summary': None,
                    'round': result.get('round', '')}
    result['positioning_log'] = analysis
    for run in result.get('run_results') or []:
        if run.get('round') == result.get('round') and run.get('round_path') == result.get('round_path'):
            run['positioning_log'] = analysis
    return analysis


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
           'lot_sites', 'map_rows_to_dies', 'plane_fit', 'correlation', 'stage_table', 'judge',
           'tip_summary', 'lot_folder', 'analyze_result', 'attach_analysis', 'ensure_analysis',
           'stage_series',
           'STAGES', 'STAGE_LABEL', 'COEF_NAMES', 'COEF_UNIT', 'NO_LOG_TEXT', 'VERDICT_RULE',
           'ScanCancelled']
