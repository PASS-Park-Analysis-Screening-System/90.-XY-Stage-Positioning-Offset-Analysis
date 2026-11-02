"""
analyzer.py — 통계·트렌드·이상치 분석 엔진
"""

import math
import logging
from typing import Optional


def is_usable(row: dict, metric_key: str = 'value') -> bool:
    """통계에 넣을 수 있는 행인가 (Valid이고 값이 유한한 숫자).

    Valid=TRUE인데 값만 NaN인 행 하나가 평균·표준편차를 통째로 NaN으로 만들고,
    NaN은 Spec 비교가 항상 거짓이라 FAIL로 나간다. 모든 집계가 이 기준을 쓴다.
    """
    val = row.get(metric_key)
    return (isinstance(val, (int, float)) and math.isfinite(val)
            and row.get('valid', True))


def compute_statistics(data: list, metric_key: str = 'value') -> dict:
    """기본 통계 계산

    Args:
        data: batch_load() 결과 리스트
        metric_key: 측정값 키

    Returns:
        {'count': N, 'mean': ..., 'stdev': ..., 'min': ..., 'max': ..., 'range': ...}
    """
    values = [r[metric_key] for r in data if is_usable(r, metric_key)]

    if not values:
        return {'count': 0, 'mean': 0, 'stdev': 0, 'min': 0, 'max': 0, 'range': 0}

    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0
    stdev = math.sqrt(variance)

    return {
        'count': n,
        'mean': round(mean, 3),
        'stdev': round(stdev, 3),
        'min': round(min(values), 3),
        'max': round(max(values), 3),
        'range': round(max(values) - min(values), 3),
    }


def compute_group_statistics(data: list, group_by: str = 'lot_name',
                             metric_key: str = 'value') -> list:
    """그룹별 통계

    Args:
        data: batch_load() 결과
        group_by: 그룹화 키 ('lot_name', 'site_id', 'method')
        metric_key: 측정값 키

    Returns:
        [{'group': 'Lot401', 'count': 22, 'mean': ..., ...}, ...]
    """
    groups = {}
    for r in data:
        key = r.get(group_by, 'Unknown')
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    results = []
    for group_name in sorted(groups.keys()):
        stats = compute_statistics(groups[group_name], metric_key)
        stats['group'] = group_name
        results.append(stats)

    return results


def compute_trend(data: list, metric_key: str = 'value') -> list:
    """Lot 순서별 트렌드 (에이징 분석용)

    Returns:
        [{'lot_name': 'Lot401', 'lot_index': 1, 'mean': ...,
          'stdev': ..., 'min': ..., 'max': ...}, ...]
    """
    lot_groups = {}
    for r in data:
        lot = r.get('lot_name', 'Unknown')
        idx = r.get('lot_index', 0)
        if lot not in lot_groups:
            lot_groups[lot] = {'index': idx, 'values': []}
        if is_usable(r, metric_key):
            lot_groups[lot]['values'].append(r[metric_key])

    trend = []
    for lot_name in sorted(lot_groups.keys(), key=lambda x: lot_groups[x]['index']):
        info = lot_groups[lot_name]
        vals = info['values']
        if not vals:
            continue

        n = len(vals)
        mean = sum(vals) / n
        variance = sum((v - mean) ** 2 for v in vals) / n if n > 1 else 0

        trend.append({
            'lot_name': lot_name,
            'lot_index': info['index'],
            'count': n,
            'mean': round(mean, 3),
            'stdev': round(math.sqrt(variance), 3),
            'min': round(min(vals), 3),
            'max': round(max(vals), 3),
        })

    return trend


def detect_outliers(data: list, metric_key: str = 'value',
                    method: str = 'iqr', threshold: float = 1.5) -> list:
    """이상치 탐지

    Args:
        method: 'iqr' (IQR × threshold) / 'zscore' (|Z| > threshold) / 'range' (절대범위)
        threshold: IQR 배수 (기본 1.5) 또는 Z-score 기준 (기본 3)

    Returns:
        원본 data에 'is_outlier': True/False 추가된 리스트

    Note:
        기준값은 **유효한 유한 측정값에서만** 계산한다. 측정 실패 행은
        Valid=FALSE이면서 값이 NaN으로 기록되는데, NaN은 모든 비교가 False라
        sorted()의 순서를 망가뜨려 사분위수를 엉뚱한 위치로 보낸다.
        그 결과 이상치가 대량으로 잘못 잡히거나(거짓 양성), 경계가 NaN이 되어
        하나도 잡히지 않는(거짓 음성) 일이 실제 현장 데이터에서 확인됐다.
        판정 대상에서도 제외한다 — 측정 자체가 실패한 행은 이상치가 아니라 결측이다.
    """
    values = drop_non_finite(
        [r.get(metric_key, 0) for r in data
         if isinstance(r.get(metric_key), (int, float)) and r.get('valid', True)],
        'detect_outliers')

    if not values:
        for r in data:
            r['is_outlier'] = False
        return data

    def _measurable(r):
        """기준과 비교할 수 있는 행인가 (유효 + 유한)."""
        val = r.get(metric_key, 0)
        return (isinstance(val, (int, float)) and math.isfinite(val)
                and r.get('valid', True))

    if method == 'iqr':
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        q1 = sorted_vals[n // 4]
        q3 = sorted_vals[3 * n // 4]
        iqr = q3 - q1
        lower = q1 - threshold * iqr
        upper = q3 + threshold * iqr

        for r in data:
            val = r.get(metric_key, 0)
            r['is_outlier'] = _measurable(r) and (val < lower or val > upper)

    elif method == 'zscore':
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 1

        for r in data:
            val = r.get(metric_key, 0)
            if not _measurable(r):
                r['is_outlier'] = False
                continue
            z = abs(val - mean) / std if std > 0 else 0
            r['is_outlier'] = z > threshold

    elif method == 'range':
        # threshold = (min_val, max_val) 튜플로 사용
        if isinstance(threshold, tuple) and len(threshold) == 2:
            lo, hi = threshold
            for r in data:
                val = r.get(metric_key, 0)
                r['is_outlier'] = _measurable(r) and (val < lo or val > hi)
        else:
            for r in data:
                r['is_outlier'] = False

    return data


def filter_by_method(data: list, method: str) -> list:
    """Method(X/Y) 필터링"""
    return [r for r in data if r.get('method', '').upper() == method.upper()]


def filter_valid_only(data: list) -> list:
    """Valid=TRUE 데이터만 필터링"""
    return [r for r in data if r.get('valid', True)]


# 이상 유형 — 화면 필터·엑셀 리포트가 같은 기준을 쓰도록 한 곳에 정의한다.
ANOMALY_FAILED = '측정 실패'
ANOMALY_OUTLIER = '이상치'
ANOMALY_SPEC = 'Spec 초과'

# ── Spec 초과 판정 노출 스위치 ────────────────────────────────────────────
# Offset Limits(LSL/USL)는 Recipe 4개가 모두 ±5000 nm로 동일한데, 정밀도 스펙은
# 1.0/2.0/4.0/6.0 µm로 6배 차이가 난다. 이 값의 Recipe별 물리적 의미와 판별 조건이
# 확정되지 않아 현장에 혼선만 줄 수 있어, 판정 노출을 잠시 끈다.
#
# 계산 로직(spec_bounds/classify_row)은 그대로 두고 이 값만 True로 되돌리면
# Raw Data의 체크박스·Spec 열과 엑셀 리포트의 Spec 항목이 함께 복원된다.
# Cpk는 이 스위치와 무관하게 계속 Offset Limits를 사용한다.
SPEC_ANOMALY_ENABLED = False


def spec_anomaly_enabled() -> bool:
    """Spec 초과 판정을 화면·리포트에 노출할지 여부 (호출 시점에 평가)."""
    return SPEC_ANOMALY_ENABLED


def active_anomaly_kinds() -> list:
    """현재 노출 중인 이상 유형 목록 — 화면과 리포트의 열 구성을 여기서 맞춘다."""
    kinds = [ANOMALY_FAILED, ANOMALY_OUTLIER]
    if spec_anomaly_enabled():
        kinds.append(ANOMALY_SPEC)
    return kinds


def spec_bounds(spec_limits: dict, recipe_name: str, axis: str) -> tuple:
    """settings의 spec_limits에서 (lsl, usl)을 꺼낸다. 없으면 (None, None).

    PASS/FAIL 판정에 쓰는 spec_deviation(range/stddev)과는 별개로,
    개별 측정값의 규격 이탈은 spec_limits의 LSL/USL로 본다 (Cpk와 동일 기준).
    """
    entry = (spec_limits or {}).get(recipe_name) or {}
    ax = entry.get((axis or '').upper()) or {}
    return ax.get('lsl'), ax.get('usl')


def axis_reference_means(data: list, metric_key: str = 'value') -> dict:
    """축별 기준 평균 = 편차의 0점. {'X': mean, 'Y': mean}

    '해당 축의 유효·유한 측정값 전체 평균'이다. data로 filter_stabilization_die의
    결과를 넘겨야 compute_deviation_matrix(die_analysis.py)의 average_nm과 같아진다.
    PASS/FAIL(Dev Range/StdDev)과 Spec 초과·Cpk가 같은 0점을 보게 하기 위한 것이다.
    """
    buckets = {}
    for r in data or []:
        val = r.get(metric_key)
        if (isinstance(val, (int, float)) and math.isfinite(val)
                and r.get('valid', True)):
            buckets.setdefault((r.get('method') or '').upper(), []).append(val)
    return {ax: sum(v) / len(v) for ax, v in buckets.items() if v}


def classify_row(row: dict, spec_limits: dict = None,
                 recipe_name: str = '', metric_key: str = 'value',
                 axis_means: dict = None) -> list:
    """측정 행 하나의 이상 유형 목록을 돌려준다 (해당 없으면 빈 리스트).

    측정에 실패한 행은 값 자체가 없으므로 이상치·Spec 판정을 하지 않는다.

    axis_means를 주면 Spec 판정을 **편차 기준**(값 − 축 기준평균)으로 한다.
    raw 절대 offset에는 레시피별 기준점 차이(bias)가 섞여 있어, 같은 Die를
    같은 Stage가 이동해도 Recipe마다 offset이 수 µm씩 다르게 나온다.
    그 bias를 그대로 판정하면 Stage 성능이 아니라 기준점 정의를 재게 되므로,
    PASS/FAIL(Dev Range/StdDev)과 같은 편차 기준으로 맞춘다.
    """
    val = row.get(metric_key, 0)
    finite = isinstance(val, (int, float)) and math.isfinite(val)

    if not finite or not row.get('valid', True):
        return [ANOMALY_FAILED]

    kinds = []
    if row.get('is_outlier'):
        kinds.append(ANOMALY_OUTLIER)

    if spec_anomaly_enabled():
        axis = (row.get('method') or '').upper()
        target = val - (axis_means or {}).get(axis, 0.0)

        lsl, usl = spec_bounds(spec_limits, recipe_name, axis)
        if (lsl is not None and target < lsl) or (usl is not None and target > usl):
            kinds.append(ANOMALY_SPEC)
    return kinds


def drop_non_finite(values, context: str = '') -> list:
    """NaN / ±inf 등 비유한 값을 제거한다 (제거가 있었으면 경고).

    측정 실패 행은 보통 Valid=FALSE로 표시돼 filter_valid_only에서 걸러지지만,
    장비·버전에 따라 Valid=TRUE인 채 값만 NaN인 경우가 있을 수 있다.
    CSV의 'NaN' 문자열은 float()이 그대로 받아들이므로 파싱 단계에서도 걸리지 않는다.
    값 하나 때문에 차트·통계가 통째로 깨지지 않도록 두는 최종 방어선이다.
    (np.histogram은 NaN이 하나만 있어도 범위 계산이 nan이 되어 전체가 실패한다)

    계측 SW이므로 조용히 버리지 않고 몇 개를 제외했는지 반드시 남긴다.
    """
    finite = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    dropped = len(values) - len(finite)
    if dropped:
        logging.warning("비유한 값 %d개 제외%s", dropped,
                        f' ({context})' if context else '')
    return finite


def compute_repeatability(data: list, metric_key: str = 'value') -> dict:
    """반복성 분석 — Lot 간 변동 + Site 별 변동

    Returns:
        {
            'lot_variation': {'mean_of_means': ..., 'stdev_of_means': ..., ...},
            'site_variation': [{'site_id': ..., 'stdev': ..., 'range': ...}, ...],
            'overall': {'mean': ..., 'stdev': ..., 'cv_percent': ...}
        }
    """
    # Overall
    all_values = [r[metric_key] for r in data if is_usable(r, metric_key)]

    if not all_values:
        return {'lot_variation': {}, 'site_variation': [], 'overall': {}}

    overall_mean = sum(all_values) / len(all_values)
    overall_var = sum((v - overall_mean) ** 2 for v in all_values) / len(all_values)
    overall_std = math.sqrt(overall_var)
    cv = (overall_std / abs(overall_mean) * 100) if overall_mean != 0 else 0

    # Lot별 평균의 변동
    # 전부 실패한 Lot은 평균이 0으로 나오므로 측정값처럼 섞이지 않게 뺀다
    lot_stats = compute_group_statistics(data, 'lot_name', metric_key)
    lot_means = [s['mean'] for s in lot_stats if s['count'] > 0]
    if lot_means:
        mean_of_means = sum(lot_means) / len(lot_means)
        var_of_means = sum((m - mean_of_means) ** 2 for m in lot_means) / len(lot_means)
    else:
        mean_of_means = 0
        var_of_means = 0

    # Site별 변동 (동일 Site의 Lot 간 변동)
    site_groups = {}
    for r in data:
        site = r.get('site_id', '')
        method = r.get('method', '')
        key = f"{site}_{method}"
        if key not in site_groups:
            site_groups[key] = []
        if is_usable(r, metric_key):
            site_groups[key].append(r[metric_key])

    site_variation = []
    for key in sorted(site_groups.keys()):
        vals = site_groups[key]
        if len(vals) < 2:
            continue
        s_mean = sum(vals) / len(vals)
        s_var = sum((v - s_mean) ** 2 for v in vals) / len(vals)
        site_variation.append({
            'site_key': key,
            'count': len(vals),
            'mean': round(s_mean, 3),
            'stdev': round(math.sqrt(s_var), 3),
            'range': round(max(vals) - min(vals), 3),
        })

    return {
        'lot_variation': {
            'count': len(lot_means),
            'mean_of_means': round(mean_of_means, 3),
            'stdev_of_means': round(math.sqrt(var_of_means), 3),
            'range_of_means': round(max(lot_means) - min(lot_means), 3) if lot_means else 0,
        },
        'site_variation': site_variation,
        'overall': {
            'count': len(all_values),
            'mean': round(overall_mean, 3),
            'stdev': round(overall_std, 3),
            'cv_percent': round(cv, 2),
        },
    }


def compute_cpk(mean: float, stdev: float, lsl: Optional[float] = None, usl: Optional[float] = None) -> float:
    """Cpk(공정능력지수) 계산

    Args:
        mean: 평균
        stdev: 표준편차
        lsl: 하한 (Lower Specification Limit)
        usl: 상한 (Upper Specification Limit)

    Returns:
        Cpk 값. 계산 불가 시(σ=0 — 단일 샘플/무변동, 또는 LSL·USL 둘 다 없음)
        실제 값 0.0과 구분되는 float('nan')을 반환한다. 표시 측에서 NaN을 'N/A'로
        처리해야 한다.
    """
    if stdev == 0 or (lsl is None and usl is None):
        return float('nan')

    cpk_lsl = (mean - lsl) / (3 * stdev) if lsl is not None else float('inf')
    cpk_usl = (usl - mean) / (3 * stdev) if usl is not None else float('inf')
    
    return round(min(cpk_lsl, cpk_usl), 3)


def compare_1st_2nd_by_site(data_1st: list, data_2nd: list, metric_key: str = 'value') -> list:
    """Site별 1st와 2nd 평균값 매칭하여 비교
    
    Returns:
        [{'site_id': '0001_X000_Y000', 'method': 'X', 
          'val_1st': 123, 'val_2nd': 125, 'diff': -2}, ...]
    """
    def _agg_site(data):
        sites = {}
        for r in data:
            if not r.get('valid', True):
                continue
            site = r.get('site_id', '')
            method = r.get('method', '').upper()
            val = r.get(metric_key)
            if not isinstance(val, (int, float)):
                continue
            
            key = (site, method)
            if key not in sites:
                sites[key] = []
            sites[key].append(val)
            
        res = {}
        for k, vals in sites.items():
            res[k] = sum(vals) / len(vals)
        return res
        
    agg_1st = _agg_site(data_1st)
    agg_2nd = _agg_site(data_2nd)
    
    all_keys = set(agg_1st.keys()) | set(agg_2nd.keys())
    
    results = []
    for site, method in sorted(all_keys):
        v1 = agg_1st.get((site, method))
        v2 = agg_2nd.get((site, method))
        diff = v1 - v2 if v1 is not None and v2 is not None else None
        
        results.append({
            'site_id': site,
            'method': method,
            'val_1st': round(v1, 3) if v1 is not None else None,
            'val_2nd': round(v2, 3) if v2 is not None else None,
            'diff': round(diff, 3) if diff is not None else None
        })
        
    return results

