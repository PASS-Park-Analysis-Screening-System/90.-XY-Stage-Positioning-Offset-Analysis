"""
core/checklist.py — 체크리스트 지표의 단일 출처

분석 시트와 같은 기준(안정화 Die 제외, 첫 리핏 제외)으로 Avg, Dev Range, Dev StdDev,
Cpk, 판정을 계산한다. Step 카드, Excel, PDF가 모두 이 함수를 써서 값이 어긋나지 않게 한다.
"""
from core.die_analysis import (MIN_JUDGE_DIES, MIN_JUDGE_REPEATS, REFERENCE_LABEL,
                               REFERENCE_REPEATS, STABILIZATION_DIE_IDX,
                               below_minimum_size, compute_deviation_matrix,
                               evaluate_deviation_pass, extract_die_number,
                               filter_stabilization_die, is_reference_size)
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
    # 판정하지 않는 축(None)은 Cpk도 없다('—'). 값이 합격처럼 읽히면 안 된다.
    # 판정은 있는데 계산할 수 없으면(σ=0, Limit 없음) compute_cpk가 NaN을 돌려주어 N/A다.
    # 측정이 없는 축은 0이 아니라 '값 없음'이다. 0.000으로 나가면 정상처럼 보인다.
    # 1 Repeat(Die 2개 이상)은 판정·Cpk를 내되 참고(reference)로 표시한다.
    has_data = bool(deviation['die_labels'])
    result = evaluate_deviation_pass(stats, deviation, spec_range, spec_stddev)
    cpk = (None if result is None
           else compute_cpk(0.0, stats['stdev'], limits.get('lsl'), limits.get('usl')))
    return {
        'axis': axis,
        'stats': stats,
        'deviation': deviation,
        'avg': stats['mean'] if has_data else None,
        'range': deviation['overall_range'] if has_data else None,
        'stddev': deviation['overall_stddev'] if has_data else None,
        'cpk': cpk,
        'result': result,
        'reference': result is not None and is_reference_size(deviation),
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
    unreadable_site = sum(1 for r in raw if extract_die_number(r.get('site_id', '')) is None)
    excluded_rows = unreadable_site + sum(1 for r in data if not is_usable(r))
    if excluded_rows:
        messages.append(f'측정 실패 또는 읽지 못한 값 {excluded_rows}행을 제외했습니다')
    for axis in ('X', 'Y'):
        dev = compute_deviation_matrix(data, axis)
        dies, repeats = len(dev['die_labels']), len(dev['repeat_labels'])
        if not dies:
            messages.append(f'{axis}축 측정값이 없습니다 (행 없음, 값 또는 Site ID를 읽지 못함)')
        elif below_minimum_size(dev):
            messages.append(f'{axis}축 {dies} Die × {repeats} Repeat는 최소 '
                            f'{MIN_JUDGE_DIES} × {REFERENCE_REPEATS}에 못 미쳐 판정하지 않습니다 '
                            f'(기준 {EXPECTED_DIES} × {EXPECTED_REPEATS})')
        elif is_reference_size(dev):
            messages.append(f'{axis}축 {dies} Die × {repeats} Repeat — Repeat이 '
                            f'{MIN_JUDGE_REPEATS}개 미만이라 판정·Cpk는 참고용입니다 '
                            f'(Die 간 편차만 봄, 반복성은 알 수 없음. '
                            f'기준 {EXPECTED_DIES} × {EXPECTED_REPEATS})')
        elif (dies, repeats) != (EXPECTED_DIES, EXPECTED_REPEATS):
            messages.append(f'{axis}축 {dies} Die × {repeats} Repeat로 분석합니다 '
                            f'(기준 {EXPECTED_DIES} × {EXPECTED_REPEATS})')
        filled = sum(v is not None for row in dev['matrix'].values() for v in row.values())
        if dies and filled != dies * repeats:
            messages.append(f'{axis}축 행렬 {dies * repeats}칸 중 {filled}칸만 값이 있습니다')
    lot_total = result.get('first_repeat_unverified', 0)
    if lot_total:
        messages.append(f'폴더 {lot_total}개 중 측정 순서상 첫 폴더가 Repeat No 1로 확인되지 않아 '
                        f'첫 리핏을 제외하지 않았습니다')
    skipped = result.get('no_measurement_folders') or []
    if skipped:
        messages.append(f"X/Y 측정 CSV가 없어 건너뛴 폴더: {', '.join(skipped)}")
    return messages


def analysis_warnings(result: dict) -> list:
    """분석 기준 라벨에 ✅를 붙이지 못하게 하는 경고 전부 (System Log의 경고와 같은 범위)."""
    failed = result.get('failed_measurements') or {}
    return ([result['error']] if result.get('error') else []) + basis_warnings(result) + (
        [failed['text']] if failed.get('count') else []) + list(
        result.get('load_errors') or []) + list(result.get('data_warnings') or [])


def result_text(result, reference: bool = False) -> str:
    """판정 값을 표시 문자열로 바꾼다 (Spec 미정의, 데이터 없음, 최소 크기 미달은 '—').

    reference: 1 Repeat 참고 판정이면 'PASS 참고 (1 Repeat)'처럼 표시한다.
    """
    if result is None:
        return '—'
    text = 'PASS' if result else 'FAIL'
    return f'{text} {REFERENCE_LABEL}' if reference else text


def basis_text(result, reference: bool = False) -> str:
    """판정 기준 칸 (Excel): 참고 판정 '참고 (1 Repeat)', 정규 판정 '정규', 판정 없음 '—'."""
    if result is None:
        return '—'
    return REFERENCE_LABEL if reference else '정규'


def cpk_text(cpk, reference: bool = False, fmt: str = '.2f') -> str:
    """Cpk 표시 문자열: 판정 없음 '—', 계산 불가 'N/A', 참고 판정이면 '(참고)'를 붙인다."""
    if cpk is None:
        return '—'
    text = 'N/A' if cpk != cpk else format(cpk, fmt)
    return f'{text} (참고)' if reference else text


def combine_verdicts(*results):
    """축별 판정을 하나로 (Step 버튼과 같은 규칙): FAIL이 있으면 FAIL, 미판정이 있으면 미판정."""
    if any(r is False for r in results):
        return False
    if any(r is None for r in results):
        return None
    return True


def verdict_reference(*axes) -> bool:
    """축별 지표(compute_axis_metrics 결과)를 합친 판정이 참고 판정인가 (단일 규칙).

    합친 판정이 FAIL이면 FAIL인 축이 모두 참고일 때만 참고다 (정규 축의 FAIL은 확정 FAIL).
    합친 판정이 PASS이면 참고 축이 하나라도 있으면 참고다. 판정이 없으면 참고도 아니다.
    """
    combined = combine_verdicts(*(m['result'] for m in axes))
    if combined is False:
        return all(m['reference'] for m in axes if m['result'] is False)
    if combined is True:
        return any(m['reference'] for m in axes)
    return False


def summarize_runs(result: dict, spec_limits: dict = None, spec_deviation: dict = None) -> list:
    """회차별 요약 (회차 비교 표·Excel 시트). 회차가 하나뿐이면 빈 목록.

    값은 체크리스트·Step 카드 기본값과 같은 분석 기준(안정화 Die 제외)과 같은 함수
    (compute_axis_metrics)로 계산한다. theta_x·theta_y는 Die 평균 편차의 평면 기울기
    (X: ∂dx/∂y, Y: ∂dy/∂x, µrad, xy_diagnosis.axis_shape)로 CSV 값만 쓴다 (Log를 읽지 않는다).
    """
    from core.xy_diagnosis import axis_shape      # xy_diagnosis가 이 모듈을 쓴다 (순환 import 방지)
    runs = result.get('run_results') or []
    if len(runs) < 2:
        return []
    recipe = result.get('short_name') or result.get('recipe', '')
    summary = []
    for run in runs:
        raw = run.get('raw_data', [])
        data = filter_stabilization_die(raw)
        axes = {axis: compute_axis_metrics(data, axis, recipe, spec_limits, spec_deviation)
                for axis in ('X', 'Y')}
        shapes = {axis: axis_shape(data, axis, dev=axes[axis]['deviation']) for axis in ('X', 'Y')}
        summary.append({
            'run': run.get('round', ''),
            'lot_count': len({r.get('lot_name') for r in raw}),
            'x': axes['X'],
            'y': axes['Y'],
            'theta_x': shapes['X']['theta'], 'theta_x_t': shapes['X']['theta_t'],
            'theta_y': shapes['Y']['theta'], 'theta_y_t': shapes['Y']['theta_t'],
            'result': combine_verdicts(axes['X']['result'], axes['Y']['result']),
            'reference': verdict_reference(axes['X'], axes['Y']),
            'warnings': analysis_warnings(run),
        })
    return summary
