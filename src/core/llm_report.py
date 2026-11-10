"""
llm_report.py — LLM(대형 언어 모델) 원인 분석용 Markdown 리포트

앱 화면은 사람이 직관적으로 보는 용도이고, 'Spec이 왜 나갔는지' 원인 추론은 이
리포트를 LLM에 붙여 넣어 맡긴다. 형식은 다른 PASS 도구에서도 다시 쓸 수 있게
고정 스키마(report_schema)로 두고, Qt 없이 결과 dict만으로 만든다.

계산은 새로 만들지 않고 화면·Excel과 같은 함수(compute_axis_metrics,
compute_deviation_matrix, compute_affine_transform, summarize_runs)를 쓴다.
같은 입력이면 같은 문자열이 나온다(시각은 인자로 받는다).
"""

import json
import math

from core.checklist import (EXPECTED_DIES, EXPECTED_REPEATS, analysis_warnings,
                            compute_axis_metrics, result_text, summarize_runs)
from core.die_analysis import (compute_affine_transform, compute_deviation_matrix,
                               extract_die_number, extract_die_positions,
                               filter_stabilization_die, stabilization_die)
from core.statistics import filter_by_method, is_usable

REPORT_SCHEMA = 'xy-stage-llm-report/1'
TOOL_NAME = 'XY Stage Positioning Offset Analyzer'
TOP_N = 3
EDGE_RATIO = 2 / 3      # 중심에서 가장 먼 Die 거리의 이 비율 이상이면 Edge
MAX_OUTLIER_LINES = 10
AXES = ('X', 'Y')


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
    return f'{diff:+.3f} ({pct:.1f}%)'


def _runs_of(result) -> list:
    return result.get('run_results') or []


def _metrics(result, spec_limits, spec_deviation) -> dict:
    data = filter_stabilization_die(result.get('raw_data', []))
    return {axis: compute_axis_metrics(data, axis, _name(result), spec_limits, spec_deviation)
            for axis in AXES}


def _verdict(metrics) -> object:
    results = [metrics[a]['result'] for a in AXES]
    if any(r is False for r in results):
        return False
    if any(r is None for r in results):
        return None
    return True


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
        lines += [f'  - name: {_q(_name(r))}',
                  f'    path: {_q(r.get("source_path") or r.get("round_path", ""))}',
                  f'    selected_run: {_q(r.get("round", ""))}',
                  f'    run_count: {max(len(_runs_of(r)), 1)}']
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
    basis = []
    for r in results:
        name = _name(r)
        if r.get('first_repeat_excluded'):
            fired = f"적용 — 폴더 {r['first_repeat_excluded']} 제외"
        elif r.get('first_repeat_unverified'):
            fired = '미적용 — 첫 폴더가 Repeat No 1로 확인되지 않음'
        else:
            fired = '미적용 — 폴더 10개 이하'
        basis.append(f'- {name} (선택 회차 {r.get("round", "")}): 첫 리핏 제외 {fired}')
    return '\n'.join([
        '## 분석 조건',
        '',
        '### Spec',
        _table(['Workflow', 'spec_range (µm)', 'spec_stddev (µm)',
                'X LSL (nm)', 'X USL (nm)', 'Y LSL (nm)', 'Y USL (nm)'], rows),
        '',
        '### 분석 기준',
        f'- 기준 크기: {EXPECTED_DIES} Die × {EXPECTED_REPEATS} Repeat. 측정은 22 Die이며 '
        '안정화 Die(Site 0001, Die1)는 판정·통계에서 제외한다.',
        '- 첫 리핏 제외: Lot 폴더가 11개 이상이고 측정 순서상 첫 폴더의 Repeat No가 1이면 '
        '그 폴더를 분석에서 뺀다.',
        *basis,
        '- 이상치: 축(X, Y)별로 IQR × 1.5 경계를 넘는 측정. 표시·건수용이며 판정에는 쓰지 않는다.',
        '- 측정 실패: Valid=FALSE 이거나 값이 숫자가 아닌 행. 통계·판정에서 제외한다.',
        '- 단위: 측정값·Avg는 nm, 편차·Range·StdDev는 µm.',
        '',
        '### 용어',
        '- X/Y 편차: 각 측정값(HZ1_O, nm)에서 그 축의 분석 대상 전체 평균을 뺀 값을 µm로 '
        '바꾼 것. 0이면 평균 위치, +/−는 방향.',
        '- Range: 분석 대상 편차의 최댓값 − 최솟값 (µm).',
        '- StdDev: 분석 대상 편차의 표본 표준편차 (n−1, µm).',
        '- 판정: 축마다 Range ≤ spec_range 이고 StdDev ≤ spec_stddev 이면 PASS. '
        'X, Y 중 하나라도 FAIL이면 Workflow FAIL. Spec이 없거나 2 Die × 2 Repeat 미만이면 —.',
        '- Cpk: 편차 기준(평균 0)으로 min(|LSL|, USL) / (3σ), σ는 측정값(nm)의 표준편차. '
        '판정이 —이면 Cpk도 —.',
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
                    exceeded.append(f"- {_name(r)} {axis} {label}: {m[key]:.3f} µm "
                                    f"(Spec {m[f'spec_{key}']:.3f} µm, {diff:+.3f} µm, {pct:.1f}%)")
    rows.sort(key=lambda row: row[:3])
    lines = ['## 결과 요약', '',
             '### Spec 초과 항목',
             *(exceeded or ['- 없음']),
             '',
             '### Workflow·축별 결과 (Spec 초과 먼저)',
             '여유 = 값 − Spec (µm), 괄호는 Spec 대비 %. +이면 Spec 초과.',
             '',
             _table(['Workflow', '회차', '축', '판정', 'Range (µm)', 'Spec Range', 'Range 여유',
                     'StdDev (µm)', 'Spec StdDev', 'StdDev 여유', 'Avg (nm)', 'Cpk',
                     'Die × Repeat', '측정 수', '경고 수'], [row[3] for row in rows])]
    for r in results:
        warns = analysis_warnings(r)
        if warns:
            lines += ['', f'#### {_name(r)} 경고', *(f'- {w}' for w in warns)]
    return '\n'.join(lines)


# ──────────────────────────────────────────────
# 4. 원인 단서
# ──────────────────────────────────────────────

def _matrix_cells(dev):
    return [(lot, die, v) for lot, row in dev['matrix'].items()
            for die, v in row.items() if v is not None]


def _variance_parts(dev):
    """(전체 SS, Die 간 SS). 편차 행렬에서 바로 구한다."""
    cells = _matrix_cells(dev)
    if len(cells) < 2:
        return None, None
    grand = sum(v for _, _, v in cells) / len(cells)
    total = sum((v - grand) ** 2 for _, _, v in cells)
    by_die = {}
    for _, die, v in cells:
        by_die.setdefault(die, []).append(v)
    between = sum(len(vs) * (sum(vs) / len(vs) - grand) ** 2 for vs in by_die.values())
    return total, between


def _systematic(data, devs, positions) -> list:
    af = compute_affine_transform(devs['X']['die_stats'], devs['Y']['die_stats'], positions)
    lines = ['#### 계통(Systematic) vs 랜덤',
             '- Affine 모델: dx = Tx + Sx·x − θ·y, dy = Ty + Sy·y + θ·x (Die 평균 편차를 '
             '실측 Die 좌표로 피팅). 입력이 평균을 뺀 편차라 Tx, Ty(병진)는 0에 가깝고 '
             '병진은 결과 요약의 Avg로 본다.']
    if af.get('degenerate'):
        lines.append(f"- 피팅 불가: 유효 Die {af.get('n_dies', 0)}개 또는 좌표가 공선. "
                     'Scaling/Rotation을 판단할 수 없음.')
        return lines
    lines.append(f"- Scaling Sx {af['sx_ppm']:+.2f} ppm, Sy {af['sy_ppm']:+.2f} ppm, "
                 f"Rotation θ {af['theta_urad']:+.2f} µrad, "
                 f"Residual RMS X {af['residual_x']:.4f} µm, Y {af['residual_y']:.4f} µm "
                 f"(Die {af['n_dies']}개)")
    rows = []
    for axis, residual in (('X', af['residual_x']), ('Y', af['residual_y'])):
        dev = devs[axis]
        total, between = _variance_parts(dev)
        avgs = [d['avg'] for d in dev['die_stats']]
        between_share = between / total * 100 if total else None
        affine_share = None
        if len(avgs) == af['n_dies'] and len(avgs) > 1:
            mean_avg = sum(avgs) / len(avgs)
            spread = sum((a - mean_avg) ** 2 for a in avgs)
            if spread:
                affine_share = max(0.0, 1 - len(avgs) * residual ** 2 / spread) * 100
        overall = (between_share * affine_share / 100
                   if between_share is not None and affine_share is not None else None)
        rows.append([axis, _num(between_share, '.1f'), _num(affine_share, '.1f'),
                     _num(overall, '.1f')])
    lines += ['',
              '분산 분해 (편차 행렬 기준, %). Die 간 = Σ n·(Die 평균 − 전체 평균)² / 전체 SS. '
              'Affine 설명 = 1 − n·Residual RMS² / Σ(Die 평균 − 평균)². '
              '전체 중 계통 ≈ Die 간 × Affine 설명. 나머지는 Die 안(Repeat 간) 랜덤 성분이다.',
              '',
              _table(['축', 'Die 간 비율 (%)', 'Die 평균 중 Affine 설명 (%)',
                      '전체 중 Affine 계통 ≈ (%)'], rows)]
    return lines


def _spatial(devs, positions) -> list:
    coords = {f'Die{i + 1}': p for i, p in positions.items()}
    dies = [d['die'] for d in devs['X']['die_stats']] or [d['die'] for d in devs['Y']['die_stats']]
    stats = {axis: {d['die']: d for d in devs[axis]['die_stats']} for axis in AXES}
    zone = {}
    known = [coords[d] for d in dies if d in coords]
    rule = 'Die 좌표가 없어 Edge/Center를 나누지 않음'
    if len(known) >= 3:
        cx = sum(p[0] for p in known) / len(known)
        cy = sum(p[1] for p in known) / len(known)
        dist = {d: math.hypot(coords[d][0] - cx, coords[d][1] - cy) for d in dies if d in coords}
        far = max(dist.values())
        zone = {d: ('Edge' if far and r >= far * EDGE_RATIO else 'Center') for d, r in dist.items()}
        rule = (f'Edge = 분석 Die 좌표 중심({cx:.1f}, {cy:.1f} mm)에서 가장 먼 Die 거리의 '
                f'{EDGE_RATIO:.0%} 이상, 나머지 Center')
    rows = []
    for d in sorted(dies, key=lambda x: int(x[3:])):
        x, y = coords.get(d, (None, None))
        row = [d, _num(x, '.1f'), _num(y, '.1f'), zone.get(d, '—')]
        for axis in AXES:
            s = stats[axis].get(d)
            row += [_num(s['avg'], '.3f', True), _num(s['stddev']), _num(s['range'])] if s else ['—'] * 3
        rows.append(row)
    lines = ['#### 공간(Die)',
             _table(['Die', 'X (mm)', 'Y (mm)', '구역', 'X 평균 편차', 'X StdDev', 'X Range',
                     'Y 평균 편차', 'Y StdDev', 'Y Range'], rows),
             '단위 µm. 구역 규칙: ' + rule + '.', '']
    for axis in AXES:
        dev = devs[axis]
        cells = _matrix_cells(dev)
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
            lines.append(f'- {axis} StdDev 기여 상위 {TOP_N} Die (편차 제곱합 비율): '
                         + ', '.join(f'{d} {v / total * 100:.1f}%' for d, v in top))
        if zone:
            parts = []
            for z in ('Center', 'Edge'):
                members = [stats[axis][d] for d in stats[axis] if zone.get(d) == z]
                if members:
                    parts.append(f'{z} {len(members)}개: 평균 |편차| '
                                 f'{sum(abs(m["avg"]) for m in members) / len(members):.3f} µm, '
                                 f'평균 StdDev {sum(m["stddev"] for m in members) / len(members):.3f} µm')
            lines.append(f'- {axis} Edge/Center: ' + ' / '.join(parts))
    return lines


def _slope(values):
    n = len(values)
    if n < 2:
        return None
    xs = range(1, n + 1)
    mx = (n + 1) / 2
    my = sum(values) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (v - my) for x, v in zip(xs, values)) / den


def _temporal(devs) -> list:
    lots = devs['X']['repeat_labels'] or devs['Y']['repeat_labels']
    col = {}
    for axis in AXES:
        matrix = devs[axis]['matrix']
        for lot in devs[axis]['repeat_labels']:
            vals = [v for v in matrix[lot].values() if v is not None]
            mean = sum(vals) / len(vals) if vals else None
            std = (math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
                   if len(vals) > 1 else None)
            col[(axis, lot)] = (mean, std)
    rows = []
    for i, lot in enumerate(lots, 1):
        row = [i, lot]
        for axis in AXES:
            mean, std = col.get((axis, lot), (None, None))
            row += [_num(mean, '.3f', True), _num(std)]
        rows.append(row)
    lines = ['#### 시간(Repeat 순서)',
             'Lot 순서는 측정 순서(Start Time → Repeat No → 폴더 이름). '
             'Start Time 값은 이 리포트에 없다.',
             _table(['순서', 'Lot', 'X 평균 편차', 'X Die 간 StdDev',
                     'Y 평균 편차', 'Y Die 간 StdDev'], rows)]
    for axis in AXES:
        means = [col[(axis, lot)][0] for lot in devs[axis]['repeat_labels']
                 if col[(axis, lot)][0] is not None]
        slope = _slope(means)
        if slope is not None:
            lines.append(f'- {axis} drift 기울기(Lot 평균 편차의 최소제곱 직선): {slope:+.4f} µm/Lot, '
                         f'첫 Lot − 마지막 Lot = {means[0] - means[-1]:+.3f} µm')
    return lines


def _quality(result) -> list:
    raw = result.get('raw_data', [])
    data = filter_stabilization_die(raw)
    lines = ['#### 이상치·측정 실패·제외']
    for axis in AXES:
        rows = [r for r in filter_by_method(data, axis) if r.get('is_outlier')]
        lines.append(f'- {axis} 이상치 {len(rows)}건 (IQR × 1.5, 판정에는 미사용)')
        for r in sorted(rows, key=lambda r: (r.get('lot_index', 0), r.get('site_id', '')))[:MAX_OUTLIER_LINES]:
            lines.append(f"  - {r.get('lot_name', '')} / Die{(extract_die_number(r.get('site_id', '')) or 0) + 1}"
                         f" ({r.get('site_id', '')}): {r.get('value')} nm")
        if len(rows) > MAX_OUTLIER_LINES:
            lines.append(f'  - … 외 {len(rows) - MAX_OUTLIER_LINES}건')
    failed = result.get('failed_measurements') or {}
    lines.append(f"- 측정 실패: {failed['text'] if failed.get('count') else '없음'}")
    if result.get('first_repeat_excluded'):
        lines.append(f"- 첫 리핏으로 제외한 폴더: {result['first_repeat_excluded']}")
    if result.get('no_measurement_folders'):
        lines.append(f"- X/Y 측정 CSV가 없어 건너뛴 폴더: {', '.join(result['no_measurement_folders'])}")
    if result.get('channel_dropped'):
        lines.append(f"- Z Drive 채널 {result['channel_dropped']}행 제외 (위치 오프셋은 Height 채널)")
    excluded = stabilization_die(raw)
    if excluded is not None:
        lines.append(f'- 안정화 Die{excluded + 1} 행 {len(raw) - len(data)}개 제외')
    for msg in analysis_warnings(result):
        lines.append(f'- 경고: {msg}')
    return lines


def _clues(results) -> str:
    lines = ['## 원인 단서', '각 Workflow의 선택 회차 기준. 편차 단위 µm.']
    for r in results:
        data = filter_stabilization_die(r.get('raw_data', []))
        lines += ['', f"### {_name(r)} (회차 {r.get('round', '')})"]
        if not data:
            lines.append('- 분석할 데이터가 없음')
            continue
        devs = {axis: compute_deviation_matrix(data, axis) for axis in AXES}
        positions = extract_die_positions(data)
        lines += _systematic(data, devs, positions) + [''] + _spatial(devs, positions) + [''] \
            + _temporal(devs) + [''] + _quality(r)
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
        rows, deltas, changes = [], [], []
        prev = None
        for run in runs:
            x, y = run['x'], run['y']
            rows.append([run['run'] + (' ⚠' if run['warnings'] else ''), run['lot_count'],
                         _num(x['range']), _num(x['stddev']), _num(x['avg'], '.1f'),
                         _num(y['range']), _num(y['stddev']), _num(y['avg'], '.1f'),
                         f"{_num(x['cpk'], '.2f')} / {_num(y['cpk'], '.2f')}",
                         result_text(run['result'])])
            if prev is not None:
                def d(axis, key, fmt='.3f'):
                    a, b = run[axis][key], prev[axis][key]
                    return '—' if a is None or b is None else format(a - b, '+' + fmt)
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
                           'Y StdDev', 'Y Avg (nm)', 'Cpk X/Y', '판정'], rows),
                   '', '이전 회차 대비 변화 (Range·StdDev µm, Avg nm):',
                   _table(['회차', 'ΔX Range', 'ΔX StdDev', 'ΔX Avg', 'ΔY Range', 'ΔY StdDev',
                           'ΔY Avg'], deltas),
                   '', f"- FAIL 회차: {', '.join(fails) if fails else '없음'}",
                   *(changes or ['- 판정이 바뀐 회차 없음'])]
    if not blocks:
        return ''
    return '\n'.join(['## 회차 비교', '회차별 값은 결과 요약과 같은 분석 기준. ⚠는 경고가 있는 회차.']
                     + blocks)


def _change_log(results) -> str:
    lines = ['## 회차 변경 이력 (사용자 기입)',
             '회차마다 바꾼 모듈·설정·조건을 적는다. 비워 두면 LLM은 회차 간 차이의 원인을 '
             '추정하지 않는다.', '',
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
    lots = sorted({r.get('lot_name', ''): r.get('lot_index', 0) for r in axis_rows}.items(),
                  key=lambda kv: (kv[1], kv[0]))
    lot_names = [name for name, _ in lots]
    flags = {}
    for r in axis_rows:
        die = extract_die_number(r.get('site_id', ''))
        if die is None:
            continue
        key = (r.get('lot_name', ''), die)
        if not is_usable(r):
            flags[key] = 'NaN'
        elif r.get('is_outlier') and key not in flags:
            flags[key] = '*'
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
            flag = flags.get((lot, die), '')
            if flag == 'NaN':
                cells.append('NaN')
            elif value is None:
                cells.append('—')
            else:
                cells.append(f'{value:+.3f}{flag}')
        rows.append([label] + cells)
    return _table(['Die'] + lot_names, rows)


def _appendix(results) -> str:
    lines = ['## 부록: Die×Repeat 편차 표',
             '단위 µm, 소수 셋째 자리(nm 단위) 반올림. 편차 = 측정값 − 그 회차·축의 분석 대상 평균. '
             '`*` 이상치(IQR × 1.5), `NaN` 측정 실패, `(제외)` 안정화 Die, `—` 값 없음. '
             '첫 리핏으로 제외한 폴더는 표에 없다.']
    for r in results:
        for run in _runs_of(r) or [r]:
            raw = run.get('raw_data', [])
            lines += ['', f"### {_name(r)} — 회차 {run.get('round', '')}"]
            if not raw:
                lines.append(f"- 데이터 없음{(' (' + run['error'] + ')') if run.get('error') else ''}")
                continue
            for axis in AXES:
                lines += ['', f'#### {axis}', _appendix_table(raw, axis)]
    return '\n'.join(lines)


LLM_REQUEST = '''## LLM 분석 요청
위 리포트는 XY Stage 위치 오프셋 측정의 분석 결과입니다. 이 리포트에 있는 숫자와 표만 근거로
다음을 한국어로 답해 주세요.

1. 어떤 Workflow·축의 어떤 Spec 항목(Range, StdDev)이 얼마나(µm, %) 초과했는지 정리해 주세요.
2. 원인을 다음 축으로 나눠 분석해 주세요.
   - 계통 성분(Scaling, Rotation, Offset) vs 랜덤 성분
   - 공간(특정 Die, Edge/Center) vs 시간(drift, warm-up, Repeat 순서)
   - 회차 간 변화 (회차 비교와 회차 변경 이력이 있으면)
3. 가능성이 높은 근본 원인을 순위로 제시하고, 각각 어느 표·숫자가 근거인지 적어 주세요.
4. 원인을 좁히기 위한 다음 확인 항목이나 실험을 제안해 주세요.
5. 이 데이터만으로는 결론 낼 수 없는 것을 분명히 적어 주세요.

리포트에 없는 장비 사양, 부품, 이력은 지어내지 마세요. 필요하면 "추가 정보 필요"로 표시해 주세요.'''


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
        _clues(results),
        _run_comparison(results, spec_limits, spec_deviation),
        _change_log(results),
        _appendix(results),
        LLM_REQUEST,
    ]
    return '\n\n'.join(s for s in sections if s) + '\n'
