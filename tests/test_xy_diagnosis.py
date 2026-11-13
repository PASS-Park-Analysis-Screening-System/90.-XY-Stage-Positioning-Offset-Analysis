"""트러블슈팅 진단 (core.xy_diagnosis, pass.xy.diagnosis/1)과 'Diagnosis (트러블슈팅)' 탭.

실데이터 (tests/fixtures/xy_diagnosis, 공유 폴더에서 읽기만 해 줄인 사본. Log는 필요한 행과 앞 6 Site만,
CSV의 Data Save Location은 비움):
- old: Vision Pattern 1회차 Lot 3개 (Sample2202~2204, 2026-11-07). X 라인 레시피 Y Offset −25 µm,
  Lot마다 기준 스캔. 조사 결론: X 라인을 스캐너 Y ≈ −28 µm에서 재서 θx ≈ 5.2 µrad, X FAIL.
- new: 레시피 −5 µm로 바꾼 10 Lot 중 첫·둘째·마지막 Lot (Sample1902·1903·1911, 2026-11-12).
  Log에 기준 스캔이 없다 (장비에 저장된 값을 씀). θx 0.95, X PASS, Y 예열 드리프트.
- old_vp_x_140lots.csv: 1~14회차 140 Lot의 X HZ1_O (what-if 검증, 조사 표의 숫자).
"""
import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import show_expert_tab
from core import xy_diagnosis
from core.recipe_scanner import load_all_recipes, recipes_from_mapping
from core.settings import DEFAULT_ROTATION_SPEC, rotation_spec
from core.xy_diagnosis import (NO_LOG_TEXT, NO_POSLOG_TEXT, NO_REF_TEXT, OTHER_TOOL_TEXT, PART_IDS,
                               SCHEMA, XYDiagnosis, build_diagnosis, parse_scanner_offsets,
                               predict_whatif, read_run_logs, store_logs)

FIXTURES = Path(__file__).parent / 'fixtures' / 'xy_diagnosis'
SPEC_DEV = {'Vision Pattern': {'spec_range': 1.0, 'spec_stddev': 0.2}}
SPEC_LIM = {'Vision Pattern': {'X': {'lsl': -5000.0, 'usl': 5000.0},
                               'Y': {'lsl': -5000.0, 'usl': 5000.0}}}
SCHEMA_KEYS = ['schema', 'generated', 'app_version', 'source', 'spec', 'verdict', 'shape',
               'scanner_offsets', 'rotation', 'positioning_log', 'drift', 'transient', 'die_field',
               'whatif', 'parts', 'actions', 'warnings', 'rotation_test']


def load(path: Path):
    recipes, problems = recipes_from_mapping([('Vision Pattern', str(path))], ['Vision Pattern'])
    assert problems == []
    return load_all_recipes('', recipes=recipes)[0]


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def old_result():
    return load(FIXTURES / 'old')


@pytest.fixture(scope='module')
def new_result():
    return load(FIXTURES / 'new')


@pytest.fixture(scope='module')
def old_diag(old_result):
    return build_diagnosis(old_result, read_run_logs(old_result), SPEC_DEV, generated='t').to_dict()


@pytest.fixture(scope='module')
def new_diag(new_result):
    return build_diagnosis(new_result, read_run_logs(new_result), SPEC_DEV, generated='t').to_dict()


# ── Log 해석·분해 ─────────────────────────────────────────────────

def test_parse_real_log_lines_fine_and_reference():
    parsed = parse_scanner_offsets(read_text(FIXTURES / 'old' / 'Sample2202' / 'Log' / 'Sample22_Log.csv'))
    lines = parsed['lines']
    # 기준 스캔의 'Set 1st line parameters'(ImageRefLineRefScanJob)는 측정 라인이 아니다
    assert [l['line'] for l in lines] == ['X', 'Y'] * 6
    assert lines[0]['direction'] == 'X Backward' and lines[1]['direction'] == 'Y Forward'
    assert lines[0]['applied'] == (0.688558, -27.83378)
    assert lines[0]['fine'] == (0.024558, -0.13878)          # 그 Site의 정밀 정렬 (µm)
    assert lines[0]['ref'] == pytest.approx((0.66406, -2.69531))   # Reference는 nm → µm
    assert parsed['refs'][0]['total'] == pytest.approx((1.06734, -2.66333))
    assert parsed['first_time'] == '2026/11/07 16:03:49.273'


def test_decomposition_gives_recipe_exactly(old_diag):
    """레시피 = 적용 − 기준 보정 − 미세 보정 → X 라인 (0, −25), Y 라인 (30, 2), 측정마다 같다."""
    x, y = old_diag['scanner_offsets']['X_line'], old_diag['scanner_offsets']['Y_line']
    assert x['recipe'] == pytest.approx([0.0, -25.0], abs=1e-3)
    assert y['recipe'] == pytest.approx([30.0, 2.0], abs=1e-3)
    assert max(x['recipe_spread']) < 1e-3 and max(y['recipe_spread']) < 1e-3
    assert x['n'] == x['n_ref'] == 18                          # 3 Lot × 6 Site
    assert x['applied'][1] == pytest.approx(-27.68, abs=0.01)
    assert x['ref'] == pytest.approx([0.677, -2.695], abs=1e-3)
    assert old_diag['scanner_offsets']['source'] == 'Log.csv ImageJob'
    assert old_diag['warnings'] == []


def test_log_without_reference_scan_keeps_recipe_unknown(new_diag):
    x = new_diag['scanner_offsets']['X_line']
    assert x['recipe'] is None and x['ref'] is None and x['n_ref'] == 0
    assert x['recipe_plus_ref'] == pytest.approx([1.133, -7.617], abs=1e-3)
    assert x['applied'][1] == pytest.approx(-8.12, abs=0.01)
    assert new_diag['scanner_offsets']['Y_line']['recipe_plus_ref'] == pytest.approx([26.133, -0.617],
                                                                                    abs=1e-3)
    assert any(NO_REF_TEXT in w for w in new_diag['warnings'])
    # 레시피를 모르면 what-if 행은 스캐너 Y 적용값 목표(지금과 같은 쪽)이고, 지금 행이 모든 Lot PASS라
    # 지금 행만 권장한다 ('현재 유지' — 잘못된 '줄이세요'를 내지 않는다)
    rows = new_diag['whatif']['rows']
    assert new_diag['whatif']['basis'] == 'scanner_y'
    assert [r['scanner_y'] for r in rows if not r['current']] == [-25.0, -15.0, -10.0, -5.0, -3.0, 0.0]
    assert all(r['recipe_y'] is None for r in rows)
    assert [r['current'] for r in rows if r['recommended']] == [True]
    assert any('스캐너 Y 적용값 -8.1 µm 유지' in a for a in new_diag['actions'])
    assert not any('줄이세요' in a and 'X 라인' in a and '(참고)' not in a for a in new_diag['actions'])


def test_reference_carries_to_later_lots_in_time_order():
    lot = {'status': 'ok', 'lines': [{'line': 'X', 'applied': (1.0, -28.0), 'fine': (0.1, -0.3),
                                      'ref': None}]}
    logs = {'lots': [dict(lot, lot='B', start=None),
                     {'lot': 'A', 'status': 'ok', 'start': None, 'lines': [],
                      'refs': [{'ref': (0.9, -2.7)}]}]}
    logs['lots'][0]['start'] = xy_diagnosis.datetime(2026, 1, 1, 2)
    logs['lots'][1]['start'] = xy_diagnosis.datetime(2026, 1, 1, 1)
    out = xy_diagnosis._line_offsets(logs)['X_line']
    assert out['recipe'] == pytest.approx([0.0, -25.0])


# ── 모양·회전·what-if ─────────────────────────────────────────────

def test_shape_theta_kappa_and_rotation_status(old_diag, new_diag):
    old, new = old_diag['rotation'], new_diag['rotation']
    assert old_diag['shape']['X']['theta'] == pytest.approx(5.22, abs=0.01)
    assert old_diag['shape']['X']['theta_t'] > 10
    assert old['kappa_eff'] == pytest.approx(5.218 / 27.684, abs=1e-3)       # ≈ 0.19 µrad/µm
    assert old['contribution_um'] == pytest.approx(5.218 * 0.24, abs=0.01)
    assert old['status'] == 'fail' and old['offset_check']['status'] == 'warn'
    assert new_diag['shape']['X']['theta'] == pytest.approx(0.95, abs=0.01)
    assert new['kappa_eff'] == pytest.approx(0.117, abs=0.002)
    # θ 0.95는 주의 구간, κ는 한도 초과 → warn (θ가 규격을 넘지 않으면 fail은 아니다)
    assert new['status'] == 'warn' and new['offset_check']['status'] == 'ok'
    assert any('참고: Y 라인 스캐너 X' in r for r in new['reasons'])
    assert old['y_line']['status'] == 'ok' and old['y_line']['kappa_eff'] < 0.01


@pytest.mark.parametrize('theta,t,k,drive,expected', [
    (5.27, 18.8, 21, -28.0, 'fail'),
    (0.95, 5.6, 21, -8.2, 'warn'),      # θ 주의 구간
    (0.50, 5.0, 21, -8.2, 'ok'),        # κ 0.061 < 0.07
    (0.60, 5.0, 21, -8.2, 'warn'),      # θ는 주의 미만이지만 κ 0.073 ≥ 0.07 (구동 8.2 ≥ 5, θ ≥ 0.35)
    (0.30, 5.0, 21, -3.0, 'ok'),        # κ 0.10이지만 구동 < 5 µm → 참고만 (M1)
    (0.30, 5.0, 21, -8.2, 'ok'),        # κ 0.037
    (0.95, 1.0, 21, -8.2, 'ok'),        # 유의하지 않음
    (0.95, 5.6, 6, -8.2, 'ok'),         # Die 8개 미만이면 유의하지 않음 (M2)
    (2.00, 6.0, 21, -1.0, 'fail'),      # 오프셋이 작아 κ는 없음, θ만
])
def test_rotation_rules(theta, t, k, drive, expected):
    rot = xy_diagnosis._rotation_line('X', {'theta': theta, 'theta_t': t, 'k': k},
                                      {'X_line': {'applied': [0.0, drive]}}, 240.0, DEFAULT_ROTATION_SPEC)
    assert rot['status'] == expected
    assert (rot['kappa_eff'] is None) == (abs(drive) < xy_diagnosis.MIN_KAPPA_OFFSET_UM)
    if (theta, drive) == (0.30, -3.0):
        assert any(r.startswith('참고: κ_eff') for r in rot['reasons'])


def test_whatif_reproduces_validation_targets():
    """θx 5.37 at −28 µm: −25 → Range 1.32 / StdDev 0.330, −5 → 0.46 / 0.113 (140 Lot, 조사 표)."""
    lines = [l for l in read_text(FIXTURES / 'old_vp_x_140lots.csv').splitlines() if not l.startswith('#')]
    header = lines[0].split(',')[1:]
    xs = dict(zip(header, map(float, lines[1].split(',')[1:])))
    ys = dict(zip(header, map(float, lines[2].split(',')[1:])))
    positions = {d: (xs[d], ys[d]) for d in header}
    matrix = {}
    for line in lines[3:]:
        cells = line.split(',')
        matrix[cells[0]] = {d: float(v) / 1000 for d, v in zip(header, cells[1:]) if v}
    kappa = 5.37 / -28.0
    now = predict_whatif(matrix, positions, kappa, 0.0, 1.0, 0.2)
    fix = predict_whatif(matrix, positions, kappa, -25.0 - -5.0, 1.0, 0.2)
    assert now['lots'] == 140
    assert now['range_mean'] == pytest.approx(1.32, abs=0.01)
    assert now['std_mean'] == pytest.approx(0.330, abs=0.002)
    assert now['pass_lots'] == 0
    assert fix['range_mean'] == pytest.approx(0.46, abs=0.01)
    assert fix['range_max'] == pytest.approx(0.57, abs=0.01)
    assert fix['std_mean'] == pytest.approx(0.113, abs=0.002)
    assert fix['pass_lots'] == 140


def test_whatif_rows_current_and_recommended(old_diag):
    rows = old_diag['whatif']['rows']
    assert [r['recipe_y'] for r in rows] == [-25.0, -15.0, -10.0, -5.0, -3.0, 0.0]
    current = [r for r in rows if r['current']]
    assert len(current) == 1 and current[0]['recipe_y'] == -25.0
    assert current[0]['range_all'] == old_diag['verdict']['X']['range']   # 현재 행 = 앱 판정 값
    best = [r for r in rows if r['recommended']]
    assert len(best) == 1 and best[0]['recipe_y'] == -5.0
    assert best[0]['scanner_y'] == pytest.approx(-7.68, abs=0.01)
    assert old_diag['whatif']['assumption'] == 'linear kappa_eff'
    assert '원점을 지나지 않는다' in old_diag['whatif']['note']          # 선형 원점 통과는 근사 (M1/L12)
    assert old_diag['actions'][0].startswith('X 라인 레시피 Y Offset을 줄이세요 (예: -25 → -5 µm')


def _synthetic_matrix(theta_signed, drive, lots=6, noise=0.02):
    """Die Y에 비례하는 X 편차(θ·y)와 Lot마다 다른 작은 잡음."""
    from core.die_analysis import DIE_PITCH_MM, DIE_POSITIONS
    positions = {f'Die{i + 2}': (x * DIE_PITCH_MM, y * DIE_PITCH_MM) for i, (x, y) in enumerate(DIE_POSITIONS)}
    matrix = {}
    for lot in range(lots):
        matrix[f'L{lot}'] = {d: theta_signed * 1e-3 * p[1] + noise * (((i * 7 + lot * 3) % 5) - 2) / 2
                             for i, (d, p) in enumerate(positions.items())}
    return matrix, positions


def test_whatif_follows_the_current_side():
    """레시피 +25 µm: 후보는 +쪽(+25 … 0), 지금 행(FAIL)이 아니라 +5가 권장 (H2)."""
    matrix, positions = _synthetic_matrix(-5.3, 28.0)
    shape = {'theta': -5.3, 'theta_t': 20.0, 'k': 21}
    w = xy_diagnosis._whatif(shape, 28.0, 25.0, matrix, positions, 1.0, 0.2)
    assert [r['recipe_y'] for r in w['rows']] == [25.0, 15.0, 10.0, 5.0, 3.0, 0.0]
    now = next(r for r in w['rows'] if r['current'])
    assert now['recipe_y'] == 25.0 and now['pass_lots'] == 0 and not now['recommended']
    best = [r for r in w['rows'] if r['recommended']]
    assert len(best) == 1 and best[0]['recipe_y'] == 5.0 and best[0]['pass_lots'] == 6
    # |지금 레시피| < 0.5는 0: 기본 쪽(−)으로 만들고, 지금 행이 PASS면 '현재 유지'
    w = xy_diagnosis._whatif({'theta': 0.4, 'theta_t': 5.0, 'k': 21}, -3.0, 0.3, *_synthetic_matrix(0.4, -3.0),
                             1.0, 0.2)
    assert [r['recipe_y'] for r in w['rows']] == [-25.0, -15.0, -10.0, -5.0, -3.0, 0.0]
    assert [r['recipe_y'] for r in w['rows'] if r['recommended']] == [0.0]
    # 지금 행이 모든 Lot PASS가 아니고 꼭짓점 여유를 지키는 PASS 후보도 없으면 권장 없음
    w = xy_diagnosis._whatif({'theta': -5.3, 'theta_t': 20.0, 'k': 21}, 28.0, 25.0,
                             *_synthetic_matrix(-5.3, 28.0, noise=0.6), 1.0, 0.2)
    assert not any(r['recommended'] for r in w['rows'])


def test_whatif_needs_significant_theta(new_result):
    flat = dict(new_result, raw_data=[dict(r, value=1000.0 if r['method'] == 'X' else r['value'])
                                      for r in new_result['raw_data']])
    d = build_diagnosis(flat, read_run_logs(new_result), SPEC_DEV).to_dict()
    assert d['whatif']['rows'] == [] and d['whatif']['kappa_eff'] is None
    assert '예측하지 않음' in d['whatif']['note']


# ── drift·transient ───────────────────────────────────────────────

def test_drift_matches_new_data(new_diag):
    drift = new_diag['drift']
    ys = [e['y'] for e in drift['lot_means']]
    assert ys[0] == pytest.approx(-3.80, abs=0.005) and ys[-1] == pytest.approx(-3.49, abs=0.005)
    assert drift['lot_means'][0]['start'] == '2026-11-12T11:25:01'
    first = next(w for w in drift['within_lot'] if w['lot'] == 'Sample1902' and w['axis'] == 'Y')
    assert first['slope_um_per_die'] == pytest.approx(-0.018, abs=0.001)
    assert first['r'] == pytest.approx(-0.87, abs=0.01)
    assert drift['status'] == 'warn' and drift['time_basis'] == 'start'
    assert any('예열(열) 드리프트 의심 — 측정 전 워밍업/더미 Lot 권장' in r for r in drift['reasons'])


def test_drift_ignores_static_theta_pattern(old_diag):
    """θx 5 µrad는 측정 순서(+Y → −Y)와 엮여 기울기처럼 보이지만 공간 모양이다 → drift 아님."""
    drift = old_diag['drift']
    assert drift['status'] == 'ok'
    x_first = next(w for w in drift['within_lot'] if w['lot'] == drift['first_lot'] and w['axis'] == 'X')
    assert abs(x_first['slope_um_per_die']) < 0.01


def test_transient_lot_start(old_diag):
    tr = old_diag['transient']
    assert tr['X']['die1'] == pytest.approx(0.69, abs=0.05)
    assert tr['X']['die2'] == pytest.approx(0.25, abs=0.05)
    assert tr['X']['die1'] > tr['X']['die2'] > tr['X']['die3']
    assert tr['status'] == 'warn' and 'Die2' in tr['reasons'][0]


# ── 부품 판정 ─────────────────────────────────────────────────────

def _parts(shapes, rotation, poslog=None, drift=None, transient=None, full=False):
    parts, _ = xy_diagnosis._parts(True, shapes, rotation, poslog, drift or {'status': 'ok'},
                                   transient or {'status': 'ok'}, DEFAULT_ROTATION_SPEC)
    return {p['id']: (p if full else p['verdict']) for p in parts}


SHAPES_OLD = {'X': {'theta': 5.2, 'theta_t': 19.0, 'k': 21}, 'Y': {'theta': 0.05, 'theta_t': 0.3, 'k': 21}}


def test_part_rules_real_runs(old_diag, new_diag):
    old = {p['id']: p for p in old_diag['parts']}
    new = {p['id']: p['verdict'] for p in new_diag['parts']}
    assert [p['id'] for p in old_diag['parts']] == list(PART_IDS)
    # Positioning Log 분석이 없으면 스캐너 Y는 많아야 주의 (H1)
    assert old['xy_scanner_y']['verdict'] == 'warn'
    assert xy_diagnosis.NO_POSLOG_TEXT in old['xy_scanner_y']['reason']
    assert old['chuck']['verdict'] == 'warn' and old['xy_scanner_x']['verdict'] == 'ok'
    assert old['vision']['verdict'] == old['xy_stage']['verdict'] == 'na'
    assert new['xy_scanner_y'] == 'warn' and new['chuck'] == 'warn'
    assert new['z_scanner_tip'] == new['f_stage'] == 'warn'   # drift·transient
    for d in (old_diag, new_diag):
        assert all(p['verdict'] in xy_diagnosis.VERDICTS and p['reason'] for p in d['parts'])
        assert any('폐루프 2지점 스윕으로 스캐너 회전 계수 측정' in a for a in d['actions'])
    assert any('측정 전 워밍업' in a for a in new_diag['actions'])


def test_part_rules_follow_positioning_log():
    vision_stage = {'judged': True, 'stages': [{'name': 'x'}], 'terms': [
        {'axis': 'X', 'term': 'θx', 'value': 5.2, 't': 19.0, 'carriers': ['coarse_match'],
         'same_sign': [True], 'span': '스테이지 이동(도착)'}]}
    after = {'judged': True, 'stages': [{'name': 'x'}], 'terms': [
        {'axis': 'X', 'term': 'θx', 'value': 5.2, 't': 19.0, 'carriers': [], 'same_sign': [], 'span': ''},
        {'axis': 'Y', 'term': 'θy', 'value': 1.5, 't': 8.0, 'carriers': [], 'same_sign': [], 'span': ''}]}
    skipped = dict(after, judged=False)        # judge가 Die 8개 미만이라 판단하지 않은 분석 (L14)
    small_offset = {'scanner_y': -5.0, 'y_line': {'scanner_x': 5.0}}
    big_offset = {'scanner_y': -28.0, 'y_line': {'scanner_x': 30.0}}
    # 비전 단계가 가졌고 스캐너 Y가 한도 안이면 스캐너가 아니라 스테이지 쪽
    got = _parts(SHAPES_OLD, small_offset, vision_stage)
    assert got['xy_scanner_y'] == 'ok' and got['xy_stage'] == 'fail' and got['vision'] == 'ok'
    # 비전 단계가 가져도 스캐너 Y가 한도를 넘으면 주의 (fail은 비전 정렬 이후일 때만)
    assert _parts(SHAPES_OLD, big_offset, vision_stage)['xy_scanner_y'] == 'warn'
    # 비전 정렬 이후 + 구동 ≥ 5 µm면 fail (증거가 다 있음)
    assert _parts(SHAPES_OLD, small_offset, after)['xy_scanner_y'] == 'fail'
    assert _parts(SHAPES_OLD, big_offset, after)['xy_scanner_y'] == 'fail'
    # 로그 분석이 없거나 판정하지 않은 분석이면 많아야 주의, vision·xy_stage는 na
    for poslog in (None, skipped):
        got = _parts(SHAPES_OLD, big_offset, poslog, full=True)
        assert got['xy_scanner_y']['verdict'] == 'warn'
        assert xy_diagnosis.NO_POSLOG_TEXT in got['xy_scanner_y']['reason']
    assert _parts(SHAPES_OLD, big_offset, None)['vision'] == 'na'
    # 구동이 작으면(< 5 µm) 원인으로 단정하지 않는다 (H1 재현: −0.4 µm, θx 1.5, t 8)
    got = _parts({'X': {'theta': 1.5, 'theta_t': 8.0, 'k': 21}, 'Y': SHAPES_OLD['Y']},
                 {'scanner_y': -0.4, 'y_line': {'scanner_x': 0.4}}, after, full=True)
    assert got['xy_scanner_y']['verdict'] == 'na'
    assert xy_diagnosis.SMALL_DRIVE_TEXT in got['xy_scanner_y']['reason']
    # 구동을 모르면(Log 없음) 많아야 주의
    assert _parts(SHAPES_OLD, {}, after)['xy_scanner_y'] == 'warn'
    # Y 라인에 같은 모양이면 스캐너 X (같은 규칙)
    shapes = {'X': {'theta': 0.1, 'theta_t': 1.0, 'k': 21}, 'Y': {'theta': 1.5, 'theta_t': 8.0, 'k': 21}}
    got = _parts(shapes, big_offset, after)
    assert got['xy_scanner_x'] == 'fail' and got['xy_scanner_y'] == 'ok' and got['chuck'] == 'ok'
    assert _parts(shapes, big_offset, None)['xy_scanner_x'] == 'warn'
    assert _parts(shapes, small_offset, after)['xy_scanner_x'] == 'fail'      # |X| 5 µm
    assert _parts(shapes, {'scanner_y': -5.0, 'y_line': {'scanner_x': 1.0}}, after)['xy_scanner_x'] == 'na'
    got = _parts(shapes, big_offset, None, drift={'status': 'warn'})
    assert got['z_scanner_tip'] == got['f_stage'] == 'warn'
    assert all(v == 'na' for v in {p['id']: p['verdict'] for p in xy_diagnosis._parts(
        False, {}, {}, None, {}, {}, DEFAULT_ROTATION_SPEC)[0]}.values())


def test_positioning_log_summary_is_copied(old_result):
    from core.positioning_log import judge, stage_table
    from core.die_analysis import extract_die_positions, filter_stabilization_die
    data = filter_stabilization_die(old_result['raw_data'])
    positions = {f'Die{i + 1}': p for i, p in extract_die_positions(data).items()}
    final_x = {d: 5.2e-3 * p[1] + 0.01 * ((i % 3) - 1) for i, (d, p) in enumerate(positions.items())}
    values = {'coarse_match': {'X': {d: 0.02 * ((i % 5) - 2) for i, d in enumerate(positions)}},
              'final': {'X': final_x}}
    table = stage_table(values, positions)
    poslog = {'status': 'ok', 'message': 'Lot 1개 대조', 'mean': {'stages': table}, 'verdict': judge(table)}
    d = build_diagnosis(old_result, read_run_logs(old_result), SPEC_DEV, poslog=poslog).to_dict()
    pl = d['positioning_log']
    assert pl['final']['axis'] == 'X' and pl['final']['theta'] == pytest.approx(5.2, abs=0.05)
    assert pl['stages'][0]['name'] == '도착 오차 (Coarse Match)'
    assert any('비전 정렬 이후' in line for line in pl['verdict_lines'])
    parts = {p['id']: p for p in d['parts']}
    assert parts['xy_scanner_y']['verdict'] == 'fail' and '비전 정렬 이후' in parts['xy_scanner_y']['reason']
    assert parts['vision']['verdict'] == 'ok' and parts['xy_stage']['verdict'] == 'ok'
    assert build_diagnosis(old_result, read_run_logs(old_result), SPEC_DEV).positioning_log is None


# ── 스키마·JSON ──────────────────────────────────────────────────

def test_schema_keys_and_json_round_trip(old_diag, old_result):
    assert list(old_diag) == SCHEMA_KEYS
    assert old_diag['schema'] == SCHEMA == 'pass.xy.diagnosis/1'
    assert old_diag['rotation_test'] is None
    assert set(old_diag['source']) >= {'root', 'workflow', 'run', 'lots', 'dies', 'recipe_id', 'sample',
                                       'start', 'end'}
    assert old_diag['source']['lots'] == 3 and old_diag['source']['dies'] == 21
    assert old_diag['source']['sample'] == 'Sample22' and old_diag['source']['start'] == '2026-11-07T16:03:49'
    assert old_diag['spec'] == {'range': 1.0, 'stddev': 0.2, **DEFAULT_ROTATION_SPEC}
    assert old_diag['verdict']['X'] == {'range': 1.445, 'stddev': 0.328, 'pass': False}
    assert set(old_diag['shape']['X']) >= {'theta', 'theta_t', 'scale_ppm', 'scale_t', 'mean'}
    assert set(old_diag['rotation']) >= {'axis', 'theta', 'scanner_y', 'kappa_eff', 'contribution_um',
                                         'status', 'reasons'}
    assert set(old_diag['drift']) >= {'status', 'reasons', 'lot_means', 'within_lot'}
    assert set(old_diag['transient']) >= {'status', 'X', 'Y'}
    field = old_diag['die_field']
    assert len(field) == 21 and set(field[0]) == {'die', 'site_x', 'site_y', 'x_mm', 'y_mm', 'dx', 'dy'}
    die10 = next(f for f in field if f['die'] == 'Die10')
    assert (die10['site_x'], die10['site_y']) == (0, 6) and die10['y_mm'] == pytest.approx(120.1, abs=0.1)
    assert set(old_diag['whatif']['rows'][0]) >= {'recipe_y', 'scanner_y', 'theta', 'range_mean', 'range_max',
                                                  'std_mean', 'std_max', 'pass_lots', 'lots', 'current'}
    diag = build_diagnosis(old_result, read_run_logs(old_result), SPEC_DEV, generated='t')
    text = diag.to_json()
    again = XYDiagnosis.from_dict(json.loads(text))
    assert again.to_dict() == diag.to_dict() == old_diag
    assert 'NaN' not in text and json.loads(text)['schema'] == SCHEMA


def test_empty_result_is_all_na():
    d = build_diagnosis({'raw_data': []}, None, SPEC_DEV).to_dict()
    assert list(d) == SCHEMA_KEYS
    assert {p['verdict'] for p in d['parts']} == {'na'}
    assert json.loads(XYDiagnosis.from_dict(d).to_json())['verdict']['X']['pass'] is None


# ── 읽기: 네트워크 오류·없음·로드 ───────────────────────────────────

def test_network_error_is_error_not_missing_and_not_cached(old_result, monkeypatch):
    from core import csv_loader
    monkeypatch.setattr(csv_loader, 'FOLDER_RETRY_DELAY', 0)
    real = xy_diagnosis.list_dir
    calls = []

    def flaky(path, *args):
        if os.path.basename(path).lower() == 'log':
            calls.append(path)
            raise OSError(64, '지정된 네트워크 이름을 더 이상 사용할 수 없습니다')
        return real(path, *args)
    monkeypatch.setattr(xy_diagnosis, 'list_dir', flaky)
    result = dict(old_result)
    logs = read_run_logs(result, workers=1)
    assert len(calls) == 6                                   # Lot 3개 × 한 번 더 읽어 본다
    assert logs['status'] == 'error' and {l['status'] for l in logs['lots']} == {'error'}
    assert NO_LOG_TEXT not in logs['message'] and logs['message'].startswith('Log를 읽지 못함')
    assert store_logs(result, logs) is False and 'xy_logs' not in result
    d = build_diagnosis(result, logs, SPEC_DEV).to_dict()
    assert any('Log를 읽지 못함' in w for w in d['warnings'])


def test_missing_log_folder_is_no_log(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / 'old' / 'Sample2202', tmp_path / 'vp' / 'Sample2202',
                    ignore=shutil.ignore_patterns('Log'))
    result = load(tmp_path / 'vp')
    logs = read_run_logs(result)
    assert logs['status'] == 'no_logs' and logs['lots'][0]['status'] == 'no_log'
    assert logs['lots'][0]['start'] is not None              # Lot 시작 시각은 CSV에서
    assert store_logs(result, logs) is True and result['xy_logs'] is logs
    d = build_diagnosis(result, logs, SPEC_DEV).to_dict()
    assert d['scanner_offsets']['X_line']['applied'] is None
    assert d['rotation']['offset_check']['status'] == 'na'
    assert d['whatif']['rows'] == []


def test_loader_thread_does_not_read_logs(monkeypatch):
    from ui.workers.data_loader_thread import DataLoaderThread
    monkeypatch.setattr(xy_diagnosis, 'read_lot_log',
                        lambda *a, **k: pytest.fail('로드 중에 Log를 읽었다'))
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(FIXTURES / 'old'))], ['Vision Pattern'])
    thread = DataLoaderThread('', recipes=recipes)
    got = []
    thread.finished.connect(lambda results, _c, _e: got.append(results))
    thread.run()
    assert got and 'xy_logs' not in got[0][0]


# ── 설정·회차 비교·LLM ───────────────────────────────────────────

def test_rotation_spec_defaults_and_bundle(tmp_path, monkeypatch):
    from core import settings
    assert rotation_spec({}) == DEFAULT_ROTATION_SPEC == {
        'theta_max': 1.0, 'theta_warn': 0.7, 'kappa_max': 0.10, 'kappa_warn': 0.07,
        'offset_max': 10.0, 'provisional': True}
    assert rotation_spec({'rotation_spec': {'theta_max': 2.0}})['theta_max'] == 2.0
    bundled = json.loads((Path(settings.__file__).parent / 'settings.json').read_text(encoding='utf-8'))
    assert bundled['rotation_spec'] == DEFAULT_ROTATION_SPEC
    old = tmp_path / 'settings.json'
    old.write_text(json.dumps({'spec_deviation': {}}), encoding='utf-8')
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(old))
    monkeypatch.setattr(settings, 'LOCAL_SETTINGS_FILE', str(tmp_path / 'local.json'))
    assert settings.load_settings()['rotation_spec'] == DEFAULT_ROTATION_SPEC    # 예전 번들도 채운다


def test_run_comparison_theta_uses_csv_only(old_result, old_diag, monkeypatch):
    from core.checklist import summarize_runs
    monkeypatch.setattr(xy_diagnosis, 'read_lot_log', lambda *a, **k: pytest.fail('Log를 읽었다'))
    other = dict(old_result, round='2')
    runs = summarize_runs(dict(old_result, run_results=[old_result, other]), SPEC_LIM, SPEC_DEV)
    assert runs[0]['theta_x'] == pytest.approx(old_diag['shape']['X']['theta'], abs=1e-3)
    assert runs[0]['theta_y'] == pytest.approx(old_diag['shape']['Y']['theta'], abs=1e-3)
    assert runs[0]['theta_x_t'] > 10


def test_llm_report_section(old_result, monkeypatch):
    from core.llm_report import build_llm_report
    result = dict(old_result)
    md = build_llm_report([result], SPEC_LIM, SPEC_DEV)
    assert md.index('## 비전 보정 로그 대조') < md.index('## 트러블슈팅 진단 (Diagnosis)') < \
        md.index('## 회차 변경 이력')
    section = md[md.index('## 트러블슈팅 진단'):md.index('## 회차 변경 이력')]
    assert '잠정' in section and '| X 라인 | +0.000 / -25.000 |' in section
    # 픽스처에는 Debug/SamplePositioning이 없다 → 비전 로그 대조 전이라 스캐너 Y는 주의까지 (H1)
    assert '| XY 스캐너 Y 구동 | xy_scanner_y | warn |' in section and NO_POSLOG_TEXT in section
    assert '1. X 라인 레시피 Y Offset을 줄이세요 (예: -25 → -5 µm' in section
    assert '| -5.0 | -7.68 | +1.45 |' in section and '권장' in section
    assert 'Transient (파생): warn' in section
    assert md == build_llm_report([result], SPEC_LIM, SPEC_DEV)          # 같은 입력 → 같은 문자열
    # 화면에서 읽어 담은 Log가 있으면 다시 읽지 않는다
    store_logs(result, read_run_logs(result))
    monkeypatch.setattr(xy_diagnosis, 'read_run_logs', lambda *a, **k: pytest.fail('담긴 Log를 다시 읽었다'))
    assert build_llm_report([result], SPEC_LIM, SPEC_DEV) == md


def test_llm_section_survives_a_failing_workflow(old_result, monkeypatch):
    from core.llm_report import build_llm_report

    def boom(*_a, **_k):
        raise RuntimeError('깨진 Log')
    monkeypatch.setattr(xy_diagnosis, 'build_diagnosis', boom)
    md = build_llm_report([old_result], SPEC_LIM, SPEC_DEV)
    assert '- 상태: 진단 실패 (RuntimeError: 깨진 Log)' in md
    assert '## LLM 분석 요청' in md


# ── 화면 ─────────────────────────────────────────────────────────

@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_limits=SPEC_LIM, spec_deviation=SPEC_DEV))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win.deleteLater()
    app.processEvents()


def _show_tab(window, name='Diagnosis (트러블슈팅)'):
    show_expert_tab(window, name)          # 전문 분석 창의 Advanced 탭


def _counting_reads(monkeypatch):
    from ui.controllers import diagnosis_controller
    calls = []
    real = diagnosis_controller.read_run_logs

    def counted(result, *args, **kwargs):
        calls.append(result.get('round'))
        return real(result, *args, **kwargs)
    monkeypatch.setattr(diagnosis_controller, 'read_run_logs', counted)
    return calls


def _wait_for(predicate):
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


def test_tab_is_next_to_positioning_log(window):
    """전문 분석 창 왼쪽 메뉴: Positioning Log 바로 다음이 진단 쪽들 (Diagnosis (트러블슈팅) 묶음)."""
    expert = window._expert_window
    names = expert.page_names()
    first = names.index('Positioning Log') + 1
    assert all(expert.section_of(n) == 'Diagnosis (트러블슈팅)' for n in names[first:first + 4])
    assert expert.section_of(names[first + 4]) is None


def test_tab_reads_lazily_only_when_visible(window, monkeypatch):
    calls = _counting_reads(monkeypatch)
    result = load(FIXTURES / 'old')
    window._update_diagnosis(result)
    assert calls == [] and 'xy_logs' not in result
    assert window.diag_label.text() == '이 회차의 Log는 이 탭을 열 때 읽습니다'
    assert window._xy_diagnosis is None
    _show_tab(window)
    _wait_for(lambda: 'xy_logs' in result)
    assert len(calls) == 1
    d = window._xy_diagnosis
    assert d['schema'] == SCHEMA and d['rotation']['status'] == 'fail'
    assert window.diag_offset_table.rowCount() == 2
    assert window.diag_offset_table.item(0, 2).text() == '-25.000'
    assert window.diag_whatif_table.rowCount() == 6
    assert window.diag_whatif_table.item(0, 0).text() == '현재'
    assert window.diag_whatif_table.item(3, 0).text() == '권장'
    assert window.diag_parts_table.rowCount() == len(PART_IDS)
    assert '잠정 규격' in window._diag_chips['rotation'].text()
    assert '1. X 라인 레시피 Y Offset을 줄이세요' in window.diag_actions_label.text()
    assert window.chart_widgets['Diagnosis Drift']._canvas is not None
    # 다른 탭으로 갔다가 돌아와도, 같은 결과를 다시 보여도 다시 읽지 않는다
    _show_tab(window, 'Positioning Log')
    window._update_diagnosis(result)
    _show_tab(window)
    window._update_diagnosis(dict(result))          # run_view 같은 사본 (같은 회차)
    assert len(calls) == 1


def test_hidden_tab_does_not_compute_or_read(window, monkeypatch):
    calls = _counting_reads(monkeypatch)
    result = load(FIXTURES / 'old')
    store_logs(result, read_run_logs(result))       # 이미 읽은 회차
    built = []
    real = window._compute_diagnosis
    monkeypatch.setattr(window, '_compute_diagnosis', lambda *a: (built.append(1), real(*a)))
    window._update_diagnosis(result)
    assert calls == [] and built == []
    assert window.diag_label.text() == '이 탭을 열면 진단을 계산합니다'
    _show_tab(window)
    assert calls == [] and built == [1] and window._xy_diagnosis['source']['lots'] == 3
    window._update_diagnosis(result)                 # Die 필터 갱신처럼 같은 결과: 다시 계산하지 않음
    assert built == [1]


def test_stale_generation_read_is_dropped(window, monkeypatch):
    from core.positioning_log import run_key
    result = load(FIXTURES / 'old')
    old = window._data_generation
    window._data_generation = old + 1           # 그사이 다시 스캔했다
    window._diag_result = result
    window._diag_reading = (run_key(result), old, 999)     # 지난 세대 읽기가 아직 돈다
    _show_tab(window)                                       # 새 세대 읽기는 막히지 않는다
    assert window._diag_reading is not None and window._diag_reading[1] == old + 1
    window._on_diagnosis_logs(result, {'status': 'no_logs', 'lots': []}, (old, 999))   # 늦은 결과
    assert 'xy_logs' not in result or result['xy_logs']['status'] != 'no_logs'
    _wait_for(lambda: 'xy_logs' in result)
    assert result['xy_logs']['status'] == 'ok'
    assert window._xy_diagnosis['scanner_offsets']['X_line']['n'] == 18


def test_finished_read_is_stored_on_every_copy_of_the_run(window):
    result = load(FIXTURES / 'old')
    copy = dict(result)
    other = dict(result, round='other')
    window.recipe_results = [dict(result, run_results=[result, other])]
    window._diag_result = copy
    logs = read_run_logs(result)
    window._on_diagnosis_logs(result, logs, (window._data_generation, 1))
    assert copy['xy_logs'] is logs and window.recipe_results[0]['run_results'][0]['xy_logs'] is logs
    assert 'xy_logs' not in other
    assert window._xy_diagnosis is not None


def test_save_json_button(window, tmp_path, monkeypatch):
    from ui.controllers import diagnosis_controller
    result = load(FIXTURES / 'old')
    store_logs(result, read_run_logs(result))
    window._update_diagnosis(result)
    _show_tab(window)
    assert window.diag_save_btn.isEnabled()
    target = tmp_path / 'diag.json'
    monkeypatch.setattr(diagnosis_controller.QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(target), '')))
    window._save_diagnosis_json()
    saved = json.loads(target.read_text(encoding='utf-8'))
    assert saved == window._xy_diagnosis and saved['schema'] == SCHEMA


def test_tab_without_data_or_logs(window):
    window._update_diagnosis({'raw_data': []})
    assert window.diag_label.text() == '분석할 데이터가 없음' and window._xy_diagnosis is None
    _show_tab(window)
    result = dict(load(FIXTURES / 'old'), round_path='')
    window._update_diagnosis(result)
    assert window._xy_diagnosis is not None
    assert window._xy_diagnosis['scanner_offsets']['X_line']['applied'] is None


# ── 스캐너 회전 시험 (워크스트림 C의 결과, set_rotation_test) ─────────────

ROTATION_TEST = {'date': '2026-11-12', 'stage': '출하 검사', 'axis': 'Y', 'loop': 'closed',
                 'locations': [{'name': 'A', 'stage_x_mm': -120, 'gain': 1.0012, 'perp_mrad': 13.1, 'n': 15},
                               {'name': 'B', 'stage_x_mm': 120, 'gain': 1.0270, 'perp_mrad': 13.4, 'n': 15}],
                 'distance_mm': 240, 'kappa': 0.107, 'status': 'fail', 'reasons': ['κ 0.107 > 0.10']}


@pytest.mark.parametrize('change,expected', [
    ({}, 'fail'),                                   # 폐루프·Y·fail → 직접 증거
    ({'status': 'warn'}, 'warn'),
    ({'status': 'na'}, 'ok'),                       # na는 무시
    ({'loop': 'open'}, 'ok'),                       # 개루프는 직접 증거가 아니다
    ({'axis': 'X'}, 'ok'),
])
def test_rotation_test_raises_scanner_y(change, expected):
    shapes = {'X': {'theta': 0.3, 'theta_t': 1.0}, 'Y': {'theta': 0.0, 'theta_t': 0.1}}
    parts, _ = xy_diagnosis._parts(True, shapes, {'scanner_y': -5.0, 'y_line': {}}, None, {'status': 'ok'},
                                   {'status': 'ok'}, DEFAULT_ROTATION_SPEC, dict(ROTATION_TEST, **change))
    got = {p['id']: p for p in parts}
    assert got['xy_scanner_y']['verdict'] == expected
    assert ('직접 증거' in got['xy_scanner_y']['reason']) == (expected != 'ok')
    assert got['chuck']['verdict'] == ('warn' if expected != 'ok' else 'ok')


def test_rotation_test_in_json_parts_and_actions(new_result):
    rt = json.loads(json.dumps(ROTATION_TEST))
    diag = build_diagnosis(new_result, read_run_logs(new_result), SPEC_DEV, rotation_test=rt)
    rt['kappa'] = 9.9                                # 사본을 담는다
    d = json.loads(diag.to_json())
    assert d['rotation_test'] == ROTATION_TEST
    scanner = next(p for p in d['parts'] if p['id'] == 'xy_scanner_y')
    assert scanner['verdict'] == 'fail'               # θx 0.95(주의)를 시험 결과가 올린다
    assert '스캐너 회전 시험 κ 0.107 µrad/µm — fail (출하 검사, 2026-11-12, closed·Y, 잠정 규격)' in scanner['reason']
    assert any(a.startswith('스캐너 회전 시험 κ 0.107') and '재시험' in a for a in d['actions'])
    assert not any(a.startswith('폐루프 2지점 스윕으로') for a in d['actions'])
    assert any('스캐너 회전 시험' in r for r in d['rotation']['reasons'])


def test_llm_section_includes_rotation_test(old_result):
    from core.llm_report import build_llm_report
    md = build_llm_report([old_result], SPEC_LIM, SPEC_DEV, rotation_test=ROTATION_TEST)
    section = md[md.index('## 트러블슈팅 진단'):md.index('## 회차 변경 이력')]
    assert '- 스캐너 회전 시험 κ 0.107 µrad/µm — fail (출하 검사' in section
    assert '스캐너 회전 시험 결과 없음' in build_llm_report([old_result], SPEC_LIM, SPEC_DEV)


def test_set_rotation_test_hook_updates_diagnosis_and_json(window, tmp_path, monkeypatch):
    from ui.controllers import diagnosis_controller
    new = load(FIXTURES / 'new')
    store_logs(new, read_run_logs(new))
    window._update_diagnosis(new)
    _show_tab(window)
    before = {p['id']: p['verdict'] for p in window._xy_diagnosis['parts']}
    assert before['xy_scanner_y'] == 'warn' and window._xy_diagnosis['rotation_test'] is None
    assert '결과 없음' in window._diag_chips['rotation_test'].text()
    sent = json.loads(json.dumps(ROTATION_TEST))
    window.set_rotation_test(sent)
    sent['kappa'] = 9.9                                # 대화상자가 바꿔도 창의 사본은 그대로
    assert window._rotation_test_result == ROTATION_TEST
    assert window._xy_diagnosis['rotation_test'] == ROTATION_TEST
    after = {p['id']: p['verdict'] for p in window._xy_diagnosis['parts']}
    assert after['xy_scanner_y'] == 'fail'
    chip = window._diag_chips['rotation_test'].text()
    assert '스캐너 회전 시험 κ 0.107' in chip and '출하 검사' in chip and '2026-11-12' in chip
    assert '잠정 규격' in chip
    target = tmp_path / 'diag.json'
    monkeypatch.setattr(diagnosis_controller.QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(target), '')))
    window._save_diagnosis_json()
    assert json.loads(target.read_text(encoding='utf-8'))['rotation_test'] == ROTATION_TEST
    # 다른 장비(여기서는 다른 데이터 폴더 — Log에 Tool ID가 없음)의 진단에는 붙이지 않는다 (M4)
    old = load(FIXTURES / 'old')
    store_logs(old, read_run_logs(old))
    window._update_diagnosis(old)
    assert window._rotation_test_result == ROTATION_TEST            # 회차를 바꿔도 지우지 않는다
    assert window._xy_diagnosis['source']['sample'] == 'Sample22'
    assert window._xy_diagnosis['rotation_test'] is None
    assert window._xy_diagnosis['source']['rotation_test_note'] == OTHER_TOOL_TEXT
    assert OTHER_TOOL_TEXT in window._diag_chips['rotation_test'].text()
    assert not any('직접 증거' in p['reason'] for p in window._xy_diagnosis['parts'])
    # 같은 장비의 다른 회차에는 붙는다
    window._update_diagnosis(dict(new, round='2'))
    assert window._xy_diagnosis['rotation_test'] == ROTATION_TEST


def test_rotation_test_binds_to_the_scanned_folder(window):
    """Log에 Tool ID가 없으면 연 폴더가 장비다: 같은 폴더 아래 회차들에는 같은 시험 결과가 붙는다."""
    window._scanned_folder = str(FIXTURES)
    new, old = load(FIXTURES / 'new'), load(FIXTURES / 'old')
    for result in (new, old):
        store_logs(result, read_run_logs(result))
    window._update_diagnosis(new)
    _show_tab(window)
    window.set_rotation_test(ROTATION_TEST)
    assert window._rotation_test_tool == window._xy_diagnosis['source']['tool_id']
    window._update_diagnosis(old)
    assert window._xy_diagnosis['rotation_test'] == ROTATION_TEST


def test_set_rotation_test_before_any_diagnosis(window):
    window.set_rotation_test(dict(ROTATION_TEST, status='ok'))
    assert window._xy_diagnosis is None
    result = load(FIXTURES / 'old')
    store_logs(result, read_run_logs(result))
    window._update_diagnosis(result)
    _show_tab(window)
    assert window._xy_diagnosis['rotation_test']['status'] == 'ok'
    assert any('규격 이내' in a for a in window._xy_diagnosis['actions'])
    window.set_rotation_test(None)
    assert window._rotation_test_result is None and window._xy_diagnosis['rotation_test'] is None


# ── 리뷰 반영 (574cbc3: HIGH 2·MEDIUM 5·LOW) ────────────────────────────

POS_FIXTURES = Path(__file__).parent / 'fixtures' / 'positioning_log'


def _lot_with_positioning_and_log(folder: Path) -> Path:
    """비전 보정 로그 픽스처 Lot(θx +5.28, 비전 정렬 이후) + 스캐너 Y −27.8 µm Log (구 Lot의 것)."""
    import shutil
    lot = folder / '46LKD105SJE61'
    (lot / 'Debug' / 'SamplePositioning').mkdir(parents=True)
    (lot / 'Debug' / 'TipPositioning').mkdir(parents=True)
    (lot / 'Log').mkdir()
    for axis in ('X', 'Y'):
        shutil.copy(POS_FIXTURES / f'lot61_{axis}_UL.csv', lot / f'SAMPLE19_{axis}_UL.csv')
    shutil.copy(POS_FIXTURES / 'Sample19_SamplePositioning.txt',
                lot / 'Debug' / 'SamplePositioning' / 'Sample19_SamplePositioning.txt')
    shutil.copy(POS_FIXTURES / 'Sample19_TipPositioning.txt',
                lot / 'Debug' / 'TipPositioning' / 'Sample19_TipPositioning.txt')
    shutil.copy(FIXTURES / 'old' / 'Sample2202' / 'Log' / 'Sample22_Log.csv', lot / 'Log' / 'Sample19_Log.csv')
    return folder


def test_scanner_fail_needs_positioning_evidence(tmp_path):
    """θx 유의 + 구동 ≥ 5 µm + Positioning Log '비전 정렬 이후'일 때만 fail (H1)."""
    from core.positioning_log import analyze_result
    result = load(_lot_with_positioning_and_log(tmp_path / 'vp'))
    logs = read_run_logs(result)
    without = build_diagnosis(result, logs, SPEC_DEV).to_dict()
    scanner = next(p for p in without['parts'] if p['id'] == 'xy_scanner_y')
    assert scanner['verdict'] == 'warn' and NO_POSLOG_TEXT in scanner['reason']
    poslog = analyze_result(result)
    assert poslog['status'] == 'ok'
    with_log = build_diagnosis(result, logs, SPEC_DEV, poslog=poslog).to_dict()
    parts = {p['id']: p for p in with_log['parts']}
    assert parts['xy_scanner_y']['verdict'] == 'fail'
    assert '비전 정렬 이후' in parts['xy_scanner_y']['reason']
    # Sy는 비전 정밀 정렬 단계(Fine Match)에도 있다 → 비전 주의, 스테이지는 성분 없음
    assert parts['vision']['verdict'] == 'warn' and 'Sy' in parts['vision']['reason']
    assert parts['xy_stage']['verdict'] == 'ok'
    assert with_log['positioning_log']['judged'] is True


def test_small_drive_gives_no_reduce_action(old_result):
    """H1 재현: 스캐너 Y −0.4 µm, θx 유의 → 스캐너 원인 단정 안 함, '줄이세요' 안 냄."""
    logs = read_run_logs(old_result)
    small = json.loads(json.dumps(logs, default=str))
    for lot in small['lots']:
        lot['start'] = None
        lot['lines'] = [dict(l, applied=(l['applied'][0], -0.4 if l['line'] == 'X' else l['applied'][1]))
                        for l in lot['lines']]
    d = build_diagnosis(old_result, small, SPEC_DEV).to_dict()
    scanner = next(p for p in d['parts'] if p['id'] == 'xy_scanner_y')
    assert scanner['verdict'] == 'na' and xy_diagnosis.SMALL_DRIVE_TEXT in scanner['reason']
    assert not any('Y Offset을 줄이세요' in a or '적용값' in a and '줄이세요' in a and 'Y 라인' not in a
                   for a in d['actions'])
    assert d['whatif']['rows'] == []                     # |구동| < 2 µm: κ_eff 없음


def test_network_path_error_reported_as_filenotfound_is_error(old_result, monkeypatch):
    """WinError 53·67은 FileNotFoundError로 온다: Lot 폴더도 안 보이면 'Log 없음'이 아니라 오류 (M3)."""
    from core import csv_loader
    monkeypatch.setattr(csv_loader, 'FOLDER_RETRY_DELAY', 0)

    def gone(path, *args):
        raise FileNotFoundError(2, '네트워크 경로를 찾을 수 없습니다 (WinError 53)', path)
    monkeypatch.setattr(xy_diagnosis, 'list_dir', gone)
    result = dict(old_result)
    logs = read_run_logs(result, workers=1)
    assert logs['status'] == 'error' and {l['status'] for l in logs['lots']} == {'error'}
    assert 'Lot 폴더를 읽지 못함' in logs['lots'][0]['error']
    assert store_logs(result, logs) is False and 'xy_logs' not in result


def test_broken_log_marks_only_that_lot(old_result, monkeypatch):
    """해석 오류(csv.Error·ValueError·UnicodeError)는 그 Lot만 오류, 다른 Lot은 그대로 (M5)."""
    real = xy_diagnosis.parse_scanner_offsets
    calls = []

    def flaky(text):
        calls.append(1)
        if len(calls) == 2:
            raise UnicodeError('깨진 바이트')
        return real(text)
    monkeypatch.setattr(xy_diagnosis, 'parse_scanner_offsets', flaky)
    logs = read_run_logs(old_result, workers=1)
    assert [l['status'] for l in logs['lots']] == ['ok', 'error', 'ok']
    assert logs['lots'][1]['error'].startswith('Log 해석 실패 (UnicodeError')
    assert logs['status'] == 'error'


def test_empty_and_utf16_logs(tmp_path):
    """0바이트 Log는 오류(담지 않음), UTF-16 BOM Log는 읽는다 (L10)."""
    import shutil
    for name, data in (('empty', b''),
                       ('utf16', read_text(FIXTURES / 'old' / 'Sample2202' / 'Log' / 'Sample22_Log.csv')
                        .encode('utf-16'))):
        lot = tmp_path / name / 'Sample2202'
        shutil.copytree(FIXTURES / 'old' / 'Sample2202', lot)
        (lot / 'Log' / 'Sample22_Log.csv').write_bytes(data)
        logs = read_run_logs(load(tmp_path / name))
        if name == 'empty':
            assert logs['status'] == 'error' and '빈 Log 파일' in logs['lots'][0]['error']
        else:
            assert logs['status'] == 'ok' and len(logs['lots'][0]['lines']) == 12


def test_retried_scan_params_and_fine_reset():
    """같은 측정의 다시 한 'Set scan params'는 한 번만, 미세 보정은 X·Y 한 쌍에만 (L9). Tool ID (M4)."""
    text = '\n'.join([
        'Time,Parent Job,Current Job,Step,Param ID,Param Value,Tack Time',
        't0,SampleProcessJob,MappingJob,Set Mapping,Link Tool,false,0',
        ',,,,Tool Type ID,NX-Wafer',
        ',,,,Tool ID,NXW-0124',
        't1,ImagePointProcessJob,ZPositioningJob,Sample pattern align - fine,Pattern ID - Sample,,1',
        ',,,,X Offset - Fine,0.1', ',,,,Y Offset - Fine,-0.3',
        't2,ImagePointProcessJob,ImageJob,STARTED,-,-',
        't3,ImagePointProcessJob,ImageJob,Set scan params,Scan Direction,X Backward,1',
        ',,,,X Offset,0.5', ',,,,Y Offset,-27.0',
        't4,ImagePointProcessJob,ImageJob,Set scan params,Scan Direction,X Backward,1',
        ',,,,X Offset,0.6', ',,,,Y Offset,-28.0',
        't5,ImagePointProcessJob,ImageJob,STARTED,-,-',
        't6,ImagePointProcessJob,ImageJob,Set scan params,Scan Direction,Y Forward,1',
        ',,,,X Offset,30.6', ',,,,Y Offset,-1.0',
        't7,ImagePointProcessJob,ImageJob,STARTED,-,-',
        't8,ImagePointProcessJob,ImageJob,Set scan params,Scan Direction,X Backward,1',
        ',,,,X Offset,0.7', ',,,,Y Offset,-28.1'])
    parsed = parse_scanner_offsets(text)
    lines = parsed['lines']
    assert [(l['line'], l['applied']) for l in lines] == [('X', (0.6, -28.0)), ('Y', (30.6, -1.0)),
                                                          ('X', (0.7, -28.1))]
    assert lines[0]['fine'] == lines[1]['fine'] == (0.1, -0.3)
    assert lines[2]['fine'] is None                    # 다음 Site에 정밀 정렬이 없었다
    assert parsed['tool_id'] == 'NXW-0124' and parsed['tool_type_id'] == 'NX-Wafer'


def test_rotation_test_only_for_the_same_tool(new_result):
    """시험 결과는 같은 장비(tool_id)의 진단에만 붙는다 (M4)."""
    logs = read_run_logs(new_result)
    tool = xy_diagnosis.tool_identity(new_result, logs)[0]
    same = build_diagnosis(new_result, logs, SPEC_DEV, rotation_test=ROTATION_TEST,
                           rotation_test_tool=tool).to_dict()
    assert same['rotation_test'] == ROTATION_TEST and same['source']['tool_id'] == tool
    assert same['source']['tool_id_basis'] == 'root'
    other = build_diagnosis(new_result, logs, SPEC_DEV, rotation_test=ROTATION_TEST,
                            rotation_test_tool='NXW-9999').to_dict()
    assert other['rotation_test'] is None and other['source']['rotation_test_note'] == OTHER_TOOL_TEXT
    assert any(OTHER_TOOL_TEXT in w for w in other['warnings'])
    assert not any('직접 증거' in p['reason'] for p in other['parts'])
    from core.llm_report import build_llm_report
    md = build_llm_report([new_result], SPEC_LIM, SPEC_DEV, rotation_test=ROTATION_TEST,
                          rotation_test_tool='NXW-9999')
    section = md[md.index('## 트러블슈팅 진단'):md.index('## 회차 변경 이력')]
    assert f'- 스캐너 회전 시험: {OTHER_TOOL_TEXT}' in section and 'κ 0.107' not in section


def test_drift_and_transient_need_three_lots_and_significance(new_result):
    """Lot 1개면 drift·transient는 판정하지 않는다 (L1)."""
    first = dict(new_result, raw_data=[r for r in new_result['raw_data'] if r['lot_name'] == 'Sample1902'])
    d = build_diagnosis(first, read_run_logs(first), SPEC_DEV).to_dict()
    assert d['drift']['status'] == 'ok'
    assert any(r.startswith('참고: 첫 Lot') for r in d['drift']['reasons'])
    assert d['transient']['status'] == 'ok'
    assert d['transient']['X']['t']['die2'] is None                # Lot 1개: SE 없음


def test_kappa_two_point_from_cached_runs(old_result, new_result, monkeypatch):
    """두 회차의 스캐너 Y가 5 µm 이상 다르면 θx 대 스캐너 Y 기울기·절편 (참고, Log 더 안 읽음)."""
    old, new = dict(old_result, round='old'), dict(new_result, round='new')
    store_logs(old, read_run_logs(old))
    store_logs(new, read_run_logs(new))
    monkeypatch.setattr(xy_diagnosis, 'read_lot_log', lambda *a, **k: pytest.fail('Log를 더 읽었다'))
    shown = dict(new, run_results=[old, new])
    two = build_diagnosis(shown, None, SPEC_DEV).to_dict()['rotation']['kappa_two_point']
    assert two['kappa'] == pytest.approx(-0.218, abs=0.002)
    assert two['intercept'] == pytest.approx(-0.82, abs=0.03)
    assert {p['run'] for p in two['points']} == {'old', 'new'}
    assert build_diagnosis(new, None, SPEC_DEV).to_dict()['rotation']['kappa_two_point'] is None


def test_clean_numpy_and_dates_and_rotation_spec_coercion():
    import numpy as np
    from datetime import date, datetime
    diag = XYDiagnosis(source={'n': np.int64(3), 'v': np.float64(1.5), 'bad': np.float64('nan'),
                               'when': datetime(2026, 11, 12, 11, 25), 'day': date(2026, 11, 12),
                               'set': {1}, 'obj': Path('a')})
    loaded = json.loads(diag.to_json())['source']
    assert loaded == {'n': 3, 'v': 1.5, 'bad': None, 'when': '2026-11-12T11:25:00', 'day': '2026-11-12',
                      'set': [1], 'obj': 'a'}
    spec = rotation_spec({'rotation_spec': {'theta_max': '2', 'theta_warn': 5, 'kappa_max': 'x',
                                            'kappa_warn': -1, 'offset_max': None, 'provisional': 'no'}})
    assert spec == {'theta_max': 2.0, 'theta_warn': 2.0, 'kappa_max': 0.1, 'kappa_warn': 0.07,
                    'offset_max': 10.0, 'provisional': True}


def test_diagnosis_tab_starts_positioning_read_and_reaches_fail(window, tmp_path):
    """진단 탭이 보이면 Positioning Log 분석도 읽고, 끝나면 다시 계산해 스캐너 Y가 근거와 함께 fail."""
    result = load(_lot_with_positioning_and_log(tmp_path / 'vp'))
    window._update_positioning_log(result)
    window._update_diagnosis(result)
    assert 'positioning_log' not in result and 'xy_logs' not in result      # 숨은 탭: 읽지 않음
    _show_tab(window)
    _wait_for(lambda: 'positioning_log' in result and 'xy_logs' in result
              and (window._xy_diagnosis or {}).get('positioning_log') is not None)
    parts = {p['id']: p['verdict'] for p in window._xy_diagnosis['parts']}
    assert parts['xy_scanner_y'] == 'fail' and parts['vision'] == 'warn' and parts['xy_stage'] == 'ok'
    assert window._xy_diagnosis['positioning_log']['judged'] is True


def test_close_cancels_diagnosis_reads(window):
    from core.csv_loader import ScanSession
    session = ScanSession()
    window._diag_sessions = {1: session}
    window._cancel_diagnosis_reads()
    assert session.cancelled
    logs = read_run_logs(load(FIXTURES / 'old'), session, workers=1)
    assert logs['status'] == 'error' and all(l['error'] == '중지됨' for l in logs['lots'])


# ── 통합: 진단 탭 버튼 → 3D 보고서 · 회전 시험 (워크스트림 A+B+C) ──────────────

def test_report_button_builds_html_from_real_diagnosis(window, tmp_path, monkeypatch):
    from ui.dialogs import troubleshoot_launcher
    from core import troubleshoot_report
    result = load(FIXTURES / 'old')
    store_logs(result, read_run_logs(result))
    assert not window.diag_report_btn.isEnabled()          # 진단 전에는 끔
    window._update_diagnosis(result)
    _show_tab(window)
    assert window.diag_report_btn.isEnabled()
    opened = []
    monkeypatch.setattr(troubleshoot_launcher.QDesktopServices, 'openUrl',
                        staticmethod(lambda url: opened.append(url.toLocalFile()) or True))
    real_write = troubleshoot_report.write_report
    monkeypatch.setattr(troubleshoot_launcher, 'write_report',
                        lambda diag, out_dir=None: real_write(diag, out_dir=str(tmp_path)), raising=False)
    monkeypatch.setattr(troubleshoot_report, 'write_report',
                        lambda diag, out_dir=None: real_write(diag, out_dir=str(tmp_path)))
    window._open_diagnosis_report()
    assert len(opened) == 1 and opened[0].endswith('.html')
    html = open(opened[0], encoding='utf-8').read()
    assert '/*__PASS_DIAG__*/' not in html and '"pass.xy.diagnosis/1"' in html


def test_rotation_result_reaches_diagnosis_and_json(window):
    result = load(FIXTURES / 'old')
    store_logs(result, read_run_logs(result))
    window._update_diagnosis(result)
    _show_tab(window)
    tool = window._xy_diagnosis['source'].get('tool_id')
    rt = {'date': '2026-11-13', 'stage': '출하 검사', 'axis': 'Y', 'loop': 'closed',
          'locations': [{'name': 'A', 'stage_x_mm': -120, 'gain': 1.0, 'perp_mrad': 13.0, 'n': 15},
                        {'name': 'B', 'stage_x_mm': 120, 'gain': 1.0256, 'perp_mrad': 13.0, 'n': 15}],
          'distance_mm': 240, 'kappa': 0.107, 'status': 'fail', 'reasons': ['시험']}
    window.set_rotation_test(rt)
    d = window._xy_diagnosis
    assert d['rotation_test'] and d['rotation_test']['kappa'] == 0.107
    assert d['source'].get('tool_id') == tool
    part = {p['id']: p for p in d['parts']}['xy_scanner_y']
    assert part['verdict'] == 'fail'
    json.loads(window._diag_obj.to_json())                  # 저장 JSON에도 들어간다
