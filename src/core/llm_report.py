"""
llm_report.py — LLM(대형 언어 모델) 원인 분석용 Markdown 리포트

앱 화면은 사람이 직관적으로 보는 용도이고, 'Spec이 왜 나갔는지' 원인 추론은 이
리포트를 LLM에 붙여 넣어 맡긴다. 형식은 다른 PASS 도구에서도 다시 쓸 수 있게
고정 스키마(report_schema)로 두고, Qt 없이 결과 dict만으로 만든다.

판정·요약 값은 화면·Excel과 같은 함수(compute_axis_metrics, summarize_runs,
compute_deviation_matrix, compute_affine_transform)로 계산한다. 그 값들에서 이
리포트가 따로 유도한 값(분산 분해, 평면 피팅 해석, what-if 등)은 '(파생)'으로 표시한다.
같은 입력이면 같은 문자열이 나온다(시각은 인자로 받는다).
"""

import json
import math

from core.checklist import (EXPECTED_DIES, EXPECTED_REPEATS, analysis_warnings,
                            combine_verdicts, compute_axis_metrics, result_text,
                            summarize_runs)
from core.die_analysis import (compute_affine_transform, compute_deviation_matrix,
                               extract_die_number, extract_die_positions,
                               filter_stabilization_die, stabilization_die)
from core.statistics import filter_by_method, is_usable

REPORT_SCHEMA = 'xy-stage-llm-report/1'
TOOL_NAME = 'XY Stage Positioning Offset Analyzer'
TOP_N = 3
EDGE_RATIO = 2 / 3      # 중심에서 가장 먼 Die 거리의 이 비율 이상이면 Edge
EDGE_TOLERANCE = 1e-6   # 경계에 걸친 Die가 부동소수점 오차로 갈리지 않게 (최대 거리 대비)
MAX_OUTLIER_LINES = 10
SIGNIFICANT_EFFECT = 2.0   # 선형 성분의 끝단 크기가 잔차 RMS의 이 배수 이상이면 '의미 있는 크기'
AXES = ('X', 'Y')
DERIVED = '(파생)'
_NO_DATA_ERRORS = ('No data loaded', 'No data found')


# ──────────────────────────────────────────────
# 형식 도우미
# ──────────────────────────────────────────────

def _num(value, fmt='.3f', signed=False):
    if value is None:
        return '—'
    if isinstance(value, float) and math.isnan(value):
        return 'N/A'
    return format(value, ('+' if signed else '') + fmt)


def _table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |',
             '|' + '|'.join('---' for _ in headers) + '|']
    lines += ['| ' + ' | '.join(str(c) for c in row) + ' |' for row in rows]
    # 앞뒤 빈 줄: 표 바로 뒤의 문장이 표의 행으로 읽히지 않게 한다 (GFM)
    return '\n' + '\n'.join(lines) + '\n'


def _q(text) -> str:
    """front matter 문자열 (JSON 따옴표 형식이라 YAML로도 그대로 읽힌다)."""
    return json.dumps(str(text), ensure_ascii=False)


def _name(result) -> str:
    return result.get('short_name') or result.get('recipe', '')


def _margin(value, spec):
    """(value − spec, value / spec × 100). 계산할 수 없으면 (None, None)."""
    if value is None or spec in (None, 0):
        return None, None
    return value - spec, value / spec * 100.0


def _margin_text(value, spec):
    diff, pct = _margin(value, spec)
    if diff is None:
        return '—'
    return f'{diff:+.3f} (Spec 대비 {pct:.1f}%)'


def _runs_of(result) -> list:
    return result.get('run_results') or []


def _metrics(result, spec_limits, spec_deviation) -> dict:
    data = filter_stabilization_die(result.get('raw_data', []))
    return {axis: compute_axis_metrics(data, axis, _name(result), spec_limits, spec_deviation)
            for axis in AXES}


def _spec(result, spec_deviation):
    ds = (spec_deviation or {}).get(_name(result), {})
    return ds.get('spec_range'), ds.get('spec_stddev')


def _would_pass(rng, std, spec_range, spec_stddev):
    if rng is None or std is None or spec_range is None or spec_stddev is None:
        return None
    return rng <= spec_range and std <= spec_stddev


def _range_std(values):
    """(Range, 표본 StdDev n−1). 판정과 같은 정의."""
    if len(values) < 2:
        return None, None
    mean = sum(values) / len(values)
    return (max(values) - min(values),
            math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1)))


def _run_status(run) -> str:
    if run.get('raw_data'):
        return 'ok'
    if run.get('error') and run['error'] not in _NO_DATA_ERRORS:
        return 'error'
    return 'no data'


def _first_repeat_text(result) -> str:
    if result.get('first_repeat_excluded'):
        return f"적용 ({result['first_repeat_excluded']} 제외)"
    if result.get('first_repeat_unverified'):
        return '미적용 (첫 폴더 Repeat No 1 확인 안 됨)'
    return '미적용 (폴더 10개 이하)'


# ──────────────────────────────────────────────
# 분석 묶음 (한 회차)
# ──────────────────────────────────────────────

class _Analysis:
    """한 회차 결과에서 원인 단서에 쓰는 값을 한 번만 계산해 둔다."""

    def __init__(self, result):
        self.raw = result.get('raw_data', [])
        self.data = filter_stabilization_die(self.raw)
        self.devs = {axis: compute_deviation_matrix(self.data, axis) for axis in AXES}
        self.positions = extract_die_positions(self.data)
        self.af = compute_affine_transform(self.devs['X']['die_stats'],
                                           self.devs['Y']['die_stats'], self.positions)

    def cells(self, axis):
        return [(lot, die, v) for lot, row in self.devs[axis]['matrix'].items()
                for die, v in row.items() if v is not None]

    def fitted(self) -> bool:
        return not self.af.get('degenerate') and 'fit_x' in self.af

    def plane(self, axis, die):
        """평면 피팅값 (µm). 좌표가 없어 피팅에서 빠진 Die는 None."""
        pos = self.af.get('die_positions_mm', {}).get(die)
        if pos is None:
            return None
        c0, c1, c2 = self.af['fit_x' if axis == 'X' else 'fit_y']
        return c0 + c1 * pos[0] + c2 * pos[1]

    def residual(self, axis, die):
        r = self.af.get('die_residuals', {}).get(die)
        return None if r is None else r[0 if axis == 'X' else 1]

    def whatif_plane(self, axis):
        """계통(평면) 성분을 뺀 셀들의 (Range, StdDev, 피팅에서 빠진 Die 수)."""
        if not self.fitted():
            return None, None, None
        values, missing = [], set()
        for _, die, v in self.cells(axis):
            fit = self.plane(axis, die)
            if fit is None:
                missing.add(die)
                values.append(v)
            else:
                values.append(v - fit)
        rng, std = _range_std(values)
        return rng, std, len(missing)

    def outlier_keys(self, axis):
        keys = set()
        for r in filter_by_method(self.data, axis):
            die = extract_die_number(r.get('site_id', ''))
            if r.get('is_outlier') and die is not None:
                keys.add((r.get('lot_name', ''), f'Die{die + 1}'))
        return keys

    def whatif_no_outliers(self, axis):
        keys = self.outlier_keys(axis)
        values = [v for lot, die, v in self.cells(axis) if (lot, die) not in keys]
        rng, std = _range_std(values)
        return rng, std, len(keys)


# ──────────────────────────────────────────────
# 1. Front matter
# ──────────────────────────────────────────────

def _front_matter(results, app_version, exported_at, source_root) -> str:
    mapping = any(r.get('source_path') for r in results)
    lines = ['---',
             f'report_schema: {REPORT_SCHEMA}',
             f'tool: {_q(TOOL_NAME)}',
             f'app_version: {_q(app_version)}',
             f'exported_at: {_q(exported_at)}',
             'data_source:',
             f'  mode: {"per-workflow" if mapping else "auto"}',
             f'  root: {_q(source_root)}',
             'workflows:']
    for r in results:
        runs = _runs_of(r) or [r]
        status = ', '.join(f'{_q(run.get("round", ""))}: {_q(_run_status(run))}' for run in runs)
        lines += [f'  - name: {_q(_name(r))}',
                  f'    path: {_q(r.get("source_path") or r.get("round_path", ""))}',
                  f'    selected_run: {_q(r.get("round", ""))}',
                  f'    run_count: {len(runs)}',
                  f'    run_status: {{{status}}}']
    lines.append('---')
    return '\n'.join(lines)


# ──────────────────────────────────────────────
# 2. 분석 조건
# ──────────────────────────────────────────────

def _conditions(results, spec_limits, spec_deviation) -> str:
    rows = []
    for r in results:
        name = _name(r)
        dev = (spec_deviation or {}).get(name, {})
        lim = (spec_limits or {}).get(name, {})
        rows.append([name, _num(dev.get('spec_range')), _num(dev.get('spec_stddev')),
                     _num(lim.get('X', {}).get('lsl'), '.1f'), _num(lim.get('X', {}).get('usl'), '.1f'),
                     _num(lim.get('Y', {}).get('lsl'), '.1f'), _num(lim.get('Y', {}).get('usl'), '.1f')])
    basis = [f'- {_name(r)} (선택 회차 {r.get("round", "")}): 첫 리핏 제외 {_first_repeat_text(r)}'
             for r in results]
    return '\n'.join([
        '## 분석 조건',
        '',
        '### Spec',
        _table(['Workflow', 'spec_range (µm)', 'spec_stddev (µm)',
                'X LSL (nm)', 'X USL (nm)', 'Y LSL (nm)', 'Y USL (nm)'], rows),
        '### 분석 기준',
        f'- 기준 크기: {EXPECTED_DIES} Die × {EXPECTED_REPEATS} Repeat. 측정은 22 Die이며 '
        '안정화 Die(Site 0001, Die1)는 판정·통계에서 제외한다.',
        '- 첫 리핏 제외: Lot 폴더가 11개 이상이고 측정 순서상 첫 폴더의 Repeat No가 1이면 '
        '그 폴더를 분석에서 뺀다.',
        *basis,
        '- 이상치: 축(X, Y)별로 IQR × 1.5 경계를 넘는 측정에 표시한다. 표시는 참고용이며, '
        '이상치도 Range·StdDev·판정에 그대로 포함된다.',
        '- 측정 실패: Valid=FALSE 이거나 값이 숫자가 아닌 행. 통계·판정에서 제외한다.',
        '- 단위: 측정값·Avg는 nm, 편차·Range·StdDev는 µm, 좌표는 mm.',
        '',
        '### 용어',
        '- X/Y 편차: 각 측정값(HZ1_O, nm)에서 그 회차·축의 분석 대상 전체 평균을 뺀 값을 µm로 '
        '바꾼 것. 0이면 평균 위치, +/−는 방향.',
        '- Range: 분석 대상 편차의 최댓값 − 최솟값 (µm).',
        '- StdDev: 분석 대상 편차의 표본 표준편차 (n−1, µm).',
        '- 판정: 축마다 Range ≤ spec_range 이고 StdDev ≤ spec_stddev 이면 PASS. '
        'X, Y 중 하나라도 FAIL이면 Workflow FAIL. Spec이 없거나 2 Die × 2 Repeat 미만이면 —.',
        '- Avg (nm): 측정값(raw)의 평균. 레시피마다 기준점 차이(bias)가 섞여 있어 '
        'Workflow끼리 비교할 수 없다. 병진(Offset) 변화는 같은 Workflow의 회차 간 Avg 변화로만 본다.',
        '- Cpk: 편차 기준(평균 0)으로 min(|LSL|, USL) / (3σ). σ는 측정값(raw, nm)의 '
        '모집단 표준편차(n으로 나눔). 판정이 —이면 Cpk도 —.',
        f'- {DERIVED}: 이 도구가 위 값들에서 유도한 참고값·경험 규칙. 통계 검정이나 결론이 아니다.',
    ])


# ──────────────────────────────────────────────
# 3. 결과 요약
# ──────────────────────────────────────────────

def _summary(results, spec_limits, spec_deviation) -> str:
    rows, exceeded = [], []
    for order, r in enumerate(results):
        metrics = _metrics(r, spec_limits, spec_deviation)
        warns = analysis_warnings(r)
        for axis in AXES:
            m = metrics[axis]
            rows.append((0 if m['result'] is False else 1 if m['result'] is None else 2,
                         order, axis, [
                             _name(r), r.get('round', ''), axis, result_text(m['result']),
                             _num(m['range']), _num(m['spec_range']),
                             _margin_text(m['range'], m['spec_range']),
                             _num(m['stddev']), _num(m['spec_stddev']),
                             _margin_text(m['stddev'], m['spec_stddev']),
                             _num(m['avg'], '.1f'), _num(m['cpk'], '.2f'),
                             f"{m['die_count']} × {m['repeat_count']}", m['stats'].get('count', 0),
                             len(warns)]))
            for key, label in (('range', 'Range'), ('stddev', 'StdDev')):
                diff, pct = _margin(m[key], m[f'spec_{key}'])
                if diff is not None and diff > 0:
                    note = '' if m['result'] is False else ' — 판정 —(최소 크기 미달), FAIL로 세지 않음'
                    exceeded.append(f"- {_name(r)} {axis} {label}: {m[key]:.3f} µm "
                                    f"(Spec {m[f'spec_{key}']:.3f} µm, {diff:+.3f} µm, "
                                    f"Spec 대비 {pct:.1f}%){note}")
    rows.sort(key=lambda row: row[:3])
    lines = ['## 결과 요약', '',
             '### Spec 초과 항목',
             *(exceeded or ['- 없음']),
             '',
             '### Workflow·축별 결과 (Spec 초과 먼저)',
             '여유 = 값 − Spec (µm). +이면 Spec 초과. Avg는 Workflow끼리 비교하지 않는다(분석 조건 참고).',
             _table(['Workflow', '회차', '축', '판정', 'Range (µm)', 'Spec Range', 'Range 여유',
                     'StdDev (µm)', 'Spec StdDev', 'StdDev 여유', 'Avg (nm)', 'Cpk',
                     'Die × Repeat', '측정 수', '경고 수'], [row[3] for row in rows])]
    for r in results:
        warns = analysis_warnings(r)
        if warns:
            lines += [f'#### {_name(r)} 경고', *(f'- {w}' for w in warns), '']
    return '\n'.join(lines).rstrip()


# ──────────────────────────────────────────────
# 4. 원인 단서
# ──────────────────────────────────────────────

def _f_pvalue(f_stat, d1, d2):
    try:
        from scipy.stats import f as f_dist
        return float(f_dist.sf(f_stat, d1, d2))
    except Exception:
        return None


def _between_die(a: _Analysis, axis):
    """일원 분산분석: (Die 간 비율, 잡음만일 때 기대 비율, F, p, k, N)."""
    cells = a.cells(axis)
    by_die = {}
    for _, die, v in cells:
        by_die.setdefault(die, []).append(v)
    k, n = len(by_die), len(cells)
    if k < 2 or n <= k:
        return None
    grand = sum(v for _, _, v in cells) / n
    sst = sum((v - grand) ** 2 for _, _, v in cells)
    ssb = sum(len(vs) * (sum(vs) / len(vs) - grand) ** 2 for vs in by_die.values())
    ssw = sst - ssb
    if sst <= 0:
        return None
    f_stat = (ssb / (k - 1)) / (ssw / (n - k)) if ssw > 0 else float('inf')
    return ssb / sst, (k - 1) / (n - 1), f_stat, _f_pvalue(f_stat, k - 1, n - k), k, n


def _plane_r2(a: _Analysis, axis):
    """Die 평균에 대한 평면 피팅: (R², 잡음만일 때 기대 R², 수정 R², F, p, Die 수)."""
    if not a.fitted():
        return None
    stats = {d['die']: d['avg'] for d in a.devs[axis]['die_stats']}
    used = [d for d in a.af['die_residuals'] if d in stats]
    k = len(used)
    if k < 4:
        return None
    mean = sum(stats[d] for d in used) / k
    sst = sum((stats[d] - mean) ** 2 for d in used)
    sse = sum(a.residual(axis, d) ** 2 for d in used)
    if sst <= 0:
        return None
    r2 = max(0.0, 1 - sse / sst)
    adj = 1 - (1 - r2) * (k - 1) / (k - 3)
    f_stat = (r2 / 2) / ((1 - r2) / (k - 3)) if r2 < 1 else float('inf')
    return r2, 2 / (k - 1), adj, f_stat, _f_pvalue(f_stat, 2, k - 3), k


def _span(a: _Analysis, index):
    coords = [p[index] for p in a.af.get('die_positions_mm', {}).values()]
    if not coords:
        return 0.0
    mid = sum(coords) / len(coords)
    return max(abs(c - mid) for c in coords)


def _pattern_hint(a: _Analysis) -> str:
    """평면 피팅의 교차 항으로 회전형/직교도형을 가리는 경험 규칙 (파생)."""
    af = a.af
    tx, ty = af['dx_dy_urad'], af['dy_dx_urad']
    rotation = (ty - tx) / 2
    shear = tx + ty
    # 교차 항이 만드는 끝단 크기(µm) = θ(µrad) × 반폭(mm) × 1e-3
    eff_x = abs(tx) * _span(a, 1) * 1e-3
    eff_y = abs(ty) * _span(a, 0) * 1e-3
    sig_x = eff_x >= SIGNIFICANT_EFFECT * max(af['residual_x'], 1e-9)
    sig_y = eff_y >= SIGNIFICANT_EFFECT * max(af['residual_y'], 1e-9)
    rule = (f'규칙: 교차 항의 끝단 크기(|θ|×좌표 반폭)가 그 축 잔차 RMS의 {SIGNIFICANT_EFFECT:g}배 이상이면 '
            '의미 있는 크기, |직교도| ≥ |회전|이면 직교도형, |직교도| < 0.5×|회전|이면 회전형, 그 사이는 혼합')
    if not (sig_x or sig_y):
        kind = '교차 항(회전·직교도)이 잔차보다 작아 해석하지 않음'
    elif abs(shear) >= abs(rotation):
        axis_text = ('X 편차가 Y 위치에 비례 (θx 우세)' if eff_x >= eff_y
                     else 'Y 편차가 X 위치에 비례 (θy 우세)')
        kind = f'직교도(직각도)형 또는 한 축 이동에 따른 다른 축 오차 — {axis_text}'
    elif abs(shear) < 0.5 * abs(rotation):
        kind = '회전형 (θx ≈ −θy)'
    else:
        kind = '회전과 직교도가 섞인 형태'
    return (f'- 해석 힌트 {DERIVED}: {kind}. 교차 항 끝단 크기 X {eff_x:.3f} µm, Y {eff_y:.3f} µm. '
            f'{rule}.')


def _systematic(a: _Analysis, spec_range, spec_stddev, verdicts) -> list:
    af = a.af
    lines = ['#### 계통(Systematic) vs 랜덤',
             '- 모델: Die 평균 편차를 실측 Die 좌표로 축마다 평면 피팅한다. '
             'dx = cx + (∂dx/∂x)·x + (∂dx/∂y)·y, dy = cy + (∂dy/∂x)·x + (∂dy/∂y)·y. '
             '입력이 평균을 뺀 편차라 상수항(병진)은 0에 가깝다.']
    if not a.fitted():
        lines.append(f"- 피팅 불가: 유효 Die {af.get('n_dies', 0)}개 또는 좌표가 공선. "
                     '계통 성분을 판단할 수 없음.')
        return lines
    tx, ty = af['dx_dy_urad'], af['dy_dx_urad']
    lines += [
        f"- 평면 계수 {DERIVED}: Sx = ∂dx/∂x {af['dx_dx_ppm']:+.2f} ppm, "
        f"θx = ∂dx/∂y {tx:+.2f} µrad, θy = ∂dy/∂x {ty:+.2f} µrad, "
        f"Sy = ∂dy/∂y {af['dy_dy_ppm']:+.2f} ppm (Die {af['n_dies']}개)",
        f"- 회전 = (θy − θx)/2 = {(ty - tx) / 2:+.2f} µrad (반시계 +, 화면의 Rotation θ와 같은 값), "
        f"직교도(비직각) = θx + θy = {tx + ty:+.2f} µrad {DERIVED}. "
        '순수 회전이면 θx ≈ −θy라 직교도 ≈ 0, X 편차만 Y 위치에 비례하면 θy ≈ 0이다.',
        f"- 평면 잔차 RMS (Die 평균 기준): X {af['residual_x']:.4f} µm, Y {af['residual_y']:.4f} µm",
        _pattern_hint(a)]
    for axis in AXES:
        rng, std, missing = a.whatif_plane(axis)
        if rng is None:
            continue
        verdict = _would_pass(rng, std, spec_range, spec_stddev)
        change = ('' if verdict is None or verdicts.get(axis) is None
                  else f" (현재 {result_text(verdicts[axis])} → {result_text(verdict)})")
        extra = f', 좌표 없어 그대로 둔 Die {missing}개' if missing else ''
        lines.append(f'- 계통 성분 제거 후 {axis} (what-if 추정) {DERIVED}: 셀마다 그 Die의 평면값을 빼면 '
                     f'Range {rng:.3f} µm, StdDev {std:.3f} µm{change}{extra}')

    rows = []
    for axis in AXES:
        b = _between_die(a, axis)
        p = _plane_r2(a, axis)
        rows.append([
            axis,
            _num(b[0] * 100, '.1f') if b else '—', _num(b[1] * 100, '.1f') if b else '—',
            f'{b[2]:.1f} ({_pvalue_text(b[3])})' if b else '—',
            _num(p[0] * 100, '.1f') if p else '—', _num(p[1] * 100, '.1f') if p else '—',
            _num(p[2] * 100, '.1f') if p else '—',
            f'{p[3]:.1f} ({_pvalue_text(p[4])})' if p else '—',
            _num(b[0] * p[0] * 100, '.1f') if b and p else '—'])
    lines += ['',
              f'분산 분해 {DERIVED}. Die 간 비율 = Σ n·(Die 평균 − 전체 평균)² / 전체 SS (일원 분산분석). '
              '잡음만일 때 기대 비율 ≈ (k−1)/(N−1). 평면 R² = Die 평균 분산 중 평면 피팅이 설명하는 비율, '
              '잡음만일 때 기대 R² ≈ 2/(k−1). 전체 중 계통 ≈ Die 간 비율 × 평면 R². '
              '나머지는 평면으로 설명되지 않는 Die 고유 성분과 Die 안(Repeat 간) 랜덤 성분이다. '
              'F와 p는 참고용이다(측정 간 독립을 가정).',
              _table(['축', 'Die 간 비율 (%)', '잡음 기대 (%)', 'Die 간 F (p)', '평면 R² (%)',
                      '잡음 기대 R² (%)', '수정 R² (%)', '평면 F (p)', '전체 중 계통 ≈ (%)'], rows)]
    return lines


def _pvalue_text(p):
    if p is None:
        return 'p 계산 안 됨'
    return 'p<1e-6' if p < 1e-6 else f'p={p:.3g}'


def _zones(a: _Analysis, dies):
    coords = {f'Die{i + 1}': p for i, p in a.positions.items()}
    known = [coords[d] for d in dies if d in coords]
    if len(known) < 3:
        return {}, {}, 'Die 좌표가 없어 Edge/Center를 나누지 않음'
    cx = sum(p[0] for p in known) / len(known)
    cy = sum(p[1] for p in known) / len(known)
    radius = {d: math.hypot(coords[d][0] - cx, coords[d][1] - cy) for d in dies if d in coords}
    far = max(radius.values())
    limit = far * EDGE_RATIO - far * EDGE_TOLERANCE
    zone = {d: ('Edge' if far and r >= limit else 'Center') for d, r in radius.items()}
    rule = (f'반경 = 분석 Die 좌표 중심({cx:.1f}, {cy:.1f} mm)에서의 거리. Edge = 반경이 최대 반경 '
            f'{far:.1f} mm의 {EDGE_RATIO:.0%} 이상(경계 허용 오차 {EDGE_TOLERANCE:g}×최대 반경), 나머지 Center')
    return zone, radius, rule


def _spatial(a: _Analysis) -> list:
    devs = a.devs
    coords = {f'Die{i + 1}': p for i, p in a.positions.items()}
    dies = sorted({d['die'] for axis in AXES for d in devs[axis]['die_stats']},
                  key=lambda x: int(x[3:]))
    stats = {axis: {d['die']: d for d in devs[axis]['die_stats']} for axis in AXES}
    zone, radius, rule = _zones(a, dies)
    rows = []
    for d in dies:
        x, y = coords.get(d, (None, None))
        row = [d, _num(x, '.1f'), _num(y, '.1f'), _num(radius.get(d), '.1f'), zone.get(d, '—')]
        for axis in AXES:
            s = stats[axis].get(d)
            row += ([_num(s['avg'], '.3f', True), _num(s['stddev']), _num(s['range'])]
                    if s else ['—'] * 3)
            row.append(_num(a.residual(axis, d), '.3f', True))
        rows.append(row)
    lines = ['#### 공간(Die)',
             _table(['Die', 'X (mm)', 'Y (mm)', '반경 (mm)', '구역',
                     'X 평균 편차', 'X StdDev', 'X Range', f'X 평면 잔차 {DERIVED}',
                     'Y 평균 편차', 'Y StdDev', 'Y Range', f'Y 평면 잔차 {DERIVED}'], rows),
             f'단위 µm. 평면 잔차 = Die 평균 편차 − 평면 피팅값. 구역 규칙: {rule}.', '']
    for axis in AXES:
        cells = a.cells(axis)
        if not cells:
            continue
        hi = max(cells, key=lambda c: (c[2], c[1], c[0]))
        lo = min(cells, key=lambda c: (c[2], c[1], c[0]))
        lines.append(f'- {axis} Range를 만든 셀: 최댓값 {hi[1]} / {hi[0]} {hi[2]:+.3f} µm, '
                     f'최솟값 {lo[1]} / {lo[0]} {lo[2]:+.3f} µm')
        grand = sum(c[2] for c in cells) / len(cells)
        total = sum((c[2] - grand) ** 2 for c in cells)
        share = {}
        for _, die, v in cells:
            share[die] = share.get(die, 0.0) + (v - grand) ** 2
        top = sorted(share.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
        if total:
            lines.append(f'- {axis} StdDev 기여 상위 {TOP_N} Die (편차 제곱합 비율) {DERIVED}: '
                         + ', '.join(f'{d} {v / total * 100:.1f}%' for d, v in top))
        residuals = [(d, a.residual(axis, d)) for d in dies if a.residual(axis, d) is not None]
        if residuals:
            top_res = sorted(residuals, key=lambda kv: (-abs(kv[1]), kv[0]))[:TOP_N]
            lines.append(f'- {axis} 평면 잔차 상위 {TOP_N} Die {DERIVED}: '
                         + ', '.join(f'{d} {v:+.3f} µm' for d, v in top_res)
                         + f" (잔차 RMS {a.af['residual_' + axis.lower()]:.3f} µm)")
        if zone:
            parts = []
            for z in ('Center', 'Edge'):
                members = [stats[axis][d] for d in stats[axis] if zone.get(d) == z]
                if members:
                    parts.append(f'{z} {len(members)}개: 평균 |편차| '
                                 f'{sum(abs(m["avg"]) for m in members) / len(members):.3f} µm, '
                                 f'평균 StdDev {sum(m["stddev"] for m in members) / len(members):.3f} µm')
            lines.append(f'- {axis} Edge/Center {DERIVED}: ' + ' / '.join(parts))
    return lines


def _fit_line(xs, ys):
    """(기울기, R²). 점이 2개 미만이거나 x가 모두 같으면 (None, None)."""
    n = len(xs)
    if n < 2:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy else 0.0
    return slope, r2


def _lot_order(rows):
    """측정 순서(lot_index)로 정렬한 Lot 목록 [(lot_index, lot_name), ...] (X·Y 합집합)."""
    seen = {}
    for r in rows:
        lot = r.get('lot_name', '')
        idx = r.get('lot_index', 0)
        seen[lot] = min(seen.get(lot, idx), idx)
    return sorted(((idx, lot) for lot, idx in seen.items()), key=lambda t: (t[0], t[1]))


def _temporal(a: _Analysis) -> list:
    order = _lot_order(a.data)
    col = {}
    for axis in AXES:
        matrix = a.devs[axis]['matrix']
        for lot in a.devs[axis]['repeat_labels']:
            vals = [v for v in matrix[lot].values() if v is not None]
            mean = sum(vals) / len(vals) if vals else None
            std = (math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
                   if len(vals) > 1 else None)
            col[(axis, lot)] = (mean, std)
    rows = []
    for idx, lot in order:
        row = [idx, lot]
        for axis in AXES:
            mean, std = col.get((axis, lot), (None, None))
            row += [_num(mean, '.3f', True), _num(std)]
        rows.append(row)
    lines = ['#### 시간(Repeat 순서)',
             'Lot 순서 = 측정 순서(Start Time → Repeat No → 폴더 이름)로 매긴 번호. '
             'Start Time 값은 이 리포트에 없다.',
             _table(['순서', 'Lot', 'X 평균 편차', 'X Die 간 StdDev',
                     'Y 평균 편차', 'Y Die 간 StdDev'], rows)]
    for axis in AXES:
        pts = [(idx, col[(axis, lot)][0]) for idx, lot in order
               if col.get((axis, lot), (None,))[0] is not None]
        slope, r2 = _fit_line([p[0] for p in pts], [p[1] for p in pts])
        if slope is not None:
            lines.append(f'- {axis} drift {DERIVED}: Lot 평균 편차를 Lot 순서에 최소제곱 직선으로 맞춘 '
                         f'기울기 {slope:+.4f} µm/Lot (R² {r2:.2f}), '
                         f'마지막 Lot − 첫 Lot = {pts[-1][1] - pts[0][1]:+.3f} µm')
    return lines


def _quality(result, a: _Analysis, spec_range, spec_stddev, verdicts) -> list:
    raw, data = a.raw, a.data
    lines = ['#### 이상치·측정 실패·제외',
             '- 이상치(IQR × 1.5)는 표시만 하며 Range·StdDev·판정에 포함되어 있다.']
    for axis in AXES:
        rows = [r for r in filter_by_method(data, axis) if r.get('is_outlier')]
        average = a.devs[axis]['average_nm']
        lines.append(f'- {axis} 이상치 {len(rows)}건')
        for r in sorted(rows, key=lambda r: (r.get('lot_index', 0), r.get('site_id', '')))[:MAX_OUTLIER_LINES]:
            value = r.get('value')
            dev = f', 편차 {(value - average) / 1000:+.3f} µm' if isinstance(value, (int, float)) else ''
            lines.append(f"  - {r.get('lot_name', '')} / Die{(extract_die_number(r.get('site_id', '')) or 0) + 1}"
                         f" ({r.get('site_id', '')}): {_num(value, '.3f')} nm{dev}")
        if len(rows) > MAX_OUTLIER_LINES:
            lines.append(f'  - … 외 {len(rows) - MAX_OUTLIER_LINES}건')
        if verdicts.get(axis) is False and rows:
            rng, std, n = a.whatif_no_outliers(axis)
            verdict = _would_pass(rng, std, spec_range, spec_stddev)
            lines.append(f'  - 이상치 셀 {n}개를 뺀다면 {axis} (what-if) {DERIVED}: Range {_num(rng)} µm, '
                         f'StdDev {_num(std)} µm → {result_text(verdict)}'
                         f"{'' if verdict is False else ' (판정이 바뀜)'}")
    failed = result.get('failed_measurements') or {}
    lines.append(f"- 측정 실패: {failed['text'] if failed.get('count') else '없음'}")
    lines.append(f'- 첫 리핏 제외: {_first_repeat_text(result)}')
    if result.get('no_measurement_folders'):
        lines.append(f"- X/Y 측정 CSV가 없어 건너뛴 폴더: {', '.join(result['no_measurement_folders'])}")
    if result.get('channel_dropped'):
        lines.append(f"- Z Drive 채널 {result['channel_dropped']}행 제외 (위치 오프셋은 Height 채널)")
    excluded = stabilization_die(raw)
    if excluded is not None:
        count = sum(1 for r in raw if extract_die_number(r.get('site_id', '')) == excluded)
        lines.append(f'- 안정화 Die{excluded + 1} 행 {count}개 제외')
    for msg in analysis_warnings(result):
        lines.append(f'- 경고: {msg}')
    return lines


def _clues(results, spec_limits, spec_deviation) -> str:
    lines = ['## 원인 단서', '각 Workflow의 선택 회차 기준. 편차 단위 µm.']
    for r in results:
        a = _Analysis(r)
        lines += ['', f"### {_name(r)} (회차 {r.get('round', '')})"]
        if not a.data:
            lines.append('- 분석할 데이터가 없음')
            continue
        spec_range, spec_stddev = _spec(r, spec_deviation)
        metrics = _metrics(r, spec_limits, spec_deviation)
        verdicts = {axis: metrics[axis]['result'] for axis in AXES}
        lines += (_systematic(a, spec_range, spec_stddev, verdicts) + [''] + _spatial(a) + ['']
                  + _temporal(a) + [''] + _quality(r, a, spec_range, spec_stddev, verdicts))
    return '\n'.join(lines)


# ──────────────────────────────────────────────
# 5. 회차 비교
# ──────────────────────────────────────────────

def _run_comparison(results, spec_limits, spec_deviation) -> str:
    blocks = []
    for r in results:
        runs = summarize_runs(r, spec_limits, spec_deviation)
        if not runs:
            continue
        full = _runs_of(r)
        spec_range, spec_stddev = _spec(r, spec_deviation)
        rows, deltas, changes, sys_rows = [], [], [], []
        analyses = [_Analysis(run) for run in full]
        prev = None
        for run, source, a in zip(runs, full, analyses):
            x, y = run['x'], run['y']
            rows.append([run['run'] + (' ⚠' if run['warnings'] else ''), run['lot_count'],
                         _num(x['range']), _num(x['stddev']), _num(x['avg'], '.1f'),
                         _num(y['range']), _num(y['stddev']), _num(y['avg'], '.1f'),
                         f"{_num(x['cpk'], '.2f')} / {_num(y['cpk'], '.2f')}",
                         result_text(run['result']), _first_repeat_text(source)])
            if a.fitted():
                af = a.af
                wx, wy = a.whatif_plane('X'), a.whatif_plane('Y')
                sys_rows.append([run['run'], f"{af['dx_dx_ppm']:+.2f}", f"{af['dy_dy_ppm']:+.2f}",
                                 f"{af['dx_dy_urad']:+.2f}", f"{af['dy_dx_urad']:+.2f}",
                                 f"{(af['dy_dx_urad'] - af['dx_dy_urad']) / 2:+.2f}",
                                 f"{af['dx_dy_urad'] + af['dy_dx_urad']:+.2f}",
                                 f"{af['residual_x']:.3f}", f"{af['residual_y']:.3f}",
                                 f'{_num(wx[0])} / {_num(wx[1])}', f'{_num(wy[0])} / {_num(wy[1])}',
                                 result_text(combine_verdicts(_would_pass(wx[0], wx[1], spec_range, spec_stddev),
                                                     _would_pass(wy[0], wy[1], spec_range, spec_stddev)))])
            else:
                sys_rows.append([run['run']] + ['—'] * 11)
            if prev is not None:
                def d(axis, key, fmt='.3f'):
                    cur, old = run[axis][key], prev[axis][key]
                    return '—' if cur is None or old is None else format(cur - old, '+' + fmt)
                deltas.append([f"{prev['run']} → {run['run']}",
                               d('x', 'range'), d('x', 'stddev'), d('x', 'avg', '.1f'),
                               d('y', 'range'), d('y', 'stddev'), d('y', 'avg', '.1f')])
                if prev['result'] is not run['result']:
                    changes.append(f"- {prev['run']} → {run['run']}: "
                                   f"{result_text(prev['result'])} → {result_text(run['result'])}")
            prev = run
        fails = [run['run'] for run in runs if run['result'] is False]
        blocks += ['', f"### {_name(r)} (회차 {len(runs)}개, 선택 {r.get('round', '')})",
                   _table(['회차', 'Lot 수', 'X Range', 'X StdDev', 'X Avg (nm)', 'Y Range',
                           'Y StdDev', 'Y Avg (nm)', 'Cpk X/Y', '판정', '첫 리핏 제외'], rows),
                   '이전 회차 대비 변화 (Range·StdDev µm, Avg nm). Avg 변화는 같은 Workflow 안의 병진 변화다.',
                   _table(['회차', 'ΔX Range', 'ΔX StdDev', 'ΔX Avg', 'ΔY Range', 'ΔY StdDev',
                           'ΔY Avg'], deltas),
                   f"- FAIL 회차: {', '.join(fails) if fails else '없음'}",
                   *(changes or ['- 판정이 바뀐 회차 없음']),
                   '',
                   f'회차별 계통 성분 {DERIVED}. 평면 계수(ppm, µrad)와 잔차 RMS(µm), 계통 성분을 뺀 '
                   'what-if Range / StdDev(µm)와 그때의 판정. 하드웨어 변경이 기하 오차(계수)를 움직였는지, '
                   '랜덤 성분만 바뀌었는지 본다.',
                   _table(['회차', 'Sx (ppm)', 'Sy (ppm)', 'θx (µrad)', 'θy (µrad)', '회전 (µrad)',
                           '직교도 (µrad)', '잔차 X', '잔차 Y', 'what-if X R/σ', 'what-if Y R/σ',
                           'what-if 판정'], sys_rows)]
        for axis in AXES:
            dies = sorted({d['die'] for a in analyses for d in a.devs[axis]['die_stats']},
                          key=lambda x: int(x[3:]))
            per_run = [{d['die']: d['avg'] for d in a.devs[axis]['die_stats']} for a in analyses]
            matrix = [[die] + [_num(m.get(die), '.3f', True) for m in per_run] for die in dies]
            blocks += [f'회차 × Die 평균 편차 — {axis} (µm). 열이 회차. 같은 Die 패턴이 회차마다 반복되는지 본다.',
                       _table(['Die'] + [run['run'] for run in runs], matrix)]
    if not blocks:
        return ''
    return '\n'.join(['## 회차 비교', '회차별 값은 결과 요약과 같은 분석 기준. ⚠는 경고가 있는 회차.']
                     + blocks).rstrip()


def _change_log(results) -> str:
    lines = ['## 회차 변경 이력 (사용자 기입)',
             '회차마다 바꾼 모듈·설정·조건을 적는다.', '',
             '| Workflow | 회차 | 변경 내용 |', '|---|---|---|']
    for r in results:
        for run in _runs_of(r) or [r]:
            lines.append(f"| {_name(r)} | {run.get('round', '')} |  |")
    return '\n'.join(lines)


# ──────────────────────────────────────────────
# 7. 부록
# ──────────────────────────────────────────────

def _appendix_table(raw, axis) -> str:
    excluded = stabilization_die(raw)
    analysis = filter_stabilization_die(raw)
    dev = compute_deviation_matrix(analysis, axis)
    axis_rows = filter_by_method(raw, axis)
    lot_names = [lot for _, lot in _lot_order(axis_rows)]
    failed, outlier = set(), set()
    for r in axis_rows:
        die = extract_die_number(r.get('site_id', ''))
        if die is None:
            continue
        key = (r.get('lot_name', ''), die)
        if not is_usable(r):
            failed.add(key)
        elif r.get('is_outlier'):
            outlier.add(key)
    dies = sorted({d for d in (extract_die_number(r.get('site_id', '')) for r in axis_rows)
                   if d is not None})
    rows = []
    for die in dies:
        label = f'Die{die + 1}'
        cells = []
        for lot in lot_names:
            if die == excluded:
                cells.append('(제외)')
                continue
            value = dev['matrix'].get(lot, {}).get(label)
            if value is None:
                cells.append('NaN' if (lot, die) in failed else '—')
            else:
                cells.append(f"{value:+.3f}{'*' if (lot, die) in outlier else ''}")
        rows.append([label] + cells)
    return _table(['Die'] + lot_names, rows)


def _appendix(results) -> str:
    lines = ['## 부록: Die×Repeat 편차 표',
             '단위 µm, 소수 셋째 자리(nm 단위) 반올림. 편차 = 측정값 − 그 회차·축의 분석 대상 평균. '
             '`*` 이상치(IQR × 1.5, 판정에 포함), `NaN` 측정 실패로 값 없음, `(제외)` 안정화 Die, '
             '`—` 값 없음. 첫 리핏으로 제외한 폴더는 표에 없다. 열은 측정 순서.']
    for r in results:
        for run in _runs_of(r) or [r]:
            raw = run.get('raw_data', [])
            lines += ['', f"### {_name(r)} — 회차 {run.get('round', '')}"]
            if not raw:
                lines.append(f"- 데이터 없음{(' (' + run['error'] + ')') if run.get('error') else ''}")
                continue
            for axis in AXES:
                lines += ['', f'#### {axis}', _appendix_table(raw, axis)]
    return '\n'.join(lines).rstrip()


# ──────────────────────────────────────────────
# 8. LLM 분석 요청
# ──────────────────────────────────────────────

def _request(results, spec_limits, spec_deviation) -> str:
    any_fail = any(_metrics(r, spec_limits, spec_deviation)[axis]['result'] is False
                   for r in results for axis in AXES)
    multi_run = any(len(_runs_of(r)) >= 2 for r in results)
    if any_fail:
        goals = ['1. 어떤 Workflow·축의 어떤 Spec 항목(Range, StdDev)이 얼마나(µm, Spec 대비 %) '
                 '초과했는지 정리해 주세요.',
                 '2. 원인을 다음 축으로 나눠 분석해 주세요.',
                 '   - 계통 성분(평면 계수: Scaling Sx·Sy, 교차 항 θx·θy, 회전·직교도) vs 랜덤 성분',
                 '   - 공간(특정 Die, Edge/Center) vs 시간(drift, warm-up, Repeat 순서)',
                 '   - 회차 간 변화 (계수·잔차·Avg 변화가 회차마다 어떻게 움직였는지)',
                 '3. 가능성이 높은 근본 원인을 순위로 제시하고, 각각 어느 표·숫자가 근거인지 적어 주세요.']
    else:
        goals = ['1. Spec 초과는 없습니다. 여유가 가장 작은 Workflow·축·항목(Range, StdDev)을 '
                 '여유(µm, Spec 대비 %) 순으로 정리해 주세요.',
                 '2. 여유를 줄이는 성분이 계통(평면 계수)인지 랜덤인지, 공간인지 시간인지 나눠 주세요.',
                 '3. 앞으로 Spec을 넘을 위험이 있는 항목과 그 근거를 적어 주세요.']
    lines = ['## LLM 분석 요청',
             '위 리포트는 XY Stage 위치 오프셋 측정의 분석 결과입니다. 이 리포트에 있는 숫자와 표만 근거로',
             '다음을 한국어로 답해 주세요.', '',
             *goals,
             '4. 원인을 좁히기 위한 다음 확인 항목이나 실험을 제안해 주세요.',
             '5. 이 데이터만으로는 결론 낼 수 없는 것을 분명히 적어 주세요.',
             '',
             '참고:',
             f'- {DERIVED}로 표시한 값(분산 분해, F·p, 평면 계수 해석 힌트, what-if, 기여 비율, drift)은 '
             '이 도구의 유도값·경험 규칙입니다. 통계 검정 결과나 결론으로 다루지 말고 근거 중 하나로만 쓰세요.',
             '- Avg(nm)는 레시피별 기준점 차이가 섞여 Workflow끼리 비교할 수 없습니다. 병진 변화는 '
             '같은 Workflow의 회차 간 Avg 변화로만 판단하세요.',
             '- X/Y 직교도(직각도) 오차, 한 축 이동에 따른 다른 축 오차, 특정 Die(예: 안정화 Die 다음 첫 Die)의 '
             'settling 같은 후보는 평면 계수·잔차·회차 × Die 표의 숫자가 뒷받침할 때만 제시하세요.',
             '- 이상치(*)는 Range·StdDev·판정에 포함되어 있습니다.']
    if multi_run:
        lines.append('- "회차 변경 이력" 표가 비어 있으면 회차 간 차이의 원인(무엇을 바꿨는지)은 추정하지 말고, '
                     '숫자가 어떻게 바뀌었는지만 적으세요.')
    lines += ['', '리포트에 없는 장비 사양, 부품, 이력은 지어내지 마세요. 필요하면 "추가 정보 필요"로 표시해 주세요.']
    return '\n'.join(lines)


def build_llm_report(recipe_results: list, spec_limits: dict = None,
                     spec_deviation: dict = None, *, app_version: str = '',
                     exported_at: str = '', source_root: str = '') -> str:
    """LLM 원인 분석용 Markdown 한 파일 (UTF-8 문자열).

    recipe_results: 화면의 Recipe 결과 목록 (선택 회차 결과, 회차가 여럿이면 run_results 포함)
    """
    results = list(recipe_results or [])
    sections = [
        _front_matter(results, app_version, exported_at, source_root),
        '# XY Stage 위치 오프셋 — AI 분석용 리포트',
        _conditions(results, spec_limits, spec_deviation),
        _summary(results, spec_limits, spec_deviation),
        _clues(results, spec_limits, spec_deviation),
        _run_comparison(results, spec_limits, spec_deviation),
        _change_log(results),
        _appendix(results),
        _request(results, spec_limits, spec_deviation),
    ]
    return '\n\n'.join(s.strip('\n') for s in sections if s) + '\n'
