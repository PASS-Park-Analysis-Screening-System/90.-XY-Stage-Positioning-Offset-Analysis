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

import csv
import io
import json
import logging
import math

from core.checklist import (EXPECTED_DIES, EXPECTED_REPEATS, analysis_warnings,
                            combine_verdicts, compute_axis_metrics, cpk_text, result_text,
                            summarize_runs)
from core.die_analysis import (REFERENCE_LABEL, compute_affine_transform, compute_deviation_matrix,
                               extract_die_number, extract_die_positions,
                               filter_stabilization_die, stabilization_die)
from core.statistics import filter_by_method, is_usable

try:   # p값만 쓴다. 없으면 F만 적는다
    from scipy.stats import f as _F_DIST
except ImportError:
    _F_DIST = None

REPORT_SCHEMA = 'xy-stage-llm-report/1'
TOOL_NAME = 'XY Stage Positioning Offset Analyzer'
TOP_N = 3
EDGE_RATIO = 2 / 3      # 중심에서 가장 먼 Die 거리의 이 비율 이상이면 Edge
EDGE_TOLERANCE = 1e-3   # 경계에 걸친 Die(좌표 반올림 차)가 갈리지 않게 (최대 거리 대비)
MAX_OUTLIER_LINES = 10
HINT_MIN_DIES = 8          # 해석 힌트를 내는 최소 Die 수
HINT_MIN_T = 3.0           # 해석 힌트를 내는 최소 |t|
RAW_CSV_COLUMNS = ['workflow', 'run', 'lot_order', 'lot', 'die', 'x_mm', 'y_mm', 'axis',
                   'deviation_um', 'flags']
AXES = ('X', 'Y')
DERIVED = '(파생)'
_NO_DATA_ERRORS = ('No data loaded', 'No data found')
SINGLE_LOT_TEXT = 'Lot 1개라 계산 안 함'
_SCIPY_WARNED = []


def _zero(value, digits):
    """반올림하면 0인 값의 '-0' 표시를 없앤다."""
    return 0.0 if value is not None and round(value, digits) == 0 else value


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
        self._coef = None

    def cells(self, axis):
        return [(lot, die, v) for lot, row in self.devs[axis]['matrix'].items()
                for die, v in row.items() if v is not None]

    def fitted(self) -> bool:
        return not self.af.get('degenerate') and 'fit_x' in self.af

    def plane(self, axis, die, cross_only=False):
        """평면 피팅값 (µm). cross_only면 교차 항(θx·y 또는 θy·x)만. 피팅에서 빠진 Die는 None."""
        pos = self.af.get('die_positions_mm', {}).get(die)
        if pos is None:
            return None
        c0, c1, c2 = self.af['fit_x' if axis == 'X' else 'fit_y']
        if cross_only:
            return c2 * pos[1] if axis == 'X' else c1 * pos[0]
        return c0 + c1 * pos[0] + c2 * pos[1]

    def residual(self, axis, die):
        r = self.af.get('die_residuals', {}).get(die)
        return None if r is None else r[0 if axis == 'X' else 1]

    def whatif_plane(self, axis, cross_only=False):
        """평면(또는 교차 항) 성분을 뺀 셀들의 (Range, StdDev, 평면 피팅에서 빠진 Die 수)."""
        if not self.fitted():
            return None, None, None
        values, missing = [], set()
        for _, die, v in self.cells(axis):
            fit = self.plane(axis, die, cross_only)
            if fit is None:
                missing.add(die)
                values.append(v)
            else:
                values.append(v - fit)
        rng, std = _range_std(values)
        return rng, std, len(missing)

    def coefficients(self):
        """평면 계수의 값·표준오차·t (표시 단위 ppm, µrad). 계산할 수 없으면 None.

        SE = sqrt(s² · diag((AᵀA)⁻¹)), s² = SSE/(k−3), A = [1, x, y] (Die 좌표).
        회전·직교도는 X 피팅의 θx와 Y 피팅의 θy를 섞는다. 두 피팅은 같은 Die 좌표를 쓰고
        잔차가 Die마다 함께 움직일 수 있으므로 공분산 Cov(θy, θx) = s_xy·inv12를 넣는다
        (s_xy = Σ eₓ·e_y /(k−3), inv12 = (AᵀA)⁻¹의 x·y 칸).
          Var(회전) = (Var θy + Var θx − 2·s_xy·inv12)/4, Var(직교도) = Var θx + Var θy + 2·s_xy·inv12
        """
        if self._coef is not None or not self.fitted():
            return self._coef
        import numpy as np
        used = list(self.af['die_positions_mm'])
        k = len(used)
        if k <= 3:
            return None
        design = np.array([[1.0, *self.af['die_positions_mm'][d]] for d in used])
        try:
            inv = np.linalg.inv(design.T @ design)
        except np.linalg.LinAlgError:
            return None
        se = {}
        for axis, index in (('X', 0), ('Y', 1)):
            sse = sum(self.af['die_residuals'][d][index] ** 2 for d in used)
            se[axis] = np.sqrt(np.diag(inv) * sse / (k - 3))
        fx, fy = self.af['fit_x'], self.af['fit_y']

        def entry(value, err):
            value, err = value * 1e3, float(err) * 1e3     # µm/mm → ppm 또는 µrad
            if err == 0:
                t = None if value == 0 else math.copysign(math.inf, value)
            else:
                t = value / err
            return {'value': value, 'se': err, 't': t}

        s_xy = sum(self.af['die_residuals'][d][0] * self.af['die_residuals'][d][1]
                   for d in used) / (k - 3)
        cov = s_xy * float(inv[1][2])
        var_sum = float(se['X'][2]) ** 2 + float(se['Y'][1]) ** 2
        se_rot = math.sqrt(max(var_sum - 2 * cov, 0.0)) / 2
        se_shear = math.sqrt(max(var_sum + 2 * cov, 0.0))
        self._coef = {
            'k': k,
            'sx': entry(fx[1], se['X'][1]),
            'tx': entry(fx[2], se['X'][2]),
            'ty': entry(fy[1], se['Y'][1]),
            'sy': entry(fy[2], se['Y'][2]),
            'rot': entry((fy[1] - fx[2]) / 2, se_rot),
            'shear': entry(fx[2] + fy[1], se_shear),
        }
        return self._coef

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

def _front_matter(results, app_version, exported_at, source_root, raw_csv=None) -> str:
    mapping = any(r.get('source_path') for r in results)
    lines = ['---',
             f'report_schema: {REPORT_SCHEMA}',
             f'tool: {_q(TOOL_NAME)}',
             f'app_version: {_q(app_version)}',
             f'exported_at: {_q(exported_at)}',
             'data_source:',
             f'  mode: {"per-workflow" if mapping else "auto"}',
             f'  root: {_q(source_root)}',
             f"raw_csv: {_q((raw_csv or {}).get('name', '') if not (raw_csv or {}).get('error') else '')}",
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
        'X, Y 중 하나라도 FAIL이면 Workflow FAIL. Spec이 없거나 Die 2개 미만이면 —. '
        'Repeat이 1개(Die 2개 이상)면 같은 규칙으로 판정하되 "참고 (1 Repeat)"로 표시한다: '
        'Die 간 편차만 보며 반복성(Repeat 간 변동)은 알 수 없다. Cpk도 같은 표시.',
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
                             _name(r), r.get('round', ''), axis,
                             result_text(m['result'], m['reference']),
                             _num(m['range']), _num(m['spec_range']),
                             _margin_text(m['range'], m['spec_range']),
                             _num(m['stddev']), _num(m['spec_stddev']),
                             _margin_text(m['stddev'], m['spec_stddev']),
                             _num(m['avg'], '.1f'), cpk_text(m['cpk'], m['reference']),
                             f"{m['die_count']} × {m['repeat_count']}", m['stats'].get('count', 0),
                             len(warns)]))
            for key, label in (('range', 'Range'), ('stddev', 'StdDev')):
                diff, pct = _margin(m[key], m[f'spec_{key}'])
                if diff is not None and diff > 0:
                    note = ('' if m['result'] is False else ' — 판정 —(최소 크기 미달), FAIL로 세지 않음')
                    if m['reference']:
                        note += f' — {REFERENCE_LABEL} 판정'
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
    if _F_DIST is None:
        if not _SCIPY_WARNED:
            _SCIPY_WARNED.append(True)
            logging.warning('scipy를 불러오지 못해 AI 분석용 리포트에 p값을 적지 않습니다')
        return None
    if f_stat is None or d1 <= 0 or d2 <= 0:
        return None
    return float(_F_DIST.sf(f_stat, d1, d2))


def _decompose(a: _Analysis, axis):
    """편차 행렬의 분산 분해.

    모든 Die×Lot 칸에 값이 있으면 2원(Die + Lot + 잔여), 결측이 있으면 1원(Die + Die 안 잔여).
    """
    dev = a.devs[axis]
    dies, lots = dev['die_labels'], dev['repeat_labels']
    cells = a.cells(axis)
    n = len(cells)
    k, n_lots = len(dies), len(lots)
    if k < 2 or n <= k:
        return None
    grand = sum(v for _, _, v in cells) / n
    sst = sum((v - grand) ** 2 for _, _, v in cells)
    if sst <= 0:
        return None
    by_die, by_lot = {}, {}
    for lot, die, v in cells:
        by_die.setdefault(die, []).append(v)
        by_lot.setdefault(lot, []).append(v)
    ssd = sum(len(vs) * (sum(vs) / len(vs) - grand) ** 2 for vs in by_die.values())
    out = {'k': len(by_die), 'lots': n_lots, 'n': n, 'die_share': ssd / sst,
           'die_expect': (len(by_die) - 1) / (n - 1)}
    balanced = n_lots >= 2 and n == k * n_lots
    if balanced:
        ssl = sum(len(vs) * (sum(vs) / len(vs) - grand) ** 2 for vs in by_lot.values())
        ssr = max(sst - ssd - ssl, 0.0)
        dof = (k - 1) * (n_lots - 1)
        msr = ssr / dof if dof else 0.0
        # 분자 SS가 0이면(그 요인의 차이가 전혀 없으면) F를 정할 수 없다. 0/0을 ∞로 적지 않는다
        # (같은 값의 평균도 부동소수 합에서 1e-30쯤 남으므로 전체 SS 대비로 본다)
        tiny = sst * 1e-12
        f_die = None if ssd <= tiny else ((ssd / (k - 1)) / msr if msr > 0 else math.inf)
        f_lot = None if ssl <= tiny else ((ssl / (n_lots - 1)) / msr if msr > 0 else math.inf)
        out.update(mode='2원', lot_share=ssl / sst, rest_share=ssr / sst,
                   die_f=f_die, die_p=_f_pvalue(f_die, k - 1, dof),
                   lot_f=f_lot, lot_p=_f_pvalue(f_lot, n_lots - 1, dof))
    else:
        ssw = sst - ssd
        dof = n - len(by_die)
        f_die = (ssd / (len(by_die) - 1)) / (ssw / dof) if ssw > 0 else math.inf
        out.update(mode='1원 (결측 칸이 있어 2원 분해 불가)', lot_share=None,
                   rest_share=ssw / sst, die_f=f_die,
                   die_p=_f_pvalue(f_die, len(by_die) - 1, dof), lot_f=None, lot_p=None)
    return out


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
    f_stat = (r2 / 2) / ((1 - r2) / (k - 3)) if r2 < 1 else math.inf
    return r2, 2 / (k - 1), adj, f_stat, _f_pvalue(f_stat, 2, k - 3), k


def _t_text(entry):
    t = entry['t']
    if t is None:
        return '—'
    if math.isinf(t):
        return '∞' if t > 0 else '−∞'
    return f'{t:+.1f}'


def _pattern_hint(a: _Analysis) -> str:
    """교차 항으로 회전형/직교도형을 가리는 경험 규칙 (파생). t가 충분할 때만 이름을 붙인다."""
    coef = a.coefficients()
    rule = (f'규칙: Die {HINT_MIN_DIES}개 이상이고, 직교도형은 |t(직교도)| ≥ {HINT_MIN_T:g} 이면서 '
            f'|직교도| ≥ |회전|, 회전형은 |t(회전)| ≥ {HINT_MIN_T:g} 이면서 |직교도| < 0.5×|회전|. '
            '둘 중 하나만 |t| 기준을 넘고 크기 조건이 맞지 않으면 혼합, 둘 다 못 넘으면 구분 안 됨')
    if coef is None or coef['k'] < HINT_MIN_DIES:
        kind = (f"Die {coef['k'] if coef else a.af.get('n_dies', 0)}개라 통계적으로 구분 안 됨 "
                f'(Die {HINT_MIN_DIES}개 이상 필요)')
    else:
        shear, rot = coef['shear'], coef['rot']
        t_shear = abs(shear['t'] or 0.0)
        t_rot = abs(rot['t'] or 0.0)
        if t_shear >= HINT_MIN_T and abs(shear['value']) >= abs(rot['value']):
            tx, ty = abs(coef['tx']['t'] or 0.0), abs(coef['ty']['t'] or 0.0)
            axis_text = ('θx 우세: Y 이동 시 X 오차' if tx >= ty else 'θy 우세: X 이동 시 Y 오차')
            kind = f'직교도(직각도)형 또는 한 축 이동에 따른 다른 축 오차 — {axis_text}'
        elif t_rot >= HINT_MIN_T and abs(shear['value']) < 0.5 * abs(rot['value']):
            kind = '회전형 (θx ≈ −θy)'
        elif t_shear >= HINT_MIN_T or t_rot >= HINT_MIN_T:
            kind = '회전과 직교도가 섞인 형태'
        else:
            kind = '통계적으로 구분 안 됨 (교차 항의 |t|가 기준 미만)'
    return f'- 해석 힌트 {DERIVED}: {kind}. {rule}.'


_COEF_ROWS = (
    ('sx', 'Sx = ∂dx/∂x', 'ppm', 'X 이동 시 X 배율 오차'),
    ('tx', 'θx = ∂dx/∂y', 'µrad', 'Y 이동 시 X 오차'),
    ('ty', 'θy = ∂dy/∂x', 'µrad', 'X 이동 시 Y 오차'),
    ('sy', 'Sy = ∂dy/∂y', 'ppm', 'Y 이동 시 Y 배율 오차'),
    ('rot', '회전 = (θy − θx)/2', 'µrad', '모델 dx=−θ·y, dy=+θ·x 기준 +. 화면의 Rotation θ와 같은 값'),
    ('shear', '직교도 = θx + θy', 'µrad', '비직각. 순수 회전이면 ≈ 0, 한 축 교차 항만 있으면 그 항과 같음'),
)


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
    coef = a.coefficients()
    rows = []
    for key, label, unit, meaning in _COEF_ROWS:
        if coef:
            e = coef[key]
            rows.append([label, f"{_zero(e['value'], 2):+.2f}", unit, f"{e['se']:.2f}",
                         _t_text(e), meaning])
        else:
            rows.append([label, '—', unit, '—', '—', meaning])
    lines += [f"평면 계수 {DERIVED} (Die {af['n_dies']}개). SE = sqrt(s²·diag((AᵀA)⁻¹)), "
              's² = 잔차 제곱합/(k−3), t = 값/SE. 회전·직교도의 SE는 두 축 피팅을 독립으로 보고 합친 값.',
              _table(['항', '값', '단위', 'SE', f't {DERIVED}', '의미'], rows),
              f"- 평면 잔차 RMS (Die 평균 기준): X {af['residual_x']:.4f} µm, Y {af['residual_y']:.4f} µm",
              _pattern_hint(a)]
    for axis in AXES:
        for cross_only, title in ((False, '계통 성분(평면) 제거 후'),
                                  (True, '교차 항(회전·직교도)만 제거 후 (Sx·Sy 유지)')):
            rng, std, missing = a.whatif_plane(axis, cross_only)
            if rng is None:
                continue
            verdict = _would_pass(rng, std, spec_range, spec_stddev)
            change = ('' if verdict is None or verdicts.get(axis) is None
                      else f" (현재 {result_text(verdicts[axis])} → {result_text(verdict)})")
            extra = f', 평면 피팅에서 빠진 Die {missing}개는 그대로' if missing else ''
            lines.append(f'- {axis} {title} (what-if 추정, 같은 데이터로 피팅한 값이라 낙관적) {DERIVED}: '
                         f'Range {rng:.3f} µm, StdDev {std:.3f} µm{change}{extra}')

    decomp_rows = []
    plane_rows = []
    for axis in AXES:
        d = _decompose(a, axis)
        p = _plane_r2(a, axis)
        if d:
            decomp_rows.append([
                axis, d['mode'], _num(d['die_share'] * 100, '.1f'),
                _num(None if d['lot_share'] is None else d['lot_share'] * 100, '.1f'),
                _num(d['rest_share'] * 100, '.1f'), _num(d['die_expect'] * 100, '.1f'),
                f"{_f_text(d['die_f'])} ({_pvalue_text(d['die_p'])})",
                '—' if d['lot_f'] is None else f"{_f_text(d['lot_f'])} ({_pvalue_text(d['lot_p'])})"])
        elif len(a.devs[axis]['repeat_labels']) == 1:
            # Lot 1개: Die마다 칸이 하나라 Die 안 변동이 없다. Lot 항도 없다 (분해하지 않는다)
            decomp_rows.append([axis, f'{SINGLE_LOT_TEXT} (Die마다 값 1개)'] + ['—'] * 6)
        else:
            decomp_rows.append([axis, '—'] + ['—'] * 6)
        plane_rows.append([
            axis,
            _num(p[0] * 100, '.1f') if p else '—', _num(p[1] * 100, '.1f') if p else '—',
            _num(p[2] * 100, '.1f') if p else '—',
            f'{_f_text(p[3])} ({_pvalue_text(p[4])})' if p else '—',
            _num(d['die_share'] * p[0] * 100, '.1f') if d and p else '—'])
    lines += ['',
              f'분산 분해 {DERIVED}. 편차 행렬(Die × Lot)의 제곱합을 Die 간, Lot 간, Die×Lot 잔여'
              '(랜덤 + 상호작용)로 나눈다(2원). 결측 칸이 있으면 1원(Die 간 + Die 안 잔여)으로 대신한다. '
              'Die 잡음 기대 ≈ (k−1)/(N−1)은 차이가 없을 때 Die 간 비율의 기대값. '
              'F는 2원이면 잔여(자유도 (k−1)(L−1))에 대해, 1원이면 Die 안 잔여에 대해 검정한다. '
              'F·p는 측정 간 독립을 가정한 참고값이다.'
              + ('' if _F_DIST is not None else ' p값 없음: scipy를 불러오지 못했다.'),
              _table(['축', '분해', 'Die 간 (%)', 'Lot 간 (%)', 'Die×Lot 잔여 (%)', 'Die 잡음 기대 (%)',
                      'Die F (p)', 'Lot F (p)'], decomp_rows),
              f'평면 피팅 {DERIVED}. 평면 R² = Die 평균 분산 중 평면이 설명하는 비율, 잡음만일 때 기대 '
              'R² ≈ 2/(k−1). 전체 중 계통 ≈ Die 간 비율 × 평면 R².',
              _table(['축', '평면 R² (%)', '잡음 기대 R² (%)', '수정 R² (%)', '평면 F (p)',
                      '전체 중 계통 ≈ (%)'], plane_rows)]
    return lines


def _f_text(f_stat):
    if f_stat is None:
        return '—'
    return '∞' if math.isinf(f_stat) else f'{f_stat:.1f}'


def _pvalue_text(p):
    if p is None:
        return 'p 없음'
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
            row += [_num(_zero(mean, 3), '.3f', True), _num(std)]
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
        if len(pts) == 1:
            lines.append(f'- {axis} drift·Lot 교대 패턴 {DERIVED}: {SINGLE_LOT_TEXT}')
        elif slope is not None:
            lines.append(f'- {axis} drift {DERIVED}: Lot 평균 편차를 Lot 순서에 최소제곱 직선으로 맞춘 '
                         f'기울기 {_zero(slope, 4):+.4f} µm/Lot (R² {r2:.2f}), '
                         f'마지막 Lot − 첫 Lot = {_zero(pts[-1][1] - pts[0][1], 3):+.3f} µm')
        means = [p[1] for p in pts]
        if len(means) >= 4:
            odd = [m for i, m in enumerate(means) if i % 2 == 0]     # 1, 3, 5번째
            even = [m for i, m in enumerate(means) if i % 2 == 1]
            diff = sum(odd) / len(odd) - sum(even) / len(even)
            lines.append(f'- {axis} Lot 교대 패턴 {DERIVED}: 홀수 번째 − 짝수 번째 Lot 평균 = '
                         f'{_zero(diff, 3):+.3f} µm, Lot 평균의 lag-1 자기상관 '
                         f'{_lag1(means):+.2f} (−1에 가까우면 교대, +1에 가까우면 느린 변화)')
    return lines


def _lag1(values):
    mean = sum(values) / len(values)
    den = sum((v - mean) ** 2 for v in values)
    if not den:
        return 0.0
    return _zero(sum((a - mean) * (b - mean) for a, b in zip(values, values[1:])) / den, 2)


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
                         f"{' (판정이 바뀜)' if verdict is True else ''}")
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
            detail = r.get('no_rows_detail')
            lines.append('- 분석할 데이터가 없음' + (f' ({detail})' if detail else ''))
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
                         f"{cpk_text(x['cpk'], x['reference'])} / {cpk_text(y['cpk'], y['reference'])}",
                         result_text(run['result'], run.get('reference')), _first_repeat_text(source)])
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


def _appendix(results, raw_csv=None) -> str:
    raw_csv = raw_csv or {}
    if raw_csv.get('error'):
        csv_text = (f"모든 회차의 셀 값을 담을 CSV `{raw_csv.get('name', '')}`는 쓰지 못했다 "
                    f"({raw_csv['error']}).")
    elif raw_csv.get('name'):
        csv_text = (f"모든 Workflow·회차의 셀 값은 같이 저장한 CSV `{raw_csv['name']}`에 있다 "
                    '(긴 형식: ' + ', '.join(RAW_CSV_COLUMNS) + '). deviation_um은 행마다 '
                    '(측정값 − 그 회차·축의 분석 대상 평균)/1000이다. 같은 Lot·Die에 행이 둘 이상이면 '
                    '이 표에 쓴 행(마지막 행) 말고는 flags에 dup이 붙는다. 필요하면 LLM에 함께 첨부한다.')
    else:
        csv_text = '다른 회차의 셀 값은 이 파일에 없다.'
    lines = ['## 부록: Die×Repeat 편차 표 (선택 회차)',
             '각 Workflow의 선택 회차만 싣는다. ' + csv_text,
             '단위 µm, 소수 셋째 자리(nm 단위) 반올림. 편차 = 측정값 − 그 회차·축의 분석 대상 평균. '
             '`*` 이상치(IQR × 1.5, 판정에 포함), `NaN` 측정 실패로 값 없음, `(제외)` 안정화 Die, '
             '`—` 값 없음. 첫 리핏으로 제외한 폴더는 표에 없다. 열은 측정 순서.']
    for r in results:
        raw = r.get('raw_data', [])
        lines += ['', f"### {_name(r)} — 회차 {r.get('round', '')}"]
        if not raw:
            lines.append(f"- 데이터 없음{(' (' + r['error'] + ')') if r.get('error') else ''}")
            continue
        for axis in AXES:
            lines += ['', f'#### {axis}', _appendix_table(raw, axis)]
    return '\n'.join(lines).rstrip()


def build_raw_csv(recipe_results: list) -> str:
    """모든 Workflow·회차의 측정 행을 긴 형식 CSV 문자열로 (BOM 없음. 쓰는 쪽이 utf-8-sig로 쓴다).

    deviation_um은 행마다 (측정값 − 그 회차·축의 분석 대상 평균)/1000 (0.1 nm까지).
    부록 표의 칸은 같은 Lot·Die의 마지막 행이라, 칸에 쓴 행은 표와 같은 값이다.
    flags: excluded(안정화 Die), failed(측정 실패), outlier(IQR × 1.5, 판정에 포함),
    unreadable(Site ID), dup(같은 Lot·Die의 다른 행 — 표·판정 칸에 쓰지 않은 행).
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(RAW_CSV_COLUMNS)
    for r in recipe_results or []:
        for run in _runs_of(r) or [r]:
            raw = run.get('raw_data', [])
            if not raw:
                continue
            excluded = stabilization_die(raw)
            analysis = filter_stabilization_die(raw)
            averages = {axis: compute_deviation_matrix(analysis, axis)['average_nm']
                        for axis in AXES}
            # 편차 행렬 칸에 들어간 행: 같은 Lot·Die에 행이 여럿이면 마지막 행 (compute_deviation_matrix와 같다)
            cell_rows = {}
            for row in analysis:
                axis = (row.get('method') or '').upper()
                die = extract_die_number(row.get('site_id', ''))
                if axis in AXES and die is not None and is_usable(row):
                    cell_rows[(axis, row.get('lot_name', ''), die)] = id(row)

            def key(row):
                die = extract_die_number(row.get('site_id', ''))
                return (AXES.index(row['method'].upper()) if row.get('method', '').upper() in AXES else 9,
                        row.get('lot_index', 0), row.get('lot_name', ''),
                        -1 if die is None else die, row.get('point_no', 0))
            for row in sorted(raw, key=key):
                axis = (row.get('method') or '').upper()
                if axis not in AXES:
                    continue
                die = extract_die_number(row.get('site_id', ''))
                flags = []
                if die is None:
                    flags.append('unreadable')
                elif die == excluded:
                    flags.append('excluded')
                if not is_usable(row):
                    flags.append('failed')
                elif row.get('is_outlier'):
                    flags.append('outlier')
                value = None
                if die is not None and die != excluded and is_usable(row):
                    # 행렬 칸과 같은 계산 (nm → µm, 6자리 반올림)
                    value = round((row['value'] - averages[axis]) / 1000.0, 6)
                    if cell_rows.get((axis, row.get('lot_name', ''), die)) != id(row):
                        flags.append('dup')
                x_um, y_um = row.get('x_um'), row.get('y_um')
                writer.writerow([
                    _name(r), run.get('round', ''), row.get('lot_index', ''), row.get('lot_name', ''),
                    '' if die is None else f'Die{die + 1}',
                    f'{x_um / 1000:.3f}' if isinstance(x_um, (int, float)) and math.isfinite(x_um) else '',
                    f'{y_um / 1000:.3f}' if isinstance(y_um, (int, float)) and math.isfinite(y_um) else '',
                    axis, '' if value is None else f'{value:.4f}', '|'.join(flags)])
    return out.getvalue()


# ──────────────────────────────────────────────
# 8. LLM 분석 요청
# ──────────────────────────────────────────────

def _request(results, spec_limits, spec_deviation) -> str:
    any_fail = any(_metrics(r, spec_limits, spec_deviation)[axis]['result'] is False
                   for r in results for axis in AXES)
    multi_run = any(len(_runs_of(r)) >= 2 for r in results)
    reference = [_name(r) for r in results
                 if any(m['reference'] for m in _metrics(r, spec_limits, spec_deviation).values())]
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
             '- 원인 후보는 아래 목록을 하나씩 확인하고, 리포트의 숫자가 뒷받침하는 것만 받아들이세요. '
             '각 후보마다 지지/반박/판단 불가와 근거 숫자를 적어 주세요.',
             '  - 병진 (같은 Workflow의 회차 간 Avg 변화)',
             '  - Scaling (Sx, Sy)',
             '  - 회전 ((θy − θx)/2)',
             '  - 직교도 (θx + θy)',
             '  - Die 고유 편차 (평면 잔차가 큰 Die)',
             '  - drift / warm-up (Lot 순서에 따른 변화)',
             '  - Lot 교대·주기 패턴 (Lot 간 비율, 홀짝 차이, lag-1 자기상관)',
             '  - 측정·비전 잡음 (Die×Lot 잔여, Die 안 StdDev)',
             '- 이상치(*)는 Range·StdDev·판정에 포함되어 있습니다.']
    if reference:
        lines.append(f"- {', '.join(reference)}: 판정이 '{REFERENCE_LABEL}'입니다. Repeat이 1개라 "
                     'Die 간 편차로만 판정했고 반복성·drift·Lot 간 변동은 알 수 없습니다. '
                     '이 Workflow의 반복성에 대해서는 결론 내지 마세요.')
    if multi_run:
        lines.append('- "회차 변경 이력" 표가 비어 있으면 회차 간 차이의 원인(무엇을 바꿨는지)은 추정하지 말고, '
                     '숫자가 어떻게 바뀌었는지만 적으세요.')
    lines += ['', '리포트에 없는 장비 사양, 부품, 이력은 지어내지 마세요. 필요하면 "추가 정보 필요"로 표시해 주세요.']
    return '\n'.join(lines)


def build_llm_report(recipe_results: list, spec_limits: dict = None,
                     spec_deviation: dict = None, *, app_version: str = '',
                     exported_at: str = '', source_root: str = '', raw_csv: dict = None) -> str:
    """LLM 원인 분석용 Markdown 한 파일 (UTF-8 문자열).

    recipe_results: 화면의 Recipe 결과 목록 (선택 회차 결과, 회차가 여럿이면 run_results 포함)
    raw_csv: 같이 쓴 셀 값 CSV {'name': 파일 이름} 또는 실패 시 {'name':…, 'error': 이유}
    """
    results = list(recipe_results or [])
    sections = [
        _front_matter(results, app_version, exported_at, source_root, raw_csv),
        '# XY Stage 위치 오프셋 — AI 분석용 리포트',
        _conditions(results, spec_limits, spec_deviation),
        _summary(results, spec_limits, spec_deviation),
        _clues(results, spec_limits, spec_deviation),
        _run_comparison(results, spec_limits, spec_deviation),
        _change_log(results),
        _appendix(results, raw_csv),
        _request(results, spec_limits, spec_deviation),
    ]
    return '\n\n'.join(s.strip('\n') for s in sections if s) + '\n'
