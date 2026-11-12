"""
xy_diagnosis.py — XY 위치 오프셋 트러블슈팅 진단 (스키마 pass.xy.diagnosis/1)

한 회차 결과(load_all_recipes의 Recipe 결과 하나)와 그 Lot 폴더들로 원인 후보를 자동으로 찾는다.
찾는 원인은 끝난 조사의 결론과 같은 종류다.
    X 라인을 잴 때 시료 XY 스캐너를 Y로 약 −28 µm 밀어 둔다. 그 Y 구동이 웨이퍼를 µm당
    약 0.19 µrad 돌려 θx ≈ 5.2 µrad, X Spec Out이 됐다. 레시피의 X 라인 Y Offset을 −25 → −5 µm로
    바꾸자 θx 0.95 µrad, X PASS.

들어가는 것
    scanner_offsets  Lot마다 Log/<Sample>_Log.csv의 ImageJob 'Set scan params'(Scan Direction,
                     X/Y Offset = 스캐너에 실제로 건 오프셋), 'Sample pattern align - fine'
                     (X/Y Offset - Fine, µm), 'Apply reference scan result'(X/Y Offset - Reference·
                     Total, nm). 레시피 값 = 적용값 − 기준 보정 − 미세 보정.
                     기준 스캔 결과가 Log에 없으면(저장된 값을 씀) 레시피와 기준 보정을 나눌 수 없어
                     둘 다 null이고 recipe_plus_ref(적용값 − 미세 보정)만 적는다.
    shape            축별 Die 평균 편차의 평면 피팅 (positioning_log.plane_fit, 안정화 Die 제외)
    rotation         κ_eff = |θ| / |구동 축 스캐너 오프셋|, 기여 = |θ| × Die 좌표 폭
    whatif           X 라인 레시피 Y Offset을 바꿨을 때 Lot별 X Range·StdDev 예측 (선형·원점 통과 가정)
    drift            Lot 평균 대 Lot 시작 시각, Lot 안 측정 순서 기울기
    transient        Lot 시작 직후 Die1·2·3의 평면 잔차 (Die4 이후 대비)
    die_field        Die 평균 편차 (3D 보고서용)
    positioning_log  Positioning Log 분석(비전 보정 로그 대조)이 있으면 그 판정을 옮긴다 (없으면 null)
    parts, actions   부품별 판정과 다음 조치 — 규칙 기반 (PART_RULES), 규칙을 그대로 적는다
    rotation_test    스캐너 회전 시험 결과 (같은 장비 tool_id일 때만)

네트워크는 보이는 회차만, Lot마다 Log 폴더 목록 1번·Log 파일 1개·측정 CSV 앞 20줄을 읽는다
(read_run_logs). 읽은 것은 회차 결과의 'xy_logs'에 담아 다시 읽지 않는다 (읽기 오류가 섞였으면
담지 않는다). 진단(build_diagnosis)은 담긴 것으로 계산만 한다.

Qt를 쓰지 않는다 (화면의 작업 스레드·AI 분석용 리포트·테스트가 같이 쓴다).
"""

import copy
import csv
import io
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import Optional

from core.checklist import compute_axis_metrics
from core.csv_loader import (LOT_LOAD_WORKERS, _decode_bytes, _open_csv_text, _read_file_bytes,
                             _with_retry, list_dir)
from core.die_analysis import (compute_deviation_matrix, extract_die_number,
                               extract_die_positions, filter_stabilization_die)
from core.positioning_log import (MIN_DIES, MIN_T, STAGE_LABEL, lot_folder, plane_fit, run_key)
from core.settings import DEFAULT_ROTATION_SPEC, rotation_spec as _rotation_spec
from core.statistics import is_usable

SCHEMA = 'pass.xy.diagnosis/1'
AXES = ('X', 'Y')
VERDICTS = ('ok', 'warn', 'fail', 'na')
PART_IDS = ('vision', 'f_stage', 'z_stage', 'z_scanner_tip', 'xy_stage',
            'xy_scanner_x', 'xy_scanner_y', 'chuck', 'sample',
            'body', 'anti_vibration', 'efem')
PART_NAMES = {
    'vision': '비전 (카메라·패턴 인식)',
    'f_stage': 'F 스테이지 (포커스)',
    'z_stage': 'Z 스테이지',
    'z_scanner_tip': 'Z 스캐너·Tip (헤드)',
    'xy_stage': 'XY 스테이지',
    'xy_scanner_x': 'XY 스캐너 X 구동',
    'xy_scanner_y': 'XY 스캐너 Y 구동',
    'chuck': '척 (웨이퍼 고정)',
    'sample': '샘플 (웨이퍼)',
    'body': '본체',
    'anti_vibration': '제진대',
    'efem': 'EFEM',
}
LINE_KEYS = {'X': 'X_line', 'Y': 'Y_line'}

# what-if: X 라인 레시피 Y Offset 후보 |값| (µm). 지금 레시피와 같은 쪽(부호)으로 만든다
WHATIF_RECIPES_Y = (-25.0, -15.0, -10.0, -5.0, -3.0, 0.0)
# 권장 후보는 패턴 꼭짓점에서 이만큼 떨어져야 한다 (체커보드 꼭짓점 여유 — X 라인이 세로 엣지를
# 가로질러야 함, 0이면 꼭짓점 위). 3D 보고서(B)의 템플릿과 같은 규칙
CORNER_CLEARANCE_UM = 5.0
# 지금 레시피가 이보다 작으면 0으로 본다 (부호를 정할 수 없음 → 기본 쪽 −)
ZERO_RECIPE_UM = 0.5
# κ_eff는 구동 오프셋이 이보다 클 때만 낸다 (작으면 나눗셈이 불안정)
MIN_KAPPA_OFFSET_UM = 2.0
# 스캐너 부품을 원인으로 '이상'까지 판정하려면 구동이 이만큼은 있어야 한다
MIN_DRIVE_FOR_ATTRIBUTION_UM = 5.0
# 이보다 작게 구동했으면 '오프셋을 줄이세요'를 내지 않는다
MIN_DRIVE_FOR_ACTION_UM = 2.0
# drift (Lot 평균 폭, 첫 Lot 안 기울기). 주의는 Lot 3개 이상, 기울기는 |r| ≥ 0.5일 때만
DRIFT_SPAN_UM = 0.2
DRIFT_SLOPE_UM_PER_DIE = 0.01
DRIFT_MIN_DIES = 5
DRIFT_MIN_LOTS = 3
DRIFT_MIN_R = 0.5
# transient: Die2(판정 대상) 평면 잔차 한도, 평면은 Die4 이후로 맞춘다. 주의는 Lot 3개 이상·|평균|/SE ≥ 3
TRANSIENT_DIE2_UM = 0.15
TRANSIENT_FIT_FROM_DIE = 4
TRANSIENT_MIN_DIES = 8
TRANSIENT_MIN_LOTS = 3
# Log의 'X/Y Offset - Reference·Total'은 nm다 (적용값·미세 보정은 µm)
REFERENCE_NM_PER_UM = 1000.0
LOG_DIR = 'Log'
LOG_SUFFIX = '_log.csv'

NO_LOG_TEXT = 'Log 없음 — Lot 폴더 Log/<Sample>_Log.csv 확인'
READ_FAIL_TEXT = 'Log를 읽지 못함'
NO_REF_TEXT = ('기준 스캔(Reference) 결과가 Log에 없어 레시피 값과 기준 보정을 나눌 수 없음 '
               '(적용값 − 미세 보정 = 레시피 + 기준 보정)')
NO_POSLOG_TEXT = '비전 로그 대조 전 — Positioning Log 확인 시 확정'
SMALL_DRIVE_TEXT = '스캐너 구동이 작아 원인으로 단정 불가'
OTHER_TOOL_TEXT = '다른 장비의 시험 결과 — 적용 안 함'
LINEAR_NOTE = ('선형·원점 통과 가정 (θ = κ_eff × 스캐너 오프셋). 실데이터는 원점을 지나지 않는다 — 예: θx 5.22 µrad '
               '@ −27.7 µm, 0.95 µrad @ −8.1 µm → 기울기 ≈ −0.22 µrad/µm, 절편 ≈ −0.8 µrad. 예측은 근사이며 '
               '오프셋을 줄이는 쪽으로는 보수적이다 (실측이 예측보다 좋았다)')

# 단계 → 부품 (positioning_log 판정에서 그 성분을 가진 가장 앞 단계)
STAGE_PART = {'coarse_match': 'xy_stage', 'coarse_move': 'xy_stage',
              'fine_match': 'vision', 'fine_move': 'vision', 'vision_total': 'vision',
              'optical_shift': 'vision', 'tip_pos': 'vision'}

ROTATION_RULE = (
    f'회전 판정 (잠정 규격): θ가 유의하지 않으면(|t| < 3 또는 Die {MIN_DIES}개 미만) ok. 유의하면 '
    '|θ| ≥ theta_max → fail, ≥ theta_warn → warn. κ_eff(= |θ| / |구동 오프셋|, 오프셋 2 µm 이상일 때만)는 '
    '구동 ≥ offset_max/2이고 |θ| ≥ theta_warn/2일 때 kappa_warn 이상이면 적어도 warn, 그 밖은 참고. '
    'X 라인 |스캐너 Y 적용값| > offset_max면 레시피 확인 warn. Y 라인 |스캐너 X 적용값| > offset_max는 참고 '
    '(기준 장비에서 X 구동은 돌지 않았다).')
PART_RULES = (
    f'xy_scanner_y: fail은 최종 θx가 유의(|t| ≥ 3, Die {MIN_DIES}개 이상)하고 |θx| ≥ theta_max이며, X 라인 '
    f'|스캐너 Y| ≥ {MIN_DRIVE_FOR_ATTRIBUTION_UM:g} µm이고, Positioning Log 분석이 그 성분을 비전 정렬 이후로 '
    '볼 때만. Positioning Log 분석이 없으면 많아야 warn (비전 로그 대조 전), 스캐너 Y를 모르면 warn, '
    f'{MIN_DRIVE_FOR_ATTRIBUTION_UM:g} µm 미만으로 구동했으면 na (단정 불가). 비전 단계가 그 성분을 가졌으면 ok '
    '(구동이 offset_max를 넘으면 warn). theta_warn 이상 theta_max 미만이면 같은 조건에서 warn.',
    'xy_scanner_x: Y 라인(θy, 스캐너 X)에 같은 규칙. Y 라인에 같은 모양이 없으면 ok.',
    'chuck: xy_scanner_y가 warn·fail이면 warn (데이터로는 스캐너와 척을 나눌 수 없다).',
    'vision / xy_stage: Positioning Log 판정이 그 성분을 비전 로그 단계에 돌리면 그 단계의 부품 '
    '(도착·거친 보정 → xy_stage, 정밀 정렬·보정·Optical Shift·Tip 위치 → vision). θ 성분이 '
    '|θ| ≥ theta_max이고 같은 부호면 fail, 그 밖은 warn. 로그 분석이 없으면 na.',
    'z_scanner_tip / f_stage: drift 또는 transient가 warn이면 warn (헤드 쪽 열·안착).',
    '스캐너 회전 시험(rotation_test, 같은 장비)이 폐루프(loop closed)·Y 축이고 status가 warn·fail이면 '
    'xy_scanner_y의 직접 증거 — xy_scanner_y를 적어도 그 판정으로 올린다 (status na는 무시).',
    '그 밖(z_stage, sample, body, anti_vibration, efem): 이 데이터로 판단할 신호가 없어 ok, '
    '데이터가 없으면 na.',
)


# ──────────────────────────────────────────────
# 숫자 도우미
# ──────────────────────────────────────────────

def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _round(value, digits=4):
    return round(float(value), digits) + 0.0 if _finite(value) else None     # + 0.0: '-0.0' 없앰


def _num(text) -> Optional[float]:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _mean(values):
    values = [v for v in values if _finite(v)]
    return sum(values) / len(values) if values else None


def _std(values):
    """표본 표준편차 (n−1), 판정과 같은 정의."""
    values = [v for v in values if _finite(v)]
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _line(xs, ys):
    """최소제곱 기울기와 피어슨 r. 계산할 수 없으면 (None, None)."""
    n = len(xs)
    if n < 2:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx, (sxy / math.sqrt(sxx * syy) if syy > 0 else None)


def _significant(theta, t, k) -> bool:
    """유의한 평면 성분: 반올림하지 않은 |t| ≥ 3이고 Die MIN_DIES개 이상 (positioning_log 판정과 같다)."""
    return _finite(theta) and _finite(t) and abs(t) >= MIN_T and _finite(k) and k >= MIN_DIES


def _worst(*verdicts):
    order = {'na': -1, 'ok': 0, 'warn': 1, 'fail': 2}
    known = [v for v in verdicts if v in order]
    return max(known, key=lambda v: order[v]) if known else 'na'


def _clean(value):
    """JSON으로 쓸 수 있게: NaN·inf → None, tuple·set → list, numpy 스칼라 → 파이썬 값,
    날짜 → ISO 문자열, 그 밖의 객체 → 문자열 (to_json이 실패하지 않게)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if type(value).__module__ == 'numpy' and hasattr(value, 'item'):
        return _clean(value.item())
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _iso(value) -> Optional[str]:
    return value.isoformat(timespec='seconds') if isinstance(value, datetime) else value


def _parse_time(text) -> Optional[datetime]:
    text = (text or '').strip()
    for fmt in ('%Y.%m.%d %H:%M:%S', '%Y/%m/%d %H:%M:%S.%f', '%Y/%m/%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt(value, fmt='.3f'):
    return '—' if not _finite(value) else format(value, fmt)


# ──────────────────────────────────────────────
# Log.csv 해석
# ──────────────────────────────────────────────

def parse_log_events(text: str) -> list:
    """Log.csv 텍스트 → [(시각, Parent Job, Current Job, Step, {param id 소문자: 값}), ...].

    한 행에 Param이 하나씩이고, 같은 Step의 다음 Param은 앞 칸 4개가 빈 행으로 이어진다.
    같은 Param ID가 두 번 나오면 처음 값을 쓴다.
    """
    events = []
    for row in csv.reader(io.StringIO(text or '')):
        if not row or (row[0] or '').strip() == 'Time':
            continue
        if (row[0] or '').strip():
            params = {}
            if len(row) > 5 and row[4].strip() not in ('-', ''):
                params[row[4].strip().lower()] = row[5].strip()
            events.append((row[0].strip(), row[1].strip() if len(row) > 1 else '',
                           row[2].strip() if len(row) > 2 else '',
                           row[3].strip() if len(row) > 3 else '', params))
        elif events and len(row) > 5 and row[4].strip():
            events[-1][4].setdefault(row[4].strip().lower(), row[5].strip())
    return events


def parse_scanner_offsets(text: str) -> dict:
    """Log.csv의 측정 라인별 스캐너 오프셋.

    Returns:
        {'lines': [{'line': 'X'|'Y', 'direction', 'time', 'applied': (x, y) µm,
                    'fine': (x, y) µm 또는 None, 'ref': (x, y) µm 또는 None}, ...],
         'refs': [{'time', 'ref': (x, y) µm, 'total': (x, y) µm 또는 None}, ...],
         'first_time': 첫 행 시각 문자열, 'tool_id', 'tool_type_id'}
        fine은 그 측정 전 마지막 'Sample pattern align - fine'이고 X·Y 라인 한 쌍에만 쓴다 (다음 Site에
        정밀 정렬이 없으면 None). 같은 측정(ImageJob)에서 같은 방향 'Set scan params'가 다시 나오면
        마지막 것만 센다. ref는 마지막 'Apply reference scan result' (Lot 안에 없으면 None).
        Reference·Total은 nm라 µm로 바꾼다. Tool ID는 처음 나온 빈칸 아닌 값.
    """
    events = parse_log_events(text)
    out = {'lines': [], 'refs': [], 'first_time': events[0][0] if events else '',
           'tool_id': '', 'tool_type_id': ''}
    fine = ref = None
    fine_used = set()
    block = None        # 지금 측정(ImageJob)의 lines 위치
    for time, _parent, job, step, params in events:
        job_l, step_l = job.lower(), step.lower()
        if not out['tool_id'] and params.get('tool id'):
            out['tool_id'] = params['tool id']
        if not out['tool_type_id'] and params.get('tool type id'):
            out['tool_type_id'] = params['tool type id']
        if step_l == 'sample pattern align - fine':
            fx, fy = _num(params.get('x offset - fine')), _num(params.get('y offset - fine'))
            fine = (fx, fy) if fx is not None and fy is not None else None
            fine_used, block = set(), None
        elif step_l == 'apply reference scan result':
            rx, ry = _num(params.get('x offset - reference')), _num(params.get('y offset - reference'))
            tx, ty = _num(params.get('x offset - total')), _num(params.get('y offset - total'))
            if rx is not None and ry is not None:
                ref = (rx / REFERENCE_NM_PER_UM, ry / REFERENCE_NM_PER_UM)
                total = ((tx / REFERENCE_NM_PER_UM, ty / REFERENCE_NM_PER_UM)
                         if tx is not None and ty is not None else None)
                out['refs'].append({'time': time, 'ref': ref, 'total': total})
        elif job_l == 'imagejob' and step_l == 'started':
            block = None
        elif job_l == 'imagejob' and step_l == 'set scan params':
            direction = params.get('scan direction', '')
            head = direction.strip()[:1].upper()
            x, y = _num(params.get('x offset')), _num(params.get('y offset'))
            if head not in AXES or x is None or y is None:
                continue
            entry = {'line': head, 'direction': direction, 'time': time, 'applied': (x, y),
                     'fine': fine, 'ref': ref}
            if block is not None and out['lines'][block]['line'] == head:
                # 같은 측정의 다시 한 설정: 마지막 것만 센다 (그 측정의 미세 보정 그대로)
                entry['fine'] = out['lines'][block]['fine']
                out['lines'][block] = entry
                continue
            out['lines'].append(entry)
            block = len(out['lines']) - 1
            fine_used.add(head)
            if fine_used >= set(AXES):
                fine, fine_used = None, set()       # X·Y 한 쌍에 썼다. 다음 Site는 자기 정밀 정렬로
    return out


def _csv_head(lot_path: str, csv_name: str) -> dict:
    """측정 CSV 메타 (Start/End Time, Recipe ID, Sample ID). 파일이 없으면 빈 dict, 읽기 오류는 올린다."""
    if not lot_path or not csv_name:
        return {}
    lines = _with_retry(lambda: _open_csv_text(os.path.join(lot_path, csv_name), 20))
    if isinstance(lines, (FileNotFoundError, NotADirectoryError)):
        return {}
    if isinstance(lines, OSError):
        raise lines
    meta = {}
    for line in lines:
        key, _, value = line.partition(',')
        key = key.strip().lower()
        if key == 'site id':
            break
        meta[key] = value.split(',')[0].strip()
    return {'start': _parse_time(meta.get('start time')), 'end': _parse_time(meta.get('end time')),
            'recipe_id': meta.get('recipe id', ''), 'sample': meta.get('sample id', '')}


def _choose_log(names: list, prefix: str):
    """(파일 이름 또는 None, 문제 문장). CSV 이름 앞부분과 같은 Log를 먼저, 하나뿐이면 그것."""
    logs = sorted(n for n in names if n.lower().endswith(LOG_SUFFIX))
    if not logs:
        return None, ''
    if prefix:
        same = [n for n in logs if n[:-len(LOG_SUFFIX)].lower() == prefix]
        if same:
            return same[0], ''
    if len(logs) == 1:
        return logs[0], ''
    return None, (f"Log가 {len(logs)}개인데 CSV 이름({prefix or '?'})과 맞는 것이 없음: "
                  + ', '.join(logs[:4]))


def _csv_prefix(csv_name: str) -> str:
    lower = (csv_name or '').lower()
    for suffix in ('_x_ul.csv', '_y_ul.csv'):
        if lower.endswith(suffix):
            return lower[:-len(suffix)]
    return ''


def _decode_log(raw: bytes, path: str) -> str:
    """Log 바이트 → 텍스트. UTF-16 BOM을 먼저 보고, 아니면 CSV와 같은 순서. 실패하면 ValueError."""
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    text, _ = _decode_bytes(raw, path)
    if text is None:
        raise ValueError(f'인코딩 해석 실패: {os.path.basename(path)}')
    return text


def read_lot_log(lot_name: str, lot_path: str, csv_name: str = '', session=None) -> dict:
    """[작업 스레드] Lot 하나의 Log와 측정 CSV 메타를 읽는다.

    status: 'ok' | 'no_log'(Lot 폴더는 보이는데 Log 폴더·파일 없음) | 'mismatch'(Log가 여럿인데 고를 수
            없음) | 'error'(네트워크·잠김·빈 파일·해석 실패 — '로그 없음'으로 보이지 않게 따로 둔다)
    Windows는 네트워크 경로 오류(WinError 53·67)도 FileNotFoundError로 올리므로, Log 폴더가 없으면
    Lot 폴더가 읽히는지 확인한다 (읽히지 않으면 error).
    """
    out = {'lot': lot_name, 'path': lot_path, 'log': None, 'status': 'ok', 'error': '',
           'problems': [], 'lines': [], 'refs': [], 'first_time': '', 'start': None, 'end': None,
           'recipe_id': '', 'sample': '', 'tool_id': '', 'tool_type_id': ''}
    if session is not None and session.cancelled:
        out['status'] = 'error'
        out['error'] = '중지됨'
        return out
    try:
        out.update(_csv_head(lot_path, csv_name))
        folder = os.path.join(lot_path, LOG_DIR)
        entries = _with_retry(lambda: list_dir(folder, session), session)
        if isinstance(entries, (FileNotFoundError, NotADirectoryError)):
            parent = _with_retry(lambda: list_dir(lot_path, session), session)
            if isinstance(parent, OSError):
                raise OSError(f'Lot 폴더를 읽지 못함 ({type(parent).__name__}: {parent})')
            out['status'] = 'no_log'
            out['problems'].append(NO_LOG_TEXT)
            return out
        if isinstance(entries, OSError):
            raise entries
        name, problem = _choose_log([n for n, is_folder in entries if not is_folder],
                                    _csv_prefix(csv_name))
        if problem:
            out['status'] = 'mismatch'
            out['problems'].append(problem)
            return out
        if name is None:
            out['status'] = 'no_log'
            out['problems'].append(NO_LOG_TEXT)
            return out
        path = os.path.join(folder, name)
        out['log'] = path
        raw = _with_retry(lambda: _read_file_bytes(path))
        if isinstance(raw, OSError):
            raise raw
        if not raw:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            raise OSError(f'파일을 열 수 없음 (잠김 또는 권한): {name}' if size else f'빈 Log 파일: {name}')
        parsed = parse_scanner_offsets(_decode_log(raw, path))
        out.update(lines=parsed['lines'], refs=parsed['refs'], first_time=parsed['first_time'],
                   tool_id=parsed['tool_id'], tool_type_id=parsed['tool_type_id'])
        if out['start'] is None:
            out['start'] = _parse_time(parsed['first_time'])
        if not parsed['lines']:
            out['problems'].append('Log에 측정 라인(ImageJob Set scan params)이 없음')
    except OSError as e:
        out['status'] = 'error'
        out['error'] = f'{type(e).__name__}: {e}'
    except (csv.Error, ValueError, UnicodeError) as e:     # 한 Lot의 깨진 파일이 다른 Lot을 막지 않게
        out['status'] = 'error'
        out['error'] = f'Log 해석 실패 ({type(e).__name__}: {e})'
    return out


def _lot_order(raw: list):
    """[(Lot 이름, 측정 CSV 이름)] 측정 순서 (lot_index)."""
    order, csv_names = {}, {}
    for r in raw:
        name = r.get('lot_name', '')
        order[name] = min(order.get(name, r.get('lot_index', 0)), r.get('lot_index', 0))
        if r.get('filename') and name not in csv_names:
            csv_names[name] = r['filename']
    return [(name, csv_names.get(name, '')) for name in sorted(order, key=lambda n: (order[n], n))]


def read_run_logs(result: dict, session=None, workers: int = LOT_LOAD_WORKERS) -> dict:
    """[작업 스레드] 회차 결과 하나의 Lot Log를 읽는다 (Lot마다 Log 폴더 목록·Log·CSV 앞부분).

    session(csv_loader.ScanSession)을 주면 중지(cancel)되면 남은 Lot은 읽지 않는다 (오류로 남아 담기지 않음).
    Returns: {'status': 'ok'|'partial'|'no_logs'|'error'|'no_data', 'message', 'lots': [...],
              'round': 회차 이름}
    """
    raw = (result or {}).get('raw_data') or []
    out = {'status': 'no_data', 'message': '분석할 데이터가 없음', 'lots': [],
           'round': (result or {}).get('round', '')}
    if not raw:
        return out
    if not result.get('round_path'):
        out.update(status='no_logs', message=NO_LOG_TEXT)
        return out
    jobs = [(name, lot_folder(result, name), csv) for name, csv in _lot_order(raw)]
    if len(jobs) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs)),
                                thread_name_prefix='xydiag') as pool:
            out['lots'] = list(pool.map(lambda j: read_lot_log(j[0], j[1], j[2], session), jobs))
    else:
        out['lots'] = [read_lot_log(name, path, csv, session) for name, path, csv in jobs]
    counts = {}
    for lot in out['lots']:
        counts[lot['status']] = counts.get(lot['status'], 0) + 1
    with_lines = sum(1 for lot in out['lots'] if lot['status'] == 'ok' and lot['lines'])
    if counts.get('error'):
        out['status'] = 'error'
        out['message'] = f'{READ_FAIL_TEXT} — ' + '; '.join(
            f"{lot['lot']}: {lot['error']}" for lot in out['lots'] if lot['status'] == 'error')[:400]
    elif with_lines == len(out['lots']):
        out['status'] = 'ok'
        out['message'] = f'Lot {with_lines}개 Log 읽음'
    elif with_lines:
        out['status'] = 'partial'
        out['message'] = f'Lot {len(out["lots"])}개 중 {with_lines}개 Log 읽음'
    else:
        out['status'] = 'no_logs'
        out['message'] = NO_LOG_TEXT
    return out


def has_read_error(logs: dict) -> bool:
    return (logs or {}).get('status') == 'error' or any(
        lot.get('status') == 'error' for lot in (logs or {}).get('lots') or [])


def store_logs(result: dict, logs: dict) -> bool:
    """읽은 Log를 회차 결과와 같은 회차의 run_results 항목에 담는다. 읽기 오류가 섞였으면 담지 않는다."""
    if has_read_error(logs):
        return False
    key = run_key(result)
    result['xy_logs'] = logs
    for run in result.get('run_results') or []:
        if run_key(run) == key:
            run['xy_logs'] = logs
    return True


def tool_identity(result: dict, logs: dict = None, tool_root: str = None) -> tuple:
    """(tool_id, 근거). Log의 'Tool ID'가 있으면 그것('log'), 없으면 데이터 폴더 경로('root':
    tool_root → Workflow 폴더(source_path) → 회차 폴더 순서, 대소문자·구분자 정규화). 모르면 (None, None).
    스캐너 회전 시험 결과를 같은 장비의 진단에만 붙이는 데 쓴다."""
    for lot in (logs or {}).get('lots') or []:
        if lot.get('tool_id'):
            return str(lot['tool_id']).strip(), 'log'
    result = result or {}
    root = tool_root or result.get('source_path') or result.get('round_path')
    if not root:
        return None, None
    return os.path.normcase(os.path.normpath(str(root))), 'root'


# ──────────────────────────────────────────────
# 계산
# ──────────────────────────────────────────────

def _die_means(dev: dict) -> dict:
    """{Die 라벨: Lot 평균 편차 µm} (반올림하지 않은 행렬 값의 평균)."""
    out = {}
    for die in dev.get('die_labels') or []:
        values = [dev['matrix'][lot].get(die) for lot in dev.get('repeat_labels') or []]
        mean = _mean([v for v in values if v is not None])
        if mean is not None:
            out[die] = mean
    return out


def _positions(rows: list) -> dict:
    return {f'Die{i + 1}': p for i, p in extract_die_positions(rows).items()}


def axis_shape(data: list, axis: str, positions: dict = None, dev: dict = None) -> dict:
    """축 하나의 평면 모양: Die 평균 편차를 Die 좌표(mm)에 v = c + a·x + b·y로 맞춘다.

    data는 분석 대상 행(안정화 Die 제외). θ는 X축이면 ∂dx/∂y(θx), Y축이면 ∂dy/∂x(θy), µrad.
    scale은 X축 ∂dx/∂x(Sx), Y축 ∂dy/∂y(Sy), ppm. t = 값/SE (Die 평균 잔차로, 반올림하지 않음).
    mean은 측정값 평균 µm, k는 피팅한 Die 수. 회차 비교 표의 θx·θy도 이 함수다 (CSV 값만 쓴다).
    """
    dev = dev if dev is not None else compute_deviation_matrix(data, axis)
    positions = positions if positions is not None else _positions(data)
    means = _die_means(dev)
    fit = plane_fit(means, positions) if len(means) >= 4 else None
    theta_key, scale_key = ('dy', 'dx') if axis == 'X' else ('dx', 'dy')
    theta = (fit or {}).get(theta_key)
    scale = (fit or {}).get(scale_key)
    return {'theta': theta['value'] if theta else None, 'theta_t': theta['t'] if theta else None,
            'theta_se': theta['se'] if theta else None,
            'scale_ppm': scale['value'] if scale else None, 'scale_t': scale['t'] if scale else None,
            'mean': dev['average_nm'] / 1000.0 if dev.get('die_labels') else None,
            'k': fit['k'] if fit else len(means)}


def _line_offsets(logs: dict) -> dict:
    """측정 라인별 오프셋 평균 (모든 측정, 안정화 Die 포함). 기준 보정이 없으면 Lot 순서로 앞 Lot의
    값을 이어 쓴다 (장비가 저장한 값을 쓰기 때문). 그래도 없으면 recipe·ref는 None."""
    lots = sorted(((logs or {}).get('lots') or []),
                  key=lambda lot: (lot.get('start') or datetime.max))
    per_line = {axis: [] for axis in AXES}
    carried = None
    for lot in lots:
        if lot.get('status') != 'ok':
            continue
        for item in lot.get('lines') or []:
            ref = item.get('ref') or carried
            per_line[item['line']].append(dict(item, ref=ref, lot=lot['lot']))
        if lot.get('refs'):
            carried = lot['refs'][-1]['ref']
    out = {}
    for axis in AXES:
        items = per_line[axis]
        entry = {'applied': None, 'recipe': None, 'ref': None, 'fine': None,
                 'recipe_plus_ref': None, 'recipe_spread': None, 'n': len(items), 'n_ref': 0}
        if items:
            fines = [i for i in items if i.get('fine') is not None]
            entry['applied'] = [_mean([i['applied'][k] for i in items]) for k in (0, 1)]
            if fines:
                entry['fine'] = [_mean([i['fine'][k] for i in fines]) for k in (0, 1)]
                entry['recipe_plus_ref'] = [_mean([i['applied'][k] - i['fine'][k] for i in fines])
                                            for k in (0, 1)]
            known = [i for i in fines if i.get('ref') is not None]
            entry['n_ref'] = len(known)
            if known:
                recipes = [[i['applied'][k] - i['ref'][k] - i['fine'][k] for i in known]
                           for k in (0, 1)]
                entry['ref'] = [_mean([i['ref'][k] for i in known]) for k in (0, 1)]
                entry['recipe'] = [_mean(v) for v in recipes]
                entry['recipe_spread'] = [max(v) - min(v) for v in recipes]
        out[LINE_KEYS[axis]] = entry
    return out


def _drive(offsets: dict, axis: str):
    """측정 라인의 구동 축 스캐너 오프셋: X 라인은 스캐너 Y, Y 라인은 스캐너 X (모르면 None)."""
    applied = (offsets.get(LINE_KEYS[axis]) or {}).get('applied')
    value = applied[1 if axis == 'X' else 0] if applied else None
    return value if _finite(value) else None


def _status_from_theta(theta, t, k, spec) -> str:
    if not _finite(theta):
        return 'na'
    if not _significant(theta, t, k):
        return 'ok'
    if abs(theta) >= spec['theta_max']:
        return 'fail'
    if abs(theta) >= spec['theta_warn']:
        return 'warn'
    return 'ok'


def _rotation_line(axis: str, shape: dict, offsets: dict, span_mm, spec: dict) -> dict:
    """한 측정 라인의 회전: X 라인은 θx와 스캐너 Y, Y 라인은 θy와 스캐너 X.

    κ_eff는 구동 ≥ offset_max/2이고 |θ| ≥ theta_warn/2일 때만 판정을 올린다 (그 밖은 참고).
    """
    theta, t, k = shape.get('theta'), shape.get('theta_t'), shape.get('k')
    drive = _drive(offsets, axis)
    significant = _significant(theta, t, k)
    kappa = kappa_signed = None
    if _finite(theta) and drive is not None and abs(drive) >= MIN_KAPPA_OFFSET_UM:
        kappa = abs(theta) / abs(drive)
        kappa_signed = theta / drive
    contribution = abs(theta) * span_mm / 1000.0 if _finite(theta) and _finite(span_mm) else None
    status = _status_from_theta(theta, t, k, spec)
    reasons = []
    name, drive_name = ('θx', '스캐너 Y') if axis == 'X' else ('θy', '스캐너 X')
    if _finite(theta):
        sig = '유의' if significant else '유의하지 않음'
        reasons.append(f"{axis} 라인 {name} {theta:+.2f} µrad (t {_fmt(t, '+.1f')}, Die {k}, {sig}) — 규격 "
                       f"{spec['theta_warn']:g} / {spec['theta_max']:g} µrad (잠정)")
    if contribution is not None:
        reasons.append(f"Range 기여 |{name}| × Die 좌표 폭 {span_mm:.0f} mm = {contribution:.3f} µm")
    if kappa is not None:
        reasons.append(f"κ_eff = |{name}| / |{drive_name} {drive:+.1f} µm| = {kappa:.3f} µrad/µm — 규격 "
                       f"{spec['kappa_warn']:g} / {spec['kappa_max']:g} (잠정)")
        if significant and kappa >= spec['kappa_warn']:
            usable = (abs(drive) >= spec['offset_max'] / 2 and abs(theta) >= spec['theta_warn'] / 2)
            level = '규격 초과' if kappa >= spec['kappa_max'] else '주의 구간'
            if usable:
                status = _worst(status, 'warn')
                reasons.append(f"κ_eff {level} — {drive_name} 구동 1 µm당 회전이 큼")
            else:
                reasons.append(f"참고: κ_eff {level}이지만 구동(|{drive:.1f}| < {spec['offset_max'] / 2:g} µm) "
                               f"또는 |{name}|(< {spec['theta_warn'] / 2:g} µrad)이 작아 판정에 쓰지 않음")
    elif drive is not None:
        reasons.append(f"{drive_name} 적용값 {drive:+.1f} µm — {MIN_KAPPA_OFFSET_UM:g} µm 미만이라 κ_eff 계산 안 함")
    return {'theta': _round(theta, 3), 'theta_t': _round(t, 2), 'kappa_eff': _round(kappa, 4),
            'kappa_signed': _round(kappa_signed, 4), 'contribution_um': _round(contribution, 3),
            'span_mm': _round(span_mm, 1), 'significant': significant, 'status': status,
            'reasons': reasons, ('scanner_y' if axis == 'X' else 'scanner_x'): _round(drive, 3)}


def _offset_check(offsets: dict, spec: dict) -> dict:
    """레시피 오프셋 확인: X 라인 |스캐너 Y|, Y 라인 |스캐너 X| (참고)."""
    limit = spec['offset_max']
    reasons, status = [], 'ok'
    x_drive, y_drive = _drive(offsets, 'X'), _drive(offsets, 'Y')
    if x_drive is not None:
        if abs(x_drive) > limit:
            status = 'warn'
            reasons.append(f"X 라인 스캐너 Y 적용값 {x_drive:+.1f} µm — |값| > {limit:g} µm (잠정 한도): "
                           "Y 구동이 웨이퍼를 돌려 θx가 생길 수 있음, 레시피 Y Offset 확인")
        else:
            reasons.append(f"X 라인 스캐너 Y 적용값 {x_drive:+.1f} µm — 한도 {limit:g} µm 이내")
    else:
        status = 'na'
        reasons.append('Log가 없어 오프셋을 확인하지 못함')
    if y_drive is not None and abs(y_drive) > limit:
        reasons.append(f"참고: Y 라인 스캐너 X 적용값 {y_drive:+.1f} µm — |값| > {limit:g} µm. 기준 장비에서는 "
                       "X 구동 회전이 없었음 (θy로 확인)")
    return {'status': status, 'reasons': reasons}


def _lot_matrix(dev: dict) -> dict:
    """{Lot: {Die: 편차 µm}} (값 있는 칸만)."""
    return {lot: {d: v for d, v in row.items() if v is not None}
            for lot, row in (dev.get('matrix') or {}).items()}


def predict_whatif(matrix: dict, positions: dict, kappa_signed: float, delta_um: float,
                   spec_range=None, spec_stddev=None) -> dict:
    """레시피(=스캐너) 오프셋을 delta_um만큼 줄였을 때(현재 − 새 값) Lot별 X Range·StdDev 예측.

    선형 가정: H_new = H − κ·Δ·y_die (κ µrad/µm, y mm → µm는 × 1e-3).
    Returns: {'range_mean','range_max','std_mean','std_max','pass_lots','lots','range_all','std_all'}
    """
    shift = kappa_signed * delta_um * 1e-3
    ranges, stds, passes, pooled = [], [], 0, []
    for lot, row in matrix.items():
        values = [v - shift * positions[d][1] for d, v in row.items() if d in positions]
        if len(values) < 2:
            continue
        rng, std = max(values) - min(values), _std(values)
        ranges.append(rng)
        stds.append(std)
        pooled += values
        if spec_range is not None and spec_stddev is not None and rng <= spec_range and std <= spec_stddev:
            passes += 1
    known = spec_range is not None and spec_stddev is not None
    return {'range_mean': _mean(ranges), 'range_max': max(ranges) if ranges else None,
            'std_mean': _mean(stds), 'std_max': max(stds) if stds else None,
            'pass_lots': passes if known and ranges else None, 'lots': len(ranges),
            'range_all': (max(pooled) - min(pooled)) if pooled else None, 'std_all': _std(pooled)}


def _passes_all(row: dict) -> bool:
    return row.get('pass_lots') is not None and row.get('lots') and row['pass_lots'] == row['lots']


def _whatif(shape: dict, drive, recipe_y, matrix: dict, positions: dict, spec_range,
            spec_stddev) -> dict:
    """X 라인 레시피 Y Offset 후보별 예측 (선형·원점 통과). 후보는 지금 레시피와 같은 쪽(부호).

    권장 (3D 보고서 템플릿과 같은 규칙, 많아야 한 행):
      지금 행이 모든 Lot PASS면 지금 행('현재 유지'). 아니면 |레시피| ≥ 5 µm(꼭짓점 여유)이고 모든 Lot
      PASS인 후보 중 |레시피|가 가장 작은 것. 그런 후보가 없으면 권장 없음. 레시피 값을 모르면
      (스캐너 Y 목표 행) 지금 행만 권장할 수 있다.
    """
    out = {'assumption': 'linear kappa_eff', 'kappa_eff': None, 'kappa_signed': None,
           'basis': 'recipe_y', 'rows': [], 'note': ''}
    theta, t, k = shape.get('theta'), shape.get('theta_t'), shape.get('k')
    if not _significant(theta, t, k) or drive is None or abs(drive) < MIN_KAPPA_OFFSET_UM:
        out['note'] = ('θx가 유의하지 않거나(|t| < 3 또는 Die 부족) 스캐너 Y 적용값이 작아 κ_eff를 정할 수 없음 — '
                       '예측하지 않음')
        return out
    kappa = theta / drive
    out['kappa_eff'] = _round(abs(kappa), 4)
    out['kappa_signed'] = _round(kappa, 4)
    out['note'] = LINEAR_NOTE
    current_recipe = recipe_y if _finite(recipe_y) else None
    if current_recipe is not None:
        if abs(current_recipe) < ZERO_RECIPE_UM:
            current_recipe = 0.0
        current = current_recipe
        side = 1.0 if current_recipe > 0 else -1.0
    else:
        # 레시피 값을 모르면 스캐너 Y 적용값 목표로 예측한다
        out['basis'] = 'scanner_y'
        out['note'] = NO_REF_TEXT + ' — 행은 스캐너 Y 적용값 목표. ' + LINEAR_NOTE
        current = drive
        side = 1.0 if drive > 0 else -1.0
    targets = [math.copysign(abs(r), side) for r in WHATIF_RECIPES_Y]
    if all(abs(v - current) > 0.05 for v in targets):
        targets.append(current)
    rows = []
    for target in targets:
        delta = current - target
        scanner_y = drive - delta
        pred = predict_whatif(matrix, positions, kappa, delta, spec_range, spec_stddev)
        rows.append({'recipe_y': _round(target, 3) if out['basis'] == 'recipe_y' else None,
                     'scanner_y': _round(scanner_y, 2), 'theta': _round(kappa * scanner_y, 2),
                     'range_mean': _round(pred['range_mean'], 3), 'range_max': _round(pred['range_max'], 3),
                     'std_mean': _round(pred['std_mean'], 3), 'std_max': _round(pred['std_max'], 3),
                     'pass_lots': pred['pass_lots'], 'lots': pred['lots'],
                     'range_all': _round(pred['range_all'], 3), 'std_all': _round(pred['std_all'], 3),
                     'current': abs(delta) < 0.05, 'recommended': False, '_target': target})
    rows.sort(key=lambda r: -abs(r['_target']))           # 먼 값 → 0 (예: −25 … 0, +25 … 0)
    now = next((r for r in rows if r['current']), None)
    if now is not None and _passes_all(now):
        now['recommended'] = True
    elif out['basis'] == 'recipe_y':
        pool = [r for r in rows if not r['current'] and abs(r['_target']) >= CORNER_CLEARANCE_UM - 1e-9
                and _passes_all(r)]
        if pool:
            min(pool, key=lambda r: abs(r['_target']))['recommended'] = True
    for r in rows:
        del r['_target']
    out['rows'] = rows
    return out


def _kappa_two_point(result: dict, logs: dict, shape_x: dict) -> Optional[dict]:
    """참고: 이 Recipe의 회차들 중 X 라인 스캐너 Y를 이미 아는 회차(담긴 Log)로 θx 대 스캐너 Y의 기울기·
    절편을 맞춘다 (구동 차이 5 µm 이상, 2회차 이상). Log를 더 읽지 않는다. 판정에 쓰지 않는다."""
    points, seen = [], set()
    candidates = [(result, logs)] + [(run, run.get('xy_logs')) for run in result.get('run_results') or []]
    for run, run_logs in candidates:
        key = run_key(run)
        if key in seen or not isinstance(run_logs, dict):
            continue
        seen.add(key)
        drive = _drive(_line_offsets(run_logs), 'X')
        if run is result:
            theta = shape_x.get('theta')
        else:
            theta = axis_shape(filter_stabilization_die(run.get('raw_data') or []), 'X').get('theta')
        if drive is not None and _finite(theta):
            points.append({'run': run.get('round', ''), 'scanner_y': drive, 'theta': theta})
    if len(points) < 2:
        return None
    xs = [p['scanner_y'] for p in points]
    if max(xs) - min(xs) < MIN_DRIVE_FOR_ATTRIBUTION_UM:
        return None
    slope, r = _line(xs, [p['theta'] for p in points])
    if slope is None:
        return None
    intercept = _mean([p['theta'] for p in points]) - slope * _mean(xs)
    return {'kappa': _round(slope, 4), 'intercept': _round(intercept, 3), 'r': _round(r, 3),
            'points': [{'run': p['run'], 'scanner_y': _round(p['scanner_y'], 2), 'theta': _round(p['theta'], 3)}
                       for p in points],
            'note': '참고: 회차별 X 라인 스캐너 Y와 θx로 맞춘 직선 (기울기 µrad/µm, 절편 µrad) — 판정에 쓰지 않음'}


def _rows_by_lot_axis(raw: list) -> dict:
    """{(Lot, 축): [쓸 수 있는 행, ...]} (측정 순서 = CSV 행 순서) — 한 번만 나눈다."""
    out = {}
    for r in raw:
        axis = (r.get('method') or '').upper()
        if axis in AXES and is_usable(r):
            out.setdefault((r.get('lot_name', ''), axis), []).append(r)
    return out


def _static_plane(dev: dict, positions: dict) -> tuple:
    """Die 평균 평면에서 유의한(|t| ≥ 3) 기울기만 (a, b) µm/mm. 유의하지 않은 항은 0.

    측정 순서가 Die 위치와 엮여 있어(앞쪽 Die는 +Y, 뒤쪽은 −Y) 큰 θx가 있으면 측정 순서 기울기로
    보인다. 그 공간 모양만 빼고 시간 흐름을 본다 (유의하지 않은 항은 빼지 않는다).
    """
    fit = plane_fit(_die_means(dev), positions)
    if not fit:
        return 0.0, 0.0
    out = []
    for key, raw_key in (('dx', 'a'), ('dy', 'b')):
        entry = fit.get(key)
        out.append(fit['raw'][raw_key] if entry and _finite(entry.get('t')) and abs(entry['t']) >= MIN_T
                   else 0.0)
    return tuple(out)


def _drift(groups: dict, data: list, lots_order: list, logs: dict, devs: dict,
           positions: dict) -> dict:
    """Lot 평균 대 Lot 시작 시각, Lot 안 측정 순서 기울기 (유의한 평면 모양만 뺀 값).

    주의: Lot 3개 이상에서 축별 Lot 평균 폭 > 0.2 µm, 또는 첫 Lot |기울기| > 0.01 µm/Die이면서 |r| ≥ 0.5.
    """
    starts = {lot['lot']: lot.get('start') for lot in (logs or {}).get('lots') or []}
    dies = {extract_die_number(r.get('site_id', '')) for r in data}
    dies.discard(None)
    lot_means, within = [], []
    planes = {axis: _static_plane(devs[axis], positions) for axis in AXES}
    for lot in lots_order:
        entry = {'lot': lot, 'start': _iso(starts.get(lot))}
        for axis in AXES:
            rows = [r for r in groups.get((lot, axis), [])
                    if extract_die_number(r.get('site_id', '')) in dies]
            entry[axis.lower()] = _round(_mean([r['value'] / 1000.0 for r in rows]), 4)
            a, b = planes[axis]
            values = []
            for r in rows:
                p = positions.get(f"Die{extract_die_number(r['site_id']) + 1}")
                if p is not None:
                    values.append(r['value'] / 1000.0 - a * p[0] - b * p[1])
            slope, r = (_line(list(range(len(values))), values) if len(values) >= DRIFT_MIN_DIES
                        else (None, None))
            within.append({'lot': lot, 'axis': axis, 'slope_um_per_die': _round(slope, 5),
                           'r': _round(r, 3), 'n': len(values)})
        lot_means.append(entry)
    if all(e['start'] for e in lot_means):
        lot_means.sort(key=lambda e: e['start'])
    enough = len(lot_means) >= DRIFT_MIN_LOTS
    status, reasons, span = 'ok', [], {}
    for axis in AXES:
        means = [e[axis.lower()] for e in lot_means if e[axis.lower()] is not None]
        span[axis] = _round(max(means) - min(means), 4) if len(means) >= 2 else None
        if span[axis] is not None and span[axis] > DRIFT_SPAN_UM:
            first, last = means[0], means[-1]
            text = (f"{axis} Lot 평균 폭 {span[axis]:.3f} µm > {DRIFT_SPAN_UM:g} µm "
                    f"(처음 {first:+.3f} → 마지막 {last:+.3f} µm)")
            if enough:
                status = 'warn'
                reasons.append(text + ' — 예열(열) 드리프트 의심 — 측정 전 워밍업/더미 Lot 권장')
            else:
                reasons.append(f'참고: {text} — Lot {len(lot_means)}개라 판정하지 않음 ({DRIFT_MIN_LOTS}개 이상 필요)')
    first_lot = lot_means[0]['lot'] if lot_means else None
    for item in within:
        if item['lot'] != first_lot or item['slope_um_per_die'] is None:
            continue
        if abs(item['slope_um_per_die']) > DRIFT_SLOPE_UM_PER_DIE:
            r_value = item['r']
            text = (f"첫 Lot({first_lot}) {item['axis']} Lot 안 기울기 {item['slope_um_per_die']:+.4f} µm/Die, "
                    f"r {_fmt(r_value, '+.2f')}")
            if enough and r_value is not None and abs(r_value) >= DRIFT_MIN_R:
                status = 'warn'
                reasons.append(f"{text} > {DRIFT_SLOPE_UM_PER_DIE:g} — 예열(열) 드리프트 의심 — "
                               "측정 전 워밍업/더미 Lot 권장")
            else:
                reasons.append(f"참고: {text} — |r| < {DRIFT_MIN_R:g} 또는 Lot {DRIFT_MIN_LOTS}개 미만이라 판정하지 않음")
    if status == 'ok':
        reasons.append(f"Lot 평균 폭 X {_fmt(span.get('X'))} / Y {_fmt(span.get('Y'))} µm, 첫 Lot 안 "
                       f"기울기 기준 {DRIFT_SLOPE_UM_PER_DIE:g} µm/Die (|r| ≥ {DRIFT_MIN_R:g}) — 주의 없음")
    if len(lot_means) < 2:
        reasons.append('Lot이 1개라 Lot 평균 흐름은 볼 수 없음')
    return {'status': status, 'reasons': reasons, 'span': span, 'first_lot': first_lot,
            'lot_means': lot_means, 'within_lot': within,
            'time_basis': 'start' if all(e['start'] for e in lot_means) else 'lot_order'}


def _plane_value(coef: dict, position) -> float:
    """plane_fit의 raw 계수로 Die 위치의 평면 값 (µm). 정하지 않은 기울기는 0."""
    return coef['c'] + (coef['a'] or 0.0) * position[0] + (coef['b'] or 0.0) * position[1]


def _transient(groups: dict, lots: list, positions_all: dict) -> dict:
    """Lot마다 Die4 이후로 평면을 맞춘 잔차의 Lot 평균: Die1·2·3 대 Die4 이후 (µm).

    평면 계수를 둘 다 정할 수 없는 Lot(공선·좌표 퍼짐 부족)은 뺀다. 주의는 Lot 3개 이상에서
    Die2 |평균| > 0.15 µm이고 |평균|/SE ≥ 3일 때.
    """
    out = {'status': 'na', 'reasons': []}
    any_value = False
    for axis in AXES:
        resid = {1: [], 2: [], 3: []}
        base = []
        for lot in lots:
            values = {}
            for r in groups.get((lot, axis), []):
                die = extract_die_number(r.get('site_id', ''))
                if die is not None:
                    values[f'Die{die + 1}'] = r['value'] / 1000.0
            fit_vals = {d: v for d, v in values.items() if int(d[3:]) >= TRANSIENT_FIT_FROM_DIE}
            fit = plane_fit(fit_vals, positions_all) if len(fit_vals) >= TRANSIENT_MIN_DIES else None
            if not fit or (fit.get('dx') is None and fit.get('dy') is None):
                continue
            base += [v - _plane_value(fit['raw'], positions_all[d])
                     for d, v in fit_vals.items() if d in positions_all]
            for k in (1, 2, 3):
                d = f'Die{k}'
                if d in values and d in positions_all:
                    resid[k].append(values[d] - _plane_value(fit['raw'], positions_all[d]))
        offset = _mean(base) or 0.0
        entry, ts = {}, {}
        for k in (1, 2, 3):
            mean = _mean(resid[k])
            value = mean - offset if mean is not None else None
            std = _std(resid[k])
            se = std / math.sqrt(len(resid[k])) if std else None
            entry[f'die{k}'] = _round(value, 3)
            ts[f'die{k}'] = _round(value / se, 2) if value is not None and se else None
        entry['t'] = ts
        entry['lots'] = max((len(v) for v in resid.values()), default=0)
        out[axis] = entry
        any_value = any_value or any(entry[f'die{k}'] is not None for k in (1, 2, 3))
    if not any_value:
        out['reasons'].append(f'Die {TRANSIENT_MIN_DIES}개 이상(Die{TRANSIENT_FIT_FROM_DIE} 이후)으로 평면을 '
                              '맞출 수 없어 계산하지 않음')
        return out
    out['status'] = 'ok'
    for axis in AXES:
        d = out[axis]
        die2, t2 = d.get('die2'), d['t'].get('die2')
        if die2 is None or abs(die2) <= TRANSIENT_DIE2_UM:
            continue
        text = (f"{axis} Die2 잔차 {die2:+.3f} µm > {TRANSIENT_DIE2_UM:g} µm (t {_fmt(t2, '+.1f')}, "
                f"Lot {d['lots']}개; Die1 {_fmt(d['die1'], '+.3f')}, Die3 {_fmt(d['die3'], '+.3f')})")
        if d['lots'] >= TRANSIENT_MIN_LOTS and t2 is not None and abs(t2) >= MIN_T:
            out['status'] = 'warn'
            out['reasons'].append(text + ' — Lot 시작 직후 과도 응답이 판정 대상 Die2에 남음')
        else:
            out['reasons'].append(f'참고: {text} — Lot {TRANSIENT_MIN_LOTS}개 이상·|t| ≥ {MIN_T:g}가 아니라 판정하지 않음')
    if out['status'] == 'ok':
        out['reasons'].append(
            f"Die2 잔차 X {_fmt(out['X'].get('die2'), '+.3f')} / Y {_fmt(out['Y'].get('die2'), '+.3f')} µm — "
            f"주의 없음 (기준 {TRANSIENT_DIE2_UM:g} µm, |t| ≥ {MIN_T:g})")
    return out


def _die_field(raw: list, devs: dict, positions: dict) -> list:
    sites = {}
    for r in raw:
        die = extract_die_number(r.get('site_id', ''))
        if die is not None and die not in sites:
            sites[die] = (r.get('site_x'), r.get('site_y'))
    means = {axis: _die_means(devs[axis]) for axis in AXES}
    labels = sorted(set(means['X']) | set(means['Y']), key=lambda d: int(d[3:]))
    out = []
    for d in labels:
        pos = positions.get(d)
        site = sites.get(int(d[3:]) - 1, (None, None))
        out.append({'die': d, 'site_x': site[0], 'site_y': site[1],
                    'x_mm': _round(pos[0], 3) if pos else None, 'y_mm': _round(pos[1], 3) if pos else None,
                    'dx': _round(means['X'].get(d), 4), 'dy': _round(means['Y'].get(d), 4)})
    return out


def _positioning_summary(poslog: dict) -> Optional[dict]:
    """Positioning Log 분석에서 판정 문장, 단계 θ, 최종 값을 옮긴다. 분석이 없으면 None.

    judged: 최종 착지가 Die MIN_DIES개 이상이라 judge가 판정한 분석인가 (아니면 '비전 정렬 이후'의
    근거가 아니다).
    """
    if not isinstance(poslog, dict):
        return None
    out = {'status': poslog.get('status'), 'message': poslog.get('message', ''), 'judged': False,
           'verdict_lines': [], 'stages': [], 'final': None, 'terms': []}
    mean = poslog.get('mean')
    if not mean:
        return out
    verdict = poslog.get('verdict') or {}
    out['verdict_lines'] = list(verdict.get('lines') or [])
    out['terms'] = [{'axis': t['axis'], 'term': t['term'], 'value': _round(t['value'], 3),
                     't': _round(t['t'], 2), 'carriers': list(t.get('carriers') or []),
                     'same_sign': [d.get('same_sign') for d in t.get('details') or []],
                     'span': t.get('span')} for t in verdict.get('terms') or []]
    final_k = 0
    for row in mean.get('stages') or []:
        name = 'θx' if row['axis'] == 'X' else 'θy'
        coef = (row.get('coef') or {}).get(name)
        item = {'name': row.get('label') or STAGE_LABEL.get(row['stage'], row['stage']),
                'stage': row['stage'], 'axis': row['axis'],
                'theta': _round(coef['value'], 3) if coef else None,
                't': _round(coef['t'], 2) if coef else None}
        if row['stage'] == 'final':
            final_k = max(final_k, row.get('k') or 0)
            if row['axis'] == 'X':
                out['final'] = {'axis': 'X', 'theta': item['theta'], 't': item['t']}
        else:
            out['stages'].append(item)
    out['judged'] = final_k >= MIN_DIES
    return out


def rotation_test_text(rotation_test) -> str:
    """스캐너 회전 시험 결과 한 줄 (κ, 판정, 시험 단계, 날짜, 루프·축). 결과가 없으면 ''."""
    rt = rotation_test if isinstance(rotation_test, dict) else None
    if not rt:
        return ''
    return (f"스캐너 회전 시험 κ {_fmt(rt.get('kappa'), '.3f')} µrad/µm — "
            f"{rt.get('status') or 'na'} ({rt.get('stage') or '단계 —'}, {rt.get('date') or '날짜 —'}, "
            f"{rt.get('loop') or '루프 —'}·{rt.get('axis') or '축 —'}, 잠정 규격)")


def _rotation_test_evidence(rotation_test):
    """폐루프·Y 축 시험의 warn·fail이면 그 판정 (xy_scanner_y의 직접 증거), 아니면 None."""
    rt = rotation_test if isinstance(rotation_test, dict) else None
    if (rt and rt.get('status') in ('warn', 'fail') and str(rt.get('loop') or '').lower() == 'closed'
            and str(rt.get('axis') or '').upper() == 'Y'):
        return rt['status']
    return None


def _apply_rotation_test(parts: dict, rotation_test) -> None:
    """회전 시험이 직접 증거면 xy_scanner_y를 적어도 그 판정으로 올리고 근거를 덧붙인다."""
    level = _rotation_test_evidence(rotation_test)
    if level is None:
        return
    verdict, reason = parts.get('xy_scanner_y', ('na', ''))
    parts['xy_scanner_y'] = (_worst(verdict, level),
                             (f'{reason}; ' if reason else '') + rotation_test_text(rotation_test)
                             + ' — 스캐너 Y 구동 회전의 직접 증거')


def _part(pid, verdict, reason):
    return {'id': pid, 'name': PART_NAMES[pid], 'verdict': verdict, 'reason': reason}


def _scanner_part(axis: str, shape: dict, drive, poslog_summary, spec: dict) -> tuple:
    """스캐너 구동 부품 판정 (X 라인 → xy_scanner_y, Y 라인 → xy_scanner_x). PART_RULES 첫 항목."""
    term, drive_name = ('θx', 'X 라인 스캐너 Y') if axis == 'X' else ('θy', 'Y 라인 스캐너 X')
    theta, t, k = shape.get('theta'), shape.get('theta_t'), shape.get('k')
    level = _status_from_theta(theta, t, k, spec)
    if level == 'na':
        return 'na', f'{term}를 정할 수 없음 (Die 좌표·데이터 부족)'
    head = f"{term} {theta:+.2f} µrad (t {_fmt(t, '+.1f')}, Die {k})"
    drive_text = f"{drive_name} {drive:+.1f} µm" if drive is not None else f"{drive_name} 모름"
    if level == 'ok':
        return 'ok', f"{head} — 유의하지 않거나 {spec['theta_warn']:g} µrad 미만, {drive_text}"
    head += f" ≥ {spec['theta_warn' if level == 'warn' else 'theta_max']:g} µrad"
    if drive is None:
        return 'warn', f"{head}, {drive_text} (Log 없음) — 스캐너 구동을 몰라 원인으로 단정 불가"
    if abs(drive) < MIN_DRIVE_FOR_ATTRIBUTION_UM:
        return 'na', (f"{head}이지만 {drive_text} (< {MIN_DRIVE_FOR_ATTRIBUTION_UM:g} µm) — "
                      f"{SMALL_DRIVE_TEXT}")
    summary = poslog_summary or {}
    if not summary.get('judged'):
        return 'warn', f"{head}, {drive_text} — {NO_POSLOG_TEXT}"
    terms = {(x['axis'], x['term']): x for x in summary.get('terms') or []}
    found = terms.get((axis, term))
    if found is None:
        return 'warn', (f"{head}, {drive_text} — Positioning Log 판정에 이 성분이 없음 (로그 쪽에서 유의하지 않음) — "
                        '확정 불가')
    if not found['carriers']:
        return level, (f"{head}, {drive_text}, Positioning Log: 비전 로그 단계에 같은 성분 없음 → "
                       '비전 정렬 이후')
    where = STAGE_LABEL.get(found['carriers'][0], found['carriers'][0])
    if abs(drive) > spec['offset_max']:
        return 'warn', f"{head}, Positioning Log: {where} 단계에도 같은 성분, 그러나 {drive_text} > {spec['offset_max']:g} µm"
    return 'ok', f"{head}이지만 Positioning Log: {where} 단계에 같은 성분 (스캐너 구동이 아닌 쪽)"


def _parts(has_data: bool, shapes: dict, rotation: dict, poslog_summary, drift: dict,
           transient: dict, spec: dict, rotation_test: dict = None, offsets: dict = None):
    """부품별 판정 (PART_RULES). (parts, 판정 근거 dict).

    shapes는 반올림하지 않은 axis_shape 결과 (θ, t, k). 구동 오프셋은 offsets(_line_offsets)에서,
    없으면 rotation의 scanner_y·y_line.scanner_x에서 읽는다.
    """
    if not has_data:
        parts = {pid: ('na', '데이터 없음') for pid in PART_IDS}
        _apply_rotation_test(parts, rotation_test)
        return [_part(pid, *parts[pid]) for pid in PART_IDS], parts
    if offsets is not None:
        drives = {axis: _drive(offsets, axis) for axis in AXES}
    else:
        drives = {'X': rotation.get('scanner_y'), 'Y': (rotation.get('y_line') or {}).get('scanner_x')}
        drives = {axis: (v if _finite(v) else None) for axis, v in drives.items()}
    has_poslog = bool(poslog_summary and poslog_summary.get('stages'))
    terms = {(t['axis'], t['term']): t for t in (poslog_summary or {}).get('terms') or []}
    parts = {'xy_scanner_y': _scanner_part('X', shapes['X'], drives['X'], poslog_summary, spec)}
    _apply_rotation_test(parts, rotation_test)      # 폐루프 회전 시험은 직접 증거 (척 판정보다 먼저)
    v, why = _scanner_part('Y', shapes['Y'], drives['Y'], poslog_summary, spec)
    parts['xy_scanner_x'] = (v, why) if v in ('warn', 'fail') else (v, f'Y 라인에 같은 모양 없음 — {why}')
    if parts['xy_scanner_y'][0] in ('warn', 'fail'):
        parts['chuck'] = ('warn', '스캐너 Y 구동 때 웨이퍼가 돈다 — 데이터로는 스캐너와 척(웨이퍼 고정)을 '
                                  '나눌 수 없음')
    else:
        parts['chuck'] = ('ok', '스캐너 Y 회전 신호 없음')
    # 비전 로그 단계가 성분을 가진 경우
    vision = {'vision': [], 'xy_stage': []}
    for (axis, term), t in terms.items():
        if not t['carriers']:
            continue
        part = STAGE_PART.get(t['carriers'][0])
        if part is None:
            continue
        same = (t.get('same_sign') or [True])[0]
        fail = term.startswith('θ') and _finite(t['value']) and abs(t['value']) >= spec['theta_max'] and same
        vision[part].append(('fail' if fail else 'warn',
                             f"{axis} {term} {t['value']:+.2f} (t {_fmt(t['t'], '+.1f')})가 "
                             f"{STAGE_LABEL.get(t['carriers'][0], t['carriers'][0])} 단계에도 있음"
                             + ('' if same else ' (반대 부호 — 부호 정의·보정 과다 확인)')))
    for part in ('vision', 'xy_stage'):
        if not has_poslog:
            parts[part] = ('na', '비전 보정 로그 대조 없음 (Diagnosis·Positioning Log 탭에서 읽으면 판단)')
        elif vision[part]:
            parts[part] = (_worst(*(x[0] for x in vision[part])), '; '.join(x[1] for x in vision[part]))
        else:
            parts[part] = ('ok', 'Positioning Log: 이 부품 단계에 최종 성분 없음')
    head_flags = []
    if drift.get('status') == 'warn':
        head_flags.append('drift (예열·열)')
    if transient.get('status') == 'warn':
        head_flags.append('Lot 시작 과도 응답 (안착)')
    for part in ('z_scanner_tip', 'f_stage'):
        parts[part] = (('warn', '헤드 쪽 ' + ', '.join(head_flags) + ' 의심') if head_flags
                       else ('ok', 'drift·과도 응답 신호 없음'))
    for part in ('z_stage', 'sample', 'body', 'anti_vibration', 'efem'):
        parts[part] = ('ok', '이 데이터에서 판단할 신호 없음')
    return [_part(pid, *parts[pid]) for pid in PART_IDS], parts


def _actions(parts: dict, rotation: dict, offsets: dict, whatif: dict, drift: dict,
             transient: dict, spec: dict, rotation_test: dict = None) -> list:
    actions = []
    scanner = parts.get('xy_scanner_y', ('na', ''))[0]
    rows = whatif.get('rows') or []
    current = next((r for r in rows if r['current']), None)
    best = next((r for r in rows if r.get('recommended')), None)
    drive = _drive(offsets, 'X')
    if scanner in ('warn', 'fail'):
        if drive is None:
            actions.append('Log/<Sample>_Log.csv로 X 라인 스캐너 Y 오프셋 확인 — 회전 원인 판단에 필요')
        elif abs(drive) < MIN_DRIVE_FOR_ACTION_UM:
            pass        # 거의 구동하지 않았다: 오프셋을 줄여도 달라지지 않는다
        elif best is not None and best['current']:
            label = (f"레시피 Y Offset {best['recipe_y']:g} µm" if best['recipe_y'] is not None
                     else f"스캐너 Y 적용값 {best['scanner_y']:+.1f} µm")
            actions.append(f"X 라인 {label} 유지 (예측 모든 Lot PASS) — 남은 θx {_fmt(rotation.get('theta'), '+.2f')} "
                           "µrad는 스캐너 회전 시험으로 확인")
        elif best is not None and current is not None:
            pass_text = (f", PASS {best['pass_lots']}/{best['lots']} Lot" if best['pass_lots'] is not None
                         else '')
            actions.append(f"X 라인 레시피 Y Offset을 줄이세요 (예: {_fmt(current['recipe_y'], 'g')} → "
                           f"{_fmt(best['recipe_y'], 'g')} µm, 예측 Range {_fmt(best['range_mean'], '.2f')} µm·"
                           f"StdDev {_fmt(best['std_mean'], '.3f')} µm (Lot 평균){pass_text}, "
                           f"θx {_fmt(best['theta'], '+.2f')} µrad, 선형 가정)")
        elif whatif.get('basis') == 'scanner_y':
            actions.append(f"X 라인 스캐너 Y 적용값({drive:+.1f} µm)을 줄이세요 — {NO_REF_TEXT}. "
                           '레시피 Y Offset을 같은 양만큼 바꾸면 됨')
        elif rows:
            actions.append('X 라인 레시피 Y Offset을 줄이세요 — what-if에서 꼭짓점 여유(5 µm)를 지키며 모든 Lot '
                           'PASS인 후보가 없음, 다른 원인도 확인')
    kappa = rotation.get('kappa_eff')
    if scanner in ('warn', 'fail') or (kappa is not None and kappa >= spec['kappa_warn']
                                       and rotation.get('significant')):
        if _rotation_test_evidence(rotation_test):
            actions.append(f'{rotation_test_text(rotation_test)} — 스캐너(Y 구동) 교체·선형화 검토 뒤 '
                           '폐루프 2지점 스윕으로 재시험')
        elif isinstance(rotation_test, dict) and rotation_test.get('status') == 'ok':
            actions.append(f'{rotation_test_text(rotation_test)} — 규격 이내: 척(웨이퍼 고정)·시험 조건 확인')
        else:
            actions.append('폐루프 2지점 스윕으로 스캐너 회전 계수 측정 (출하 검사/스캐너 교체·선형화 후 관리 포인트)')
    if parts.get('chuck', ('ok',))[0] == 'warn':
        actions.append('척(웨이퍼 고정) 상태 확인 — 스캐너와 나누려면 척 위에서 스캐너 Y 구동 중 회전 측정')
    for part in ('xy_stage', 'vision'):
        verdict, reason = parts.get(part, ('ok', ''))
        if verdict in ('warn', 'fail'):
            actions.append(f"{PART_NAMES[part]} 점검 — {reason}")
    y_drive = _drive(offsets, 'Y')
    if parts.get('xy_scanner_x', ('ok',))[0] in ('warn', 'fail') and y_drive is not None \
            and abs(y_drive) >= MIN_DRIVE_FOR_ACTION_UM:
        actions.append('Y 라인 레시피 X Offset을 줄이세요 (Y 라인에도 스캐너 구동 회전)')
    if drift.get('status') == 'warn':
        actions.append('측정 전 워밍업 (또는 더미 Lot 1개 측정 뒤 본 측정) — 예열(열) 드리프트')
    if transient.get('status') == 'warn':
        actions.append('Lot 시작 직후 대기 (Tip Positioning·Z 이동 뒤 약 1분) 또는 안정화 Die 추가 검토 — '
                       'Die2 과도 응답')
    if y_drive is not None and abs(y_drive) > spec['offset_max'] \
            and parts.get('xy_scanner_x', ('ok',))[0] not in ('warn', 'fail'):
        actions.append(f"(참고) Y 라인 스캐너 X 적용값 {y_drive:+.1f} µm — 줄일 수 있으면 줄이세요 "
                       '(기준 장비에서 X 구동 회전은 없었음)')
    if not actions:
        actions.append('특이 사항 없음 — 현재 조건 유지')
    return actions


# ──────────────────────────────────────────────
# 진단
# ──────────────────────────────────────────────

@dataclass
class XYDiagnosis:
    """pass.xy.diagnosis/1. 길이는 µm, 각도는 µrad (필드 이름에 다른 단위가 없으면). 모르는 값은 None."""
    generated: str = ''
    app_version: str = ''
    source: dict = None
    spec: dict = None
    verdict: dict = None
    shape: dict = None
    scanner_offsets: dict = None
    rotation: dict = None
    positioning_log: dict = None
    drift: dict = None
    transient: dict = None
    die_field: list = None
    whatif: dict = None
    parts: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    rotation_test: dict = None
    schema: str = SCHEMA

    _ORDER = ('schema', 'generated', 'app_version', 'source', 'spec', 'verdict', 'shape',
              'scanner_offsets', 'rotation', 'positioning_log', 'drift', 'transient', 'die_field',
              'whatif', 'parts', 'actions', 'warnings', 'rotation_test')

    def to_dict(self) -> dict:
        return _clean({key: getattr(self, key) for key in self._ORDER})

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, allow_nan=False)

    @classmethod
    def from_dict(cls, data: dict) -> 'XYDiagnosis':
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


def build_diagnosis(result: dict, logs: dict = None, spec_deviation: dict = None,
                    rotation_spec: dict = None, poslog: dict = None, app_version: str = '',
                    generated: str = None, rotation_test: dict = None,
                    rotation_test_tool: str = None, tool_root: str = None) -> XYDiagnosis:
    """회차 결과 하나의 진단 (계산만, 파일을 읽지 않는다).

    logs: read_run_logs 결과 (없으면 result['xy_logs'], 그것도 없으면 오프셋은 모름)
    poslog: Positioning Log 분석 (없으면 result['positioning_log'], 그것도 없으면 null)
    rotation_test: 스캐너 회전 시험 결과 (설계의 rotation_test 형식, 회차가 아니라 장비의 것). 사본을
        담고, 폐루프·Y 축의 warn·fail은 xy_scanner_y 판정에 쓴다. rotation_test_tool(시험을 받은 장비의
        tool_id)이 이 진단의 tool_id와 다르면 붙이지 않는다 ('다른 장비의 시험 결과 — 적용 안 함').
    tool_root: tool_id를 Log에서 못 찾을 때 쓸 데이터 폴더 (화면이 연 폴더)
    """
    result = result or {}
    logs = logs if logs is not None else result.get('xy_logs')
    poslog = poslog if poslog is not None else result.get('positioning_log')
    spec_rot = _rotation_spec({'rotation_spec': rotation_spec}) if rotation_spec else dict(DEFAULT_ROTATION_SPEC)
    name = result.get('short_name') or result.get('recipe', '')
    ds = (spec_deviation or {}).get(name, {})
    spec_range, spec_stddev = ds.get('spec_range'), ds.get('spec_stddev')
    raw = result.get('raw_data') or []
    data = filter_stabilization_die(raw)
    diag = XYDiagnosis(generated=generated or datetime.now().isoformat(timespec='seconds'),
                       app_version=app_version)
    diag.spec = {'range': spec_range, 'stddev': spec_stddev, **spec_rot}
    lots_order = [n for n, _csv in _lot_order(raw)]
    log_lots = (logs or {}).get('lots') or []
    starts = [lot['start'] for lot in log_lots if isinstance(lot.get('start'), datetime)]
    ends = [lot['end'] for lot in log_lots if isinstance(lot.get('end'), datetime)]
    first = next((lot for lot in log_lots if lot.get('recipe_id') or lot.get('sample')), {})
    tool_id, tool_basis = tool_identity(result, logs, tool_root)
    diag.source = {'root': result.get('round_path') or result.get('source_path') or '',
                   'workflow': name, 'run': result.get('round', ''), 'lots': len(lots_order),
                   'dies': len({extract_die_number(r.get('site_id', '')) for r in data} - {None}),
                   'recipe_id': first.get('recipe_id') or None, 'sample': first.get('sample') or None,
                   'start': _iso(min(starts)) if starts else None,
                   'end': _iso(max(ends)) if ends else None,
                   'tool_id': tool_id, 'tool_id_basis': tool_basis,
                   'log_status': (logs or {}).get('status'), 'log_message': (logs or {}).get('message', '')}
    warnings = []
    rotation_test = copy.deepcopy(rotation_test) if isinstance(rotation_test, dict) else None
    if rotation_test is not None and rotation_test_tool is not None and rotation_test_tool != tool_id:
        rotation_test = None
        diag.source['rotation_test_note'] = OTHER_TOOL_TEXT
        warnings.append(f'스캐너 회전 시험: {OTHER_TOOL_TEXT}')
    diag.rotation_test = rotation_test
    if not data:
        diag.verdict = {axis: {'range': None, 'stddev': None, 'pass': None} for axis in AXES}
        diag.parts, _ = _parts(False, {}, {}, None, {}, {}, spec_rot, rotation_test)
        diag.actions = ['데이터가 없어 진단하지 않음']
        diag.warnings = warnings + ['분석할 데이터가 없음']
        return diag

    devs = {axis: compute_deviation_matrix(data, axis) for axis in AXES}
    positions = _positions(data)
    positions_all = _positions(raw)
    groups = _rows_by_lot_axis(raw)
    diag.verdict = {}
    for axis in AXES:
        m = compute_axis_metrics(data, axis, name, None, spec_deviation)
        diag.verdict[axis] = {'range': m['range'], 'stddev': m['stddev'], 'pass': m['result']}
    shapes = {axis: axis_shape(data, axis, positions, devs[axis]) for axis in AXES}
    diag.shape = {axis: {'theta': _round(s['theta'], 3), 'theta_t': _round(s['theta_t'], 2),
                         'theta_se': _round(s['theta_se'], 3), 'scale_ppm': _round(s['scale_ppm'], 3),
                         'scale_t': _round(s['scale_t'], 2), 'mean': _round(s['mean'], 4), 'k': s['k']}
                  for axis, s in shapes.items()}

    offsets = _line_offsets(logs)
    diag.scanner_offsets = {key: {k: ([_round(x, 3) for x in v] if isinstance(v, list) else v)
                                  for k, v in entry.items()}
                            for key, entry in offsets.items()}
    diag.scanner_offsets['source'] = 'Log.csv ImageJob'
    for key, entry in offsets.items():
        if entry['n'] and entry['recipe'] is None:
            warnings.append(f'{key}: {NO_REF_TEXT}')
        elif entry['n'] and entry['n_ref'] < entry['n']:
            warnings.append(f"{key}: 측정 {entry['n']}개 중 {entry['n_ref']}개만 기준 보정 값이 있음")
        if entry['recipe_spread'] and max(entry['recipe_spread']) > 0.01:
            warnings.append(f"{key}: 레시피 값이 측정마다 다름 (폭 {max(entry['recipe_spread']):.3f} µm) — "
                            '레시피가 바뀌었거나 분해 가정이 맞지 않음')
    for lot in log_lots:
        if lot['status'] == 'error':
            warnings.append(f"{lot['lot']}: {READ_FAIL_TEXT} ({lot['error']})")
        elif lot['status'] != 'ok' or lot.get('problems'):
            warnings.append(f"{lot['lot']}: {'; '.join(lot.get('problems') or [lot['status']])}")
    if logs is None:
        warnings.append('Log를 아직 읽지 않음 — 스캐너 오프셋·Lot 시작 시각 없음')

    ys = [p[1] for p in positions.values()]
    xs = [p[0] for p in positions.values()]
    span_y = (max(ys) - min(ys)) if ys else None
    span_x = (max(xs) - min(xs)) if xs else None
    rotation = _rotation_line('X', shapes['X'], offsets, span_y, spec_rot)
    rotation['axis'] = 'X_line'
    rotation['y_line'] = _rotation_line('Y', shapes['Y'], offsets, span_x, spec_rot)
    check = _offset_check(offsets, spec_rot)
    rotation['offset_check'] = check
    if check['status'] == 'warn':
        rotation['status'] = _worst(rotation['status'], 'warn')
    rotation['reasons'] = rotation['reasons'] + check['reasons']
    if rotation_test:
        rotation['reasons'].append(rotation_test_text(rotation_test))
    rotation['kappa_two_point'] = _kappa_two_point(result, logs, shapes['X'])
    rotation['rule'] = ROTATION_RULE
    diag.rotation = rotation

    diag.positioning_log = _positioning_summary(poslog)
    diag.drift = _drift(groups, data, lots_order, logs, devs, positions)
    diag.transient = _transient(groups, lots_order, positions_all)
    diag.die_field = _die_field(raw, devs, positions)
    recipe = (offsets.get('X_line') or {}).get('recipe')
    diag.whatif = _whatif(shapes['X'], _drive(offsets, 'X'), recipe[1] if recipe else None,
                          _lot_matrix(devs['X']), positions, spec_range, spec_stddev)
    parts, part_map = _parts(True, shapes, rotation, diag.positioning_log, diag.drift,
                             diag.transient, spec_rot, rotation_test, offsets)
    diag.parts = parts
    diag.actions = _actions(part_map, rotation, offsets, diag.whatif, diag.drift, diag.transient,
                            spec_rot, rotation_test)
    diag.warnings = warnings
    return diag


def diagnose(result: dict, spec_deviation: dict = None, rotation_spec: dict = None,
             session=None, workers: int = LOT_LOAD_WORKERS, app_version: str = '',
             generated: str = None, rotation_test: dict = None,
             rotation_test_tool: str = None) -> XYDiagnosis:
    """[작업 스레드] 담긴 Log가 없으면 읽고 진단한다 (결과를 바꾸지 않는다). 내보내기·명령줄용."""
    logs = result.get('xy_logs')
    if not isinstance(logs, dict):
        logs = read_run_logs(result, session, workers)
    return build_diagnosis(result, logs, spec_deviation, rotation_spec, app_version=app_version,
                           generated=generated, rotation_test=rotation_test,
                           rotation_test_tool=rotation_test_tool)


__all__ = ['SCHEMA', 'PART_IDS', 'PART_NAMES', 'PART_RULES', 'ROTATION_RULE', 'XYDiagnosis',
           'axis_shape', 'build_diagnosis', 'diagnose', 'has_read_error', 'parse_log_events',
           'parse_scanner_offsets', 'predict_whatif', 'read_lot_log', 'read_run_logs',
           'rotation_test_text', 'store_logs', 'tool_identity',
           'NO_LOG_TEXT', 'NO_POSLOG_TEXT', 'NO_REF_TEXT', 'OTHER_TOOL_TEXT', 'READ_FAIL_TEXT',
           'SMALL_DRIVE_TEXT', 'WHATIF_RECIPES_Y']
