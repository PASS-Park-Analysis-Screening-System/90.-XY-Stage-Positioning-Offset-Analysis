"""
core/checklist.py — 체크리스트 지표의 단일 출처

분석 시트와 같은 기준(안정화 Die 제외, 첫 리핏 제외)으로 Avg, Dev Range, Dev StdDev,
Cpk, 판정을 계산한다. Step 카드, Excel, PDF가 모두 이 함수를 써서 값이 어긋나지 않게 한다.
"""
from core.die_analysis import (STABILIZATION_DIE_IDX, compute_deviation_matrix,
                               evaluate_deviation_pass, extract_die_number,
                               filter_stabilization_die)
from core.statistics import compute_cpk, compute_statistics, filter_by_method, is_usable

# 분석 시트의 기준
EXPECTED_DIES = 21
EXPECTED_REPEATS = 10


def compute_axis_metrics(data: list, axis: str, recipe_name: str,
                         spec_limits: dict = None,
                         spec_deviation: dict = None) -> dict:
    """주어진 행으로 한 축의 지표를 계산한다 (행 선택은 호출자 몫)."""
    # 통계도 편차 행렬과 같은 행으로 계산한다 (Site ID를 해석하지 못한 행은 양쪽 모두 제외)
    axis_rows = [r for r in filter_by_method(data, axis)
                 if extract_die_number(r.get('site_id', '')) is not None]
    stats = compute_statistics(axis_rows)
    deviation = compute_deviation_matrix(data, axis)
    limits = (spec_limits or {}).get(recipe_name, {}).get(axis, {})
    ds = (spec_deviation or {}).get(recipe_name, {})
    spec_range = ds.get('spec_range')
    spec_stddev = ds.get('spec_stddev')
    # Cpk는 편차 기준(0점 = 축 기준평균)으로 계산한다.
    #   raw 평균에는 레시피별 기준점 차이(bias)가 섞여 있어, 그대로 쓰면 Stage 성능이
    #   아니라 기준점 정의를 재게 된다. 평균이 0이므로 Cpk = min(|LSL|, USL) / 3σ다.
    # Spec이 없으면 compute_cpk가 NaN을 돌려주어 N/A로 표시된다.
    # 측정이 없는 축은 0이 아니라 '값 없음'이다. 0.000으로 나가면 정상처럼 보인다.
    has_data = bool(deviation['die_labels'])
    cpk = (compute_cpk(0.0, stats['stdev'], limits.get('lsl'), limits.get('usl'))
           if has_data else float('nan'))
    return {
        'axis': axis,
        'stats': stats,
        'deviation': deviation,
        'avg': stats['mean'] if has_data else None,
        'range': deviation['overall_range'] if has_data else None,
        'stddev': deviation['overall_stddev'] if has_data else None,
        'cpk': cpk,
        'result': evaluate_deviation_pass(stats, deviation, spec_range, spec_stddev),
        'spec_range': spec_range,
        'spec_stddev': spec_stddev,
        'die_count': len(deviation['die_labels']),
        'repeat_count': len(deviation['repeat_labels']),
    }


def build_checklist(recipe_results: list, spec_limits: dict = None,
                    spec_deviation: dict = None) -> list:
    """전체 Recipe의 체크리스트 행 (Recipe마다 X, Y 한 행씩, 분석 기준 고정)."""
    rows = []
    for result in recipe_results or []:
        recipe = result.get('short_name') or result.get('recipe', '')
        data = filter_stabilization_die(result.get('raw_data', []))
        for axis in ('X', 'Y'):
            metrics = compute_axis_metrics(data, axis, recipe, spec_limits, spec_deviation)
            metrics.update(recipe=recipe, round=result.get('round', ''))
            rows.append(metrics)
    return rows


def basis_warnings(result: dict) -> list:
    """분석 대상이 시트 기준(21 Die × 10 Repeat)과 다른 점을 문장 목록으로 돌려준다.

    값은 계산되지만 시트와 비교할 수 없는 경우다. 화면 로그와 Excel·PDF가 같은 문장을 쓴다.
    """
    raw = result.get('raw_data', [])
    if not raw:
        return []
    messages = []
    dies_in_raw = {extract_die_number(r.get('site_id', '')) for r in raw}
    if STABILIZATION_DIE_IDX not in dies_in_raw:
        messages.append('Site 0001이 없어 제외한 Die가 없습니다')
    data = filter_stabilization_die(raw)
    excluded_rows = sum(1 for r in data if not is_usable(r)
                        or extract_die_number(r.get('site_id', '')) is None)
    if excluded_rows:
        messages.append(f'측정 실패 또는 읽지 못한 값 {excluded_rows}행을 제외했습니다')
    for axis in ('X', 'Y'):
        dev = compute_deviation_matrix(data, axis)
        dies, repeats = len(dev['die_labels']), len(dev['repeat_labels'])
        if not dies:
            messages.append(f'{axis}축 측정값이 없습니다 (행 없음, 값 또는 Site ID를 읽지 못함)')
        elif (dies, repeats) != (EXPECTED_DIES, EXPECTED_REPEATS):
            messages.append(f'{axis}축 {dies} Die × {repeats} Repeat로 분석합니다 '
                            f'(기준 {EXPECTED_DIES} × {EXPECTED_REPEATS})')
    lot_total = result.get('first_repeat_unverified', 0)
    if lot_total:
        messages.append(f'폴더 {lot_total}개 중 측정 순서상 첫 폴더가 Repeat No 1로 확인되지 않아 '
                        f'첫 리핏을 제외하지 않았습니다')
    skipped = result.get('no_measurement_folders') or []
    if skipped:
        messages.append(f"X/Y 측정 CSV가 없어 건너뛴 폴더: {', '.join(skipped)}")
    return messages


def result_text(result) -> str:
    """판정 값을 표시 문자열로 바꾼다 (Spec 미정의나 데이터 없음은 '—')."""
    if result is None:
        return '—'
    return 'PASS' if result else 'FAIL'
