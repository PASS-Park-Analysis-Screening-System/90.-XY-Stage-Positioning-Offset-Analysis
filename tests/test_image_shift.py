"""이미지 2장 이동량 분석 — 위상 상관·템플릿 추적의 sub-pixel 정확도, 각도·배율, 경고, 여러 장, 파일 형식.

실제 Capture 이미지(1600×1200, 0.3409 µm/px)를 흉내 낸 합성 이미지를 쓴다:
흰 상자 안에 어두운 사각형 두 개가 대각선으로 놓인 정렬 마크 + 먼지 점 + 광학 흐림(σ 1.5 px)
+ 카메라에 고정된 조명(위쪽 어두운 띠, 비네팅) + 픽셀 잡음.
장면을 큰 판에 그려 두고 카메라 창을 옮겨 잘라 내며, 소수점 이동은 푸리에 이동으로 만든다.
(실제 샘플 *_C_Result.bmp는 화면 오버레이·좌표 글자가 들어 있고 450 KB라 저장소에 넣지 않았다)
"""
import csv
import math
import os

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import image_shift as ims  # noqa: E402

H, W = 1200, 1600
PS = ims.DEFAULT_PIXEL_SIZE_UM
MARGIN = 64


def _blur(img, sigma=1.5):
    fy = np.fft.fftfreq(img.shape[0])[:, None]
    fx = np.fft.rfftfreq(img.shape[1])[None, :]
    g = np.exp(-2.0 * (np.pi * sigma) ** 2 * (fy ** 2 + fx ** 2))
    return np.fft.irfft2(np.fft.rfft2(img) * g, s=img.shape)


def _fourier_shift(img, dx, dy):
    """주기 경계 푸리에 이동: 결과(x) = 원본(x − dx)."""
    fy = np.fft.fftfreq(img.shape[0])[:, None]
    fx = np.fft.rfftfreq(img.shape[1])[None, :]
    return np.fft.irfft2(np.fft.rfft2(img) * np.exp(-2j * np.pi * (fx * dx + fy * dy)), s=img.shape)


def _draw_mark(img, cx, cy, size):
    x0, y0 = int(cx - size / 2), int(cy - size / 2)
    img[y0:y0 + size, x0:x0 + size] = 245.0
    edge = max(2, size // 200 + 2)
    img[y0:y0 + edge, x0:x0 + size] = 150.0
    img[y0 + size - edge:y0 + size, x0:x0 + size] = 150.0
    img[y0:y0 + size, x0:x0 + edge] = 150.0
    img[y0:y0 + size, x0 + size - edge:x0 + size] = 150.0
    q, off = int(size * 0.4), int(size * 0.1)
    img[y0 + off:y0 + off + q, x0 + size - off - q:x0 + size - off] = 120.0
    img[y0 + size - off - q:y0 + size - off, x0 + off:x0 + off + q] = 120.0


def _illumination():
    yy = np.arange(H)[:, None] / H
    xx = np.arange(W)[None, :] / W
    band = 1.0 - 0.7 * np.exp(-(yy / 0.08) ** 2)
    return band * (1.0 - 0.15 * ((xx - 0.5) ** 2 + (yy - 0.5) ** 2))


ILLUM = _illumination()


class Scene:
    """큰 판에 그린 장면. frame(dx, dy)는 이미지 1(원점)의 무늬가 (dx, dy) px 옮겨 간 이미지."""

    def __init__(self, marks, extra=(0, 0), seed=0, dust=200):
        ex, ey = extra
        ch, cw = H + 2 * MARGIN + ey, W + 2 * MARGIN + ex
        rng = np.random.default_rng(seed)
        canvas = np.full((ch, cw), 200.0)
        self.ox, self.oy = MARGIN + ex, MARGIN + ey
        for fx, fy, size in marks:            # 이미지 1 좌표
            _draw_mark(canvas, self.ox + fx, self.oy + fy, size)
        n = int(dust * ch * cw / (H * W))
        for y, x in zip(rng.integers(3, ch - 3, n), rng.integers(3, cw - 3, n)):
            canvas[y - 2:y + 3, x - 2:x + 3] -= rng.uniform(30, 90)
        self.canvas = _blur(canvas)

    def frame(self, dx=0.0, dy=0.0, noise=2.0, seed=0, illum=True):
        ix, iy = math.floor(dx), math.floor(dy)
        x0, y0 = self.ox - ix, self.oy - iy
        m = 32
        region = self.canvas[y0 - m:y0 + H + m, x0 - m:x0 + W + m]
        img = _fourier_shift(region, dx - ix, dy - iy)[m:m + H, m:m + W]
        if illum:
            img = img * ILLUM
        if noise:
            img = img + np.random.default_rng(seed).normal(0.0, noise, img.shape)
        return img


@pytest.fixture(scope='module')
def scene():
    """실제 C_PR.png처럼 590 px 마크가 화면 오른쪽 가운데에 있다."""
    return Scene([(1100, 520, 590)], seed=1)


@pytest.fixture(scope='module')
def wide_scene():
    """스테이지 X +400 µm(≈1173 px)용: 200 px 마크가 이미지 1의 왼쪽에 있다."""
    return Scene([(160, 600, 200)], extra=(1300, 0), seed=2, dust=250)


def _motion(stage_um, phi_deg):
    """지시 스테이지 이동(화면 x 오른쪽 = 스테이지 +X, 화면 y 아래 = 스테이지 +Y)이
    화면에서 φ(반시계 +)만큼 돌아 보일 때의 이미지 이동 (px)."""
    sx, sy = stage_um
    ex, ey = sx / PS, sy / PS
    c, s = math.cos(math.radians(phi_deg)), math.sin(math.radians(phi_deg))
    # 화면(y 아래)에서 반시계 회전 = 수학 좌표(y 위)에서 반시계 회전
    mx, my_up = ex * c - (-ey) * s, ex * s + (-ey) * c
    return mx, -my_up


MARK_ROI = (30, 470, 260, 260)        # wide_scene 이미지 1의 마크 둘레


# ── 작은 이동: 위상 상관 ±0.05 px ──────────────────────

@pytest.mark.parametrize('shift', [(0.3, -0.7), (3.37, -1.82), (-7.25, 0.45), (12.5, 13.3), (-25.6, 18.04)])
def test_small_subpixel_shift_phase_and_auto(scene, shift):
    a = scene.frame(noise=2.0, seed=10)
    b = scene.frame(*shift, noise=2.0, seed=11)
    for method in (ims.METHOD_PHASE, ims.METHOD_AUTO):
        r = ims.measure_shift(a, b, method)
        assert r.method == ims.METHOD_PHASE
        assert abs(r.dx_px - shift[0]) <= 0.05 and abs(r.dy_px - shift[1]) <= 0.05, (method, r)
        assert r.reliable, r.warnings
        assert r.peak > 0.3 and r.ncc > 0.9


@pytest.mark.parametrize('shift', [(0.37, 13.3), (-4.62, -2.15)])
def test_small_shift_template_method(scene, shift):
    a = scene.frame(noise=2.0, seed=20)
    b = scene.frame(*shift, noise=2.0, seed=21)
    r = ims.measure_shift(a, b, ims.METHOD_TEMPLATE)
    assert r.method == ims.METHOD_TEMPLATE
    assert abs(r.dx_px - shift[0]) <= 0.05 and abs(r.dy_px - shift[1]) <= 0.05, r
    assert r.ncc > 0.95
    assert r.notes and '템플릿 자동 선택' in r.notes[0]


def test_template_13px_shift_with_roi(scene):
    """13.3 px(4.53 µm) 어긋남 — 템플릿을 마크의 모서리(사각형 하나 + 상자 테두리)에 두고 찾는다."""
    shift = (0.21, 13.3)
    a = scene.frame(noise=2.0, seed=30)
    b = scene.frame(*shift, noise=2.0, seed=31)
    r = ims.measure_shift(a, b, ims.METHOD_TEMPLATE, roi=(780, 480, 380, 360))
    assert abs(r.dx_px - shift[0]) <= 0.2 and abs(r.dy_px - shift[1]) <= 0.2
    assert abs(r.dx_px - shift[0]) <= 0.05 and abs(r.dy_px - shift[1]) <= 0.05   # 실제로는 더 정확하다
    assert r.roi == (780, 480, 380, 360)


# ── 큰 이동: 템플릿 추적 ±0.2 px, φ ±0.02° ──────────────

@pytest.mark.parametrize('phi', [0.65, -0.3, 0.0, 2.0])
def test_large_x_move_template_tracking_and_angle(wide_scene, phi):
    dx, dy = _motion((400.0, 0.0), phi)            # φ 0.65° → (1173.29, −13.31) px
    a = wide_scene.frame(noise=2.0, seed=40)
    b = wide_scene.frame(dx, dy, noise=2.0, seed=41)
    r = ims.measure_shift(a, b, ims.METHOD_AUTO, roi=MARK_ROI)
    assert r.method == ims.METHOD_TEMPLATE
    assert r.notes[0].startswith('자동:') and '템플릿 추적 사용' in r.notes[0]
    assert abs(r.dx_px - dx) <= 0.2 and abs(r.dy_px - dy) <= 0.2, (r.dx_px - dx, r.dy_px - dy)
    m = ims.analyze_motion(r.dx_px, r.dy_px, PS, 400.0, 0.0)
    assert abs(m['phi_deg'] - phi) <= 0.02, m['phi_deg']
    assert abs(m['phi_mrad'] - math.radians(phi) * 1000) <= 0.4
    assert abs(m['scale_ratio'] - 1.0) <= 0.0005
    assert m['mapping'] == '스테이지 +X → 화면 오른쪽(+x)'


def test_large_shift_auto_without_roi_picks_template(wide_scene):
    dx, dy = _motion((400.0, 0.0), 0.65)
    a = wide_scene.frame(noise=2.0, seed=50)
    b = wide_scene.frame(dx, dy, noise=2.0, seed=51)
    r = ims.measure_shift(a, b, ims.METHOD_AUTO)
    assert r.method == ims.METHOD_TEMPLATE and r.reliable
    assert abs(r.dx_px - dx) <= 0.2 and abs(r.dy_px - dy) <= 0.2


def test_large_shift_phase_only_is_flagged(wide_scene):
    """화면을 가로지르는 이동을 위상 상관으로 억지로 재면 경고가 붙는다 (조용히 틀리지 않는다)."""
    dx, dy = _motion((400.0, 0.0), 0.65)
    a = wide_scene.frame(noise=2.0, seed=52)
    b = wide_scene.frame(dx, dy, noise=2.0, seed=53)
    r = ims.measure_shift(a, b, ims.METHOD_PHASE)
    assert not r.reliable


def test_near_half_shift_warns(wide_scene):
    a = wide_scene.frame(noise=1.0, seed=60)
    b = wide_scene.frame(0.38 * W, 4.0, noise=1.0, seed=61)
    r = ims.phase_correlation(a, b)
    assert any('절반' in w for w in r.warnings), r


# ── 각도·배율 계산 (정확한 벡터) ─────────────────────────

def test_angle_sign_convention_and_sentence():
    # 스테이지 X +400 µm에 화면 위쪽(−y)으로 13.31 px → 화면 반시계 → φ > 0
    m = ims.analyze_motion(1173.29, -13.311, PS, 400.0, 0.0)
    assert m['phi_deg'] == pytest.approx(0.65, abs=0.001)
    assert m['perp_um'] == pytest.approx(13.311 * PS, abs=1e-3)
    assert '스테이지 X +400 µm 이동에 화면 세로 13.3 px(4.54 µm)' in m['interpretation']
    assert '0.65° 돌아가 있음' in m['interpretation']
    # 아래쪽(+y)이면 φ < 0
    m2 = ims.analyze_motion(1173.29, 13.311, PS, 400.0, 0.0)
    assert m2['phi_deg'] == pytest.approx(-0.65, abs=0.001)
    assert 'φ = -0.65°' in m2['interpretation']


def test_parallel_sentence_and_scale():
    m = ims.analyze_motion(1173.36, 1.5, PS, 400.0, 0.0)     # 0.073°
    assert abs(m['phi_deg']) <= ims.PARALLEL_DEG
    assert '평행' in m['interpretation']
    assert m['scale_ratio'] == pytest.approx(1173.36 * PS / 400.0, rel=1e-4)
    m2 = ims.analyze_motion(1173.36 * 1.01, 0.0, PS, 400.0, 0.0)
    assert m2['scale_ratio'] == pytest.approx(1.01 * 1173.36 * PS / 400.0, rel=1e-6)
    assert '이동 거리 비' in m2['interpretation'] and 'µm/px에 해당' in m2['interpretation']
    assert m2['pixel_size_implied'] == pytest.approx(PS / m2['scale_ratio'])


@pytest.mark.parametrize('stage, dpx, mapping, phi', [
    ((-400.0, 0.0), (-1173.36, 6.0), '스테이지 +X → 화면 오른쪽(+x)', None),
    ((400.0, 0.0), (-1173.36, -6.0), '스테이지 +X → 화면 왼쪽(−x)', None),
    ((0.0, 300.0), (8.0, -880.0), '스테이지 +Y → 화면 위(−y)', None),
    ((0.0, 300.0), (880.0, 8.0), '스테이지 +Y → 화면 오른쪽(+x)', None),
])
def test_axis_mapping_follows_measured_direction(stage, dpx, mapping, phi):
    m = ims.analyze_motion(dpx[0], dpx[1], PS, *stage)
    assert m['mapping'] == mapping
    ex, ey = m['expected_px']
    # 기대 벡터는 측정 벡터와 가까운 축 방향, 잔차는 그 차이
    assert math.hypot(ex - dpx[0], ey - dpx[1]) < 12
    rx, ry = m['residual_px']
    assert rx == pytest.approx(dpx[0] - ex) and ry == pytest.approx(dpx[1] - ey)
    expected_phi = math.degrees(math.atan2(-(ex * dpx[1] - ey * dpx[0]), ex * dpx[0] + ey * dpx[1]))
    assert m['phi_deg'] == pytest.approx(expected_phi)


def test_y_move_rotation_with_tracking():
    """스테이지 Y +300 µm(≈880 px) — 화면 세로로 크게 움직여도 φ ±0.02°."""
    sc = Scene([(800, 150, 180)], extra=(0, 950), seed=3)
    dx, dy = _motion((0.0, 300.0), -0.42)
    a = sc.frame(noise=2.0, seed=70)
    b = sc.frame(dx, dy, noise=2.0, seed=71)
    r = ims.measure_shift(a, b, ims.METHOD_TEMPLATE, roi=(680, 30, 240, 240))
    assert abs(r.dx_px - dx) <= 0.2 and abs(r.dy_px - dy) <= 0.2
    m = ims.analyze_motion(r.dx_px, r.dy_px, PS, 0.0, 300.0)
    assert m['mapping'] == '스테이지 +Y → 화면 아래(+y)'
    assert abs(m['phi_deg'] - (-0.42)) <= 0.02
    assert '화면 가로' in m['interpretation']


def test_no_stage_move_reports_shift_only():
    m = ims.analyze_motion(-3.5, 2.25, PS)
    assert m['phi_deg'] is None and m['scale_ratio'] is None
    assert m['dx_um'] == pytest.approx(-3.5 * PS)
    assert m['interpretation'] == '이미지가 왼쪽 3.50 px(1.19 µm), 아래 2.25 px(0.77 µm) 이동'


def test_tiny_measured_motion_with_stage_move_warns():
    m = ims.analyze_motion(0.1, 0.05, PS, 400.0, 0.0)
    assert m['warnings'] and '0.5 px' in m['warnings'][0]


def test_bad_pixel_size_rejected():
    with pytest.raises(ValueError):
        ims.analyze_motion(1, 1, 0.0, 1, 0)


# ── ROI ────────────────────────────────────────────────

def test_roi_restricts_analysis_to_moving_pattern(scene):
    """화면에 고정된 강한 무늬(센서 먼지 격자)가 있어도 ROI를 마크에 두면 마크의 이동을 잰다."""
    shift = (6.42, -3.18)
    fixed = np.zeros((H, W))
    for y in range(80, H, 160):
        for x in range(80, 760, 160):
            fixed[y - 6:y + 7, x - 6:x + 7] = -120.0
    fixed = _blur(fixed, 1.0)
    a = scene.frame(noise=2.0, seed=80) + fixed
    b = scene.frame(*shift, noise=2.0, seed=81) + fixed
    roi = (760, 180, 700, 680)
    r = ims.measure_shift(a, b, ims.METHOD_PHASE, roi=roi)
    assert r.roi == roi
    assert abs(r.dx_px - shift[0]) <= 0.05 and abs(r.dy_px - shift[1]) <= 0.05


def test_clip_roi():
    assert ims.clip_roi(None, (100, 200)) is None
    assert ims.clip_roi((-10, -5, 50, 40), (100, 200)) == (0, 0, 40, 35)
    assert ims.clip_roi((150, 60, 100, 100), (100, 200)) == (150, 60, 50, 40)
    assert ims.clip_roi((60, 50, -40, -30), (100, 200)) == (20, 20, 40, 30)    # 거꾸로 끈 사각형
    with pytest.raises(ValueError):
        ims.clip_roi((190, 10, 50, 50), (100, 200))                            # 남는 폭 10 px


# ── 잡음·약한 피크 ──────────────────────────────────────

@pytest.mark.parametrize('noise, tol', [(5.0, 0.05), (10.0, 0.1)])
def test_noise_robustness(scene, noise, tol):
    shift = (2.63, -5.41)
    a = scene.frame(noise=noise, seed=90)
    b = scene.frame(*shift, noise=noise, seed=91)
    r = ims.measure_shift(a, b)
    assert abs(r.dx_px - shift[0]) <= tol and abs(r.dy_px - shift[1]) <= tol, r
    assert r.reliable, r.warnings


def test_weak_peak_warning_for_unrelated_images(scene):
    other = Scene([(400, 400, 300)], seed=9).frame(noise=2.0, seed=92)
    r = ims.phase_correlation(scene.frame(noise=2.0, seed=93), other)
    assert not r.reliable
    assert any('피크' in w for w in r.warnings)


def test_weak_peak_warning_for_blank_frames():
    """무늬 없이 조명 얼룩만 있는 두 장 — 겹친 영역 NCC가 조명에 속지 않는다."""
    rng = np.random.default_rng(7)
    a = 200 * ILLUM + rng.normal(0, 2, (H, W))
    b = 200 * ILLUM + rng.normal(0, 2, (H, W))
    r = ims.phase_correlation(a, b)
    assert not r.reliable
    assert r.ncc < ims.OVERLAP_NCC_WEAK


def test_template_weak_when_pattern_left_frame(wide_scene):
    a = wide_scene.frame(noise=2.0, seed=94)
    b = wide_scene.frame(1173.0, 0.0, noise=2.0, seed=95)
    r = ims.template_track(a, b, roi=(900, 300, 200, 200))   # 마크 없는 곳(먼지뿐)이 화면 밖으로
    assert not r.reliable


def test_flat_template_rejected():
    flat = np.full((200, 300), 50.0)
    with pytest.raises(ValueError):
        ims.template_track(flat, flat, roi=(10, 10, 40, 40))


def test_size_mismatch_rejected():
    with pytest.raises(ValueError, match='크기'):
        ims.measure_shift(np.zeros((100, 120)), np.zeros((100, 121)))


# ── 여러 장 (이미지 1 기준) ─────────────────────────────

def test_batch_mean_std_of_phi(wide_scene, tmp_path):
    phis = [0.60, 0.65, 0.70]
    images = [wide_scene.frame(noise=2.0, seed=100)]
    for k, phi in enumerate(phis):
        images.append(wide_scene.frame(*_motion((400.0, 0.0), phi), noise=2.0, seed=101 + k))
    images.append(np.zeros((H // 2, W)))                          # 크기가 다른 한 장은 실패로 남는다
    names = ['ref.bmp', 'r1.bmp', 'r2.bmp', 'r3.bmp', 'bad.bmp']
    calls = []
    batch = ims.analyze_images(images, PS, (400.0, 0.0), ims.METHOD_AUTO, MARK_ROI, names=names,
                               progress=lambda d, t: calls.append((d, t)))
    assert [p.index for p in batch.pairs] == [2, 3, 4]
    assert len(batch.errors) == 1 and batch.errors[0][:2] == (5, 'bad.bmp') and '크기' in batch.errors[0][2]
    assert calls[-1] == (4, 4)
    mean, std, n = batch.stats['phi_deg']
    assert n == 3
    assert mean == pytest.approx(0.65, abs=0.02)
    assert std == pytest.approx(0.05, abs=0.01)                 # ddof=1
    assert batch.stats['scale_ratio'][0] == pytest.approx(1.0, abs=0.0005)

    text = ims.format_report(batch)
    assert 'φ: 평균 +0.65' in text and '(n=3' in text and '#5 bad.bmp: 실패' in text
    assert '템플릿 NCC' in text

    out = tmp_path / 'shift.csv'
    ims.write_csv(out, batch)
    with open(out, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    assert rows[0][0] == '픽셀 크기 (µm/px)'
    assert rows[1] == ims.CSV_HEADERS
    assert len(rows) == 2 + 3 + 1 + 2
    assert rows[2][2] == 'r1.bmp' and float(rows[2][9]) == pytest.approx(0.60, abs=0.02)
    assert rows[5][-2].startswith('실패')
    assert rows[6][0] == '평균' and float(rows[6][9]) == pytest.approx(0.65, abs=0.02)
    assert rows[7][0].startswith('표준편차') and float(rows[7][9]) == pytest.approx(0.05, abs=0.01)
    assert all(len(r) == len(ims.CSV_HEADERS) for r in rows[1:])


def test_single_pair_has_no_std():
    stats = ims.batch_stats([ims.PairResult(2, 'a', 'b', ims.ShiftResult(1.0, 2.0, 'phase', 1, 1, 0, 1),
                                            ims.analyze_motion(1.0, 2.0, PS, 0.3, 0.0))])
    assert stats['phi_deg'][1] is None and stats['phi_deg'][2] == 1


def test_batch_needs_two_images():
    with pytest.raises(ValueError):
        ims.analyze_images([np.zeros((50, 50))])


# ── 파일 형식 ──────────────────────────────────────────

def _save(img, path, mode):
    if mode == 'L':
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path)
    elif mode == 'I;16':
        Image.fromarray((np.clip(img, 0, 255) * 257).astype(np.uint16)).save(path)
    elif mode == 'RGB':
        g = np.clip(img, 0, 255).astype(np.uint8)
        Image.fromarray(np.dstack([g, g, g])).save(path)
    elif mode == 'JPEG':
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, quality=95)


@pytest.mark.parametrize('ext, mode, top, tol', [
    ('bmp', 'L', 255, 0.05), ('bmp', 'RGB', 255, 0.05), ('png', 'L', 255, 0.05),
    ('png', 'I;16', 65535, 0.05), ('tif', 'L', 255, 0.05), ('tiff', 'I;16', 65535, 0.05),
    ('jpg', 'JPEG', 255, 0.1),
])
def test_file_formats(scene, tmp_path, ext, mode, top, tol):
    shift = (4.37, -2.61)
    a = scene.frame(noise=1.0, seed=110)
    b = scene.frame(*shift, noise=1.0, seed=111)
    p1, p2 = tmp_path / f'1.{ext}', tmp_path / f'2.{ext}'
    _save(a, p1, mode)
    _save(b, p2, mode)
    g = ims.load_gray(p1)
    assert g.shape == (H, W) and g.dtype == np.float64
    assert g.max() <= top and (top == 255 or g.max() > 255)        # 16비트는 값 범위를 그대로 둔다
    batch = ims.analyze_images([str(p1), str(p2)], PS)
    assert batch.names == [f'1.{ext}', f'2.{ext}'] and not batch.errors
    s = batch.pairs[0].shift
    assert abs(s.dx_px - shift[0]) <= tol and abs(s.dy_px - shift[1]) <= tol, (s.dx_px, s.dy_px)


def test_load_errors(tmp_path):
    with pytest.raises(ims.ImageLoadError, match='파일이 없습니다'):
        ims.load_gray(tmp_path / 'none.png')
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not an image')
    with pytest.raises(ims.ImageLoadError, match='읽을 수 없습니다'):
        ims.load_gray(bad)
    batch = ims.analyze_images([np.ones((40, 40)), str(bad)])
    assert batch.errors and batch.errors[0][1] == 'bad.png'


def test_to_uint8_stretches_16bit():
    g = np.linspace(1000, 3000, 100 * 100).reshape(100, 100)
    u = ims.to_uint8(g)
    assert u.dtype == np.uint8 and u.min() == 0 and u.max() == 255
    assert ims.to_uint8(np.full((10, 10), 7.0)).max() == 0


# ── 화면 (대화상자, 메인 창 단추) ────────────────────────

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _save_frames(wide_scene, tmp_path, phis):
    paths = []
    for k, phi in enumerate([None] + list(phis)):
        shift = (0.0, 0.0) if phi is None else _motion((400.0, 0.0), phi)
        img = wide_scene.frame(*shift, noise=2.0, seed=300 + k)
        p = tmp_path / f'cap_{k + 1}.bmp'
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(p)
        paths.append(str(p))
    return paths


def test_dialog_drag_roi_analyse_copy_and_save(qapp, wide_scene, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog
    from ui.dialogs import image_shift_dialog as mod

    dlg = mod.ImageShiftDialog()
    dlg.resize(1300, 900)
    dlg.show()
    try:
        assert not dlg.btn_run.isEnabled() and not dlg.btn_copy.isEnabled()
        dlg.set_paths(_save_frames(wide_scene, tmp_path, [0.60, 0.70]))
        assert [dlg.file_list.item(i).text() for i in range(3)] == ['1. cap_1.bmp  (기준)', '2. cap_2.bmp', '3. cap_3.bmp']
        mod._wait_live_threads()                 # 미리보기는 작업 스레드에서 읽는다
        qapp.processEvents()
        assert dlg.view1.image is not None and dlg.view1.image.width() == W

        # 이미지 1 위를 끌어 ROI 지정 (화면 좌표 → 이미지 좌표)
        rect, scale = dlg.view1._target()

        def at(x, y):
            return QPoint(int(round(rect.x() + x * scale)), int(round(rect.y() + y * scale)))
        QTest.mousePress(dlg.view1, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(30, 470))
        QTest.mouseMove(dlg.view1, at(290, 730))
        QTest.mouseRelease(dlg.view1, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(290, 730))
        x, y, w, h = dlg.roi
        assert abs(x - 30) <= 3 and abs(y - 470) <= 3 and abs(w - 260) <= 6 and abs(h - 260) <= 6
        assert dlg.roi_label.text().startswith('ROI: x ')

        dlg.stage_dx.setValue(400.0)
        dlg.run_and_wait()
        assert dlg.batch is not None and not dlg.batch.errors
        assert dlg.table.rowCount() == 2 and dlg.table.item(0, 1).text() == 'cap_2.bmp'
        assert float(dlg.table.item(0, 7).text()) == pytest.approx(0.60, abs=0.02)
        assert '스테이지 X +400 µm 이동에 화면 세로' in dlg.interpretation.text()
        assert 'φ 평균 +0.6' in dlg.stats.text() and 'n=2' in dlg.stats.text()
        assert dlg.btn_copy.isEnabled() and dlg.btn_csv.isEnabled()
        assert dlg.view2.arrow is not None and any(b[3] == '찾은 자리' for b in dlg.view2.boxes)

        dlg.table.selectRow(1)                   # 행을 고르면 그 쌍을 보인다
        assert dlg.view2.caption == 'cap_3.bmp'
        assert 'φ = +0.70°' in dlg.interpretation.text()

        # 픽셀 크기만 바꾸면 다시 재지 않고 각도·배율을 바로 다시 계산한다
        scale_before = dlg.batch.pairs[0].motion['scale_ratio']
        dlg.pixel_size.setValue(0.35)
        assert dlg.batch.pixel_size_um == 0.35
        assert dlg.batch.pairs[0].motion['scale_ratio'] == pytest.approx(scale_before * 0.35 / PS)

        dlg._copy()
        assert QApplication.clipboard().text().startswith('[이미지 이동량 분석]')
        out = tmp_path / 'shift.csv'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: (str(out), '')))
        dlg._save_csv()
        with open(out, encoding='utf-8-sig', newline='') as f:
            assert list(csv.reader(f))[1] == ims.CSV_HEADERS

        # ROI를 지우면(짧은 클릭) 이전 결과는 맞지 않으므로 지운다
        QTest.mouseClick(dlg.view1, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(500, 500))
        assert dlg.roi is None and dlg.batch is None and dlg.table.rowCount() == 0
        assert not dlg.btn_copy.isEnabled()
    finally:
        dlg.close()
        mod._wait_live_threads()
        qapp.processEvents()
        dlg.deleteLater()


def test_dialog_reports_unreadable_image(qapp, tmp_path):
    from ui.dialogs import image_shift_dialog as mod
    good = tmp_path / 'a.png'
    Image.fromarray(np.full((120, 160), 100, np.uint8)).save(good)
    bad = tmp_path / 'b.png'
    bad.write_bytes(b'broken')
    dlg = mod.ImageShiftDialog()
    try:
        dlg.set_paths([good, bad])
        dlg.run_and_wait()
        assert dlg.batch is not None and not dlg.batch.pairs
        assert 'b.png' in dlg.warn.text() and '실패 1장' in dlg.status.text()
        assert not dlg.btn_copy.isEnabled()
        mod._wait_live_threads()
        qapp.processEvents()
        assert '읽을 수 없습니다' in dlg._preview_errors[os.path.normpath(str(bad))]
    finally:
        mod._wait_live_threads()
        dlg.deleteLater()
        qapp.processEvents()


def test_main_window_button_opens_single_dialog(qapp, monkeypatch):
    import main
    from PySide6.QtWidgets import QPushButton
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation={}))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    monkeypatch.setattr(win, '_save_settings', lambda: None)
    try:
        buttons = [b for b in win.findChildren(QPushButton) if b.text() == '이미지 2장 이동량 분석']
        assert len(buttons) == 1
        buttons[0].click()
        dlg = win._image_shift_dialog
        assert dlg.isVisible() and dlg.parent() is win
        dlg.close()
        buttons[0].click()
        assert win._image_shift_dialog is dlg and dlg.isVisible()      # 같은 창을 다시 연다
        dlg.close()
    finally:
        win.deleteLater()
        qapp.processEvents()
