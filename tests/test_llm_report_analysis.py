"""AI 분석용 리포트의 원인 단서 — 정답을 아는 합성 데이터로 고정한다.

실데이터(Vision Pattern 14회차)에서 X 오차가 Y 위치에 비례(θx ≈ 5 µrad, θy ≈ 0)했는데,
이전 리포트는 두 교차 항의 평균(회전 −2.5 µrad) 하나만 적어 LLM을 '회전'으로 이끌 수 있었다.
"""
import math
import re

import pytest
import yaml

from core.checklist import compute_axis_metrics, result_text, summarize_runs
from core.die_analysis import filter_stabilization_die
from core.llm_report import build_llm_report
from core.statistics import detect_outliers_by_axis

NAME = 'Vision Pattern'
SPEC_DEV = {NAME: {'spec_range': 1.0, 'spec_stddev': 0.2}}
SPEC_LIM = {NAME: {'X': {'lsl': -5000, 'usl': 5000}, 'Y': {'lsl': -5000, 'usl': 5000}}}
FIXED = dict(app_version='1.1.4', exported_at='2026-11-10 18:00:00', source_root='D:/data')

# 사용자 실데이터의 Die 좌표 (mm). Die1(안정화 Die)은 분석에서 빠진다
COORDS = {1: (-42.6, -79.9), 2: (-2.6, 0.1), 3: (37.4, 0.1), 4: (77.4, 0.1), 5: (117.4, 0.1),
          6: (37.4, 40.1), 7: (77.4, 80.1), 8: (-2.6, 40.1), 9: (-2.6, 80.1), 10: (-2.6, 120.1),
          11: (-42.6, 40.1), 12: (-82.6, 80.1), 13: (-42.6, 0.1), 14: (-82.6, 0.1),
          15: (-122.6, 0.1), 16: (-42.6, -39.9), 17: (-82.6, -79.9), 18: (-2.6, -39.9),
          19: (-2.6, -79.9), 20: (-2.6, -119.9), 21: (37.4, -39.9), 22: (77.4, -79.9)}


def noise(axis, die, lot, amp=0.03, seed=0):
    """결정적인 의사 잡음 (µm)."""
    return amp * math.sin(die * 12.9898 + lot * 78.233 + (axis == 'Y') * 3.1 + seed * 1.7)


def make_result(dev, lots=10, round_name='14', seed=0, dies=None, coords=None):
    """dev(axis, die, x_mm, y_mm, lot) → 편차(µm)로 한 회차 결과를 만든다."""
    rows = []
    coords = coords or COORDS
    for lot in range(1, lots + 1):
        for die, (x, y) in coords.items():
            if dies is not None and die not in dies:
                continue
            for axis in ('X', 'Y'):
                rows.append({'lot_name': f'Lot{lot:02d}', 'lot_index': lot,
                             'site_id': f'{die:04d}_X000_Y000', 'method': axis,
                             'value': 1000.0 + 1000.0 * dev(axis, die, x, y, lot),
                             'valid': True, 'is_outlier': False,
                             'x_um': x * 1000, 'y_um': y * 1000, 'filename': 'f.csv'})
    detect_outliers_by_axis(filter_stabilization_die(rows), method='iqr')
    return {'recipe': NAME, 'short_name': NAME, 'round': round_name, 'raw_data': rows,
            'load_errors': [], 'data_warnings': [], 'failed_measurements': {'count': 0}}


def with_runs(runs):
    main = dict(runs[-1])
    main.update(run_results=runs, default_run=runs[-1]['round'])
    return main


def orthogonality(axis, die, x, y, lot):
    """X 편차가 Y 위치에 비례(5.16 µrad), Die2 +0.25 µm, 잡음 0.03 µm."""
    if axis == 'X':
        return 0.00516 * y + (0.25 if die == 2 else 0) + noise(axis, die, lot)
    return noise(axis, die, lot, 0.06)


def report(results):
    return build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)


def coeff(md, label):
    """평면 계수 표의 값 열."""
    return float(re.search(r'\| ' + re.escape(label) + r' \| ([+-]\d+\.\d+) \|', md).group(1))


def hint(md):
    return next(l for l in md.splitlines() if l.startswith('- 해석 힌트'))


def test_orthogonality_found_and_named(capsys):
    md = report([make_result(orthogonality)])
    assert coeff(md, 'θx = ∂dx/∂y') == pytest.approx(5.16, abs=0.15)
    assert abs(coeff(md, 'θy = ∂dy/∂x')) < 0.3
    assert '직교도(직각도)형' in hint(md) and 'θx 우세: Y 이동 시 X 오차' in hint(md)
    assert '| θx = ∂dx/∂y | +5.' in md and 'Y 이동 시 X 오차 |' in md
    assert '회전형 (θx ≈ −θy)' not in md


def test_orthogonality_whatif_passes_after_plane_removal():
    result = make_result(orthogonality)
    md = report([result])
    m = compute_axis_metrics(filter_stabilization_die(result['raw_data']), 'X', NAME,
                             SPEC_LIM, SPEC_DEV)
    assert m['result'] is False
    line = next(l for l in md.splitlines() if l.startswith('- X 계통 성분(평면) 제거 후'))
    rng, std = map(float, re.search(r'Range (\d+\.\d+) µm, StdDev (\d+\.\d+) µm', line).groups())
    assert rng < 0.45 and std < 0.08               # 남는 것은 Die2 편향 + 잡음
    assert '(현재 FAIL → PASS)' in line and '(파생)' in line and '낙관적' in line
    cross = next(l for l in md.splitlines() if l.startswith('- X 교차 항(회전·직교도)만 제거 후'))
    assert '(현재 FAIL → PASS)' in cross


def test_pure_rotation_is_not_called_orthogonality():
    phi = 0.005     # 5 µrad (µm/mm)

    def rotation(axis, die, x, y, lot):
        return (-phi * y if axis == 'X' else phi * x) + noise(axis, die, lot, 0.01)

    md = report([make_result(rotation)])
    assert coeff(md, 'θx = ∂dx/∂y') == pytest.approx(-5.0, abs=0.1)
    assert coeff(md, 'θy = ∂dy/∂x') == pytest.approx(5.0, abs=0.1)
    assert coeff(md, '회전 = (θy − θx)/2') == pytest.approx(5.0, abs=0.1)
    assert '회전형 (θx ≈ −θy)' in md
    assert '직교도(직각도)형' not in md


def test_scaling_term():
    def scaling(axis, die, x, y, lot):
        return (0.003 * x if axis == 'X' else 0.0) + noise(axis, die, lot, 0.01)

    md = report([make_result(scaling)])
    assert coeff(md, 'Sx = ∂dx/∂x') == pytest.approx(3.0, abs=0.1)
    assert abs(coeff(md, 'Sy = ∂dy/∂y')) < 0.2


def test_single_die_bias_is_top_residual():
    md = report([make_result(orthogonality)])
    line = next(l for l in md.splitlines() if l.startswith('- X 평면 잔차 상위'))
    first = re.search(r': (Die\d+) ([+-]\d+\.\d+) µm', line)
    assert first.group(1) == 'Die2' and float(first.group(2)) == pytest.approx(0.25, abs=0.06)
    die2 = next(l for l in md.splitlines() if l.startswith('| Die2 |'))
    assert die2.split(' | ')[8] == first.group(2)          # Die 표의 X 평면 잔차 열


def test_linear_drift_sign_and_r2():
    def drift(axis, die, x, y, lot):
        return (0.01 * lot if axis == 'X' else 0.0) + noise(axis, die, lot, 0.005)

    md = report([make_result(drift)])
    line = next(l for l in md.splitlines() if l.startswith('- X drift'))
    slope, r2, delta = re.search(r'기울기 ([+-]\d+\.\d+) µm/Lot \(R² (\d\.\d+)\), '
                                 r'마지막 Lot − 첫 Lot = ([+-]\d+\.\d+)', line).groups()
    assert float(slope) == pytest.approx(0.01, abs=0.001)
    assert float(r2) > 0.95 and float(delta) == pytest.approx(0.09, abs=0.01)


def test_edge_center_is_symmetric_with_real_coordinates():
    md = report([make_result(orthogonality)])
    die_rows = [l.split(' | ') for l in md.splitlines()
                if l.startswith('| Die') and len(l.split(' | ')) == 13]
    zone = {cells[0][2:]: cells[4] for cells in die_rows}
    assert zone['Die9'] == zone['Die19'] == 'Edge'      # 둘 다 최대 반경의 정확히 2/3
    assert zone['Die12'] == zone['Die17'] == 'Edge'
    assert zone['Die8'] == zone['Die18'] == 'Center'
    assert '반경 (mm)' in md


def test_noise_floor_and_f_test_columns():
    md = report([make_result(orthogonality)])
    row = next(l for l in md.splitlines() if l.startswith('| X | 2원 |'))
    cells = row.strip('| ').split(' | ')
    assert float(cells[2]) > 95                      # Die 간
    assert float(cells[5]) == pytest.approx(20 / 209 * 100, abs=0.1)   # (k−1)/(N−1)
    assert cells[6].endswith('(p<1e-6)')
    assert '(p' in cells[7]                          # Lot F (p)


def test_summary_and_run_table_match_core_values():
    runs = [make_result(orthogonality, round_name=str(k), seed=k) for k in range(1, 4)]
    result = with_runs(runs)
    md = report([result])
    for axis in ('X', 'Y'):
        m = compute_axis_metrics(filter_stabilization_die(result['raw_data']), axis, NAME,
                                 SPEC_LIM, SPEC_DEV)
        row = next(l for l in md.splitlines() if l.startswith(f'| {NAME} | 3 | {axis} |'))
        cells = row.strip('| ').split(' | ')
        assert cells[3:5] == [result_text(m['result']), f"{m['range']:.3f}"]
        assert cells[7] == f"{m['stddev']:.3f}"
        assert cells[10:12] == [f"{m['avg']:.1f}", f"{m['cpk']:.2f}"]
    comparison = md[md.index('## 회차 비교'):md.index('## 회차 변경 이력')]
    for run in summarize_runs(result, SPEC_LIM, SPEC_DEV):
        row = next(l for l in comparison.splitlines() if l.startswith(f"| {run['run']} | 10 |"))
        cells = row.strip('| ').split(' | ')
        x, y = run['x'], run['y']
        assert cells[2:8] == [f"{x['range']:.3f}", f"{x['stddev']:.3f}", f"{x['avg']:.1f}",
                              f"{y['range']:.3f}", f"{y['stddev']:.3f}", f"{y['avg']:.1f}"]
        assert cells[8:10] == [f"{x['cpk']:.2f} / {y['cpk']:.2f}", result_text(run['result'])]


def test_run_comparison_has_systematic_terms_and_die_matrix():
    runs = [make_result(orthogonality, round_name=str(k), seed=k) for k in range(1, 4)]
    md = report([with_runs(runs)])
    comparison = md[md.index('## 회차 비교'):md.index('## 회차 변경 이력')]
    assert '| 회차 | Sx (ppm) | Sy (ppm) | θx (µrad) |' in comparison
    sys_rows = [l for l in comparison.splitlines() if re.match(r'\| [123] \| [+-]', l)]
    assert len(sys_rows) == 3 and all('| PASS |' in l for l in sys_rows)   # what-if 판정
    assert '회차 × Die 평균 편차 — X' in comparison
    die2 = next(l for l in comparison.splitlines() if l.startswith('| Die2 | +0.2'))
    assert len(die2.split(' | ')) == 1 + 3
    assert '첫 리핏 제외' in comparison and '미적용 (폴더 10개 이하)' in comparison


def test_outliers_are_stated_as_included_with_whatif():
    def spike(axis, die, x, y, lot):
        return (1.3 if (axis, die, lot) == ('X', 5, 3) else 0.0) + noise(axis, die, lot, 0.03)

    md = report([make_result(spike)])
    assert '이상치도 Range·StdDev·판정에 그대로 포함된다' in md
    line = next(l for l in md.splitlines() if '이상치 셀' in l and 'what-if' in l)
    assert '(판정이 바뀜)' in line and 'PASS' in line
    outlier = next(l for l in md.splitlines() if l.strip().startswith('- Lot03 / Die5'))
    assert re.search(r': \d+\.\d{3} nm, 편차 \+1\.2\d{2} µm', outlier)


def test_no_spec_out_asks_for_smallest_margin():
    def quiet(axis, die, x, y, lot):
        return noise(axis, die, lot, 0.02)

    md = report([make_result(quiet)])
    request = md[md.index('## LLM 분석 요청'):]
    assert 'Spec 초과는 없습니다' in request and '근본 원인을 순위로' not in request
    assert '(파생)로 표시한 값' in request


def test_spec_out_request_and_change_log_rule():
    runs = [make_result(orthogonality, round_name=str(k), seed=k) for k in range(1, 3)]
    md = report([with_runs(runs)])
    request = md[md.index('## LLM 분석 요청'):]
    assert '근본 원인을 순위로' in request
    assert '"회차 변경 이력" 표가 비어 있으면' in request
    assert '뒷받침하는 것만' in request
    for item in ('병진', 'Scaling', '회전', '직교도', 'Die 고유 편차', 'drift / warm-up',
                 'Lot 교대·주기 패턴', '측정·비전 잡음'):
        assert f'  - {item}' in request
    assert 'settling' not in request and '안정화 Die 다음' not in request
    assert 'Offset' not in request
    log = md[md.index('## 회차 변경 이력'):md.index('## 부록')]
    assert '추정하지 않는다' not in log


def test_front_matter_run_status():
    ok = make_result(orthogonality, round_name='1')
    empty = {'recipe': NAME, 'short_name': NAME, 'round': '2', 'raw_data': [],
             'error': 'No data loaded'}
    broken = {'recipe': NAME, 'short_name': NAME, 'round': '3', 'raw_data': [],
              'error': 'ValueError: x'}
    main = dict(ok)
    main.update(run_results=[ok, empty, broken], default_run='1')
    front = yaml.safe_load(report([main]).split('---\n')[1])
    assert front['workflows'][0]['run_status'] == {'1': 'ok', '2': 'no data', '3': 'error'}


def test_stabilization_count_is_die1_rows_only():
    md = report([make_result(orthogonality)])
    assert '- 안정화 Die1 행 20개 제외' in md


# ── 리뷰 반영: t 기준 힌트, 2원 분해, 회귀 고정 ─────────────────────

@pytest.mark.parametrize('k', [5, 6, 8])
@pytest.mark.parametrize('seed', range(4))
def test_pure_noise_never_gets_a_pattern_label(k, seed):
    dies = [1] + list(range(2, 2 + k))          # Die1(안정화) + 분석 Die k개

    def pure(axis, die, x, y, lot):
        return noise(axis, die, lot, 0.05, seed=seed)

    text = hint(report([make_result(pure, dies=dies, seed=seed)]))
    assert '구분 안 됨' in text, text


def test_near_threshold_rotation_is_not_called_orthogonality():
    def rotation(axis, die, x, y, lot):
        phi = 0.0005    # 0.5 µrad
        return (-phi * y if axis == 'X' else phi * x) + noise(axis, die, lot, 0.05)

    assert '직교도(직각도)형' not in hint(report([make_result(rotation)]))


def test_y_dominant_orthogonality():
    def y_shear(axis, die, x, y, lot):
        return (0.005 * x if axis == 'Y' else 0.0) + noise(axis, die, lot, 0.03)

    md = report([make_result(y_shear)])
    assert coeff(md, 'θy = ∂dy/∂x') == pytest.approx(5.0, abs=0.1)
    assert abs(coeff(md, 'θx = ∂dx/∂y')) < 0.2
    assert '직교도(직각도)형' in hint(md) and 'θy 우세: X 이동 시 Y 오차' in hint(md)


def _analysis(result):
    from core.llm_report import _Analysis
    return _Analysis(result)


def test_rotation_equals_screen_theta():
    a = _analysis(make_result(orthogonality))
    coef = a.coefficients()
    assert coef['rot']['value'] == pytest.approx(a.af['theta_urad'], abs=0.01)
    md = report([make_result(orthogonality)])
    assert coeff(md, '회전 = (θy − θx)/2') == pytest.approx(a.af['theta_urad'], abs=0.01)


def test_plane_f_p_adjusted_r2_match_independent_fit():
    import numpy as np
    from scipy.stats import f as f_dist
    from core.llm_report import _plane_r2
    a = _analysis(make_result(orthogonality))
    for axis, index in (('X', 0), ('Y', 1)):
        dies = list(a.af['die_positions_mm'])
        means = {d['die']: d['avg'] for d in a.devs[axis]['die_stats']}
        design = np.array([[1.0, *a.af['die_positions_mm'][d]] for d in dies])
        y = np.array([means[d] for d in dies])
        fit, *_ = np.linalg.lstsq(design, y, rcond=None)
        sse = float(np.sum((y - design @ fit) ** 2))
        sst = float(np.sum((y - y.mean()) ** 2))
        k = len(dies)
        r2 = 1 - sse / sst
        f_stat = (r2 / 2) / ((1 - r2) / (k - 3))
        got = _plane_r2(a, axis)
        # 잔차는 compute_affine_transform이 반올림 전 값으로 돌려준다
        assert got[0] == pytest.approx(r2, abs=1e-3)
        assert got[2] == pytest.approx(1 - (1 - r2) * (k - 1) / (k - 3), abs=1e-3)
        assert got[3] == pytest.approx(f_stat, rel=1e-3)
        assert got[4] == pytest.approx(float(f_dist.sf(f_stat, 2, k - 3)), rel=1e-2, abs=1e-12)


def test_coefficient_standard_errors_match_ols_formula():
    import numpy as np
    a = _analysis(make_result(orthogonality))
    dies = list(a.af['die_positions_mm'])
    design = np.array([[1.0, *a.af['die_positions_mm'][d]] for d in dies])
    means = {d['die']: d['avg'] for d in a.devs['X']['die_stats']}
    y = np.array([means[d] for d in dies])
    fit, *_ = np.linalg.lstsq(design, y, rcond=None)
    s2 = float(np.sum((y - design @ fit) ** 2)) / (len(dies) - 3)
    se = np.sqrt(np.diag(np.linalg.inv(design.T @ design)) * s2) * 1e3
    coef = a.coefficients()
    assert coef['tx']['se'] == pytest.approx(se[2], rel=1e-6)
    assert coef['tx']['t'] == pytest.approx(fit[2] * 1e3 / se[2], rel=1e-6)


def test_whatif_counts_dies_missing_from_plane_fit():
    coords = dict(COORDS)
    coords[5] = (0.0, 0.0)          # 좌표 0,0은 좌표 없음으로 본다 → 평면 피팅에서 빠진다
    md = report([make_result(orthogonality, coords=coords)])
    line = next(l for l in md.splitlines() if l.startswith('- X 계통 성분(평면) 제거 후'))
    assert '평면 피팅에서 빠진 Die 1개는 그대로' in line


def test_old_affine_keys_unchanged():
    from core.die_analysis import compute_affine_transform
    pos = {1: (-2.6, 0.1), 2: (37.4, 0.1), 3: (77.4, 0.1), 4: (-2.6, 40.1), 5: (-42.6, -39.9),
           6: (77.4, -79.9), 7: (-82.6, 80.1)}
    xs = [{'die': f'Die{i + 1}', 'avg': 0.00516 * y + 0.0003 * x + (0.02 if i == 1 else 0)}
          for i, (x, y) in pos.items()]
    ys = [{'die': f'Die{i + 1}', 'avg': 0.0002 * x - 0.0004 * y + (-0.01 if i == 3 else 0)}
          for i, (x, y) in pos.items()]
    af = compute_affine_transform(xs, ys, pos)
    # 179b8aa(확장 전)의 compute_affine_transform이 같은 입력에 돌려준 값
    expected = {'tx': 0.003, 'ty': -0.001, 'sx_ppm': 0.28, 'sy_ppm': -0.44,
                'theta_deg': -0.000143, 'theta_urad': -2.5, 'residual_x': 0.007,
                'residual_y': 0.0027, 'n_dies': 7, 'degenerate': False}
    assert {k: af[k] for k in expected} == expected


def lot_blocks(axis, die, x, y, lot):
    """X: Lot마다 ±0.3 µm 교대 + 2 µrad 평면 (X ∝ y)."""
    if axis == 'X':
        return (0.3 if lot % 2 else -0.3) + 0.002 * y + noise(axis, die, lot, 0.01)
    return noise(axis, die, lot, 0.01)


def test_two_way_decomposition_finds_lot_blocks():
    md = report([make_result(lot_blocks)])
    row = next(l for l in md.splitlines() if l.startswith('| X | 2원 |'))
    cells = row.strip('| ').split(' | ')
    assert float(cells[3]) > 70                         # Lot 간
    assert float(cells[2]) > 10                         # Die 간 (평면)
    assert cells[6].endswith('(p<1e-6)') and cells[7].endswith('(p<1e-6)')
    alternation = next(l for l in md.splitlines() if l.startswith('- X Lot 교대 패턴'))
    diff, lag = re.search(r'= ([+-]\d\.\d{3}) µm, Lot 평균의 lag-1 자기상관 ([+-]\d\.\d{2})',
                          alternation).groups()
    assert float(diff) == pytest.approx(0.6, abs=0.02) and float(lag) < -0.8


def test_unbalanced_matrix_falls_back_to_one_way():
    result = make_result(orthogonality)
    for r in result['raw_data']:
        if (r['lot_name'], r['site_id'][:4], r['method']) == ('Lot03', '0005', 'X'):
            r['valid'] = False
            r['value'] = float('nan')
    md = report([result])
    row = next(l for l in md.splitlines() if l.startswith('| X | 1원'))
    assert '결측 칸이 있어 2원 분해 불가' in row
    assert row.strip('| ').split(' | ')[3] == '—'       # Lot 간 없음


def test_drift_has_no_negative_zero():
    def flat(axis, die, x, y, lot):
        return 0.0

    md = report([make_result(flat)])
    assert '-0.0000' not in md and '-0.000 ' not in md


def test_missing_scipy_reports_no_p_values_and_warns_once(monkeypatch, caplog):
    import core.llm_report as llm_report
    monkeypatch.setattr(llm_report, '_F_DIST', None)
    monkeypatch.setattr(llm_report, '_SCIPY_WARNED', [])
    with caplog.at_level('WARNING'):
        md = report([make_result(orthogonality)])
        report([make_result(orthogonality)])
    assert 'p값 없음: scipy를 불러오지 못했다' in md and '(p 없음)' in md
    assert sum('scipy' in r.getMessage() for r in caplog.records) == 1


# ── 리뷰 반영: 회전·직교도 SE의 공분산 항, 분자 SS 0의 F, 셀 값 CSV의 행별 편차 ──

def correlated_axes(axis, die, x, y, lot):
    """X·Y가 Die마다 같은 방향으로 움직인다 (두 축 피팅 잔차가 상관)."""
    shared = 0.05 * math.sin(die * 2.3) + 0.03 * math.cos(die * 5.1)
    if axis == 'X':
        return 0.002 * y + shared + noise(axis, die, lot, 0.005)
    return 0.0005 * x + 0.8 * shared + noise(axis, die, lot, 0.005)


# 비대칭 배치: 오른쪽 위에 몰린 Die (x·y 교차 합이 0이 아니어서 inv12 ≠ 0)
ASYMMETRIC = {d: COORDS[d] for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 21, 22)}


def test_rotation_and_orthogonality_se_include_axis_covariance():
    import numpy as np
    a = _analysis(make_result(correlated_axes, coords=ASYMMETRIC))
    dies = list(a.af['die_positions_mm'])
    design = np.array([[1.0, *a.af['die_positions_mm'][d]] for d in dies])
    k = len(dies)
    fits, resid = {}, {}
    for axis in ('X', 'Y'):
        means = {d['die']: d['avg'] for d in a.devs[axis]['die_stats']}
        y = np.array([means[d] for d in dies])
        fits[axis], *_ = np.linalg.lstsq(design, y, rcond=None)
        resid[axis] = y - design @ fits[axis]
    inv = np.linalg.inv(design.T @ design)
    s = np.array([[resid[p] @ resid[q] for q in ('X', 'Y')] for p in ('X', 'Y')]) / (k - 3)
    joint = np.kron(s, inv)                 # [bx0, bx1, bx2, by0, by1, by2]의 공분산 (같은 설계)
    w_rot = np.zeros(6)
    w_rot[4], w_rot[2] = 0.5, -0.5          # (θy − θx)/2
    w_shear = np.zeros(6)
    w_shear[4], w_shear[2] = 1.0, 1.0       # θx + θy
    coef = a.coefficients()
    assert abs(s[0, 1] * inv[1, 2]) > 1e-12                       # 공분산 항이 실제로 있다
    assert coef['rot']['se'] == pytest.approx(math.sqrt(w_rot @ joint @ w_rot) * 1e3, rel=1e-3)
    assert coef['shear']['se'] == pytest.approx(
        math.sqrt(w_shear @ joint @ w_shear) * 1e3, rel=1e-3)
    independent = math.sqrt(joint[2, 2] + joint[4, 4]) * 1e3
    assert coef['shear']['se'] != pytest.approx(independent, rel=1e-3)


def test_two_way_f_is_none_when_a_factor_has_no_variation():
    from core.llm_report import _decompose

    def lots_only(axis, die, x, y, lot):
        return 0.3 if lot % 2 else -0.3

    def dies_only(axis, die, x, y, lot):
        return 0.01 * die

    lot_case = _decompose(_analysis(make_result(lots_only)), 'X')
    assert lot_case['mode'] == '2원' and lot_case['die_f'] is None and lot_case['die_p'] is None
    die_case = _decompose(_analysis(make_result(dies_only)), 'X')
    assert die_case['lot_f'] is None and die_case['lot_p'] is None
    md = report([make_result(lots_only)])
    row = next(l for l in md.splitlines() if l.startswith('| X | 2원 |'))
    assert row.strip('| ').split(' | ')[6] == '— (p 없음)'     # Die 간 F: ∞로 적지 않는다


def test_raw_csv_deviation_is_per_row_and_duplicates_are_flagged():
    import csv as csv_module
    import io
    from core.die_analysis import compute_deviation_matrix
    from core.llm_report import build_raw_csv
    result = make_result(orthogonality, lots=3)
    original = next(r for r in result['raw_data'] if (r['lot_name'], r['site_id'][:4],
                                                       r['method']) == ('Lot02', '0005', 'X'))
    extra = dict(original, value=original['value'] + 400.0, point_no=2)
    result['raw_data'].insert(result['raw_data'].index(original), extra)   # 표에는 뒤 행이 들어간다
    analysis = filter_stabilization_die(result['raw_data'])
    dev = compute_deviation_matrix(analysis, 'X')
    rows = list(csv_module.DictReader(io.StringIO(build_raw_csv([result]))))

    both = [r for r in rows if (r['lot'], r['die'], r['axis']) == ('Lot02', 'Die5', 'X')]
    assert len(both) == 2
    by_flag = {('dup' in r['flags'].split('|')): r for r in both}
    for row, source in ((by_flag[True], extra), (by_flag[False], original)):
        assert float(row['deviation_um']) == pytest.approx(
            (source['value'] - dev['average_nm']) / 1000, abs=1e-4)
    assert float(by_flag[False]['deviation_um']) == pytest.approx(
        dev['matrix']['Lot02']['Die5'], abs=1e-4)
    assert sum('dup' in r['flags'] for r in rows) == 1
    assert 'dup' in build_llm_report([result], SPEC_LIM, SPEC_DEV, raw_csv={'name': 'r.csv'},
                                     **FIXED)
