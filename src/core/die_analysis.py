"""
core/die_analysis.py
"""
import math
from typing import Optional

from core.statistics import is_usable


# ──────────────────────────────────────────────
# Die-based Deviation Analysis (from xy_stage_offset plugin)
# ──────────────────────────────────────────────

# 21 Die 표준 좌표 (Site 좌표, 1칸 = DIE_PITCH_MM) — fallback 용.
# 순서는 Site 0002 ~ 0022다. Site 0001은 안정화 측정이라 Site 0002와 같은 (0, 0)에 있다.
DIE_PITCH_MM = 10.0
STABILIZATION_DIE_IDX = 0   # Site 0001
DIE_POSITIONS = [
    (0, 0),   (2, 0),   (4, 0),   (6, 0),   (2, 2),
    (4, 4),   (0, 2),   (0, 4),   (0, 6),   (-2, 2),
    (-4, 4),  (-2, 0),  (-4, 0),  (-6, 0),  (-2, -2),
    (-4, -4), (0, -2),  (0, -4),  (0, -6),  (2, -2),
    (4, -4),
]


def extract_die_positions(data: list) -> dict:
    """측정 데이터에서 Die별 실제 스테이지 좌표 추출.

    raw_data의 x_um, y_um 필드를 Die 번호별로 그룹핑하여
    평균 좌표를 계산합니다.

    Args:
        data: raw_data 리스트 (x_um, y_um, site_id 필드 필요)

    Returns:
        {0: (x_mm, y_mm), 1: (x_mm, y_mm), ...}
        Die 번호(0-based) → (x_mm, y_mm) 튜플
    """
    die_coords = {}  # die_idx -> {'xs': [], 'ys': []}

    for r in data:
        die_idx = extract_die_number(r.get('site_id', ''))
        if die_idx is None:
            continue
        # 측정 실패 행의 좌표는 믿을 수 없다. NaN이 섞이면 Affine 계산이 예외로 끝난다.
        if not r.get('valid', True):
            continue
        x_um = r.get('x_um', 0)
        y_um = r.get('y_um', 0)
        if not (math.isfinite(x_um) and math.isfinite(y_um)):
            continue
        if x_um == 0 and y_um == 0:
            continue

        if die_idx not in die_coords:
            die_coords[die_idx] = {'xs': [], 'ys': []}
        die_coords[die_idx]['xs'].append(x_um)
        die_coords[die_idx]['ys'].append(y_um)

    # 평균 좌표 계산 (µm → mm 변환)
    positions = {}
    for die_idx, coords in sorted(die_coords.items()):
        avg_x = sum(coords['xs']) / len(coords['xs']) / 1000.0  # µm → mm
        avg_y = sum(coords['ys']) / len(coords['ys']) / 1000.0
        positions[die_idx] = (round(avg_x, 3), round(avg_y, 3))

    return positions


def extract_die_number(site_id: str) -> Optional[int]:
    """Site ID에서 Die 번호 추출 (0-based).
    예: '0002_X000_Y000' → 패턴 ^\\d{2}(\\d{2})_ → 02 → 2-1 = 1

    Site 번호가 00(예: '0100_...', '0000_...')이면 0-1 = -1이 되는데, 이는
    유효한 Die 인덱스가 아니므로 None을 반환한다(음수 센티넬이 'Die0' 유령 die로
    둔갑해 통계/좌표 인덱싱을 오염시키는 것을 방지).
    """
    import re
    m = re.match(r'^\d{2}(\d{2})_', str(site_id))
    if m:
        n = int(m.group(1)) - 1
        return n if n >= 0 else None
    return None


def get_die_position(die_label: str, dynamic_positions: dict = None) -> Optional[tuple]:
    """Die 라벨 → wafer 좌표. 'Die1' → index 0 → (x, y)

    Args:
        die_label: 'Die1', 'Die2', ... (1-based 표기)
        dynamic_positions: extract_die_positions()에서 반환된 동적 좌표 dict
                          None이면 하드코딩된 DIE_POSITIONS 사용
    """
    import re
    m = re.match(r'Die(\d+)', die_label)
    if not m:
        return None
    die_num_1based = int(m.group(1))
    idx = die_num_1based - 1  # 1-based label(= Site 번호) → 0-based index

    # 동적 좌표가 있으면 그것만 쓴다. 표는 단위가 달라(격자) 섞으면 위치가 틀어진다.
    if dynamic_positions:
        return dynamic_positions.get(idx)

    # fallback: 하드코딩 좌표. 표의 첫 칸이 Site 0002이므로 한 칸 당겨 읽는다.
    # Site 0001(idx 0)은 Site 0002와 같은 위치다.
    table_idx = max(idx - 1, 0)
    if table_idx < len(DIE_POSITIONS):
        return DIE_POSITIONS[table_idx]
    return None


def stabilization_die(data: list) -> Optional[int]:
    """분석에서 뺄 안정화 Die 번호(0-based). 없으면 None.

    Site 0001이다 (Site 0002와 같은 위치를 안정화 목적으로 먼저 측정한다).
    행 순서로 판단하면 첫 Lot에 Site 0001 행이 빠졌을 때 유효한 Die가 대신 제외된다.
    Site 0001이 없거나 그것뿐이면 뺄 Die가 없으므로 None이다.
    """
    dies = {extract_die_number(r.get('site_id', '')) for r in data or []}
    dies.discard(None)
    if STABILIZATION_DIE_IDX in dies and len(dies) > 1:
        return STABILIZATION_DIE_IDX
    return None


def is_analysis_row(row: dict, excluded_die: Optional[int]) -> bool:
    """Die 행렬에 들어갈 수 있는 행인가.

    Site ID를 읽지 못한 행은 Die에 배정할 수 없어 행렬에서 빠진다. 안정화 Die도 뺀다.
    """
    die = extract_die_number(row.get('site_id', ''))
    return die is not None and die != excluded_die


def filter_stabilization_die(data: list) -> list:
    """분석 대상 행만 남긴다: 안정화 Die(Site 0001)와 Site ID를 읽지 못한 행을 뺀다.

    통계, 편차 0점, 이상치, 분석 대상 표시가 모두 이 정의를 쓴다. 그래야
    compute_deviation_matrix의 행렬과 같은 행을 본다.
    """
    excluded_die = stabilization_die(data)
    return [r for r in data or [] if is_analysis_row(r, excluded_die)]


def select_display_rows(data: list, excluded_dies: set) -> list:
    """화면 Die 필터 결과: 체크 해제한 Die와 Site ID를 읽지 못한 행을 뺀다."""
    return [r for r in data
            if (die := extract_die_number(r.get('site_id', ''))) is not None
            and die not in excluded_dies]


def compute_deviation_matrix(data: list, method: str = 'X',
                             metric_key: str = 'value') -> dict:
    """Die × Repeat(Lot) Deviation 행렬 계산.

    Returns:
        {
            'die_labels': ['Die0', 'Die1', ...],
            'repeat_labels': ['Lot401', 'Lot402', ...],
            'matrix': {repeat_label: {die_label: deviation_um}},
            'average_nm': float,
            'die_stats': [{'die': 'Die0', 'avg': ..., 'stddev': ..., 'range': ...}],
            'overall_range': float,
            'overall_stddev': float,
        }
    """
    # 1) Filter by method
    filtered = [r for r in data
                if r.get('method', '').upper() == method.upper()
                and is_usable(r, metric_key)]
    if not filtered:
        return {'die_labels': [], 'repeat_labels': [], 'matrix': {},
                'average_nm': 0, 'die_stats': [], 'overall_range': 0, 'overall_stddev': 0}

    # 2) Extract die numbers
    for r in filtered:
        r['_die_num'] = extract_die_number(r.get('site_id', ''))

    valid = [r for r in filtered if r['_die_num'] is not None]

    # 3) Overall average (nm): 행렬에 들어가는 행으로 구해야 편차의 0점이 밀리지 않는다
    values = [r[metric_key] for r in (valid or filtered)]
    average = sum(values) / len(values)
    die_numbers = sorted(set(r['_die_num'] for r in valid))

    # 4) Repeat = Lot name ordering
    seen = []
    seen_set = set()
    for r in valid:
        lot = r.get('lot_name', '')
        if lot not in seen_set:
            seen_set.add(lot)
            seen.append(lot)
    repeat_map = {name: i for i, name in enumerate(seen)}

    die_labels = [f"Die{d + 1}" for d in die_numbers]  # 1-based 표기
    repeat_labels = list(seen)

    # 5) Build deviation matrix (nm → µm)
    matrix = {}
    for rl in repeat_labels:
        matrix[rl] = {dl: None for dl in die_labels}

    for r in valid:
        die_label = f"Die{r['_die_num'] + 1}"  # 1-based 표기
        lot = r.get('lot_name', '')
        if lot in matrix and die_label in matrix[lot]:
            deviation = (r[metric_key] - average) / 1000.0
            matrix[lot][die_label] = round(deviation, 6)

    # 6) Statistics
    all_devs = []
    for rl in repeat_labels:
        for dl in die_labels:
            v = matrix[rl].get(dl)
            if v is not None:
                all_devs.append(v)

    if all_devs:
        overall_range = round(max(all_devs) - min(all_devs), 3)
        n = len(all_devs)
        mean_d = sum(all_devs) / n
        var_d = sum((v - mean_d) ** 2 for v in all_devs) / (n - 1) if n > 1 else 0
        overall_stddev = round(math.sqrt(var_d), 3)
    else:
        overall_range = 0.0
        overall_stddev = 0.0

    # Die-level stats
    die_stats = []
    for dl in die_labels:
        dvals = [matrix[rl][dl] for rl in repeat_labels if matrix[rl].get(dl) is not None]
        if dvals:
            davg = sum(dvals) / len(dvals)
            if len(dvals) > 1:
                dvar = sum((v - davg) ** 2 for v in dvals) / (len(dvals) - 1)
                dstd = math.sqrt(dvar)
            else:
                dstd = 0
            drng = max(dvals) - min(dvals)
        else:
            davg = dstd = drng = 0
        die_stats.append({'die': dl, 'avg': round(davg, 3),
                          'stddev': round(dstd, 3), 'range': round(drng, 3)})

    return {
        'die_labels': die_labels,
        'repeat_labels': repeat_labels,
        'matrix': matrix,
        'average_nm': round(average, 3),
        'die_stats': die_stats,
        'overall_range': overall_range,
        'overall_stddev': overall_stddev,
    }


# 판정에 필요한 최소 크기. 이보다 작으면 Range, StdDev가 Stage 성능을 나타내지 못한다.
MIN_JUDGE_DIES = 2
MIN_JUDGE_REPEATS = 2


def below_minimum_size(deviation: dict) -> bool:
    """행렬이 판정 최소 크기보다 작은가 (Die나 Repeat 라벨이 있는 행렬만 검사)."""
    return (('die_labels' in deviation and len(deviation['die_labels']) < MIN_JUDGE_DIES)
            or ('repeat_labels' in deviation
                and len(deviation['repeat_labels']) < MIN_JUDGE_REPEATS))


def evaluate_deviation_pass(stats: dict, deviation: dict,
                            spec_range: Optional[float],
                            spec_stddev: Optional[float]) -> Optional[bool]:
    """Spec 편차 기반 PASS/FAIL 판정 — 단일 출처(Single Source of Truth).

    StepMixin(_compute_all_step_pass_states)과 CardMixin(_update_cards)이 동일 규칙을
    각자 구현해 한쪽 가드만 어긋나면 다른 쪽이 크래시하던 문제를 막기 위해, 두 경로가
    반드시 이 함수 하나만 호출하도록 통합한다.

    Args:
        stats: compute_statistics 결과 ('count' 키 사용)
        deviation: compute_deviation_matrix 결과 ('overall_range', 'overall_stddev' 키)
        spec_range: spec_deviation의 spec_range (None이면 미정의)
        spec_stddev: spec_deviation의 spec_stddev (None이면 미정의)

    Returns:
        True(PASS) / False(FAIL) / None(데이터 없음, 최소 크기 미달 또는 spec 미정의 → '미분석')
        spec_range/spec_stddev 중 하나라도 None이면 None을 반환해, 부분 spec에서
        float<=None 비교로 TypeError가 나는 것을 원천 차단한다.
    """
    # 값은 있어도 Site ID를 해석하지 못하면 행렬이 비어 Range가 0이다. 그것은 PASS가 아니다.
    # 값이 하나뿐인 행렬도 Range가 0이므로 최소 크기에 못 미치면 판정하지 않는다.
    if (stats.get('count', 0) == 0 or below_minimum_size(deviation)
            or spec_range is None or spec_stddev is None):
        return None
    return (deviation['overall_range'] <= spec_range
            and deviation['overall_stddev'] <= spec_stddev)


def compute_xy_product(x_die_stats: list, y_die_stats: list) -> dict:
    """X_avg × Y_avg 곱 맵 데이터 (Contour 용).
    Returns: {die_label: x_avg * y_avg}
    """
    x_map = {ds['die']: ds['avg'] for ds in x_die_stats}
    y_map = {ds['die']: ds['avg'] for ds in y_die_stats}
    result = {}
    for d in x_map:
        if d in y_map:
            result[d] = x_map[d] * y_map[d]
    return result


def compute_affine_transform(x_die_stats: list, y_die_stats: list,
                             die_positions: dict = None) -> dict:
    """Affine Transform 기반 계통 오차(Systematic Error) 분리.

    수학적 모델:
        dx = Tx + Sx * x - Theta * y
        dy = Ty + Sy * y + Theta * x

    Args:
        die_positions: extract_die_positions()의 실측 좌표 {die_idx: (x_mm, y_mm)}.
            없으면 표준 좌표표(DIE_POSITIONS × DIE_PITCH_MM)를 쓴다.

    Returns:
        {
            'tx': float,       # X Translation Shift (µm)
            'ty': float,       # Y Translation Shift (µm)
            'sx_ppm': float,   # X Scaling error (ppm)
            'sy_ppm': float,   # Y Scaling error (ppm)
            'theta_deg': float,# Rotation error (degrees)
            'theta_urad': float,# Rotation error (µrad)
            'residual_x': float, # X fitting residual RMS (µm)
            'residual_y': float, # Y fitting residual RMS (µm)
            'n_dies': int,
            'degenerate': bool,  # True면 데이터 부족/공선으로 피팅 신뢰 불가 (모든 값 0)
        }
    """
    import numpy as np

    def _degenerate(n=0):
        """피팅 불가(데이터 부족/공선) 시 0으로 채운 결과 + degenerate 플래그."""
        return {'tx': 0, 'ty': 0, 'sx_ppm': 0, 'sy_ppm': 0,
                'theta_deg': 0, 'theta_urad': 0,
                'residual_x': 0, 'residual_y': 0, 'n_dies': n,
                'degenerate': True}

    # Build position and offset arrays
    x_map = {ds['die']: ds['avg'] for ds in x_die_stats}
    y_map = {ds['die']: ds['avg'] for ds in y_die_stats}
    common = sorted(set(x_map.keys()) & set(y_map.keys()))

    if len(common) < 3:
        return _degenerate()

    positions = []
    dx_vals = []
    dy_vals = []
    for d in common:
        if die_positions:
            pos = die_positions.get(int(d[3:]) - 1)   # 'Die5' → die_idx 4
        else:
            table = get_die_position(d)
            pos = (table[0] * DIE_PITCH_MM, table[1] * DIE_PITCH_MM) if table else None
        if pos is None:
            continue
        positions.append(pos)
        dx_vals.append(x_map[d])
        dy_vals.append(y_map[d])

    n = len(positions)
    if n < 3:
        return _degenerate(n)

    xs = np.array([p[0] for p in positions], dtype=float)
    ys = np.array([p[1] for p in positions], dtype=float)
    dx = np.array(dx_vals, dtype=float)
    dy = np.array(dy_vals, dtype=float)

    # --- Solve for X: dx = Tx + Sx * x - Theta * y ---
    #   A_x @ [Tx, Sx, -Theta]^T = dx
    A_x = np.column_stack([np.ones(n), xs, -ys])
    sol_x, res_x, rank_x, _ = np.linalg.lstsq(A_x, dx, rcond=None)
    tx, sx, neg_theta_x = sol_x

    # --- Solve for Y: dy = Ty + Sy * y + Theta * x ---
    A_y = np.column_stack([np.ones(n), ys, xs])
    sol_y, res_y, rank_y, _ = np.linalg.lstsq(A_y, dy, rcond=None)
    ty, sy, theta_y = sol_y

    # 랭크 결손(공선 Die 등) → lstsq가 예외 없이 임의 최소노름 해를 반환하므로
    # Theta/Scaling이 무의미한 값으로 채워진다. degenerate로 표시해 호출부가
    # 회전/스케일을 신뢰하지 않도록 한다.
    if rank_x < 3 or rank_y < 3:
        return _degenerate(n)

    # 좌표는 mm, 오차는 µm이므로 기울기 단위가 µm/mm(= 1e-3)다. 무차원으로 환산한다.
    sx *= 1e-3
    sy *= 1e-3

    # Average theta from both equations
    theta_rad = (neg_theta_x + theta_y) / 2.0 * 1e-3

    # Residuals
    dx_fit = A_x @ sol_x
    dy_fit = A_y @ sol_y
    res_rms_x = float(np.sqrt(np.mean((dx - dx_fit) ** 2)))
    res_rms_y = float(np.sqrt(np.mean((dy - dy_fit) ** 2)))

    return {
        'tx': round(float(tx), 4),
        'ty': round(float(ty), 4),
        'sx_ppm': round(float(sx) * 1e6, 2),
        'sy_ppm': round(float(sy) * 1e6, 2),
        'theta_deg': round(float(math.degrees(theta_rad)), 6),
        'theta_urad': round(float(theta_rad * 1e6), 2),
        'residual_x': round(res_rms_x, 4),
        'residual_y': round(res_rms_y, 4),
        'n_dies': n,
        'degenerate': False,
    }


# ──────────────────────────────────────────────
# Pareto & Correlation Analysis (Phase 2)
# ──────────────────────────────────────────────

def compute_pareto_data(data: list, group_by: str = 'die') -> list:
    """Die별 또는 Lot별 이상치 빈도 파레토 분석.

    Args:
        data: raw_data 리스트 (is_outlier 필드 필요)
        group_by: 'die' 또는 'lot'

    Returns:
        [{'label': 'Die5', 'count': 12, 'percent': 30.0, 'cumulative': 30.0}, ...]
        (이상치 수 내림차순 정렬)
    """
    counts = {}
    for r in data:
        if not r.get('is_outlier', False):
            continue
        if group_by == 'die':
            die_num = extract_die_number(r.get('site_id', ''))
            key = f'Die{die_num + 1}' if die_num is not None else 'Unknown'  # 1-based
        else:
            key = r.get('lot_name', 'Unknown')
        counts[key] = counts.get(key, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return []

    # 내림차순 정렬
    sorted_items = sorted(counts.items(), key=lambda x: -x[1])

    result = []
    cumulative = 0.0
    for label, count in sorted_items:
        pct = count / total * 100
        cumulative += pct
        result.append({
            'label': label,
            'count': count,
            'percent': round(pct, 1),
            'cumulative': round(cumulative, 1),
        })

    return result


def compute_correlation(x_die_stats: list, y_die_stats: list) -> dict:
    """X/Y Die별 평균 편차 간 피어슨 상관관계 분석.

    Returns:
        {
            'pearson_r': float,     # 피어슨 상관계수
            'r_squared': float,     # 결정계수
            'slope': float,         # 회귀선 기울기
            'intercept': float,     # 회귀선 절편
            'points': [(x_avg, y_avg, die_label), ...],
            'n': int,
        }
    """
    x_map = {ds['die']: ds['avg'] for ds in x_die_stats}
    y_map = {ds['die']: ds['avg'] for ds in y_die_stats}
    common = sorted(set(x_map.keys()) & set(y_map.keys()))

    if len(common) < 3:
        return {'pearson_r': 0, 'r_squared': 0, 'slope': 0, 'intercept': 0,
                'points': [], 'n': 0}

    xs = [x_map[d] for d in common]
    ys = [y_map[d] for d in common]
    n = len(xs)

    # Pearson r
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n) if n > 1 else 1
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n) if n > 1 else 1
    r = cov / (sx * sy) if sx > 0 and sy > 0 else 0

    # Linear regression: y = slope * x + intercept
    slope = cov / (sx ** 2) if sx > 0 else 0
    intercept = my - slope * mx

    points = [(x_map[d], y_map[d], d) for d in common]

    return {
        'pearson_r': round(r, 4),
        'r_squared': round(r ** 2, 4),
        'slope': round(slope, 4),
        'intercept': round(intercept, 4),
        'points': points,
        'n': n,
    }
