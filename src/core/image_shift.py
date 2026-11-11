"""
image_shift.py — 카메라 이미지 2장(이상)의 이동량을 sub-pixel로 재고 각도·배율로 바꾼다 (Qt 없음)

장비 소프트웨어는 패턴 매칭 픽셀 좌표를 내보내지 않고, 라이브 화면에서 1~15 px 어긋남은
눈으로 보이지 않는다. Positioning Panel "Capture…"로 받은 원본 이미지(1600×1200,
약 0.3409 µm/px) 두 장을 비교해 이미지가 얼마나 움직였는지 재고, 지시한 스테이지 이동과
비교해 카메라 축과 스테이지 축 사이 각도(φ)·이동 거리 비(배율)·잔차를 낸다.

이동량 부호
    (dx, dy) = 이미지 1의 무늬가 이미지 2에서 옮겨 간 양 (px).
    x는 화면 오른쪽이 +, y는 화면 아래쪽이 + (이미지 행 번호가 커지는 쪽).

측정 방법
    위상 상관   두 이미지(또는 같은 ROI)를 Hann 창으로 감싸 정규화 교차 파워 스펙트럼의
                피크를 찾고(가우스 저역 통과로 잡음 고주파를 누른다), 피크 주변을 절반 정규화
                스펙트럼의 확대 DFT(0.1 px → 0.01 px)로 다시 찾는다.
                이동이 분석 영역의 1/4보다 작을 때 가장 정확하다.
    템플릿 추적  이미지 1의 ROI(없으면 무늬가 뚜렷한 곳)를 템플릿으로 삼아 이미지 2 전체에서
                정규화 상호상관(NCC)으로 찾고, 찾은 자리에서 위상 상관으로 소수점을 다듬는다.
                무늬가 화면을 가로질러 크게(예: 1600 px 중 1173 px) 움직여도 된다.
    자동        위상 상관 결과가 믿을 만하면 그대로 쓰고, 아니면 템플릿 추적으로 바꾼다.
    위상 상관은 정수 이동만큼 겹친 영역으로 잘라 한 번 더 재서 큰 이동의 0 쪽 치우침을 없앤다.

화면 고정 오버레이 (Result.bmp의 글자·십자선·상자)
    두 장에서 값이 정확히 같고 둘레 밝기 차가 큰 픽셀을 후보로 잡고, 후보를 주변 밝기로 채운 뒤
    잰 이동으로 이미지 1을 옮겨 보아 '장면이라면 바뀌었어야 하는데 그대로인' 후보만 오버레이로
    확정한다 (잡음이 작은 8비트 이미지는 장면 픽셀도 20% 넘게 우연히 같으므로 같다는 것만으로는
    오버레이가 아니다). 확정한 픽셀을 채우고 다시 재며 경고한다. 합성 시험에서 5 px 이상 이동은
    0.05 px 안, 2 px 안팎은 0.1 px 안이지만 1 px보다 작은 이동은 0.2 px쯤 0 쪽으로 끌리고, 0.1 px
    아래는 오버레이를 확정조차 못 한다. 원본 PR.png를 쓰는 것이 가장 좋다.

각도 φ (스테이지 이동을 입력했을 때)
    지시 이동 (ΔX, ΔY) µm가 화면에서 어느 방향인지(축 대응: 스테이지 X·Y → 화면 오른쪽/왼쪽/
    위/아래, 축 바뀜 포함 8가지)를 사용자가 고르거나 자동으로 정한다.
    자동은 측정 방향에 가장 가까운 대응을 고르되, 후보 기대 방향끼리 10° 이상 떨어져 있을 때만
    정한다. 한 축만 움직이면 후보가 90°씩 떨어져 늘 정해지고(카메라가 45° 넘게 돌지 않았다는
    가정), X·Y를 함께 움직이면 (400, 4)·(400, 395)처럼 거울·축 바뀜 후보가 1° 안쪽으로 붙어
    φ가 조용히 틀릴 수 있어 정하지 않고 경고한다 (φ·배율 없음 — 축 대응을 직접 고른다).
    여러 장은 경고 없는 쌍들의 평균 측정 방향으로 대응 하나를 정하고, 다른 대응에 가까운 쌍은 경고한다.
    φ = atan2(수직 성분, 진행 성분) — 기대 방향에서 측정 방향으로 화면에서 반시계 방향이 +.
    배율 = 측정 이동 거리 / 지시 이동 거리, 잔차 = 측정 벡터 − 기대 벡터 (화면 좌표).
    측정 이동이 0.5 px보다 작으면 방향을 정할 수 없어 φ·배율을 내지 않는다.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field

import numpy as np

DEFAULT_PIXEL_SIZE_UM = 0.3409
PARALLEL_DEG = 0.1                # |φ|가 이 이하면 '평행'으로 말한다
SCALE_NOTE = 0.005                # 배율이 1에서 이만큼 넘게 벗어나면 해석에 덧붙인다

METHOD_AUTO = 'auto'
METHOD_PHASE = 'phase'
METHOD_TEMPLATE = 'template'
METHOD_LABELS = {METHOD_AUTO: '자동', METHOD_PHASE: '위상 상관', METHOD_TEMPLATE: '템플릿 추적'}

IMAGE_EXTENSIONS = ('.bmp', '.png', '.jpg', '.jpeg', '.tif', '.tiff')
MIN_ROI = 16                      # ROI·템플릿 한 변의 최소 길이 (px)

# 신뢰도 기준 (합성·실제 카메라 이미지로 맞춤: 같은 장면 피크 0.2~0.5, 다른 장면 0.05~0.09, 잡음 0.015)
PEAK_WEAK = 0.1                   # 위상 상관: 정규화 피크 높이 (같은 이미지면 1)
SECOND_PEAK_WARN = 0.6            # 위상 상관: 2등 피크 / 1등 피크
TEMPLATE_NCC_WEAK = 0.5
TEMPLATE_SECOND_WARN = 0.9        # 템플릿: 2등 NCC / 1등 NCC
OVERLAP_NCC_WEAK = 0.3            # 겹친 영역의 대역 통과 NCC (잡음·조명 얼룩을 뺀 무늬 일치도)
BANDPASS_FINE, BANDPASS_COARSE = 2, 8   # 대역 통과: 5×5 평균 − 17×17 평균
NEAR_HALF = 0.35                  # 이동이 분석 영역의 이 비율을 넘으면 방향이 모호하다
AUTO_PHASE_LIMIT = 0.25           # 자동: 위상 상관은 이 비율 이하 이동에서만 믿는다
LOWPASS_SIGMA = 0.1               # 상관 스펙트럼 가우스 저역 통과 (cycles/px) — 잡음 고주파를 누른다
REFINE_MIN_SHIFT = 2              # 위상 상관: 이 이상(px) 움직이면 겹친 영역으로 잘라 한 번 더 잰다
MIN_MOTION_PX = 0.5               # 측정 이동이 이보다 작으면 방향(φ·배율)을 정하지 않는다
MAPPING_MIN_SEPARATION_DEG = 10.0  # 자동 축 대응: 후보 기대 방향끼리 이만큼은 떨어져 있어야 정한다
OVERLAY_CONTRAST = 0.1            # 오버레이 후보: 3×3 둘레 밝기 차 ≥ (1~99 백분위 폭) × 이 값
OVERLAY_MIN_FRACTION = 1e-4       # 오버레이로 보는 최소 픽셀 비율 (1600×1200이면 192 px)
OVERLAY_MIN_PIXELS = 100
OVERLAY_FILL_RADIUS = 6           # 오버레이 픽셀은 (2r+1)² 둘레의 다른 픽셀 평균으로 채운다
OVERLAY_GROW_STEPS = 3            # 확정한 오버레이를 값이 같은 이웃으로 넓히는 횟수
CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')   # 엑셀 수식으로 읽히는 첫 글자

MAPPING_AUTO = 'auto'
# 축 대응 (swap, gx, gy): swap이 아니면 기대 = (gx·ΔX, gy·ΔY), swap이면 (gx·ΔY, gy·ΔX) (화면 x 오른쪽, y 아래)
MAPPING_CHOICES = [(False, 1, 1), (False, 1, -1), (False, -1, 1), (False, -1, -1),
                   (True, 1, 1), (True, -1, 1), (True, 1, -1), (True, -1, -1)]


class ImageLoadError(ValueError):
    """이미지를 읽을 수 없다 (형식·손상·경로)."""


class AnalysisCancelled(Exception):
    """사용자가 분석을 멈췄다 (progress 콜백이 던진다)."""


# ── 이미지 읽기 ─────────────────────────────────────────

def load_gray(path) -> np.ndarray:
    """이미지 파일 → 2D float64 그레이 배열 (원래 값 범위 그대로: 8비트 0~255, 16비트 0~65535).

    bmp/png/jpg/tiff를 읽는다. 그레이는 8·16비트(·32비트 정수·실수) 값 그대로, 컬러는 밝기
    (0.299 R + 0.587 G + 0.114 B)로 바꾼다. 16비트 컬러(48비트 RGB TIFF 등)는 Pillow가 8비트로
    줄여 읽으므로 그레이 16비트만큼 정밀하지 않다. 여러 장이 든 TIFF는 첫 장만 쓴다.
    NaN·무한대 픽셀이 있거나 Pillow의 압축 폭탄 한도를 넘는 이미지는 거절한다.
    """
    from PIL import Image, UnidentifiedImageError

    name = os.path.basename(str(path))
    try:
        with Image.open(path) as im:
            im.seek(0)
            arr = _pil_to_gray(im)
    except FileNotFoundError:
        raise ImageLoadError(f'파일이 없습니다: {name}') from None
    except Image.DecompressionBombError as e:
        raise ImageLoadError(f'이미지가 너무 큽니다: {name} ({e})') from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as e:
        raise ImageLoadError(f'이미지를 읽을 수 없습니다: {name} ({e})') from None
    if arr.ndim != 2 or min(arr.shape) < MIN_ROI:
        raise ImageLoadError(f'이미지가 너무 작거나 2차원이 아닙니다: {name} {arr.shape}')
    if not np.isfinite(arr).all():
        raise ImageLoadError(f'NaN·무한대 픽셀이 있습니다: {name}')
    return arr


def _pil_to_gray(im) -> np.ndarray:
    mode = im.mode
    if mode in ('1', 'L', 'I', 'F') or mode.startswith('I;16'):
        return np.asarray(im).astype(np.float64)
    if mode in ('LA', 'La'):
        return np.asarray(im)[..., 0].astype(np.float64)
    if mode not in ('RGB', 'RGBA', 'RGBX', 'RGBa'):
        im = im.convert('RGB')
    rgb = np.asarray(im)[..., :3].astype(np.float64)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def to_uint8(gray: np.ndarray, low=0.5, high=99.5) -> np.ndarray:
    """화면 표시용 8비트 배열. 하위·상위 백분위로 늘려 16비트도 잘 보이게 한다."""
    g = np.asarray(gray, dtype=np.float64)
    lo, hi = np.percentile(g, [low, high])
    if not hi > lo:
        lo, hi = float(g.min()), float(g.max())
    if not hi > lo:
        return np.zeros(g.shape, dtype=np.uint8)
    out = (g - lo) * (255.0 / (hi - lo))
    return np.ascontiguousarray(np.clip(out, 0, 255).astype(np.uint8))


def clip_roi(roi, shape):
    """(x, y, w, h) → 이미지 안으로 자른 정수 ROI. None이면 None. 너무 작으면 ValueError."""
    if roi is None:
        return None
    x, y, w, h = (int(round(float(v))) for v in roi)
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    H, W = shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 - x0 < MIN_ROI or y1 - y0 < MIN_ROI:
        raise ValueError(f'ROI가 너무 작습니다 (최소 {MIN_ROI}×{MIN_ROI} px, 이미지 안쪽)')
    return (x0, y0, x1 - x0, y1 - y0)


def _crop(img, roi):
    if roi is None:
        return img
    x, y, w, h = roi
    return img[y:y + h, x:x + w]


# ── 결과 ────────────────────────────────────────────────

@dataclass
class ShiftResult:
    """이미지 1 → 이미지 2 이동량과 신뢰도."""
    dx_px: float
    dy_px: float
    method: str                  # METHOD_PHASE / METHOD_TEMPLATE (실제로 쓴 방법)
    peak: float                  # 위상 상관 정규화 피크 높이(같은 이미지면 1) / 템플릿 NCC 최댓값
    psr: float                   # 위상 상관 피크 대 주변(sidelobe) 비 (템플릿은 nan) — 참고값
    second_ratio: float          # 2등 피크 / 1등 피크 — 1에 가까우면 위치가 모호하다
    ncc: float                   # 일치도 -1~1: 위상 상관은 겹친 영역의 대역 통과 NCC, 템플릿은 NCC 최댓값
    roi: tuple | None = None     # 위상 상관: 두 이미지 공통 분석 영역 / 템플릿: 이미지 1의 템플릿
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def reliable(self):
        return not self.warnings


# ── 위상 상관 ───────────────────────────────────────────

def _prepare(img):
    a = np.asarray(img, dtype=np.float64)
    a = a - a.mean()
    h, w = a.shape
    return a * np.outer(np.hanning(h), np.hanning(w))


def _half_weights(w):
    """rfft 절반 스펙트럼으로 실수 역변환을 만들 때 열 가중치 (0·나이퀴스트 1, 나머지 2)."""
    n = w // 2 + 1
    wt = np.full(n, 2.0)
    wt[0] = 1.0
    if w % 2 == 0:
        wt[-1] = 1.0
    return wt


def _dft_patch(spec_w, H, W, ys, xs):
    """절반 스펙트럼(가중치 반영)의 실수 역DFT를 임의 좌표 ys(행)·xs(열)에서 계산 (H·W로 나눔)."""
    ky = np.exp(2j * np.pi * np.outer(ys, np.fft.fftfreq(H)))
    kx = np.exp(2j * np.pi * np.outer(np.fft.rfftfreq(W), xs))
    return (ky @ spec_w @ kx).real / (H * W)


def _refine_peak(spec_w, H, W, py, px, upsample):
    """정수 피크 (py, px) 주변을 확대 DFT로 다시 찾아 소수점 위치를 낸다.

    곡면이 평평하거나(무늬 없음) 최댓값이 찾는 칸의 가장자리에 걸리면(피크가 뚜렷하지 않음)
    None — 가장자리로 끌려가 엉뚱한 값을 내지 않는다.
    """
    cy, cx = float(py), float(px)
    res = 0.1
    while True:
        offs = np.arange(-15, 16) * res          # ±1.5 단계 → 다음 단계는 10배 촘촘히
        patch = _dft_patch(spec_w, H, W, cy + offs, cx + offs)
        span = float(patch.max() - patch.min())
        if not span > 1e-12 * max(1.0, float(np.abs(patch).max())):
            return None
        i, j = np.unravel_index(int(np.argmax(patch)), patch.shape)
        if not (0 < i < len(offs) - 1 and 0 < j < len(offs) - 1):
            return None
        fy = _parabola(patch[i - 1, j], patch[i, j], patch[i + 1, j])
        fx = _parabola(patch[i, j - 1], patch[i, j], patch[i, j + 1])
        if res <= 1.0 / upsample + 1e-12:
            return cy + (offs[i] + fy * res), cx + (offs[j] + fx * res)
        cy += offs[i]
        cx += offs[j]
        res /= 10.0


def _parabola(m, c, p):
    den = m - 2.0 * c + p
    if den >= 0 or not np.isfinite(den):
        return 0.0
    return float(np.clip(0.5 * (m - p) / den, -0.5, 0.5))


def _wrap(v, n):
    """FFT 인덱스 → 부호 있는 이동 (-n/2 ~ n/2)."""
    v = float(v) % n
    return v - n if v >= n / 2 else v


def _peak_stats(surface, py, px, radius):
    """(PSR, 2등 피크/1등 피크). 피크 주변 radius 안쪽(주기 경계 포함)을 빼고 계산한다."""
    H, W = surface.shape
    peak = surface[py, px]
    mask = np.ones(surface.shape, dtype=bool)
    rows = np.arange(py - radius, py + radius + 1) % H
    cols = np.arange(px - radius, px + radius + 1) % W
    mask[np.ix_(rows, cols)] = False
    side = surface[mask]
    if side.size < 2:
        return float('nan'), float('nan')
    std = side.std()
    psr = float((peak - side.mean()) / std) if std > 0 else float('inf')
    second = float(side.max() / peak) if peak > 0 else float('nan')
    return psr, second


def _box_mean(img, r):
    """(2r+1)² 상자 평균 (가장자리는 끝값을 늘려 채운다). 크기는 그대로."""
    if r <= 0:
        return img
    k = 2 * r + 1
    return _window_sums(np.pad(img, r, mode='edge'), k, k) / (k * k)


def _bandpass(img, fine=BANDPASS_FINE, coarse=BANDPASS_COARSE):
    """작은 상자 평균 − 큰 상자 평균: 픽셀 잡음과 카메라에 고정된 조명 얼룩·비네팅을 함께 빼고
    윤곽·점만 남긴다."""
    return _box_mean(img, fine) - _box_mean(img, coarse)


def _overlap(a, b, dy, dx):
    """정수 이동 (dy, dx)로 겹친 영역 (a 조각, b 조각). b[y+dy, x+dx] ≈ a[y, x]. 없으면 (None, None)."""
    H, W = a.shape
    y0, y1 = max(0, -dy), min(H, H - dy)
    x0, x1 = max(0, -dx), min(W, W - dx)
    if y1 - y0 < 2 or x1 - x0 < 2:
        return None, None
    return a[y0:y1, x0:x1], b[y0 + dy:y1 + dy, x0 + dx:x1 + dx]


def _overlap_ncc(a, b, dy, dx):
    """정수 이동 (dy, dx)로 겹친 영역의 zero-mean NCC."""
    pa, pb = _overlap(a, b, dy, dx)
    if pa is None:
        return float('nan')
    pa = pa - pa.mean()
    pb = pb - pb.mean()
    den = math.sqrt(float((pa * pa).sum()) * float((pb * pb).sum()))
    return float((pa * pb).sum() / den) if den > 0 else float('nan')


def _correlate(a, b, upsample):
    """같은 크기 두 배열의 위상 상관 → (dy, dx, 정규화 피크, PSR, 2등/1등). 잴 수 없으면 ValueError."""
    H, W = a.shape
    Fa = np.fft.rfft2(_prepare(a))
    Fb = np.fft.rfft2(_prepare(b))
    cross = Fb * np.conj(Fa)
    mag = np.abs(cross)
    floor = mag.max() * 1e-12
    mag = np.maximum(mag, floor if floor > 0 else 1e-300)
    f2 = np.fft.fftfreq(H)[:, None] ** 2 + np.fft.rfftfreq(W)[None, :] ** 2
    lowpass = np.exp(-f2 / (2.0 * LOWPASS_SIGMA ** 2))
    half_w = _half_weights(W)[None, :]
    # 위치 찾기·신뢰도: 완전 정규화(위상만) — 피크가 좁고 높이가 0~1로 비교된다
    surface = np.fft.irfft2(cross / mag * lowpass, s=(H, W))
    py, px = np.unravel_index(int(np.argmax(surface)), surface.shape)
    psr, second = _peak_stats(surface, py, px, radius=5)
    peak = float(surface[py, px] / ((lowpass * half_w).sum() / (H * W)))
    # 소수점: 절반 정규화 — 잡음이 큰 고주파의 비중을 줄여 sub-pixel 오차가 작다
    spec_w = cross / np.sqrt(mag) * lowpass * half_w
    found = _refine_peak(spec_w, H, W, py, px, max(1, int(upsample)))
    if found is None:
        raise ValueError('상관 피크가 뚜렷하지 않아 이동량을 잴 수 없습니다 (무늬가 없거나 평평함)')
    return _wrap(found[0], H), _wrap(found[1], W), peak, psr, second


def phase_correlation(img1, img2, roi=None, upsample=100) -> ShiftResult:
    """위상 상관으로 이미지 1 → 이미지 2 이동량을 잰다. roi는 두 이미지에 같이 적용한다."""
    a0 = np.asarray(img1, dtype=np.float64)
    b0 = np.asarray(img2, dtype=np.float64)
    if a0.shape != b0.shape:
        raise ValueError(f'이미지 크기가 다릅니다 ({_size_text(a0.shape)} / {_size_text(b0.shape)})')
    roi = clip_roi(roi, a0.shape)
    a, b = _crop(a0, roi), _crop(b0, roi)
    H, W = a.shape
    if min(H, W) < MIN_ROI:
        raise ValueError('분석 영역이 너무 작습니다')
    dy, dx, peak, psr, second = _correlate(a, b, upsample)
    # 큰 이동: Hann 창이 두 이미지의 다른 자리를 눌러 0 쪽으로 치우친다 (300 px에서 약 0.05 px).
    # 정수 이동만큼 겹친 영역으로 잘라 같은 내용끼리 다시 재면 남은 소수점만 잰다.
    iy, ix = int(round(dy)), int(round(dx))
    if max(abs(iy), abs(ix)) >= REFINE_MIN_SHIFT:
        pa, pb = _overlap(a, b, iy, ix)
        if pa is not None and min(pa.shape) >= 2 * MIN_ROI:
            try:
                ry, rx = _correlate(pa, pb, upsample)[:2]
            except ValueError:
                ry = rx = None
            if ry is not None and abs(ry) <= 1.0 and abs(rx) <= 1.0:
                dy, dx = iy + ry, ix + rx

    ncc = _overlap_ncc(_bandpass(a), _bandpass(b), int(round(dy)), int(round(dx)))
    res = ShiftResult(dx_px=dx, dy_px=dy, method=METHOD_PHASE, peak=peak,
                      psr=psr, second_ratio=second, ncc=ncc, roi=roi)
    area = '분석 영역(ROI)' if roi is not None else '이미지'
    if not peak >= PEAK_WEAK:
        res.warnings.append(f'상관 피크가 낮습니다 ({peak:.3f}) — 두 이미지에 같은 무늬가 충분히 보이는지 확인하세요')
    if second >= SECOND_PEAK_WARN:
        res.warnings.append(f'비슷한 높이의 피크가 여러 개입니다 (2등/1등 {second:.2f}) — 반복 무늬로 위치가 모호할 수 있습니다')
    if abs(dx) > NEAR_HALF * W or abs(dy) > NEAR_HALF * H:
        res.warnings.append(f'이동량이 {area}의 절반에 가깝습니다 — 방향(±)이 모호할 수 있어 템플릿 추적을 권장합니다')
    if not ncc >= OVERLAP_NCC_WEAK:
        res.warnings.append(f'겹친 영역의 일치도가 낮습니다 (NCC {ncc:.2f})')
    return res


# ── 템플릿 추적 ─────────────────────────────────────────

def _standardize(img):
    """평균 0·표준편차 1로 (16비트 영상의 누적합 정밀도 손실을 막는다)."""
    a = np.asarray(img, dtype=np.float64)
    a = a - a.mean()
    s = a.std()
    return a / s if s > 0 else a


def _window_sums(img, h, w):
    """모든 (h, w) 창의 합 — 크기 (H-h+1, W-w+1)."""
    c = np.zeros((img.shape[0] + 1, img.shape[1] + 1))
    c[1:, 1:] = img.cumsum(0).cumsum(1)
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


class _Searcher:
    """이미지 2 하나에 템플릿 여러 개를 찾을 때 FFT·누적합을 한 번만 만든다."""

    def __init__(self, img2):
        self.img = _standardize(img2)
        self.H, self.W = self.img.shape
        self.F = np.fft.rfft2(self.img)
        self._sums = {}

    def ncc_map(self, template):
        h, w = template.shape
        if h > self.H or w > self.W:
            raise ValueError('템플릿이 이미지 2보다 큽니다')
        t = template - template.mean()
        tn = math.sqrt(float((t * t).sum()))
        if not tn > 0:
            raise ValueError('템플릿 영역에 무늬가 없습니다 (밝기가 균일)')
        Ft = np.fft.rfft2(t, s=(self.H, self.W))
        num = np.fft.irfft2(self.F * np.conj(Ft), s=(self.H, self.W))[:self.H - h + 1, :self.W - w + 1]
        if (h, w) not in self._sums:
            s1 = _window_sums(self.img, h, w)
            s2 = _window_sums(self.img * self.img, h, w)
            var = np.maximum(s2 - s1 * s1 / (h * w), 0.0)
            self._sums[(h, w)] = np.sqrt(var)
        den = self._sums[(h, w)] * tn
        # 거의 균일한 창(분모 ≈ 0)은 일치로 보지 않는다
        ok = den > tn * 1e-6 * math.sqrt(h * w)
        out = np.zeros_like(num)
        out[ok] = num[ok] / den[ok]
        return np.clip(out, -1.0, 1.0)


def auto_template_rois(img, count=3):
    """템플릿 후보 (x, y, w, h): 가로·세로 1/5 크기 창 중 윤곽이 가장 강한 곳부터.

    윤곽 세기는 (원본 − 5×5 평균) 제곱합이라 카메라에 고정된 완만한 조명 얼룩은 고르지 않는다.
    """
    g = np.asarray(img, dtype=np.float64)
    H, W = g.shape
    th, tw = max(MIN_ROI, H // 5), max(MIN_ROI, W // 5)
    f = max(1, min(H, W) // 300)                       # 1200 px → 4배 줄여서 계산
    hs, ws = H // f, W // f
    small = g[:hs * f, :ws * f].reshape(hs, f, ws, f).mean(axis=(1, 3))
    energy = (small - _box_mean(small, 2)) ** 2
    sh, sw = max(1, th // f), max(1, tw // f)
    if sh > hs or sw > ws:
        return [(0, 0, W, H)]
    sums = _window_sums(energy, sh, sw)
    step_y, step_x = max(1, sh // 4), max(1, sw // 4)
    cand = []
    for yy in range(0, sums.shape[0], step_y):
        for xx in range(0, sums.shape[1], step_x):
            cand.append((float(sums[yy, xx]), xx * f, yy * f))
    cand.sort(reverse=True)
    picked = []
    for _e, x, y in cand:
        if all(abs(x - px) >= tw // 2 or abs(y - py) >= th // 2 for px, py in picked):
            picked.append((x, y))
        if len(picked) >= count:
            break
    return [(x, y, tw, th) for x, y in picked]


def template_track(img1, img2, roi=None, upsample=100) -> ShiftResult:
    """이미지 1의 템플릿을 이미지 2 전체에서 NCC로 찾고 위상 상관으로 소수점을 다듬는다.

    roi를 주지 않으면 auto_template_rois 후보를 모두 찾아, 경고가 없고 일치도는 높으면서
    비슷한 곳(2등 피크)이 적은 것을 쓴다 (마크의 똑같은 두 사각형처럼 헷갈리는 템플릿을 피한다).
    두 이미지 모두 대역 통과한 뒤 찾는다 — 화면 위쪽 어두운 띠처럼 카메라에 고정된 밝기 기울기가
    템플릿을 차지하면 같은 높이 어디서나 맞아 버리기 때문이다.
    """
    a = _bandpass(np.asarray(img1, dtype=np.float64))
    b = _bandpass(np.asarray(img2, dtype=np.float64))
    searcher = _Searcher(b)
    if roi is not None:
        rois = [clip_roi(roi, a.shape)]
    else:
        rois = [clip_roi(r, a.shape) for r in auto_template_rois(a)]
    best = None
    errors = []
    for r in rois:
        try:
            res = _track_one(a, b, searcher, r, upsample)
        except ValueError as e:
            errors.append(str(e))
            continue
        if best is None or _template_score(res) > _template_score(best):
            best = res
    if best is None:
        raise ValueError(errors[0] if errors else '템플릿을 찾지 못했습니다')
    if roi is None:
        best.notes.append(f'템플릿 자동 선택 ({roi_text(best.roi)})')
    return best


def _template_score(res):
    """후보 비교: 경고 없는 것 먼저, 그다음 일치도는 높고 비슷한 곳(2등)은 적은 것."""
    second = res.second_ratio if np.isfinite(res.second_ratio) else 1.0
    return (res.reliable, res.ncc - 0.5 * max(0.0, second))


def _track_one(a, b, searcher, roi, upsample):
    x0, y0, w, h = roi
    template = _crop(a, roi)
    ncc = searcher.ncc_map(_standardize(template))
    v, u = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
    peak = float(ncc[v, u])
    ry, rx = max(2, h // 4), max(2, w // 4)
    side = ncc.copy()
    side[max(0, v - ry):v + ry + 1, max(0, u - rx):u + rx + 1] = -np.inf
    finite = side[np.isfinite(side)]
    second = float(finite.max() / peak) if finite.size and peak > 0 else float('nan')

    # 소수점: 찾은 자리의 이미지 2 조각과 템플릿을 위상 상관 (남은 이동은 ±0.5 px 안)
    patch = b[v:v + h, u:u + w]
    fy = fx = 0.0
    try:
        sub = phase_correlation(template, patch, upsample=upsample)
        if abs(sub.dx_px) <= 1.0 and abs(sub.dy_px) <= 1.0:
            fx, fy = sub.dx_px, sub.dy_px
        else:
            raise ValueError
    except ValueError:
        if 0 < v < ncc.shape[0] - 1:
            fy = _parabola(ncc[v - 1, u], ncc[v, u], ncc[v + 1, u])
        if 0 < u < ncc.shape[1] - 1:
            fx = _parabola(ncc[v, u - 1], ncc[v, u], ncc[v, u + 1])
    res = ShiftResult(dx_px=float(u - x0) + fx, dy_px=float(v - y0) + fy, method=METHOD_TEMPLATE,
                      peak=peak, psr=float('nan'), second_ratio=second, ncc=peak, roi=roi)
    if peak < TEMPLATE_NCC_WEAK:
        res.warnings.append(f'템플릿 일치도가 낮습니다 (NCC {peak:.2f}) — 무늬가 화면 밖으로 나갔거나 모양이 바뀌었을 수 있습니다')
    if second >= TEMPLATE_SECOND_WARN:
        res.warnings.append(f'템플릿과 비슷한 곳이 여러 곳입니다 (2등/1등 {second:.2f}) — 반복 무늬가 없는 곳을 ROI로 지정하세요')
    return res


# ── 방법 고르기 ─────────────────────────────────────────

def measure_shift(img1, img2, method=METHOD_AUTO, roi=None, upsample=100) -> ShiftResult:
    """이미지 1 → 이미지 2 이동량. method: 'auto' / 'phase' / 'template'.

    화면 고정 오버레이(두 장에서 그대로인 글자·십자선)가 있으면 그 픽셀을 빼고 재며 경고한다.
    """
    a = np.asarray(img1, dtype=np.float64)
    b = np.asarray(img2, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f'이미지 크기가 다릅니다 ({_size_text(a.shape)} / {_size_text(b.shape)})')
    if method not in (METHOD_AUTO, METHOD_PHASE, METHOD_TEMPLATE):
        raise ValueError(f'알 수 없는 방법: {method}')
    roi = clip_roi(roi, a.shape)
    mask = fixed_overlay_mask(a, b, method, roi, upsample)
    if mask is None:
        return _measure(a, b, method, roi, upsample)
    res = _measure(_fill_masked(a, mask), _fill_masked(b, mask), method, roi, upsample)
    res.warnings.append(f'화면 고정 오버레이 감지 ({mask.mean() * 100:.1f}% 픽셀, 빼고 계산) — '
                        f'원본(PR.png) 사용 권장')
    return res


def _local_range(img):
    """3×3 둘레의 최대 − 최소."""
    H, W = img.shape
    p = np.pad(img, 1, mode='edge')
    lo = hi = img
    for i in range(3):
        for j in range(3):
            v = p[i:i + H, j:j + W]
            lo = np.minimum(lo, v)
            hi = np.maximum(hi, v)
    return hi - lo


def _dilate(mask):
    H, W = mask.shape
    p = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for i in range(3):
        for j in range(3):
            out |= p[i:i + H, j:j + W]
    return out


def _fill_masked(img, mask, r=OVERLAY_FILL_RADIUS):
    """mask 픽셀을 둘레(2r+1)²의 mask 아닌 픽셀 평균으로 채운다 (둘레에 없으면 전체 평균)."""
    keep = (~mask).astype(np.float64)
    num = _box_mean(img * keep, r)
    den = _box_mean(keep, r)
    rest = img[~mask]
    fallback = float(rest.mean()) if rest.size else 0.0
    fill = np.where(den > 1e-9, num / np.maximum(den, 1e-9), fallback)
    return np.where(mask, fill, img)


def _shift_image(img, dy, dx):
    """img를 (dx, dy) px 옮긴 예측 — 정수는 잘라 옮기고 소수는 푸리에. 범위 밖·가장자리 4 px는 nan."""
    H, W = img.shape
    iy, ix = int(round(dy)), int(round(dx))
    fy, fx = dy - iy, dx - ix
    src = img
    if fy or fx:
        ky = np.fft.fftfreq(H)[:, None]
        kx = np.fft.rfftfreq(W)[None, :]
        src = np.fft.irfft2(np.fft.rfft2(img) * np.exp(-2j * np.pi * (kx * fx + ky * fy)), s=(H, W))
    out = np.full((H, W), np.nan)
    y0, y1 = max(0, iy), min(H, H + iy)
    x0, x1 = max(0, ix), min(W, W + ix)
    if y1 > y0 and x1 > x0:
        out[y0:y1, x0:x1] = src[y0 - iy:y1 - iy, x0 - ix:x1 - ix]
    e = 4
    out[:e, :] = out[-e:, :] = np.nan
    out[:, :e] = out[:, -e:] = np.nan
    return out


def fixed_overlay_mask(a, b, method=METHOD_AUTO, roi=None, upsample=100):
    """화면에 고정된 오버레이 픽셀 mask (한 칸 넓힘). 없으면 None.

    1) 후보: 두 장에서 값이 정확히 같고 3×3 둘레 밝기 차가 큰 픽셀 (글자·선의 윤곽)
    2) 후보를 채우고 이동량을 잰 뒤, 이미지 1을 그만큼 옮긴 예측과 이미지 2가 크게 다른 후보만
       확정한다 — 장면이었다면 바뀌었어야 할 자리가 그대로인 것. 우연히 같은 장면 픽셀(잡음 작은
       8비트에서 20% 넘음)이나 움직이지 않은 장면은 예측과 맞아 빠진다.
    포화·검은 픽셀은 따로 빼지 않는다: 장면과 함께 움직인 포화 영역은 예측과 맞아 빠지고,
    화면에 고정된 포화 영역(글자, 고정 반사)은 오버레이처럼 빼는 것이 맞다.
    """
    sample = a[::4, ::4]
    lo, hi = np.percentile(sample, [1, 99])
    contrast = OVERLAY_CONTRAST * float(hi - lo)
    if not contrast > 0:
        return None
    least = max(OVERLAY_MIN_PIXELS, OVERLAY_MIN_FRACTION * a.size)
    cand = (a == b) & (_local_range(a) >= contrast) & (_local_range(b) >= contrast)
    if cand.sum() < least:
        return None
    try:
        first = _measure(_fill_masked(a, cand), _fill_masked(b, cand), method, roi, upsample)
    except ValueError:
        return None
    pred = _shift_image(a, first.dy_px, first.dx_px)
    with np.errstate(invalid='ignore'):
        confirmed = cand & (np.abs(pred - b) >= contrast)
    if confirmed.sum() < least:
        return None
    # 확정한 윤곽에서 그대로인(값이 같은) 이웃으로 몇 칸 넓혀 글자 획 안쪽·번진 가장자리까지 덮는다.
    # 남은 고정 무늬가 작은 이동(2 px 안팎)의 피크와 겹쳐 0 쪽으로 끄는 것을 줄인다
    mask = _dilate(confirmed)
    same = a == b
    for _ in range(OVERLAY_GROW_STEPS):
        mask |= _dilate(mask) & same
    return mask


def _measure(a, b, method, roi, upsample):
    """방법 하나로 잰다 (오버레이 처리 없음). roi는 이미 잘라 둔 값."""
    if method == METHOD_PHASE:
        return phase_correlation(a, b, roi, upsample)
    if method == METHOD_TEMPLATE:
        return template_track(a, b, roi, upsample)

    try:
        pc = phase_correlation(a, b, roi, upsample)
    except ValueError as e:
        tm = template_track(a, b, roi, upsample)
        tm.notes.insert(0, f'자동: 위상 상관을 잴 수 없어({e}) 템플릿 추적 사용')
        return tm
    h, w = (roi[3], roi[2]) if roi is not None else a.shape
    small = abs(pc.dx_px) <= AUTO_PHASE_LIMIT * w and abs(pc.dy_px) <= AUTO_PHASE_LIMIT * h
    if pc.reliable and small:
        pc.notes.append('자동: 위상 상관 사용')
        return pc
    try:
        tm = template_track(a, b, roi, upsample)
    except ValueError as e:
        pc.notes.append(f'자동: 템플릿 추적 실패 ({e}) — 위상 상관 결과 사용')
        return pc
    why = '이동이 커서' if not small else '위상 상관 결과가 불안정해'
    if tm.reliable or not pc.reliable and tm.ncc >= pc.ncc:
        tm.notes.insert(0, f'자동: {why} 템플릿 추적 사용')
        return tm
    pc.notes.append('자동: 템플릿 추적도 불안정해 위상 상관 결과 사용')
    return pc


# ── 이동 → 각도·배율 ────────────────────────────────────

def expected_vector(mapping, sx, sy):
    """축 대응 (swap, gx, gy)로 지시 이동 (sx, sy)가 화면에서 가야 할 벡터 (화면 x 오른쪽, y 아래)."""
    swap, gx, gy = mapping
    p, q = (sy, sx) if swap else (sx, sy)
    return gx * p, gy * q


def mapping_label(mapping):
    """'X→오른쪽, Y→아래' 꼴 (자동이면 '자동')."""
    if mapping == MAPPING_AUTO:
        return '자동'
    swap, gx, gy = mapping
    horiz = '오른쪽' if gx > 0 else '왼쪽'
    vert = '아래' if gy > 0 else '위'
    return f'X→{vert}, Y→{horiz}' if swap else f'X→{horiz}, Y→{vert}'


def _angle_deg(u, v):
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu == 0 or nv == 0:
        return 0.0
    c = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _candidates(sx, sy):
    """서로 다른 기대 벡터마다 [(벡터, 축 대응)] — 한 축 이동이면 4개, 두 축이면 8개."""
    out = []
    for m in MAPPING_CHOICES:
        e = expected_vector(m, sx, sy)
        if not any(abs(e[0] - v[0]) < 1e-9 and abs(e[1] - v[1]) < 1e-9 for v, _m in out):
            out.append((e, m))
    return out


def mapping_separation_deg(sx, sy):
    """축 대응 후보 기대 방향끼리의 가장 작은 각도. 한 축 이동 90°, (400, 4) 1.1°, (400, 395) 0.7°."""
    vecs = [e for e, _m in _candidates(sx, sy)]
    if len(vecs) < 2:
        return 180.0
    return min(_angle_deg(u, v) for i, u in enumerate(vecs) for v in vecs[i + 1:])


def infer_mapping(sx, sy, mx, my):
    """측정 방향 (mx, my)에 가장 가까운 축 대응 → (축 대응 또는 None, 못 정한 이유).

    후보끼리 MAPPING_MIN_SEPARATION_DEG 이상 떨어져 있을 때만 정한다 (카메라가 그 절반보다 덜
    돌아가 있다는 가정). 측정 이동이 MIN_MOTION_PX보다 작으면 정하지 않는다.
    """
    sep = mapping_separation_deg(sx, sy)
    if sep < MAPPING_MIN_SEPARATION_DEG:
        return None, (f'스테이지 X·Y를 함께 움직여 축 대응 후보가 {sep:.1f}°밖에 떨어지지 않아 자동으로 '
                      f'정할 수 없습니다 — 축 대응을 직접 고르세요 (φ·배율 없음)')
    if math.hypot(mx, my) < MIN_MOTION_PX:
        return None, f'측정 이동이 {MIN_MOTION_PX:g} px보다 작아 화면 방향을 정할 수 없습니다'
    best = min(_candidates(sx, sy), key=lambda c: _angle_deg(c[0], (mx, my)))
    return best[1], ''


_SCREEN_DIR = {('x', 1): '화면 오른쪽(+x)', ('x', -1): '화면 왼쪽(−x)',
               ('y', 1): '화면 아래(+y)', ('y', -1): '화면 위(−y)'}


def _mapping_text(sx, sy, swap, gx, gy):
    parts = []
    if sx:
        parts.append('스테이지 +X → ' + (_SCREEN_DIR[('y', gy)] if swap else _SCREEN_DIR[('x', gx)]))
    if sy:
        parts.append('스테이지 +Y → ' + (_SCREEN_DIR[('x', gx)] if swap else _SCREEN_DIR[('y', gy)]))
    return ', '.join(parts)


def analyze_motion(dx_px, dy_px, pixel_size_um=DEFAULT_PIXEL_SIZE_UM, stage_dx_um=0.0, stage_dy_um=0.0,
                   mapping=MAPPING_AUTO, mapping_source=None):
    """이미지 이동 (px)과 지시 스테이지 이동 (µm) → µm 이동, 각도 φ, 배율, 잔차, 해석 문장.

    mapping: MAPPING_AUTO(infer_mapping으로 정함) 또는 (swap, gx, gy).
    스테이지 이동이 0이면 이동량만 낸다. 측정 이동이 0.5 px보다 작거나 자동 축 대응을 정할 수
    없으면 φ·배율·잔차는 None이고 warnings에 이유를 남긴다.
    """
    ps = float(pixel_size_um)
    if not ps > 0:
        raise ValueError('픽셀 크기는 0보다 커야 합니다')
    sx, sy = float(stage_dx_um or 0.0), float(stage_dy_um or 0.0)
    mx, my = float(dx_px) * ps, float(dy_px) * ps
    out = {
        'dx_um': mx, 'dy_um': my,
        'length_px': math.hypot(dx_px, dy_px), 'length_um': math.hypot(mx, my),
        'stage_dx_um': sx, 'stage_dy_um': sy,
        'expected_um': None, 'expected_px': None, 'mapping': '', 'mapping_key': None, 'mapping_source': '',
        'phi_deg': None, 'phi_mrad': None, 'along_um': None, 'perp_um': None,
        'scale_ratio': None, 'pixel_size_implied': None,
        'residual_um': None, 'residual_px': None, 'warnings': [],
    }
    out['interpretation'] = _shift_sentence(dx_px, dy_px, mx, my)
    if sx == 0 and sy == 0:
        return out
    if out['length_px'] < MIN_MOTION_PX:
        out['warnings'].append(f'측정 이동이 {MIN_MOTION_PX:g} px보다 작아 방향(φ)·배율을 낼 수 없습니다')
        out['interpretation'] += ' — φ·배율 없음 (이동이 너무 작음)'
        return out
    if mapping == MAPPING_AUTO:
        mapping, why = infer_mapping(sx, sy, dx_px, dy_px)
        if mapping is None:
            out['warnings'].append(why)
            out['interpretation'] += ' — φ·배율 없음 (축 대응 미정)'
            return out
        mapping_source = mapping_source or '자동'
    swap, gx, gy = mapping
    ex, ey = expected_vector(mapping, sx, sy)
    out['mapping_key'] = tuple(mapping)
    out['mapping_source'] = mapping_source or '지정'
    L = math.hypot(ex, ey)
    cross = ex * my - ey * mx                       # 화면 좌표(y 아래)의 외적
    along = (ex * mx + ey * my) / L
    perp = -cross / L                               # 화면 반시계 쪽이 +
    phi = math.degrees(math.atan2(perp, along))
    scale = out['length_um'] / L
    out.update({
        'expected_um': (ex, ey), 'expected_px': (ex / ps, ey / ps),
        'mapping': _mapping_text(sx, sy, swap, gx, gy),
        'phi_deg': phi, 'phi_mrad': math.radians(phi) * 1000.0,
        'along_um': along, 'perp_um': perp, 'scale_ratio': scale,
        'pixel_size_implied': ps / scale if scale > 0 else None,
        'residual_um': (mx - ex, my - ey), 'residual_px': ((mx - ex) / ps, (my - ey) / ps),
    })
    out['interpretation'] = _angle_sentence(sx, sy, ex, ey, perp, ps, phi, scale)
    return out


def _fmt_um(v):
    return f'{v:+g}'


def _shift_sentence(dx, dy, mx, my):
    h = '오른쪽' if dx >= 0 else '왼쪽'
    v = '아래' if dy >= 0 else '위'
    return (f'이미지가 {h} {abs(dx):.2f} px({abs(mx):.2f} µm), '
            f'{v} {abs(dy):.2f} px({abs(my):.2f} µm) 이동')


def _angle_sentence(sx, sy, ex, ey, perp, ps, phi, scale):
    if sx and not sy:
        axis, move = 'X', f'스테이지 X {_fmt_um(sx)} µm'
    elif sy and not sx:
        axis, move = 'Y', f'스테이지 Y {_fmt_um(sy)} µm'
    else:
        axis, move = '이동 방향', f'스테이지 (X {_fmt_um(sx)}, Y {_fmt_um(sy)}) µm'
    if ex == 0 or ey == 0:
        side = '화면 세로' if ey == 0 else '화면 가로'
    else:
        side = '수직 방향'
    head = f'{move} 이동에 {side} {abs(perp) / ps:.1f} px({abs(perp):.2f} µm)'
    if abs(phi) <= PARALLEL_DEG:
        text = f'{head} → 카메라 축은 스테이지 {axis}와 평행 (|φ| ≤ {PARALLEL_DEG:g}°)'
    else:
        text = (f'{head} → 카메라 축이 스테이지 {axis}에 대해 {abs(phi):.2f}° 돌아가 있음 '
                f'(φ = {phi:+.2f}°, 화면에서 반시계 +)')
    if scale > 0 and abs(scale - 1.0) > SCALE_NOTE:
        text += f' · 이동 거리 비 {scale:.4f} (픽셀 크기 {ps / scale:.4f} µm/px에 해당)'
    return text


# ── 여러 장 (이미지 1 기준) ──────────────────────────────

@dataclass
class PairResult:
    index: int                  # 비교한 이미지 번호 (2부터)
    name1: str
    name2: str
    shift: ShiftResult
    motion: dict

    @property
    def warnings(self):
        return list(self.shift.warnings) + list(self.motion.get('warnings') or [])


@dataclass
class BatchResult:
    pixel_size_um: float
    stage_um: tuple
    method: str
    roi: tuple | None
    names: list
    pairs: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # (번호, 이름, 문구)
    mapping: object = MAPPING_AUTO                 # 사용자가 고른 축 대응 (자동 또는 (swap, gx, gy))
    mapping_used: tuple | None = None              # 실제로 쓴 축 대응 (못 정했으면 None)
    warnings: list = field(default_factory=list)   # 여러 장 전체에 대한 경고 (축 대응 불일치 등)

    @property
    def stats(self):
        return batch_stats(self.pairs)

    @property
    def excluded(self):
        """경고가 있어 평균·표준편차에서 뺀 쌍 수."""
        return sum(1 for p in self.pairs if p.warnings)


_STAT_KEYS = ('dx_px', 'dy_px', 'dx_um', 'dy_um', 'phi_deg', 'phi_mrad', 'scale_ratio')


def batch_stats(pairs):
    """{키: (평균, 표준편차 ddof=1 또는 None, 개수)}.

    경고가 있는 쌍은 빼고(BatchResult.excluded), φ 등이 없는 쌍은 그 키에서 뺀다.
    """
    out = {}
    clean = [p for p in pairs if not p.warnings]
    for key in _STAT_KEYS:
        vals = []
        for p in clean:
            v = getattr(p.shift, key) if key in ('dx_px', 'dy_px') else p.motion.get(key)
            if v is not None and np.isfinite(v):
                vals.append(float(v))
        if vals:
            arr = np.asarray(vals)
            out[key] = (float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else None, arr.size)
    return out


def analyze_images(images, pixel_size_um=DEFAULT_PIXEL_SIZE_UM, stage_um=(0.0, 0.0),
                   method=METHOD_AUTO, roi=None, names=None, upsample=100, progress=None,
                   mapping=MAPPING_AUTO):
    """이미지 2..N을 각각 이미지 1과 비교한다. images는 경로 또는 2D 배열.

    한 장이 실패해도(어떤 예외든) 나머지는 계속하고 errors에 남긴다. progress(done, total)를
    시작과 한 장마다 부르며, progress가 AnalysisCancelled를 던지면 그대로 멈춘다.
    각도·배율은 다 잰 뒤 interpret_batch로 한꺼번에 계산한다 (자동 축 대응은 여러 장이 하나로).
    """
    images = list(images)
    if len(images) < 2:
        raise ValueError('이미지가 2장 이상 필요합니다')
    if names is None:
        names = [os.path.basename(str(x)) if isinstance(x, (str, os.PathLike)) else f'이미지 {i + 1}'
                 for i, x in enumerate(images)]
    sx, sy = (float(v or 0.0) for v in stage_um)
    batch = BatchResult(pixel_size_um=float(pixel_size_um), stage_um=(sx, sy), method=method,
                        roi=None, names=list(names), mapping=mapping)
    total = len(images) - 1
    if progress is not None:
        progress(0, total)
    ref = _as_gray(images[0])
    batch.roi = clip_roi(roi, ref.shape)
    for k in range(1, len(images)):
        try:
            img = _as_gray(images[k])
            shift = measure_shift(ref, img, method, batch.roi, upsample)
            batch.pairs.append(PairResult(k + 1, names[0], names[k], shift, {}))
        except AnalysisCancelled:
            raise
        except Exception as e:      # 한 장의 어떤 실패도 나머지 쌍을 막지 않는다
            batch.errors.append((k + 1, names[k], str(e) or type(e).__name__))
        if progress is not None:
            progress(k, total)
    return interpret_batch(batch)


def interpret_batch(batch, pixel_size_um=None, stage_um=None, mapping=None):
    """잰 이동량으로 각도·배율을 (다시) 계산한다. 픽셀 크기·지시 이동·축 대응만 바뀌면 다시 재지 않는다.

    자동 축 대응은 경고 없는 쌍(없으면 전체)의 평균 측정 방향으로 하나를 정해 모든 쌍에 쓰고,
    자기 방향으로는 다른 대응이 나오는 쌍에 경고한다. 모든 쌍을 계산한 뒤 한꺼번에 바꾸므로
    중간에 실패해도 batch는 이전 상태 그대로다.
    """
    ps = batch.pixel_size_um if pixel_size_um is None else float(pixel_size_um)
    if not ps > 0:
        raise ValueError('픽셀 크기는 0보다 커야 합니다')
    sx, sy = batch.stage_um if stage_um is None else tuple(float(v or 0.0) for v in stage_um)
    want = batch.mapping if mapping is None else mapping
    use, source, warnings = want, None, []
    if (sx or sy) and want == MAPPING_AUTO and batch.pairs:
        good = [p for p in batch.pairs if p.shift.reliable] or batch.pairs
        mean_dx = float(np.mean([p.shift.dx_px for p in good]))
        mean_dy = float(np.mean([p.shift.dy_px for p in good]))
        found, why = infer_mapping(sx, sy, mean_dx, mean_dy)
        if found is not None:
            use, source = found, '자동 (여러 장 평균 방향)' if len(batch.pairs) > 1 else '자동'
        elif len(batch.pairs) > 1:
            warnings.append(why)
    motions, disagree = [], 0
    for p in batch.pairs:
        m = analyze_motion(p.shift.dx_px, p.shift.dy_px, ps, sx, sy, use, source)
        if source and m['phi_deg'] is not None:
            own, _why = infer_mapping(sx, sy, p.shift.dx_px, p.shift.dy_px)
            if own is not None and expected_vector(own, sx, sy) != expected_vector(use, sx, sy):
                m['warnings'].append(f'이 쌍의 측정 방향은 다른 축 대응({mapping_label(own)})에 더 가깝습니다 '
                                     f'— 축 대응을 직접 확인하세요')
                disagree += 1
        motions.append(m)
    if disagree:
        warnings.append(f'{disagree}쌍이 여러 장 평균과 다른 축 대응({mapping_label(use)} 아님)에 더 가깝습니다 '
                        f'— 축 대응을 직접 고르세요')
    for p, m in zip(batch.pairs, motions):
        p.motion = m
    batch.pixel_size_um, batch.stage_um, batch.mapping = ps, (sx, sy), want
    batch.mapping_used = use if use != MAPPING_AUTO else None
    batch.warnings = warnings
    return batch


def _as_gray(x):
    if isinstance(x, (str, os.PathLike)):
        return load_gray(x)
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 3:
        a = a[..., :3] @ np.array([0.299, 0.587, 0.114]) if a.shape[2] >= 3 else a[..., 0]
    if a.ndim != 2:
        raise ValueError('2차원 이미지가 아닙니다')
    if not np.isfinite(a).all():
        raise ValueError('NaN·무한대 픽셀이 있습니다')
    return a


# ── 보고 (클립보드 문장, CSV) ────────────────────────────

def _size_text(shape):
    return f'{shape[1]}×{shape[0]}'


def roi_text(roi):
    if roi is None:
        return '전체'
    x, y, w, h = roi
    return f'x {x}, y {y}, {w}×{h} px'


def _num(v, fmt):
    return '' if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v, fmt)


def quality_text(s: ShiftResult) -> str:
    """신뢰도 한 줄. 위상 상관: 피크 높이·PSR·2등 비·겹친 영역 NCC / 템플릿: NCC·2등 비."""
    if s.method == METHOD_PHASE:
        return (f'상관 피크 {_num(s.peak, ".3f")}, PSR {_num(s.psr, ".0f")}, '
                f'2등/1등 {_num(s.second_ratio, ".2f")}, 겹친 영역 NCC {_num(s.ncc, ".3f")}')
    return f'템플릿 NCC {_num(s.ncc, ".3f")}, 2등/1등 {_num(s.second_ratio, ".2f")}'


def pair_row(p):
    """표·CSV 한 줄 값 (문자열). CSV_HEADERS 순서."""
    s, m = p.shift, p.motion
    res = m.get('residual_um') or (None, None)
    return [
        str(p.index), _csv_text(p.name1), _csv_text(p.name2), METHOD_LABELS.get(s.method, s.method),
        _num(s.dx_px, '.3f'), _num(s.dy_px, '.3f'),
        _num(m['dx_um'], '.3f'), _num(m['dy_um'], '.3f'), _num(m['length_um'], '.3f'),
        _num(m['phi_deg'], '.4f'), _num(m['phi_mrad'], '.3f'), _num(m['scale_ratio'], '.5f'),
        _num(res[0], '.3f'), _num(res[1], '.3f'),
        _num(s.ncc, '.3f'), _num(s.peak, '.3f'), _num(s.psr, '.1f'), _num(s.second_ratio, '.2f'),
        _csv_text(' / '.join(p.warnings)), _csv_text(m['interpretation']),
    ]


CSV_HEADERS = ['번호', '이미지 1', '비교 이미지', '방법', 'Δx (px)', 'Δy (px)', 'Δx (µm)', 'Δy (µm)',
               '거리 (µm)', 'φ (°)', 'φ (mrad)', '배율', '잔차 x (µm)', '잔차 y (µm)',
               '일치도 NCC', '상관 피크', 'PSR', '2등/1등 피크', '경고', '해석']


def stats_count_text(n, excluded):
    """'n=3, 경고 1쌍 제외' — 평균·표준편차에 쓴 쌍 수와 경고로 뺀 쌍 수."""
    return f'n={n}' + (f', 경고 {excluded}쌍 제외' if excluded else '')


def stats_summary(batch: BatchResult) -> str:
    """화면 한 줄: φ(또는 이동량) 평균 ± 표준편차, 쓴 쌍·뺀 쌍 수. 쌍이 하나면 빈 문자열."""
    if len(batch.pairs) < 2:
        return ''
    stats, excluded = batch.stats, batch.excluded

    def pm(key, fmt):
        mean, std, _n = stats[key]
        return format(mean, fmt) + (f' ± {format(std, fmt.replace("+", ""))}' if std is not None else '')

    if 'phi_deg' in stats:
        n = stats['phi_deg'][2]
        text = (f'φ 평균 {pm("phi_deg", "+.4f")}° ({stats_count_text(n, excluded)}) · '
                f'{pm("phi_mrad", "+.3f")} mrad')
        if 'scale_ratio' in stats:
            text += f' · 배율 평균 {stats["scale_ratio"][0]:.5f}'
        return text
    if 'dx_px' in stats:
        n = stats['dx_px'][2]
        return (f'Δx 평균 {pm("dx_px", "+.3f")} px · Δy 평균 {pm("dy_px", "+.3f")} px '
                f'({stats_count_text(n, excluded)})')
    return f'평균·표준편차 없음 — 모든 쌍({excluded})에 경고가 있습니다'


def mapping_text(batch: BatchResult) -> str:
    """'자동 → X→오른쪽, Y→아래' / '지정: …' / '자동 (정하지 못함)'."""
    if batch.mapping != MAPPING_AUTO:
        return f'지정: {mapping_label(batch.mapping)}'
    if batch.mapping_used is None:
        return '자동 (정하지 못함)'
    return f'자동 → {mapping_label(batch.mapping_used)}'


def _stat_line(stats, key, label, unit, fmt, excluded=0):
    if key not in stats:
        return None
    mean, std, n = stats[key]
    sd = f' ± {format(std, fmt.replace("+", ""))}' if std is not None else ''
    return f'{label}: 평균 {format(mean, fmt)}{sd} {unit} ({stats_count_text(n, excluded)}, 표준편차 ddof=1)'


def _csv_text(value):
    """엑셀이 수식으로 읽지 않게: =, +, -, @, 탭, CR로 시작하는 글은 앞에 ' 를 붙인다 (숫자 칸에는 쓰지 않는다)."""
    text = str(value)
    return "'" + text if text.startswith(CSV_FORMULA_PREFIXES) else text


def format_report(batch: BatchResult) -> str:
    """클립보드용 문장 (사람이 읽고 그대로 붙여 넣는다)."""
    sx, sy = batch.stage_um
    lines = ['[이미지 이동량 분석]',
             f'픽셀 크기: {batch.pixel_size_um:g} µm/px',
             f'지시 스테이지 이동: X {sx:+g} µm, Y {sy:+g} µm' if (sx or sy) else '지시 스테이지 이동: 없음 (이동량만)',
             f'방법: {METHOD_LABELS.get(batch.method, batch.method)} · ROI: {roi_text(batch.roi)}'
             + (f' · 축 대응: {mapping_text(batch)}' if (sx or sy) else ''),
             '부호: Δx 화면 오른쪽 +, Δy 화면 아래 +, φ는 기대 방향 → 측정 방향이 화면에서 반시계면 +',
             '']
    for w in batch.warnings:
        lines.append(f'경고: {w}')
    for p in batch.pairs:
        s, m = p.shift, p.motion
        lines.append(f'#{p.index} {p.name1} → {p.name2} ({METHOD_LABELS.get(s.method, s.method)})')
        lines.append(f'  Δx {s.dx_px:+.3f} px ({m["dx_um"]:+.3f} µm), Δy {s.dy_px:+.3f} px ({m["dy_um"]:+.3f} µm), '
                     f'거리 {m["length_px"]:.3f} px ({m["length_um"]:.3f} µm)')
        if m['phi_deg'] is not None:
            rx, ry = m['residual_um']
            lines.append(f'  φ {m["phi_deg"]:+.4f}° ({m["phi_mrad"]:+.3f} mrad), 배율 {m["scale_ratio"]:.5f}, '
                         f'잔차 ({rx:+.3f}, {ry:+.3f}) µm · {m["mapping"]}')
        lines.append('  품질: ' + quality_text(s))
        for note in s.notes:
            lines.append(f'  참고: {note}')
        for w in p.warnings:
            lines.append(f'  경고: {w}')
        lines.append(f'  해석: {m["interpretation"]}')
    for idx, name, msg in batch.errors:
        lines.append(f'#{idx} {name}: 실패 — {msg}')
    stats = batch.stats
    if len(batch.pairs) > 1:
        lines.append('')
        if not stats:
            lines.append(f'평균·표준편차: 모든 쌍({batch.excluded})에 경고가 있어 내지 않음')
        for key, label, unit, fmt in (('phi_deg', 'φ', '°', '+.4f'), ('phi_mrad', 'φ', 'mrad', '+.3f'),
                                      ('dx_px', 'Δx', 'px', '+.3f'), ('dy_px', 'Δy', 'px', '+.3f'),
                                      ('scale_ratio', '배율', '', '.5f')):
            line = _stat_line(stats, key, label, unit, fmt, batch.excluded)
            if line:
                lines.append(line)
    return '\n'.join(lines)


def write_csv(path, batch: BatchResult):
    """쌍마다 한 줄 + 평균·표준편차 줄. Excel에서 한글이 깨지지 않게 utf-8-sig."""
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        sx, sy = batch.stage_um
        w.writerow(['픽셀 크기 (µm/px)', f'{batch.pixel_size_um:g}', '지시 이동 X (µm)', f'{sx:g}',
                    '지시 이동 Y (µm)', f'{sy:g}', '방법', METHOD_LABELS.get(batch.method, batch.method),
                    'ROI', roi_text(batch.roi), '축 대응', mapping_text(batch)])
        w.writerow(CSV_HEADERS)
        for p in batch.pairs:
            w.writerow(pair_row(p))
        for idx, name, msg in batch.errors:
            row = [str(idx), _csv_text(batch.names[0]) if batch.names else '', _csv_text(name)] \
                + [''] * (len(CSV_HEADERS) - 3)
            row[-2] = f'실패: {msg}'
            w.writerow(row)
        stats = batch.stats
        if len(batch.pairs) > 1:
            cols = {'dx_px': 4, 'dy_px': 5, 'dx_um': 6, 'dy_um': 7, 'phi_deg': 9, 'phi_mrad': 10, 'scale_ratio': 11}
            n_text = stats_count_text(len(batch.pairs) - batch.excluded, batch.excluded)
            for label, pick in ((f'평균 ({n_text})', 0), (f'표준편차 (ddof=1, {n_text})', 1)):
                row = [label] + [''] * (len(CSV_HEADERS) - 1)
                for key, col in cols.items():
                    if key in stats and stats[key][pick] is not None:
                        row[col] = f'{stats[key][pick]:.5f}'
                w.writerow(row)
        for warning in batch.warnings:
            w.writerow([_csv_text(f'경고: {warning}')] + [''] * (len(CSV_HEADERS) - 1))
