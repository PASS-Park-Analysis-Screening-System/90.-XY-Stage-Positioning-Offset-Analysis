"""
scanner_rotation_test.py — 스캐너 회전 시험: 폐루프 2지점 스윕으로 회전 κ(µrad/µm)를 잰다 (Qt 없음)

배경
    샘플 XY 스캐너는 Y로 구동하면 웨이퍼를 비튼다 (폐루프 잔류 회전 ≈ 0.19 µrad/µm).
    이 회전을 출하 검사와 스캐너 교체·선형화 후의 관리 항목으로 삼는다.

시험 (WI-XY Scanner-01 Non-linearity 절차와 같고 서보만 ON)
    스캐너 Y를 −70 → +70 µm, 10 µm 간격(15점)으로 옮기며 단계마다 비전 "GRAB and RUN" 패턴 매칭을
    X로 멀리 떨어진 두 지점(예: 스테이지 X −120, +120 mm Die)에서 기록한다.
    대조(선택): Y로 멀리 떨어진 두 지점에서 X 스윕.

계산
    이득 g        지점마다 구동 축 방향 비전 변위(µm)를 지시 µm에 직선 맞춤한 기울기 (표준오차 포함)
    κ [µrad/µm]   (g_B − g_A) / L[mm] × 1000,  L = B 좌표 − A 좌표 (구동 축에 직각인 스테이지 축)
                  예: 240 mm 떨어진 두 지점 이득 차 0.0256 → κ 0.107
    σκ            √(se_A² + se_B²) / |L| × 1000  (두 기울기의 표준오차에서, 1σ)
    직각 기울기    직각 방향 비전 변위 / 지시 µm × 1000 (mrad) — 카메라-스캐너 축 사이 정적 각도 (참고)
    선형 잔차      직선 맞춤 잔차의 p-p·RMS (µm), 비선형 % = p-p / (|g| × 명령 폭) × 100

판정 (잠정 규격 rotation_spec — 장비 데이터 평균 + 3σ로 확정 예정)
    |κ| ≤ kappa_warn 정상(ok) · ≤ kappa_max 주의(warn) · 그 위 초과(fail) · κ를 못 내면 na
    근거: ±120 mm Die는 회전 θ µrad마다 Range에 θ × 0.24 µm를 더한다. Range 규격 1.0 µm의 25 %를
    배분하면 θ ≤ 1.0 µrad, 레시피 오프셋 ≤ 10 µm이면 κ ≤ 0.10 (주의 0.07).

픽셀 → µm
    기본 픽셀 크기 0.34278 µm/px (FOV 836.37819 µm / 2440 px) — 바꿀 수 있다.
    방향은 PQC Non-linearity 분석 시트와 같다: 스테이지 X = −(화면 x − 평균) × 픽셀 크기,
    스테이지 Y = +(화면 y − 평균) × 픽셀 크기.

입력
    XEPi 로그 글  "15:22:33 Match 1 : (1369.01, 1066.92),Angle = 0.02,Score = 0.97" — 한 단계에 한 줄,
                  명령 목록(기본 −70 … +70, 10 간격)과 순서대로 짝짓는다. 빈 줄·다른 줄은 건너뛰고
                  'Match 2' 이상은 같은 단계의 다른 후보라 버린다. 매칭 실패 줄(Match … fail, not found)이나
                  '누락'·'-'만 적은 줄은 빠진 단계로 센다. 모든 줄 앞에 명령 값이 탭·쉼표로 붙어 있으면
                  (분석 시트 A~D 열 복사) 명령 목록 대신 그 값을 쓴다.
    CSV           command_um, x_px, y_px[, angle, score] — 머리글은 있어도 없어도 되고, 빈 x·y는 빠진 단계.
    PQC Non-linearity 분석 시트 (xlsx, servo OFF)
                  X1/X2/Y1/Y2 × Before/After 시트의 'Move' 머리글 아래 행(8~22행): A 명령, B 매칭 글,
                  F·G 화소, H·I 비전 µm, K 검출기, Q 비선형 / 'Det Stroke' 옆 두 칸(M2·N2) 전·후.
                  개루프라 폐루프 회전이 아니다 — 검출기 지점별 이득, Y1↔Y2·X1↔X2 이득 차(%),
                  Det Stroke를 가설 ④(Y1/Y2 상대 교정) 참고값으로 낸다. 지점 좌표를 주면 개루프 비틀림을
                  계산하지만 규격으로 판정하지 않는다 (status 'na').

출력
    진단 JSON(pass.xy.diagnosis/1)의 rotation_test 형식 dict (stage·loop 포함, JSON으로 그대로 쓸 수 있게
    NaN 대신 None), 복사용 글(format_report), CSV(write_csv).
"""

import copy
import csv
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

FOV_UM = 836.378189094547
FOV_PX = 2440
DEFAULT_PIXEL_SIZE_UM = FOV_UM / FOV_PX          # 0.34278 µm/px
DEFAULT_COMMANDS = tuple(float(v) for v in range(-70, 71, 10))
DEFAULT_COMMANDS_TEXT = '-70:70:10'
DEFAULT_SPOT_MM = (-120.0, 120.0)
DEFAULT_ROTATION_SPEC = {'theta_max': 1.0, 'theta_warn': 0.7,
                         'kappa_max': 0.10, 'kappa_warn': 0.07,
                         'offset_max': 10.0, 'provisional': True}
DIE_SPAN_MM = 240.0              # ±120 mm Die 사이 거리 — 회전 θ µrad마다 Range에 θ × 0.24 µm

STAGES = ('출하 검사', '스캐너 교체·선형화 후', '기타')
AXES = ('Y', 'X')
LOOP_CLOSED = 'closed'
LOOP_OPEN = 'open'
LOOP_LABELS = {LOOP_CLOSED: '폐루프(servo ON)', LOOP_OPEN: '개루프(servo OFF)'}
STATUS_OK, STATUS_WARN, STATUS_FAIL, STATUS_NA = 'ok', 'warn', 'fail', 'na'
STATUS_LABELS = {STATUS_OK: '정상', STATUS_WARN: '주의', STATUS_FAIL: '초과', STATUS_NA: '판정 없음'}

PIXEL_X_SIGN = -1.0              # 화면 x 오른쪽 = 스테이지 −X (PQC 분석 시트 H열)
PIXEL_Y_SIGN = 1.0               # 화면 y 아래 = 스테이지 +Y (PQC 분석 시트 I열)
MIN_POINTS = 3                   # 직선 맞춤 최소 점 수 (표준오차는 3점부터)
FEW_POINTS = 10                  # 이보다 적으면 불확도가 크다고 알린다
CLOSED_GAIN_TOL = 0.05           # 폐루프 평균 이득이 1에서 이만큼 넘게 벗어나면 확인 문구
MAX_TEXT_BYTES = 20 * 1024 * 1024
XLSX_MAX_ROWS = 120
CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')   # 엑셀 수식으로 읽히는 첫 글자

_NUM = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_MATCH_RE = re.compile(rf'Match\s*(\d+)\s*:\s*\(\s*({_NUM})\s*,\s*({_NUM})\s*\)', re.IGNORECASE)
_ANGLE_RE = re.compile(rf'Angle\s*=\s*({_NUM})', re.IGNORECASE)
_SCORE_RE = re.compile(rf'Score\s*=\s*({_NUM})', re.IGNORECASE)
_LEAD_CMD_RE = re.compile(rf'^\s*({_NUM})\s*[\t,;]')
_FAIL_CONTEXT_RE = re.compile(r'match|pattern|grab', re.IGNORECASE)
_FAIL_WORD_RE = re.compile(r'fail|not\s*found|no\s*match|error|실패', re.IGNORECASE)
_MISSING_MARKS = {'-', '누락', '없음', 'nan', 'na', 'n/a', 'skip', 'missing'}
_SHEET_RE = re.compile(r'^\s*([XY])\s*([12])\s*[_\- ]?\s*(before|after)\s*$', re.IGNORECASE)
_CSV_ALIASES = {
    'command': ('command_um', 'command', 'cmd', 'move', 'offset', 'offset_um', '명령'),
    'x': ('x_px', 'x', 'pixel_x', 'px_x', 'vision_x'),
    'y': ('y_px', 'y', 'pixel_y', 'px_y', 'vision_y'),
    'angle': ('angle',),
    'score': ('score',),
}


class RotationTestError(ValueError):
    """입력을 읽거나 계산할 수 없다 (문구는 화면에 그대로 보인다)."""


class AnalysisCancelled(Exception):
    """작업 스레드가 멈추라는 깃발을 보았다."""


@dataclass
class Sweep:
    """지점 하나의 스윕 측정 — 단계마다 지시 µm와 화면 매칭 좌표(px). 빠진 단계는 NaN."""
    commands: np.ndarray
    x_px: np.ndarray
    y_px: np.ndarray
    angle: np.ndarray
    score: np.ndarray
    kind: str = 'log'                   # 'log' | 'csv'
    notes: list = field(default_factory=list)


# ── 작은 도구 ───────────────────────────────────────────

def _clean(text) -> str:
    """유니코드 빼기·대시·줄 바꿈 안 되는 공백을 보통 글자로."""
    return str(text).replace('−', '-').replace('–', '-').replace('\xa0', ' ')


def _to_float(v):
    """숫자·숫자 글 → float, 아니면 None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = _clean(v).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _r(v, nd):
    """JSON용 반올림 — NaN·무한대·None은 None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None


def _num_list(arr, nd=6):
    return [_r(v, nd) for v in np.asarray(arr, float)]


def fmt_num(v, fmt, empty='—'):
    return empty if v is None or (isinstance(v, float) and not math.isfinite(v)) else format(v, fmt)


def _csv_text(value):
    """글 칸이 엑셀 수식으로 읽히지 않게 앞에 '를 붙인다."""
    s = '' if value is None else str(value)
    return "'" + s if s.startswith(CSV_FORMULA_PREFIXES) else s


def resolve_spec(spec=None) -> dict:
    """설정 rotation_spec을 잠정 기본값 위에 얹는다 (숫자가 아닌 값은 무시)."""
    out = dict(DEFAULT_ROTATION_SPEC)
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key == 'provisional':
                out[key] = bool(value)
            elif key in DEFAULT_ROTATION_SPEC:
                f = _to_float(value)
                if f is not None and f > 0:
                    out[key] = f
    if out['kappa_warn'] > out['kappa_max']:
        out['kappa_warn'] = out['kappa_max']
    return out


def classify_kappa(kappa, spec=None) -> str:
    """|κ| ≤ kappa_warn → ok, ≤ kappa_max → warn, 그 위 → fail, κ 없음 → na."""
    if kappa is None or not math.isfinite(kappa):
        return STATUS_NA
    spec = resolve_spec(spec)
    k = abs(kappa)
    if k <= spec['kappa_warn']:
        return STATUS_OK
    if k <= spec['kappa_max']:
        return STATUS_WARN
    return STATUS_FAIL


def spec_text(spec=None) -> str:
    spec = resolve_spec(spec)
    head = '잠정 규격' if spec.get('provisional', True) else '규격'
    return (f'{head}: |κ| ≤ {spec["kappa_warn"]:.2f} 정상 · ≤ {spec["kappa_max"]:.2f} 주의 · '
            f'> {spec["kappa_max"]:.2f} 초과 (µrad/µm)')


# ── 명령 목록 ───────────────────────────────────────────

def parse_commands(text) -> list:
    """'-70:70:10'(시작:끝:간격, 끝 포함) 또는 '-70, -60, …, 70' → [µm]."""
    s = _clean(text).strip()
    if not s:
        raise RotationTestError('명령 목록이 비어 있습니다 (예: -70:70:10 또는 -70, -60, …, 70)')
    if ':' in s:
        parts = [p.strip() for p in s.split(':')]
        vals = [_to_float(p) for p in parts]
        if len(parts) != 3 or any(v is None for v in vals):
            raise RotationTestError(f'명령 목록 "{s}"을 읽을 수 없습니다 — 시작:끝:간격 (예: -70:70:10)')
        start, stop, step = vals
        if step == 0 or (stop - start) / step < 0:
            raise RotationTestError(f'명령 목록 "{s}": 간격의 부호·크기가 맞지 않습니다')
        n = int(math.floor((stop - start) / step + 1e-9)) + 1
        if n > 1000:
            raise RotationTestError(f'명령 목록 "{s}": 단계가 너무 많습니다 ({n})')
        values = [start + i * step for i in range(n)]
    else:
        values = []
        for token in (t for t in re.split(r'[\s,;]+', s) if t):
            v = _to_float(token)
            if v is None:
                raise RotationTestError(f'명령 목록의 "{token}"은 숫자가 아닙니다')
            values.append(v)
    values = [round(v, 9) for v in values]
    if len(values) < MIN_POINTS:
        raise RotationTestError(f'명령이 {len(values)}개뿐입니다 — {MIN_POINTS}개 이상 필요')
    if len(set(values)) < 2:
        raise RotationTestError('명령 값이 모두 같습니다 — 기울기를 낼 수 없습니다')
    return values


def format_commands(values) -> str:
    """등간격이면 '시작:끝:간격', 아니면 쉼표 목록."""
    vals = [float(v) for v in values]
    if len(vals) >= 3:
        step = vals[1] - vals[0]
        if step != 0 and all(abs((b - a) - step) < 1e-9 for a, b in zip(vals, vals[1:])):
            return f'{vals[0]:g}:{vals[-1]:g}:{step:g}'
    return ', '.join(f'{v:g}' for v in vals)


# ── 로그·CSV 해석 ───────────────────────────────────────

def looks_like_log(text) -> bool:
    return bool(_MATCH_RE.search(_clean(text)))


def parse_match_log(text) -> list:
    """XEPi 로그 글 → 단계 목록 [{command|None, x_px, y_px, angle, score}] (빠진 단계는 NaN)."""
    nan = math.nan
    steps = []
    for line in _clean(text).splitlines():
        s = line.strip()
        if not s:
            continue
        lead = _LEAD_CMD_RE.match(s)
        cmd = _to_float(lead.group(1)) if lead else None
        rest = s[lead.end():].strip() if lead else s
        m = _MATCH_RE.search(rest)
        if m:
            if int(m.group(1)) != 1:
                continue                    # 같은 단계의 다른 후보 (Match 2 …)
            tail = rest[m.end():]
            a, sc = _ANGLE_RE.search(tail), _SCORE_RE.search(tail)
            steps.append({'command': cmd, 'x_px': float(m.group(2)), 'y_px': float(m.group(3)),
                          'angle': float(a.group(1)) if a else nan, 'score': float(sc.group(1)) if sc else nan})
        elif (rest.strip(' .,;\t').lower() in _MISSING_MARKS
              or (_FAIL_CONTEXT_RE.search(rest) and _FAIL_WORD_RE.search(rest))):
            steps.append({'command': cmd, 'x_px': nan, 'y_px': nan, 'angle': nan, 'score': nan})
    return steps


def _steps_to_sweep(steps, commands, label) -> Sweep:
    if not steps:
        raise RotationTestError(f'지점 {label}: "Match 1 : (x, y)" 줄을 찾지 못했습니다')
    notes = []
    leads = [st['command'] for st in steps]
    if all(c is not None for c in leads):
        cmds = leads
        notes.append('줄 앞의 명령 값을 썼습니다 (명령 목록 대신)')
    else:
        commands = list(commands)
        if len(steps) != len(commands):
            raise RotationTestError(
                f'지점 {label}: 명령 {len(commands)}단계인데 측정 줄이 {len(steps)}개입니다 — '
                f'빠진 단계는 그 자리에 "누락" 한 줄을 넣거나 명령 목록을 고치세요')
        cmds = commands
    return Sweep(commands=np.asarray(cmds, float),
                 x_px=np.asarray([st['x_px'] for st in steps], float),
                 y_px=np.asarray([st['y_px'] for st in steps], float),
                 angle=np.asarray([st['angle'] for st in steps], float),
                 score=np.asarray([st['score'] for st in steps], float),
                 kind='log', notes=notes)


def parse_csv_text(text, label='') -> Sweep:
    """command_um, x_px, y_px[, angle, score] 글 → Sweep. 머리글은 있어도 없어도 된다."""
    lines = [(i + 1, ln) for i, ln in enumerate(_clean(text).splitlines()) if ln.strip()]
    if not lines:
        raise RotationTestError(f'지점 {label}: 입력이 비어 있습니다')
    first = lines[0][1]
    delim = '\t' if '\t' in first else (';' if ';' in first and ',' not in first else ',')
    rows = [(no, [c.strip() for c in row]) for (no, _), row in
            zip(lines, csv.reader([ln for _, ln in lines], delimiter=delim))]
    cols = {'command': 0, 'x': 1, 'y': 2, 'angle': 3, 'score': 4}
    if rows and rows[0][1] and _to_float(rows[0][1][0]) is None:
        header = [c.lower() for c in rows[0][1]]
        rows = rows[1:]
        cols = {}
        for key, names in _CSV_ALIASES.items():
            idx = next((header.index(n) for n in names if n in header), None)
            if idx is not None:
                cols[key] = idx
        missing = [k for k in ('command', 'x', 'y') if k not in cols]
        if missing:
            raise RotationTestError(f'지점 {label}: CSV 머리글에 command_um, x_px, y_px 열이 있어야 합니다 '
                                    f'(찾은 머리글: {", ".join(header)})')

    def cell(cells, key, no, required=False):
        idx = cols.get(key)
        raw = cells[idx] if idx is not None and idx < len(cells) else ''
        if raw == '' or raw.lower() in _MISSING_MARKS:
            if required:
                raise RotationTestError(f'지점 {label}: {no}번째 줄에 명령 값이 없습니다')
            return math.nan
        v = _to_float(raw)
        if v is None:
            raise RotationTestError(f'지점 {label}: {no}번째 줄의 "{raw}"은 숫자가 아닙니다')
        return v

    data = []
    for no, cells in rows:
        if not any(cells):
            continue
        data.append((cell(cells, 'command', no, True), cell(cells, 'x', no), cell(cells, 'y', no),
                     cell(cells, 'angle', no), cell(cells, 'score', no)))
    if not data:
        raise RotationTestError(f'지점 {label}: CSV에 측정 줄이 없습니다')
    arr = np.asarray(data, float)
    return Sweep(commands=arr[:, 0], x_px=arr[:, 1], y_px=arr[:, 2], angle=arr[:, 3], score=arr[:, 4],
                 kind='csv', notes=[])


def parse_spot_text(text, commands=DEFAULT_COMMANDS, label='') -> Sweep:
    """로그 글('Match 1 : …')이면 로그로, 아니면 CSV로 읽는다."""
    if not str(text or '').strip():
        raise RotationTestError(f'지점 {label}: 로그 글이나 CSV를 붙여 넣거나 불러오세요')
    if looks_like_log(text):
        return _steps_to_sweep(parse_match_log(text), commands, label)
    return parse_csv_text(text, label)


def read_text_file(path) -> str:
    """로그·CSV 파일 → 글 (utf-8 → cp949 → latin-1 순으로 시도)."""
    try:
        size = os.path.getsize(path)
        if size > MAX_TEXT_BYTES:
            raise RotationTestError(f'파일이 너무 큽니다 ({size / 1e6:.1f} MB): {path}')
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        raise RotationTestError(f'파일을 읽을 수 없습니다: {path} ({e.strerror or e})') from e
    for enc in ('utf-8-sig', 'cp949'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


# ── 계산 ─────────────────────────────────────────────────

def _check_pixel_size(pixel_size_um):
    ps = _to_float(pixel_size_um)
    if ps is None or ps <= 0:
        raise RotationTestError(f'픽셀 크기가 잘못됐습니다: {pixel_size_um}')
    return ps


def pixel_to_um(x_px, y_px, pixel_size_um=DEFAULT_PIXEL_SIZE_UM):
    """화면 매칭 좌표(px) → 스테이지 방향 비전 변위(µm, 유효 점 평균 기준). 빠진 점은 NaN 그대로."""
    ps = _check_pixel_size(pixel_size_um)
    x = np.asarray(x_px, float)
    y = np.asarray(y_px, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.any():
        return np.full(x.shape, np.nan), np.full(y.shape, np.nan)
    return PIXEL_X_SIGN * (x - x[ok].mean()) * ps, PIXEL_Y_SIGN * (y - y[ok].mean()) * ps


def _fit_line(x, y):
    """최소제곱 직선 → (기울기, 절편, 기울기 표준오차(ddof 2), 잔차)."""
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    slope = float(((x - xm) * (y - ym)).sum() / sxx)
    intercept = float(ym - slope * xm)
    resid = y - (intercept + slope * x)
    n = len(x)
    se = math.sqrt(float((resid ** 2).sum()) / (n - 2) / sxx) if n > 2 else math.nan
    return slope, intercept, se, resid


def _slope(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2 or np.unique(x[ok]).size < 2:
        return None
    return _fit_line(x[ok], y[ok])[0]


def fit_spot(commands, along_um, perp_um, name=''):
    """지점 하나: 축 방향 이득·직각 기울기·선형 잔차. 빠진 점(NaN)은 빼고 맞춘다."""
    cmd = np.asarray(commands, float)
    along = np.asarray(along_um, float)
    perp = np.asarray(perp_um, float)
    valid = np.isfinite(cmd) & np.isfinite(along) & np.isfinite(perp)
    n = int(valid.sum())
    if n < MIN_POINTS:
        raise RotationTestError(f'지점 {name}: 유효한 측정이 {n}점뿐입니다 — {MIN_POINTS}점 이상 필요')
    if np.unique(cmd[valid]).size < 2:
        raise RotationTestError(f'지점 {name}: 유효한 측정의 명령 값이 모두 같습니다')
    gain, intercept, gain_se, resid = _fit_line(cmd[valid], along[valid])
    perp_slope, _, perp_se, _ = _fit_line(cmd[valid], perp[valid])
    span = float(cmd[valid].max() - cmd[valid].min())
    pp = float(resid.max() - resid.min())
    res_full = np.full(cmd.shape, np.nan)
    res_full[valid] = resid
    return {
        'name': name, 'gain': gain, 'gain_se': gain_se, 'intercept_um': intercept,
        'perp_mrad': perp_slope * 1000.0, 'perp_se_mrad': perp_se * 1000.0,
        'n': n, 'n_total': int(cmd.size),
        'residual_pp_um': pp, 'residual_rms_um': float(math.sqrt(float((resid ** 2).mean()))),
        'nonlinearity_pct': pp / (abs(gain) * span) * 100.0 if gain else math.nan,
        'missing_um': [float(c) for c in cmd[~valid] if math.isfinite(c)],
        'command_um': cmd, 'along_um': along, 'perp_um': perp, 'residual_um': res_full,
    }


def kappa_from_gains(gain_a, gain_b, coord_a_mm, coord_b_mm, se_a=None, se_b=None):
    """κ = (g_B − g_A) / L × 1000 [µrad/µm], σκ (1σ). L이 0이면 (None, None)."""
    try:
        dist = float(coord_b_mm) - float(coord_a_mm)
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(dist) or abs(dist) < 1e-9:
        return None, None
    kappa = (gain_b - gain_a) / dist * 1000.0
    sigma = None
    if se_a is not None and se_b is not None and math.isfinite(se_a) and math.isfinite(se_b):
        sigma = math.sqrt(se_a ** 2 + se_b ** 2) / abs(dist) * 1000.0
    return kappa, sigma


def _coord_keys(axis):
    """구동 축에 직각인 스테이지 좌표 이름 (Y 스윕 → 지점은 X로 떨어져 있다)."""
    return ('stage_x_mm', 'stage_y_mm') if axis == 'Y' else ('stage_y_mm', 'stage_x_mm')


def _location(spot, axis, coord_mm, extra=None, points_extra=None):
    key, other = _coord_keys(axis)
    loc = {
        'name': spot['name'], key: _r(coord_mm, 3), other: None,
        'gain': _r(spot['gain'], 6), 'gain_se': _r(spot['gain_se'], 7),
        'perp_mrad': _r(spot['perp_mrad'], 3), 'perp_se_mrad': _r(spot['perp_se_mrad'], 4),
        'n': spot['n'], 'n_total': spot['n_total'],
        'residual_pp_um': _r(spot['residual_pp_um'], 4), 'residual_rms_um': _r(spot['residual_rms_um'], 4),
        'nonlinearity_pct': _r(spot['nonlinearity_pct'], 4),
        'missing_um': [_r(c, 4) for c in spot['missing_um']],
    }
    if extra:
        loc.update(extra)
    loc['points'] = {
        'command_um': _num_list(spot['command_um'], 4),
        'along_um': _num_list(spot['along_um'], 5),
        'perp_um': _num_list(spot['perp_um'], 5),
        'residual_um': _num_list(spot['residual_um'], 5),
    }
    if points_extra:
        loc['points'].update(points_extra)
    return loc


def analyze_sweep(sweep: Sweep, axis='Y', pixel_size_um=DEFAULT_PIXEL_SIZE_UM, name=''):
    """Sweep → fit_spot 결과 (+ 화면 좌표·출처·메모)."""
    if axis not in AXES:
        raise RotationTestError(f'구동 축은 X 또는 Y여야 합니다: {axis}')
    x_um, y_um = pixel_to_um(sweep.x_px, sweep.y_px, pixel_size_um)
    along, perp = (y_um, x_um) if axis == 'Y' else (x_um, y_um)
    spot = fit_spot(sweep.commands, along, perp, name)
    spot['x_px'] = sweep.x_px
    spot['y_px'] = sweep.y_px
    spot['kind'] = sweep.kind
    spot['notes'] = list(sweep.notes)
    return spot


def date_text(date):
    if date is None:
        return datetime.now().isoformat(timespec='seconds')
    if isinstance(date, datetime):
        return date.isoformat(timespec='seconds')
    return str(date)


def analyze_rotation_test(sweep_a: Sweep, sweep_b: Sweep, axis='Y', pixel_size_um=DEFAULT_PIXEL_SIZE_UM,
                          coords_mm=DEFAULT_SPOT_MM, stage=STAGES[0], spec=None, date=None) -> dict:
    """폐루프 2지점 스윕 → rotation_test dict."""
    spec = resolve_spec(spec)
    ps = _check_pixel_size(pixel_size_um)
    ca, cb = (float(c) for c in coords_mm)
    spot_a = analyze_sweep(sweep_a, axis, ps, 'A')
    spot_b = analyze_sweep(sweep_b, axis, ps, 'B')
    kappa, sigma = kappa_from_gains(spot_a['gain'], spot_b['gain'], ca, cb, spot_a['gain_se'], spot_b['gain_se'])
    status = classify_kappa(kappa, spec)
    locations = [_location(spot, axis, coord, {'source': spot['kind']},
                           {'x_px': _num_list(spot['x_px'], 3), 'y_px': _num_list(spot['y_px'], 3)})
                 for spot, coord in ((spot_a, ca), (spot_b, cb))]
    commands = sorted({c for spot in (spot_a, spot_b) for c in spot['command_um'].tolist() if math.isfinite(c)})
    return {
        'date': date_text(date),
        'stage': stage,
        'axis': axis,
        'loop': LOOP_CLOSED,
        'locations': locations,
        'distance_mm': _r(abs(cb - ca), 3),
        'kappa': _r(kappa, 5),
        'kappa_sigma': _r(sigma, 5),
        'status': status,
        'reasons': _closed_reasons(spot_a, spot_b, ca, cb, kappa, sigma, status, axis, spec),
        'spec': spec,
        'pixel_size_um': _r(ps, 7),
        'commands_um': [_r(c, 4) for c in commands],
        'control': None,
        'open_loop': None,
    }


def _closed_reasons(spot_a, spot_b, ca, cb, kappa, sigma, status, axis, spec):
    coord_axis = 'X' if axis == 'Y' else 'Y'
    reasons = []
    if kappa is None:
        reasons.append(f'두 지점의 스테이지 {coord_axis} 좌표가 같아 κ를 계산할 수 없습니다 — 좌표를 확인하세요')
    else:
        dg = spot_b['gain'] - spot_a['gain']
        sig = f' ± {sigma:.4f}' if sigma is not None else ''
        reasons.append(f'κ = {kappa:+.4f}{sig} µrad/µm (1σ) — 지점 A {coord_axis} {ca:+g} mm 이득 {spot_a["gain"]:.4f}, '
                       f'B {coord_axis} {cb:+g} mm 이득 {spot_b["gain"]:.4f}, 이득 차 {dg:+.4f} / {abs(cb - ca):g} mm')
        reasons.append(f'{spec_text(spec)} → {STATUS_LABELS[status]}')
        theta = abs(kappa) * spec['offset_max']
        reasons.append(f'스캐너 오프셋 {spec["offset_max"]:g} µm에서 회전 ≈ {theta:.2f} µrad '
                       f'(허용 {spec["theta_max"]:g} µrad) → ±120 mm Die Range 기여 ≈ {theta * DIE_SPAN_MM / 1000:.2f} µm')
        if sigma:
            for bound in (spec['kappa_warn'], spec['kappa_max']):
                if abs(abs(kappa) - bound) < 2 * sigma:
                    reasons.append(f'κ ± 2σ가 판정 경계 {bound:g}에 걸칩니다 — 다시 재서 확인하세요')
                    break
    mean_gain = (spot_a['gain'] + spot_b['gain']) / 2
    if min(spot_a['gain'], spot_b['gain']) < 0:
        reasons.append('이득이 음수입니다 — 카메라 축 방향(부호)이 다르거나 구동 축을 잘못 골랐는지 확인하세요')
    elif abs(mean_gain - 1.0) > CLOSED_GAIN_TOL:
        reasons.append(f'평균 이득 {mean_gain:.3f} — 폐루프(servo ON)라면 1.00 근처여야 합니다. '
                       f'servo OFF로 쟀거나 픽셀 크기(FOV 보정)가 틀렸을 수 있습니다')
    reasons.append(f'직각 기울기(카메라-스캐너 축 각도, 참고): A {spot_a["perp_mrad"]:+.2f} mrad, '
                   f'B {spot_b["perp_mrad"]:+.2f} mrad')
    reasons.append(f'선형 잔차 p-p: A {spot_a["residual_pp_um"]:.3f} µm ({spot_a["nonlinearity_pct"]:.2f} %), '
                   f'B {spot_b["residual_pp_um"]:.3f} µm ({spot_b["nonlinearity_pct"]:.2f} %)')
    for spot in (spot_a, spot_b):
        if spot['missing_um']:
            miss = ', '.join(f'{c:+g}' for c in spot['missing_um'])
            reasons.append(f'지점 {spot["name"]}: 명령 {miss} µm 단계 측정 없음 — {spot["n"]}점으로 계산')
        elif spot['n'] < spot['n_total']:
            reasons.append(f'지점 {spot["name"]}: {spot["n_total"] - spot["n"]}단계 측정 없음 — {spot["n"]}점으로 계산')
        if spot['n'] < FEW_POINTS:
            reasons.append(f'지점 {spot["name"]}: 점이 {spot["n"]}개뿐이라 불확도가 큽니다')
        for note in spot.get('notes', []):
            reasons.append(f'지점 {spot["name"]}: {note}')
    return reasons


def combine_control(primary: dict, control=None) -> dict:
    """본 시험(보통 Y 스윕) 결과에 다른 축 스윕(대조)을 붙인다. 판정은 본 시험 것 그대로."""
    out = copy.deepcopy(primary)
    if not control:
        out['control'] = None
        return out
    out['control'] = {key: copy.deepcopy(control.get(key)) for key in
                      ('date', 'axis', 'loop', 'locations', 'distance_mm', 'kappa', 'kappa_sigma', 'status',
                       'reasons')}
    k, s = control.get('kappa'), control.get('kappa_sigma')
    text = '계산 못 함' if k is None else f'{k:+.4f}' + (f' ± {s:.4f}' if s is not None else '') + ' µrad/µm'
    out['reasons'] = list(out['reasons']) + [
        f'대조 {control.get("axis")} 스윕: κ = {text} ({STATUS_LABELS.get(control.get("status"), "?")}, 참고)']
    return out


# ── PQC Non-linearity 분석 시트 (개루프) ──────────────────

def _cell_text(v):
    return _clean(v).strip() if v is not None else ''


def _read_sheet(name, match, rows):
    axis, ch = match.group(1).upper(), match.group(2)
    phase = 'After' if match.group(3).lower() == 'after' else 'Before'

    def at(r, c):
        return rows[r][c] if r < len(rows) and c < len(rows[r]) else None

    header = next((i for i in range(min(len(rows), 20)) if _cell_text(at(i, 0)).lower() == 'move'), 6)
    data = []
    for r in range(header + 1, len(rows)):
        if _to_float(at(r, 0)) is None:
            if data:
                break
            continue
        data.append(r)
    if not data:
        raise RotationTestError(f'{name}: Move(명령) 열 아래에 데이터가 없습니다')

    stroke = (None, None)
    pixel_size = None
    for r in range(min(len(rows), 6)):
        for c in range(len(rows[r])):
            label = _cell_text(at(r, c)).lower()
            if 'det stroke' in label and stroke == (None, None):
                stroke = (_to_float(at(r, c + 1)), _to_float(at(r, c + 2)))
            elif label in ('um/pxl', 'µm/pxl', 'um/px', 'µm/px') and pixel_size is None:
                pixel_size = _to_float(at(r, c + 1))
    if stroke == (None, None):
        stroke = (_to_float(at(1, 12)), _to_float(at(1, 13)))          # M2, N2

    nan = math.nan

    def col(c):
        vals = [_to_float(at(r, c)) for r in data]
        return [nan if v is None else v for v in vals]

    x_px, y_px = col(5), col(6)
    for i, r in enumerate(data):                    # 화소 칸이 비면 B열 매칭 글에서
        if not (math.isfinite(x_px[i]) and math.isfinite(y_px[i])):
            m = _MATCH_RE.search(_cell_text(at(r, 1)))
            if m:
                x_px[i], y_px[i] = float(m.group(2)), float(m.group(3))
    x_um, y_um = col(7), col(8)
    if not any(math.isfinite(v) for v in (y_um if axis == 'Y' else x_um)):
        cx, cy = pixel_to_um(x_px, y_px, pixel_size or DEFAULT_PIXEL_SIZE_UM)
        x_um, y_um = cx.tolist(), cy.tolist()
    return {
        'name': name, 'axis': axis, 'channel': f'{axis}{ch}', 'phase': phase,
        'commands': col(0), 'x_px': x_px, 'y_px': y_px, 'x_um': x_um, 'y_um': y_um,
        'detector': col(10), 'nonlinearity': col(16),
        'stroke_before': stroke[0], 'stroke_after': stroke[1], 'pixel_size_um': pixel_size,
    }


def parse_nonlinearity_xlsx(path, progress=None) -> dict:
    """PQC 'XY Non-linearity error Analysis sheet' xlsx → 시트별 측정 (읽기 전용으로 열고 바로 닫는다)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:      # 손상·암호·다른 형식 — 문구로 보인다
        raise RotationTestError(f'xlsx를 열 수 없습니다: {os.path.basename(str(path))} ({e})') from e
    try:
        targets = [(name, m) for name in wb.sheetnames for m in [_SHEET_RE.match(name)] if m]
        if not targets:
            raise RotationTestError('Non-linearity 분석 시트(X1_Before … Y2_After)가 없습니다: '
                                    + ', '.join(wb.sheetnames))
        sheets, warnings = [], []
        for i, (name, m) in enumerate(targets):
            if progress is not None:
                progress(i, len(targets))
            rows = [list(r) for r in wb[name].iter_rows(min_row=1, max_row=XLSX_MAX_ROWS, max_col=17,
                                                         values_only=True)]
            try:
                sheets.append(_read_sheet(name, m, rows))
            except RotationTestError as e:
                warnings.append(str(e))
        if progress is not None:
            progress(len(targets), len(targets))
    finally:
        wb.close()
    if not sheets:
        raise RotationTestError('읽을 수 있는 Non-linearity 시트가 없습니다 — ' + '; '.join(warnings))
    return {'path': str(path), 'sheets': sheets, 'warnings': warnings}


def _channel(sheet):
    along, perp = (sheet['y_um'], sheet['x_um']) if sheet['axis'] == 'Y' else (sheet['x_um'], sheet['y_um'])
    spot = fit_spot(sheet['commands'], along, perp, sheet['channel'])
    nl = np.asarray(sheet['nonlinearity'], float)
    nl = nl[np.isfinite(nl)]
    det = np.asarray(sheet['detector'], float)
    return spot, {
        'sheet': sheet['name'], 'channel': sheet['channel'], 'axis': sheet['axis'], 'phase': sheet['phase'],
        'gain': _r(spot['gain'], 6), 'gain_se': _r(spot['gain_se'], 7),
        'detector_gain': _r(_slope(sheet['commands'], det), 6),
        'vision_per_detector': _r(_slope(det, along), 6),
        'perp_mrad': _r(spot['perp_mrad'], 3), 'n': spot['n'],
        'residual_pp_um': _r(spot['residual_pp_um'], 4), 'nonlinearity_pct': _r(spot['nonlinearity_pct'], 4),
        'sheet_nonlinearity_pp': _r(float(nl.max() - nl.min()), 5) if nl.size else None,
        'stroke_before': _r(sheet['stroke_before'], 4), 'stroke_after': _r(sheet['stroke_after'], 4),
        'points': {'command_um': _num_list(spot['command_um'], 4), 'along_um': _num_list(spot['along_um'], 5),
                   'detector_um': _num_list(det, 5), 'residual_um': _num_list(spot['residual_um'], 5)},
    }


def analyze_open_loop(parsed: dict, axis='Y', positions_mm=None, stage=STAGES[0], spec=None, date=None) -> dict:
    """Non-linearity 시트(개루프) → rotation_test dict (loop 'open', 판정 'na') + open_loop 상세."""
    if axis not in AXES:
        raise RotationTestError(f'구동 축은 X 또는 Y여야 합니다: {axis}')
    spec = resolve_spec(spec)
    channels, spots, warnings = [], {}, list(parsed.get('warnings', []))
    for sheet in parsed['sheets']:
        try:
            spot, ch = _channel(sheet)
        except RotationTestError as e:
            warnings.append(f'{sheet["name"]}: {e}')
            continue
        channels.append(ch)
        spots[(ch['channel'], ch['phase'])] = (spot, ch)
    pairs = []
    for phase in ('Before', 'After'):
        for ax in ('X', 'Y'):
            c1, c2 = spots.get((f'{ax}1', phase)), spots.get((f'{ax}2', phase))
            if not (c1 and c2):
                continue
            g1, g2 = c1[0]['gain'], c2[0]['gain']
            s1, s2 = c1[1]['stroke_after'], c2[1]['stroke_after']
            pairs.append({'phase': phase, 'axis': ax, 'gain1': _r(g1, 6), 'gain2': _r(g2, 6),
                          'diff_pct': _r((g2 / g1 - 1.0) * 100.0, 3) if g1 else None,
                          'stroke1_before': c1[1]['stroke_before'], 'stroke1_after': s1,
                          'stroke2_before': c2[1]['stroke_before'], 'stroke2_after': s2,
                          'stroke_ratio': _r(s2 / s1, 5) if s1 and s2 else None})

    phase = next((p for p in ('After', 'Before') if (f'{axis}1', p) in spots and (f'{axis}2', p) in spots), None)
    locations, kappa, sigma, dist = [], None, None, None
    if phase:
        a, b = spots[(f'{axis}1', phase)][0], spots[(f'{axis}2', phase)][0]
        pos = tuple(float(v) for v in positions_mm) if positions_mm is not None else (None, None)
        locations = [_location(a, axis, pos[0], {'sheet': f'{axis}1_{phase}'}),
                     _location(b, axis, pos[1], {'sheet': f'{axis}2_{phase}'})]
        if positions_mm is not None:
            kappa, sigma = kappa_from_gains(a['gain'], b['gain'], pos[0], pos[1], a['gain_se'], b['gain_se'])
            dist = abs(pos[1] - pos[0])
    reasons = ['개루프(servo OFF) PQC Non-linearity 분석 시트입니다 — 폐루프 스캐너 회전(κ)을 잰 것이 아니므로 '
               '잠정 규격으로 판정하지 않습니다. 가설 ④(Y1/Y2 상대 교정)의 참고값입니다.']
    for p in pairs:
        reasons.append(f'{p["phase"]}: {p["axis"]}1 이득 {p["gain1"]:.3f}, {p["axis"]}2 {p["gain2"]:.3f} → '
                       f'{p["axis"]}2/{p["axis"]}1 {fmt_num(p["diff_pct"], "+.1f")} %')
    for p in pairs:
        if p['stroke1_after'] is not None and p['stroke2_after'] is not None:
            reasons.append(f'Det Stroke(MicrometerPerAdc) {p["phase"]}: '
                           f'{p["axis"]}1 {fmt_num(p["stroke1_before"], ".2f")} → {p["stroke1_after"]:.2f}, '
                           f'{p["axis"]}2 {fmt_num(p["stroke2_before"], ".2f")} → {p["stroke2_after"]:.2f} '
                           f'({p["axis"]}2/{p["axis"]}1 {fmt_num(p["stroke_ratio"], ".3f")})')
    if not phase:
        reasons.append(f'{axis}1·{axis}2 시트가 함께 있는 단계가 없어 {axis} 지점 비교를 하지 않았습니다')
    elif kappa is not None:
        reasons.append(f'검출기 지점 좌표({axis}1 {pos[0]:+g} mm, {axis}2 {pos[1]:+g} mm)로 계산한 개루프 비틀림 '
                       f'κ = {kappa:+.4f}' + (f' ± {sigma:.4f}' if sigma is not None else '')
                       + ' µrad/µm — 폐루프 회전이 아니므로 판정하지 않습니다')
    else:
        reasons.append('검출기 지점의 스테이지 좌표를 넣지 않아 비틀림(κ)은 계산하지 않았습니다')
    reasons.extend(f'경고: {w}' for w in warnings)
    return {
        'date': date_text(date),
        'stage': stage,
        'axis': axis,
        'loop': LOOP_OPEN,
        'locations': locations,
        'distance_mm': _r(dist, 3),
        'kappa': _r(kappa, 5),
        'kappa_sigma': _r(sigma, 5),
        'status': STATUS_NA,
        'reasons': reasons,
        'spec': spec,
        'pixel_size_um': None,
        'commands_um': locations[0]['points']['command_um'] if locations else [],
        'control': None,
        'open_loop': {'source': os.path.basename(parsed.get('path', '')), 'phase': phase,
                      'channels': channels, 'pairs': pairs, 'warnings': warnings},
    }


# ── 글·CSV ───────────────────────────────────────────────

def kappa_text(res):
    k, s = res.get('kappa'), res.get('kappa_sigma')
    if k is None:
        return 'κ 없음'
    dist = res.get('distance_mm')
    return (f'κ = {k:+.4f}' + (f' ± {s:.4f}' if s is not None else '') + ' µrad/µm (1σ)'
            + (f' · 지점 거리 {dist:g} mm' if dist is not None else ''))


def location_coord(loc):
    """(축 이름, 좌표) — 좌표가 없으면 (축, None)."""
    if loc.get('stage_x_mm') is not None:
        return 'X', loc['stage_x_mm']
    if loc.get('stage_y_mm') is not None:
        return 'Y', loc['stage_y_mm']
    return '', None


def _loc_line(loc):
    ax, c = location_coord(loc)
    where = f'스테이지 {ax} {c:+g} mm' if c is not None else '좌표 없음'
    se = loc.get('gain_se')
    return (f'지점 {loc["name"]}: {where}, 이득 {fmt_num(loc.get("gain"), ".5f")}'
            + (f' ± {se:.5f}' if se is not None else '')
            + f', 직각 {fmt_num(loc.get("perp_mrad"), "+.2f")} mrad, n={loc.get("n")}, '
            f'잔차 p-p {fmt_num(loc.get("residual_pp_um"), ".3f")} µm ({fmt_num(loc.get("nonlinearity_pct"), ".2f")} %)')


def format_report(res: dict) -> str:
    """복사용 글."""
    lines = [f'[스캐너 회전 시험] {res.get("date")} · {res.get("stage")} · {res.get("axis")} 스윕 · '
             f'{LOOP_LABELS.get(res.get("loop"), res.get("loop"))}']
    if res.get('loop') == LOOP_OPEN:
        lines.append('판정: 없음 — 개루프 참고값 (가설 ④ Y1/Y2 상대 교정)')
    else:
        lines.append(f'판정: {STATUS_LABELS.get(res.get("status"), res.get("status"))} — {spec_text(res.get("spec"))}')
    lines.append(kappa_text(res) + (f' · 픽셀 크기 {res["pixel_size_um"]:g} µm/px' if res.get('pixel_size_um') else ''))
    lines.extend(_loc_line(loc) for loc in res.get('locations') or [])
    ctrl = res.get('control')
    if ctrl:
        lines.append(f'[대조 {ctrl.get("axis")} 스윕] {kappa_text(ctrl)} '
                     f'({STATUS_LABELS.get(ctrl.get("status"), "?")}, 참고)')
        lines.extend(_loc_line(loc) for loc in ctrl.get('locations') or [])
    ol = res.get('open_loop')
    if ol:
        lines.append(f'[개루프 검출기 지점] {ol.get("source")}')
        for ch in ol.get('channels', []):
            lines.append(f'{ch["sheet"]}: 이득 {fmt_num(ch["gain"], ".4f")}, 검출기/명령 {fmt_num(ch["detector_gain"], ".4f")}, '
                         f'비전/검출기 {fmt_num(ch["vision_per_detector"], ".4f")}, 직각 {fmt_num(ch["perp_mrad"], "+.2f")} mrad, '
                         f'Det Stroke {fmt_num(ch["stroke_before"], ".2f")} → {fmt_num(ch["stroke_after"], ".2f")}')
    lines.append('이유')
    lines.extend(f'- {r}' for r in res.get('reasons') or [])
    return '\n'.join(lines)


LOCATION_HEADERS = ['구분', '지점', '스테이지 X (mm)', '스테이지 Y (mm)', '이득', '이득 σ', '직각 (mrad)', 'n',
                    '잔차 p-p (µm)', '비선형 (%)', '빠진 단계 (µm)']
POINT_HEADERS = ['구분', '지점', '명령 (µm)', '화면 x (px)', '화면 y (px)', '축 방향 변위 (µm)', '직각 변위 (µm)',
                 '잔차 (µm)']
CHANNEL_HEADERS = ['시트', '검출기', '단계', '이득 (비전/명령)', '이득 σ', '검출기/명령', '비전/검출기', '직각 (mrad)',
                   '잔차 p-p (µm)', '시트 비선형 p-p', 'Det Stroke 전', 'Det Stroke 후', 'n']


def _s(v, fmt='.6g'):
    return '' if v is None else format(v, fmt)


def write_csv(path, res: dict):
    """요약·지점·점별 표·이유. Excel에서 한글이 깨지지 않게 utf-8-sig."""
    groups = [(f'{res.get("axis")} 스윕', res)]
    if res.get('control'):
        groups.append((f'대조 {res["control"].get("axis")} 스윕', res['control']))
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['스캐너 회전 시험', _csv_text(res.get('date')), _csv_text(res.get('stage')),
                    f'{res.get("axis")} 스윕', LOOP_LABELS.get(res.get('loop'), res.get('loop'))])
        spec = resolve_spec(res.get('spec'))
        w.writerow(['κ (µrad/µm)', _s(res.get('kappa')), 'σ (1σ)', _s(res.get('kappa_sigma')),
                    '판정', STATUS_LABELS.get(res.get('status'), res.get('status')),
                    '잠정 규격 kappa_warn', _s(spec['kappa_warn']), 'kappa_max', _s(spec['kappa_max'])])
        w.writerow(['픽셀 크기 (µm/px)', _s(res.get('pixel_size_um')), '지점 거리 (mm)', _s(res.get('distance_mm'))])
        w.writerow([])
        w.writerow(LOCATION_HEADERS)
        for label, g in groups:
            for loc in g.get('locations') or []:
                w.writerow([label, _csv_text(loc['name']), _s(loc.get('stage_x_mm'), 'g'),
                            _s(loc.get('stage_y_mm'), 'g'), _s(loc.get('gain')), _s(loc.get('gain_se')),
                            _s(loc.get('perp_mrad')), loc.get('n'), _s(loc.get('residual_pp_um')),
                            _s(loc.get('nonlinearity_pct')),
                            ' '.join(f'{c:g}' for c in loc.get('missing_um') or [])])
        w.writerow([])
        w.writerow(POINT_HEADERS)
        for label, g in groups:
            for loc in g.get('locations') or []:
                pts = loc.get('points') or {}
                cmds = pts.get('command_um') or []
                cols = [pts.get(key) or [None] * len(cmds)
                        for key in ('x_px', 'y_px', 'along_um', 'perp_um', 'residual_um')]
                for i, c in enumerate(cmds):
                    w.writerow([label, _csv_text(loc['name']), _s(c, 'g')] + [_s(v[i]) for v in cols])
        ol = res.get('open_loop')
        if ol:
            w.writerow([])
            w.writerow(['개루프 검출기 지점 (PQC Non-linearity)', _csv_text(ol.get('source'))])
            w.writerow(CHANNEL_HEADERS)
            for ch in ol.get('channels', []):
                w.writerow([_csv_text(ch['sheet']), ch['channel'], ch['phase'], _s(ch['gain']), _s(ch['gain_se']),
                            _s(ch['detector_gain']), _s(ch['vision_per_detector']), _s(ch['perp_mrad']),
                            _s(ch['residual_pp_um']), _s(ch['sheet_nonlinearity_pp']), _s(ch['stroke_before']),
                            _s(ch['stroke_after']), ch['n']])
            w.writerow([])
            w.writerow(['단계', '축', '이득 1', '이득 2', '2/1 차 (%)', 'Det Stroke 1 후', 'Det Stroke 2 후', 'Stroke 2/1'])
            for p in ol.get('pairs', []):
                w.writerow([p['phase'], p['axis'], _s(p['gain1']), _s(p['gain2']), _s(p['diff_pct']),
                            _s(p['stroke1_after']), _s(p['stroke2_after']), _s(p['stroke_ratio'])])
        w.writerow([])
        for reason in res.get('reasons') or []:
            w.writerow(['이유', _csv_text(reason)])
