"""
회귀 테스트 — 2026-05-30/31 디버그 감사에서 수정한 결함들을 고정한다.

각 테스트는 수정 전이라면 실패하고, 수정 후 통과하도록 작성되었다.
헤드리스(Qt 불필요) 코어 로직만 대상으로 한다. UI/스레딩 수정(#1)은
별도 수동/통합 테스트 영역이라 여기서는 다루지 않는다.
"""
import math

import pytest

np = pytest.importorskip("numpy")

from core.statistics import compute_cpk
from core.die_analysis import (
    extract_die_number,
    compute_affine_transform,
    compute_deviation_matrix,
    evaluate_deviation_pass,
)
from core.recipe_scanner import scan_recipes
from core.settings import parse_geometry_string


# ──────────────────────────────────────────────────────────────────────────
# #5 compute_cpk — '계산 불가'를 NaN으로 반환(실제 0.0과 구분)
# ──────────────────────────────────────────────────────────────────────────
class TestComputeCpk:
    def test_zero_stdev_returns_nan(self):
        """σ=0(단일 샘플/무변동)은 계산 불가 → NaN (이전엔 0.0이라 오해 소지)."""
        assert math.isnan(compute_cpk(100.0, 0.0, lsl=-5000, usl=5000))

    def test_no_limits_returns_nan(self):
        assert math.isnan(compute_cpk(100.0, 5.0, lsl=None, usl=None))

    def test_real_value_computed(self):
        r = compute_cpk(0.0, 1000.0, lsl=-5000, usl=5000)
        assert not math.isnan(r)
        assert r == pytest.approx(5000 / 3000, abs=1e-3)

    def test_real_zero_cpk_preserved_not_nan(self):
        """평균이 USL에 정확히 위치 → Cpk 0.0은 '실제 값'이며 NaN이 아니어야 한다."""
        r = compute_cpk(5000.0, 1000.0, lsl=-5000, usl=5000)
        assert r == 0.0
        assert not math.isnan(r)


# ──────────────────────────────────────────────────────────────────────────
# #6 extract_die_number — site 00 → None (Die0 유령 die 방지)
# ──────────────────────────────────────────────────────────────────────────
class TestExtractDieNumber:
    def test_valid_sites(self):
        assert extract_die_number('0001_X000_Y000') == 0
        assert extract_die_number('0002_X000_Y000') == 1
        assert extract_die_number('0022_X000_Y000') == 21

    def test_site_zero_returns_none(self):
        # 캡처되는 두 자리가 '00' → 0-1 = -1 → None
        assert extract_die_number('0000_X000_Y000') is None
        assert extract_die_number('0100_X000_Y000') is None

    def test_non_match_returns_none(self):
        assert extract_die_number('garbage') is None
        assert extract_die_number('') is None

    def test_deviation_matrix_excludes_die0_ghost(self):
        data = [
            {'site_id': '0000_X000_Y000', 'method': 'X', 'value': 100.0, 'valid': True, 'lot_name': 'L1'},
            {'site_id': '0002_X000_Y000', 'method': 'X', 'value': 110.0, 'valid': True, 'lot_name': 'L1'},
            {'site_id': '0003_X000_Y000', 'method': 'X', 'value': 120.0, 'valid': True, 'lot_name': 'L1'},
        ]
        res = compute_deviation_matrix(data, 'X')
        assert 'Die0' not in res['die_labels']
        assert set(res['die_labels']) == {'Die2', 'Die3'}


# ──────────────────────────────────────────────────────────────────────────
# #4 compute_affine_transform — 공선/데이터 부족 → degenerate (무경고 허위값 방지)
# ──────────────────────────────────────────────────────────────────────────
class TestAffineDegeneracy:
    def test_collinear_dies_is_degenerate(self):
        # DIE_POSITIONS 인덱스 0~3 (Die1~Die4) = (0,0),(2,0),(4,0),(6,0) → 모두 y=0 (공선)
        dies = ['Die1', 'Die2', 'Die3', 'Die4']
        x_stats = [{'die': d, 'avg': float(i)} for i, d in enumerate(dies)]
        y_stats = [{'die': d, 'avg': float(i) * 0.5} for i, d in enumerate(dies)]
        af = compute_affine_transform(x_stats, y_stats)
        assert af['degenerate'] is True
        assert af['theta_deg'] == 0
        assert af['sx_ppm'] == 0

    def test_full_rank_dies_not_degenerate(self):
        # Die1=(0,0), Die5=(2,2), Die6=(4,4), Die7=(0,2) → 2D 분포(비공선)
        dies = ['Die1', 'Die5', 'Die6', 'Die7']
        x_stats = [{'die': d, 'avg': v} for d, v in zip(dies, [0.1, 0.2, 0.3, 0.15])]
        y_stats = [{'die': d, 'avg': v} for d, v in zip(dies, [0.05, 0.1, 0.2, 0.1])]
        af = compute_affine_transform(x_stats, y_stats)
        assert af['degenerate'] is False
        assert af['n_dies'] == 4

    def test_too_few_dies_is_degenerate(self):
        af = compute_affine_transform([{'die': 'Die1', 'avg': 1.0}],
                                      [{'die': 'Die1', 'avg': 1.0}])
        assert af['degenerate'] is True
        assert af['n_dies'] == 0


# ──────────────────────────────────────────────────────────────────────────
# 리팩터링: evaluate_deviation_pass — Spec PASS/FAIL 판정 단일 출처
# (#2의 근본 원인이던 step/card 가드 중복을 한 함수로 통합)
# ──────────────────────────────────────────────────────────────────────────
class TestEvaluateDeviationPass:
    _LABELS = {'die_labels': ['Die2', 'Die3'], 'repeat_labels': ['Lot1', 'Lot2']}
    _DEV = {**_LABELS, 'overall_range': 1.0, 'overall_stddev': 0.1}

    def test_within_spec_is_pass(self):
        assert evaluate_deviation_pass({'count': 5}, self._DEV, 2.0, 0.2) is True

    def test_range_exceeds_is_fail(self):
        dev = {**self._LABELS, 'overall_range': 3.0, 'overall_stddev': 0.1}
        assert evaluate_deviation_pass({'count': 5}, dev, 2.0, 0.2) is False

    def test_stddev_exceeds_is_fail(self):
        dev = {**self._LABELS, 'overall_range': 1.0, 'overall_stddev': 0.5}
        assert evaluate_deviation_pass({'count': 5}, dev, 2.0, 0.2) is False

    def test_boundary_is_inclusive_pass(self):
        dev = {**self._LABELS, 'overall_range': 2.0, 'overall_stddev': 0.2}
        assert evaluate_deviation_pass({'count': 5}, dev, 2.0, 0.2) is True

    def test_no_data_is_none(self):
        assert evaluate_deviation_pass({'count': 0}, self._DEV, 2.0, 0.2) is None

    def test_missing_axis_zero_deviation_is_none_not_spurious_pass(self):
        # 한 축 미측정 → compute_deviation_matrix가 overall_range=0/stddev=0을 돌려줘
        # 0<=spec로 허위 PASS가 날 수 있는데, count==0 게이트로 None이 되어야 한다.
        empty_dev = {'overall_range': 0.0, 'overall_stddev': 0.0}
        assert evaluate_deviation_pass({'count': 0}, empty_dev, 2.0, 0.2) is None

    def test_missing_spec_stddev_is_none_not_typeerror(self):
        # #2 근본 원인: spec_stddev=None이어도 float<=None TypeError 없이 None
        assert evaluate_deviation_pass({'count': 5}, self._DEV, 2.0, None) is None

    def test_missing_spec_range_is_none(self):
        assert evaluate_deviation_pass({'count': 5}, self._DEV, None, 0.2) is None

    def test_exported_from_core_package(self):
        # 두 컨트롤러가 `from core import evaluate_deviation_pass`로 쓰므로 export 고정
        from core import evaluate_deviation_pass as pkg_fn
        assert pkg_fn is evaluate_deviation_pass


# ──────────────────────────────────────────────────────────────────────────
# #7 scan_recipes — Step index가 항상 유일·연속, 번호는 숫자 순으로 정렬
# ──────────────────────────────────────────────────────────────────────────
class TestScanRecipesIndex:
    @staticmethod
    def _make_lot(lot_dir):
        lot_dir.mkdir(parents=True, exist_ok=True)
        (lot_dir / 'S_X_UL.csv').write_text(
            "Lot ID,LOT001\n"
            "Recipe ID,R1\n"
            "Site ID,Site X,Site Y,HZ1_O (nm),Method ID,Valid\n"
            "0001_X000_Y000,0,0,100,X,TRUE\n",
            encoding='utf-8',
        )

    def test_indices_unique_contiguous_and_numeric_order(self, tmp_path):
        # 번호 폴더(1,2,10) + 무번호 폴더 혼재 — 이전엔 index 충돌/사전식 정렬 위험
        for rname in ['1. Vision', '2. Align', '10. Final', 'Extra Recipe']:
            self._make_lot(tmp_path / rname / 'Lot1')

        recipes = scan_recipes(str(tmp_path))
        assert len(recipes) == 4

        idxs = sorted(r['index'] for r in recipes)
        assert idxs == list(range(1, len(recipes) + 1))  # 유일 + 연속

        ordered = [r['name'] for r in sorted(recipes, key=lambda r: r['index'])]
        # 숫자 순(10이 2 뒤로) + 무번호는 맨 뒤
        assert ordered == ['1. Vision', '2. Align', '10. Final', 'Extra Recipe']


# ──────────────────────────────────────────────────────────────────────────
# #11 parse_geometry_string — "WxH+X+Y" 라운드트립 + 손상 입력 폴백
# ──────────────────────────────────────────────────────────────────────────
class TestParseGeometry:
    def test_roundtrip(self):
        assert parse_geometry_string('1600x1050+100+50') == (100, 50, 1600, 1050)

    def test_negative_coordinates(self):
        assert parse_geometry_string('800x600+-5+-10') == (-5, -10, 800, 600)

    def test_empty_returns_none(self):
        assert parse_geometry_string('') is None

    def test_malformed_returns_none(self):
        assert parse_geometry_string('garbage') is None
        assert parse_geometry_string('1600x1050') is None  # +X+Y 누락


# ──────────────────────────────────────────────────────────────────────────
# #3 surface3d 보간 그리드 방향 — meshgrid(indexing='ij')로 X/Y축이 안 뒤바뀜
# (위젯은 Qt/OpenGL 필요 → 동일 수치 계약을 순수 numpy/scipy로 검증)
# ──────────────────────────────────────────────────────────────────────────
class TestSurfaceGridOrientation:
    def test_meshgrid_ij_preserves_xy_axes(self):
        interp = pytest.importorskip("scipy.interpolate")
        griddata = interp.griddata

        # 평면 z = 10*x + y (x/y 의존이 비대칭이라 전치 시 값이 크게 달라짐)
        pts = [(x, y) for x in range(5) for y in range(5)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [10 * x + y for x, y in pts]

        xi = np.linspace(0, 4, 9)
        yi = np.linspace(0, 4, 9)
        Xi, Yi = np.meshgrid(xi, yi, indexing='ij')
        Zi = griddata((xs, ys), zs, (Xi, Yi), method='cubic')

        # indexing='ij' → Zi[i,j] = f(xi[i], yi[j]) = 10*xi[i] + yi[j]
        assert Zi.shape == (len(xi), len(yi))
        for i in (2, 4, 6):
            for j in (2, 4, 6):
                assert Zi[i, j] == pytest.approx(10 * xi[i] + yi[j], abs=1e-3)
