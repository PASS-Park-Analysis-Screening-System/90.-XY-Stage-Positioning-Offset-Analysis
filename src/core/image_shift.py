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

각도 φ (스테이지 이동을 입력했을 때)
    지시 이동 (ΔX, ΔY) µm가 화면에서 어느 방향이어야 하는지(기대 방향)는 측정 방향에 가장
    가까운 축 대응(오른쪽/왼쪽/위/아래, 축 바뀜 포함)으로 정한다. 카메라가 45° 넘게 돌아가
    있지 않다고 가정한다.
    φ = atan2(수직 성분, 진행 성분) — 기대 방향에서 측정 방향으로 화면에서 반시계 방향이 +.
    배율 = 측정 이동 거리 / 지시 이동 거리, 잔차 = 측정 벡터 − 기대 벡터 (화면 좌표).
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


class ImageLoadError(ValueError):
    """이미지를 읽을 수 없다 (형식·손상·경로)."""


# ── 이미지 읽기 ─────────────────────────────────────────

def load_gray(path) -> np.ndarray:
    """이미지 파일 → 2D float64 그레이 배열 (원래 값 범위 그대로: 8비트 0~255, 16비트 0~65535).

    bmp/png/jpg/tiff, 8·16비트 그레이와 컬러를 읽는다. 컬러는 밝기(0.299 R + 0.587 G + 0.114 B)로
    바꾼다. 여러 장이 든 TIFF는 첫 장만 쓴다.
    """
    from PIL import Image, UnidentifiedImageError

    name = os.path.basename(str(path))
    try:
        with Image.open(path) as im:
            im.seek(0)
            arr = _pil_to_gray(im)
    except FileNotFoundError:
        raise ImageLoadError(f'파일이 없습니다: {name}') from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as e:
        raise ImageLoadError(f'이미지를 읽을 수 없습니다: {name} ({e})') from None
    if arr.ndim != 2 or min(arr.shape) < MIN_ROI:
        raise ImageLoadError(f'이미지가 너무 작거나 2차원이 아닙니다: {name} {arr.shape}')
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
    """정수 피크 (py, px) 주변을 확대 DFT로 다시 찾아 소수점 위치를 낸다."""
    cy, cx = float(py), float(px)
    res = 0.1
    while True:
        offs = np.arange(-15, 16) * res          # ±1.5 단계 → 다음 단계는 10배 촘촘히
        patch = _dft_patch(spec_w, H, W, cy + offs, cx + offs)
        i, j = np.unravel_index(int(np.argmax(patch)), patch.shape)
        if 0 < i < len(offs) - 1 and 0 < j < len(offs) - 1:
            fy = _parabola(patch[i - 1, j], patch[i, j], patch[i + 1, j])
            fx = _parabola(patch[i, j - 1], patch[i, j], patch[i, j + 1])
        else:
            fy = fx = 0.0
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


def _overlap_ncc(a, b, dy, dx):
    """정수 이동 (dy, dx)로 겹친 영역의 zero-mean NCC. b[y+dy, x+dx] ≈ a[y, x]."""
    H, W = a.shape
    y0, y1 = max(0, -dy), min(H, H - dy)
    x0, x1 = max(0, -dx), min(W, W - dx)
    if y1 - y0 < 2 or x1 - x0 < 2:
        return float('nan')
    pa = a[y0:y1, x0:x1]
    pb = b[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    pa = pa - pa.mean()
    pb = pb - pb.mean()
    den = math.sqrt(float((pa * pa).sum()) * float((pb * pb).sum()))
    return float((pa * pb).sum() / den) if den > 0 else float('nan')


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
    fy, fx = _refine_peak(spec_w, H, W, py, px, max(1, int(upsample)))
    dy, dx = _wrap(fy, H), _wrap(fx, W)

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
    """이미지 1 → 이미지 2 이동량. method: 'auto' / 'phase' / 'template'."""
    a = np.asarray(img1, dtype=np.float64)
    b = np.asarray(img2, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f'이미지 크기가 다릅니다 ({_size_text(a.shape)} / {_size_text(b.shape)})')
    roi = clip_roi(roi, a.shape)
    if method == METHOD_PHASE:
        return phase_correlation(a, b, roi, upsample)
    if method == METHOD_TEMPLATE:
        return template_track(a, b, roi, upsample)
    if method != METHOD_AUTO:
        raise ValueError(f'알 수 없는 방법: {method}')

    pc = phase_correlation(a, b, roi, upsample)
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

def _axis_mapping(sx, sy, mx, my):
    """지시 이동 (sx, sy)를 화면 축으로 옮긴 기대 벡터 중 측정 (mx, my)와 가장 가까운 것.

    Returns: ((ex, ey), swap, gx, gy) — swap이면 스테이지 X↔화면 y. gx, gy는 화면 x·y 부호.
    """
    best = None
    for swap in (False, True):
        p, q = (sy, sx) if swap else (sx, sy)
        for gx in (1, -1):
            for gy in (1, -1):
                ex, ey = gx * p, gy * q
                score = ex * mx + ey * my
                if best is None or score > best[0] + 1e-12:
                    best = (score, (ex, ey), swap, gx, gy)
    return best[1:]


_SCREEN_DIR = {('x', 1): '화면 오른쪽(+x)', ('x', -1): '화면 왼쪽(−x)',
               ('y', 1): '화면 아래(+y)', ('y', -1): '화면 위(−y)'}


def _mapping_text(sx, sy, swap, gx, gy):
    parts = []
    if sx:
        parts.append('스테이지 +X → ' + (_SCREEN_DIR[('y', gy)] if swap else _SCREEN_DIR[('x', gx)]))
    if sy:
        parts.append('스테이지 +Y → ' + (_SCREEN_DIR[('x', gx)] if swap else _SCREEN_DIR[('y', gy)]))
    return ', '.join(parts)


def analyze_motion(dx_px, dy_px, pixel_size_um=DEFAULT_PIXEL_SIZE_UM, stage_dx_um=0.0, stage_dy_um=0.0):
    """이미지 이동 (px)과 지시 스테이지 이동 (µm) → µm 이동, 각도 φ, 배율, 잔차, 해석 문장.

    스테이지 이동이 0이면 이동량만 낸다 (φ 등은 None).
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
        'expected_um': None, 'expected_px': None, 'mapping': '',
        'phi_deg': None, 'phi_mrad': None, 'along_um': None, 'perp_um': None,
        'scale_ratio': None, 'pixel_size_implied': None,
        'residual_um': None, 'residual_px': None, 'warnings': [],
    }
    if sx == 0 and sy == 0:
        out['interpretation'] = _shift_sentence(dx_px, dy_px, mx, my)
        return out
    if out['length_px'] < 0.5:
        out['warnings'].append('측정 이동이 0.5 px보다 작아 화면 방향을 정할 수 없습니다')
    (ex, ey), swap, gx, gy = _axis_mapping(sx, sy, mx, my)
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
    if abs(scale - 1.0) > SCALE_NOTE:
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

    @property
    def stats(self):
        return batch_stats(self.pairs)


_STAT_KEYS = ('dx_px', 'dy_px', 'dx_um', 'dy_um', 'phi_deg', 'phi_mrad', 'scale_ratio')


def batch_stats(pairs):
    """{키: (평균, 표준편차 ddof=1 또는 None, 개수)} — φ 등이 없는 쌍은 그 키에서 뺀다."""
    out = {}
    for key in _STAT_KEYS:
        vals = []
        for p in pairs:
            v = getattr(p.shift, key) if key in ('dx_px', 'dy_px') else p.motion.get(key)
            if v is not None and np.isfinite(v):
                vals.append(float(v))
        if vals:
            arr = np.asarray(vals)
            out[key] = (float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else None, arr.size)
    return out


def analyze_images(images, pixel_size_um=DEFAULT_PIXEL_SIZE_UM, stage_um=(0.0, 0.0),
                   method=METHOD_AUTO, roi=None, names=None, upsample=100, progress=None):
    """이미지 2..N을 각각 이미지 1과 비교한다. images는 경로 또는 2D 배열.

    한 장이 실패해도 나머지는 계속하고 errors에 남긴다. progress(done, total)를 부른다.
    """
    images = list(images)
    if len(images) < 2:
        raise ValueError('이미지가 2장 이상 필요합니다')
    if names is None:
        names = [os.path.basename(str(x)) if isinstance(x, (str, os.PathLike)) else f'이미지 {i + 1}'
                 for i, x in enumerate(images)]
    sx, sy = (float(v or 0.0) for v in stage_um)
    batch = BatchResult(pixel_size_um=float(pixel_size_um), stage_um=(sx, sy), method=method,
                        roi=None, names=list(names))
    ref = _as_gray(images[0])
    batch.roi = clip_roi(roi, ref.shape)
    total = len(images) - 1
    for k in range(1, len(images)):
        try:
            img = _as_gray(images[k])
            shift = measure_shift(ref, img, method, batch.roi, upsample)
            motion = analyze_motion(shift.dx_px, shift.dy_px, pixel_size_um, sx, sy)
            batch.pairs.append(PairResult(k + 1, names[0], names[k], shift, motion))
        except (ValueError, MemoryError) as e:
            batch.errors.append((k + 1, names[k], str(e)))
        if progress is not None:
            progress(k, total)
    return batch


def _as_gray(x):
    if isinstance(x, (str, os.PathLike)):
        return load_gray(x)
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 3:
        a = a[..., :3] @ np.array([0.299, 0.587, 0.114]) if a.shape[2] >= 3 else a[..., 0]
    if a.ndim != 2:
        raise ValueError('2차원 이미지가 아닙니다')
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
        str(p.index), p.name1, p.name2, METHOD_LABELS.get(s.method, s.method),
        _num(s.dx_px, '.3f'), _num(s.dy_px, '.3f'),
        _num(m['dx_um'], '.3f'), _num(m['dy_um'], '.3f'), _num(m['length_um'], '.3f'),
        _num(m['phi_deg'], '.4f'), _num(m['phi_mrad'], '.3f'), _num(m['scale_ratio'], '.5f'),
        _num(res[0], '.3f'), _num(res[1], '.3f'),
        _num(s.ncc, '.3f'), _num(s.peak, '.3f'), _num(s.psr, '.1f'), _num(s.second_ratio, '.2f'),
        ' / '.join(p.warnings), m['interpretation'],
    ]


CSV_HEADERS = ['번호', '이미지 1', '비교 이미지', '방법', 'Δx (px)', 'Δy (px)', 'Δx (µm)', 'Δy (µm)',
               '거리 (µm)', 'φ (°)', 'φ (mrad)', '배율', '잔차 x (µm)', '잔차 y (µm)',
               '일치도 NCC', '상관 피크', 'PSR', '2등/1등 피크', '경고', '해석']


def _stat_line(stats, key, label, unit, fmt):
    if key not in stats:
        return None
    mean, std, n = stats[key]
    sd = f' ± {format(std, fmt)}' if std is not None else ''
    return f'{label}: 평균 {format(mean, fmt)}{sd} {unit} (n={n}, 표준편차 ddof=1)'


def format_report(batch: BatchResult) -> str:
    """클립보드용 문장 (사람이 읽고 그대로 붙여 넣는다)."""
    sx, sy = batch.stage_um
    lines = ['[이미지 이동량 분석]',
             f'픽셀 크기: {batch.pixel_size_um:g} µm/px',
             f'지시 스테이지 이동: X {sx:+g} µm, Y {sy:+g} µm' if (sx or sy) else '지시 스테이지 이동: 없음 (이동량만)',
             f'방법: {METHOD_LABELS.get(batch.method, batch.method)} · ROI: {roi_text(batch.roi)}',
             '부호: Δx 화면 오른쪽 +, Δy 화면 아래 +, φ는 기대 방향 → 측정 방향이 화면에서 반시계면 +',
             '']
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
        for key, label, unit, fmt in (('phi_deg', 'φ', '°', '+.4f'), ('phi_mrad', 'φ', 'mrad', '+.3f'),
                                      ('dx_px', 'Δx', 'px', '+.3f'), ('dy_px', 'Δy', 'px', '+.3f'),
                                      ('scale_ratio', '배율', '', '.5f')):
            line = _stat_line(stats, key, label, unit, fmt)
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
                    'ROI', roi_text(batch.roi)])
        w.writerow(CSV_HEADERS)
        for p in batch.pairs:
            w.writerow(pair_row(p))
        for idx, name, msg in batch.errors:
            row = [str(idx), batch.names[0] if batch.names else '', name] + [''] * (len(CSV_HEADERS) - 3)
            row[-2] = f'실패: {msg}'
            w.writerow(row)
        stats = batch.stats
        if len(batch.pairs) > 1:
            cols = {'dx_px': 4, 'dy_px': 5, 'dx_um': 6, 'dy_um': 7, 'phi_deg': 9, 'phi_mrad': 10, 'scale_ratio': 11}
            for label, pick in (('평균', 0), ('표준편차 (ddof=1)', 1)):
                row = [label] + [''] * (len(CSV_HEADERS) - 1)
                for key, col in cols.items():
                    if key in stats and stats[key][pick] is not None:
                        row[col] = f'{stats[key][pick]:.5f}'
                w.writerow(row)
