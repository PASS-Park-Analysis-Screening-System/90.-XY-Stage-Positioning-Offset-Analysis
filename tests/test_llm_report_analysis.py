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


def make_result(dev, lots=10, round_name='14', seed=0):
    """dev(axis, die, x_mm, y_mm, lot) → 편차(µm)로 한 회차 결과를 만든다."""
    rows = []
    for lot in range(1, lots + 1):
        for die, (x, y) in COORDS.items():
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
    return float(re.search(re.escape(label) + r' ([+-]\d+\.\d+)', md).group(1))


def test_orthogonality_found_and_named(capsys):
    md = report([make_result(orthogonality)])
    assert coeff(md, 'θx = ∂dx/∂y') == pytest.approx(5.16, abs=0.15)
    assert abs(coeff(md, 'θy = ∂dy/∂x')) < 0.3
    assert '직교도(직각도)형' in md and 'θx 우세' in md
    assert '회전형 (θx ≈ −θy)' not in md


def test_orthogonality_whatif_passes_after_plane_removal():
    result = make_result(orthogonality)
    md = report([result])
    m = compute_axis_metrics(filter_stabilization_die(result['raw_data']), 'X', NAME,
                             SPEC_LIM, SPEC_DEV)
    assert m['result'] is False
    line = next(l for l in md.splitlines() if l.startswith('- 계통 성분 제거 후 X'))
    rng, std = map(float, re.search(r'Range (\d+\.\d+) µm, StdDev (\d+\.\d+) µm', line).groups())
    assert rng < 0.45 and std < 0.08               # 남는 것은 Die2 편향 + 잡음
    assert '(현재 FAIL → PASS)' in line and '(파생)' in line


def test_pure_rotation_is_not_called_orthogonality():
    phi = 0.005     # 5 µrad (µm/mm)

    def rotation(axis, die, x, y, lot):
        return (-phi * y if axis == 'X' else phi * x) + noise(axis, die, lot, 0.01)

    md = report([make_result(rotation)])
    assert coeff(md, 'θx = ∂dx/∂y') == pytest.approx(-5.0, abs=0.1)
    assert coeff(md, 'θy = ∂dy/∂x') == pytest.approx(5.0, abs=0.1)
    assert coeff(md, '회전 = (θy − θx)/2 =') == pytest.approx(5.0, abs=0.1)
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
    row = next(l for l in md.splitlines() if l.startswith('| X | ') and '(p' in l)
    cells = row.split(' | ')
    assert float(cells[1]) > 95                      # Die 간 비율
    assert float(cells[2]) == pytest.approx(20 / 209 * 100, abs=0.1)   # (k−1)/(N−1)
    assert cells[3].endswith('(p<1e-6)')


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
    assert '직교도(직각도) 오차' in request and '뒷받침할 때만' in request
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
