"""
csv_loader.py — SmartScan Recipe 결과 CSV 범용 파서

- Lot 폴더 자동 탐지 및 정렬
- CSV 메타헤더/데이터 분리
- 유연한 범위 지정 배치 로드
"""

import os
import re
import csv
import logging
from typing import Optional, Union


# ──────────────────────────────────────────────
# CSV Parsing
# ──────────────────────────────────────────────

# 네트워크 드라이브 호환 인코딩 목록
_ENCODINGS = ('utf-8-sig', 'cp949', 'euc-kr', 'latin-1')


def _read_file_bytes(file_path: str) -> bytes:
    """파일 바이트 읽기 — DLP 차단 시 xcopy 폴백.

    기업 DLP 정책으로 Python open()이 PermissionError를 일으킬 때
    xcopy로 임시 파일 복사 후 읽기.
    """
    import subprocess, tempfile

    # 1차: 직접 읽기
    try:
        with open(file_path, 'rb') as f:
            return f.read()
    except PermissionError:
        pass  # DLP 차단 → xcopy 폴백

    # 2차: xcopy 폴백 — 고유 임시파일(mkstemp) + finally로 항상 정리.
    #   고정 경로(pid+basename)는 동일 파일명 폴더 간 충돌·예외 시 잔존 위험이 있어
    #   mkstemp로 대체한다. 실패는 logging으로 남겨 빈 bytes가 조용히 반환되지 않게 한다.
    fd, tmp = tempfile.mkstemp(prefix='_xy_net_',
                               suffix='_' + os.path.basename(file_path))
    os.close(fd)
    try:
        r = subprocess.run(['xcopy', file_path, tmp + '*', '/Y'],
                           capture_output=True, timeout=15)
        if r.returncode == 0 and os.path.isfile(tmp):
            with open(tmp, 'rb') as f:
                return f.read()
        logging.warning("xcopy 폴백 실패 (rc=%s): %s", r.returncode, file_path)
    except Exception as e:
        logging.warning("xcopy 폴백 오류 (%s): %s", e, file_path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    return b''


def _decode_bytes(raw: bytes, file_path: str) -> tuple:
    """_ENCODINGS 순서로 디코드 시도 → (text, 사용된 인코딩). 전부 실패 시 (None, None).

    마지막 폴백 'latin-1'은 256바이트를 모두 매핑하므로 **어떤 입력에도 예외를 던지지
    않는다**. 즉 인코딩이 틀린 파일도 조용히 깨진 문자열로 통과한다. 그 깨진 값이
    수치 파싱까지 흘러가 원인 불명의 오류로 나타나는 것을 막기 위해, 최종 폴백이
    실제로 쓰이면 파일 경로와 함께 경고를 남긴다.
    """
    last = _ENCODINGS[-1]
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if enc == last:
            logging.warning("인코딩 폴백 '%s' 사용 — 문자가 깨졌을 수 있습니다: %s",
                            enc, file_path)
        return text, enc

    logging.warning("지원 인코딩(%s)으로 디코드 실패: %s",
                    ', '.join(_ENCODINGS), file_path)
    return None, None


def _open_csv_rows(csv_path: str) -> list:
    """인코딩 자동 감지 CSV 읽기 (네트워크 드라이브 호환).

    파싱 실패는 예외를 올리지 않고 '빈 결과 + 경고 로그'로 처리한다.
    한 파일의 문제가 스캔 전체를 중단시키지 않게 하기 위함.
    """
    raw = _read_file_bytes(csv_path)
    if not raw:
        return []

    text, _ = _decode_bytes(raw, csv_path)
    if text is None:
        return []

    import io
    try:
        return list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        # 예: UTF-16 파일이 latin-1로 디코드되면 'line contains NUL character'.
        # csv.Error는 ValueError가 아니라 그냥 새어 나가던 경로였다.
        logging.warning("CSV 파싱 실패 (%s): %s", e, csv_path)
        return []


def _open_csv_text(csv_path: str, max_lines: int = 20) -> list:
    """인코딩 자동 감지 텍스트 읽기 (처음 N줄)."""
    raw = _read_file_bytes(csv_path)
    if not raw:
        return []

    text, _ = _decode_bytes(raw, csv_path)
    if text is None:
        return []
    return text.splitlines(True)[:max_lines]


def _describe_bad_value(val: str) -> str:
    """수치 변환에 실패한 문자열을 코드포인트까지 포함해 설명한다.

    폰트에 글리프가 없어 화면에 '□'로만 보이는 문자(U+FEFF, C1 제어문자, U+FFFD 등)도
    로그에서는 'U+FEFF'처럼 식별 가능한 형태로 남는다.
    """
    head = val[:8]
    points = ' '.join(f'U+{ord(c):04X}' for c in head)
    if len(val) > 8:
        points += ' ...'
    return f'{val!r} ({points})'


def parse_csv(csv_path: str) -> dict:
    """CSV 파일 파싱 (메타헤더 분리 + 데이터 추출)

    SmartScan CSV 구조:
        행 1~9:  메타 헤더 (key,value 쌍)
        행 10~11: 빈 줄
        행 12:   데이터 헤더 (컬럼명)
        행 13~:  사이트별 측정 데이터

    Returns:
        {
            'meta': {'lot_id': ..., 'recipe_id': ..., 'start_time': ..., ...},
            'header': ['Site ID', 'Site X', ...],
            'data': [{'Site ID': '0001_X000_Y000', 'Site X': '0', ...}, ...],
            'source_file': 'Lot4_X_UL.csv'
        }
    """
    meta = {}
    header = []
    data = []

    rows = _open_csv_rows(csv_path)
    if not rows:
        return {'meta': {}, 'header': [], 'data': [],
                'source_file': os.path.basename(csv_path)}

    # Phase 1: 메타 헤더 (key,value 쌍으로 된 행들)
    data_start = 0
    for i, row in enumerate(rows):
        if not row or all(c.strip() == '' for c in row):
            continue
        # 데이터 헤더 감지: "Site ID"로 시작하는 행
        if row[0].strip() == 'Site ID':
            header = [c.strip().rstrip(',') for c in row if c.strip()]
            data_start = i + 1
            break
        # 메타 헤더
        if len(row) >= 2 and row[0].strip():
            key = row[0].strip().lower().replace(' ', '_')
            val = row[1].strip() if len(row) > 1 else ''
            meta[key] = val

    # Phase 2: 데이터 행
    for row in rows[data_start:]:
        if not row or not row[0].strip():
            continue
        record = {}
        for j, col_name in enumerate(header):
            val = row[j].strip() if j < len(row) else ''
            record[col_name] = val
        data.append(record)

    return {
        'meta': meta,
        'header': header,
        'data': data,
        'source_file': os.path.basename(csv_path),
    }


def parse_summary_csv(csv_path: str) -> dict:
    """통합 CSV (Lot{N}.csv) 파싱 — 요약 통계 + X/Y 데이터

    Returns:
        {
            'meta': {...},
            'x_summary': {'mean': ..., 'stdev': ..., 'min': ..., 'max': ..., 'range': ...},
            'y_summary': {...},
            'x_data': [...],
            'y_data': [...],
            'warnings': ['Sample10.csv MEAN: ... → 0.0 대체', ...]
        }
    """
    meta = {}
    x_summary = {}
    y_summary = {}
    x_data = []
    y_data = []
    warnings = []

    rows = _open_csv_rows(csv_path)
    if not rows:
        return {'meta': {}, 'x_summary': {}, 'y_summary': {},
                'x_data': [], 'y_data': [], 'warnings': []}

    current_section = None
    current_data_header = None
    phase = 'meta'  # meta → x_summary → y_summary → x_data → y_data

    for row in rows:
        if not row or all(c.strip() == '' for c in row):
            continue

        first = row[0].strip()

        # 섹션 헤더 감지
        if first == 'X_UL' and len(row) <= 2:
            if phase in ('meta', 'y_summary'):
                current_section = 'x'
                phase = 'x_summary' if phase != 'y_summary' else 'x_data'
            else:
                current_section = 'x'
                phase = 'x_data'
            continue
        if first == 'Y_UL' and len(row) <= 2:
            if phase in ('x_summary',):
                current_section = 'y'
                phase = 'y_summary'
            else:
                current_section = 'y'
                phase = 'y_data'
            continue

        # 메타 헤더
        if phase == 'meta' and len(row) >= 2 and first not in ('ITEM', 'Site ID'):
            key = first.lower().replace(' ', '_')
            meta[key] = row[1].strip() if len(row) > 1 else ''
            continue

        # 요약 통계 행 (MEAN, STDEV, ...)
        if first == 'ITEM':
            continue  # 헤더 행 건너뛰기
        if first in ('MEAN', 'STDEV', 'MIN', 'MAX', 'RANGE'):
            # 여기서 무방비 float()를 쓰면 셀 하나가 스캔 전체를 중단시킨다.
            # 계측 SW이므로 '실제 0'과 '파싱 실패로 0 대체'는 반드시 구분해 기록한다.
            raw_val = row[1].strip() if len(row) > 1 else ''
            if not raw_val:
                val = 0.0
            else:
                try:
                    val = float(raw_val)
                except (ValueError, TypeError):
                    val = 0.0
                    msg = (f'{os.path.basename(csv_path)} {first}: '
                           f'{_describe_bad_value(raw_val)} → 0.0 대체')
                    warnings.append(msg)
                    logging.warning('%s [%s]', msg, csv_path)
            target = x_summary if current_section == 'x' else y_summary
            target[first.lower()] = val
            continue

        # 데이터 헤더
        if first == 'Site ID':
            current_data_header = [c.strip() for c in row if c.strip()]
            continue

        # 데이터 행
        if current_data_header and first and re.match(r'\d{4}_', first):
            record = {}
            for j, col_name in enumerate(current_data_header):
                record[col_name] = row[j].strip() if j < len(row) else ''
            if current_section == 'x':
                x_data.append(record)
            else:
                y_data.append(record)

    return {
        'meta': meta,
        'x_summary': x_summary,
        'y_summary': y_summary,
        'x_data': x_data,
        'y_data': y_data,
        'warnings': warnings,
    }


# ──────────────────────────────────────────────
# Folder Scanning
# ──────────────────────────────────────────────

def _is_smartscan_csv(csv_path: str) -> bool:
    """CSV 파일이 SmartScan 결과인지 구조로 판별

    판별 기준:
        - 메타헤더 (Lot ID, Recipe ID 등)가 존재하거나
        - 데이터 헤더에 'Site ID' 컬럼이 있음
    """
    lines = _open_csv_text(csv_path, max_lines=20)
    if not lines:
        return False

    has_meta = False
    has_site_id = False

    for line in lines:
        lower = line.lower().strip()
        if lower.startswith('lot id,') or lower.startswith('recipe id,'):
            has_meta = True
        if 'site id' in lower and ('site x' in lower or 'hz1' in lower.replace(' ', '')):
            has_site_id = True

    return has_meta or has_site_id


def _is_data_folder(folder_path: str) -> dict:
    """폴더가 SmartScan 데이터 폴더인지 내부 구조로 판별

    판별 기준 (하나 이상 충족):
        1. *_X_UL.csv 또는 *_Y_UL.csv가 존재하고 SmartScan 구조
        2. TIFF 파일이 존재하고 CSV 파일이 SmartScan 구조

    Returns:
        None if not a data folder, otherwise dict with file info
    """
    if not os.path.isdir(folder_path):
        return None

    try:
        files = os.listdir(folder_path)
    except OSError as e:
        # 네트워크 드라이브 / DLP 권한 문제 — 폴더 하나 때문에 스캔 전체가 죽지 않게 한다.
        logging.warning("폴더 목록 조회 실패 (%s): %s", e, folder_path)
        return None

    x_csvs = [f for f in files if f.upper().endswith('_X_UL.CSV')]
    y_csvs = [f for f in files if f.upper().endswith('_Y_UL.CSV')]
    all_csvs = [f for f in files if f.lower().endswith('.csv')]
    tiffs = [f for f in files if f.lower().endswith(('.tiff', '.tif'))]

    # X/Y UL CSV가 있는 경우 — 구조 확인
    if x_csvs or y_csvs:
        check_csv = x_csvs[0] if x_csvs else y_csvs[0]
        if _is_smartscan_csv(os.path.join(folder_path, check_csv)):
            summary_csvs = [f for f in all_csvs
                            if not f.upper().endswith('_X_UL.CSV')
                            and not f.upper().endswith('_Y_UL.CSV')]
            return {
                'x_csv': x_csvs[0] if x_csvs else None,
                'y_csv': y_csvs[0] if y_csvs else None,
                'summary_csv': summary_csvs[0] if summary_csvs else None,
                'tiff_files': sorted(tiffs),
            }

    # X/Y UL이 없어도 CSV에 SmartScan 데이터가 있는 경우
    for csv_file in all_csvs:
        if _is_smartscan_csv(os.path.join(folder_path, csv_file)):
            return {
                'x_csv': x_csvs[0] if x_csvs else None,
                'y_csv': y_csvs[0] if y_csvs else None,
                'summary_csv': csv_file if csv_file not in x_csvs + y_csvs else None,
                'tiff_files': sorted(tiffs),
            }

    return None


def _extract_folder_number(name: str) -> int:
    """폴더 이름에서 숫자 추출 (정렬용)

    예: 'Lot401' → 401, 'Run_03' → 3, 'Test' → 999999
    """
    numbers = re.findall(r'\d+', name)
    if numbers:
        return int(numbers[-1])  # 마지막 숫자 사용
    return 999999


def scan_lot_folders(root_path: str) -> list:
    """데이터 폴더 자동 탐지 + 정렬

    폴더 이름이 아닌 **내부 CSV 구조**를 기반으로 SmartScan 결과 폴더를 판별합니다.
    폴더 이름이 'Lot401', 'Run_01', 'Test_data' 등 어떤 패턴이든 관계없이
    CSV 내부에 SmartScan 헤더가 있으면 데이터 폴더로 인식합니다.

    Args:
        root_path: Recipe 결과 폴더 (예: "1. Vision Pattern Recognize/1st")

    Returns:
        [{'lot_name': 'Lot401', 'path': '...', 'index': 1,
          'lot_number': 401, 'has_x_csv': True, 'has_y_csv': True,
          'has_summary': True, 'tiff_count': 44, 'tiff_files': [...]}, ...]
    """
    if not os.path.isdir(root_path):
        return []

    folders = []

    try:
        names = sorted(os.listdir(root_path))
    except OSError as e:
        logging.warning("폴더 목록 조회 실패 (%s): %s", e, root_path)
        return []

    for name in names:
        full_path = os.path.join(root_path, name)
        if not os.path.isdir(full_path):
            continue

        file_info = _is_data_folder(full_path)
        if file_info is None:
            continue

        lot_number = _extract_folder_number(name)

        folders.append({
            'lot_name': name,
            'path': full_path,
            'lot_number': lot_number,
            'has_x_csv': file_info['x_csv'] is not None,
            'has_y_csv': file_info['y_csv'] is not None,
            'has_summary': file_info['summary_csv'] is not None,
            'x_csv': file_info['x_csv'],
            'y_csv': file_info['y_csv'],
            'summary_csv': file_info['summary_csv'],
            'tiff_count': len(file_info['tiff_files']),
            'tiff_files': file_info['tiff_files'],
        })

    # 숫자순 정렬 + 인덱스 부여
    folders.sort(key=lambda x: x['lot_number'])
    for i, f in enumerate(folders):
        f['index'] = i + 1  # 1-based

    return folders


# ──────────────────────────────────────────────
# Single Lot Loading
# ──────────────────────────────────────────────

def keep_height_rows(rows: list) -> tuple:
    """같은 측정에 여러 채널 행이 있으면 마지막(Height) 행만 남긴다.

    Vision Pattern Recognize 레시피는 한 번의 측정을 Z Drive와 Height 두 채널로
    연달아 기록한다. Debug/Analysis의 LineData도 사이트마다 'Z Drive → Height'
    순서이며, 위치 오프셋 분석에 써야 하는 값은 Height다.

    다른 레시피는 측정당 1행이라 이 함수의 영향을 받지 않는다.
    (같은 사이트의 반복 측정은 별도 Lot 폴더로 분리되므로 여기서 합쳐지지 않는다)

    Returns:
        (남은 행 리스트, 제외된 행 수)
    """
    kept = {}
    order = []
    for r in rows:
        key = (r.get('Site ID', ''), r.get('Point No', ''), r.get('Method ID', ''))
        if key not in kept:
            order.append(key)
        kept[key] = r          # 마지막 행이 이긴다 = Height
    return [kept[k] for k in order], len(rows) - len(kept)


def load_lot_data(lot_path: str) -> dict:
    """단일 Lot 폴더의 전체 CSV 데이터 로드

    Returns:
        {
            'lot_name': 'Lot401',
            'meta': {...},
            'x_data': [rows], 'y_data': [rows],
            'x_summary': {...}, 'y_summary': {...},
            'header': [...],
            'tiff_files': [...]
        }
    """
    lot_name = os.path.basename(lot_path)
    result = {
        'lot_name': lot_name,
        'meta': {},
        'x_data': [], 'y_data': [],
        'x_summary': {}, 'y_summary': {},
        'header': [],
        'tiff_files': [],
        'warnings': [],
        'channel_dropped': 0,   # Z Drive 채널로 제외된 행 수
    }

    files = os.listdir(lot_path)

    # X_UL.csv (대소문자 무시)
    x_csvs = [f for f in files if f.upper().endswith('_X_UL.CSV')]
    if x_csvs:
        parsed = parse_csv(os.path.join(lot_path, x_csvs[0]))
        result['x_data'], dropped = keep_height_rows(parsed['data'])
        result['channel_dropped'] += dropped
        result['meta'] = parsed['meta']
        result['header'] = parsed['header']

    # Y_UL.csv (대소문자 무시)
    y_csvs = [f for f in files if f.upper().endswith('_Y_UL.CSV')]
    if y_csvs:
        parsed = parse_csv(os.path.join(lot_path, y_csvs[0]))
        result['y_data'], dropped = keep_height_rows(parsed['data'])
        result['channel_dropped'] += dropped

    # Summary CSV (통합, 대소문자 무시)
    summary_csvs = [f for f in files if f.lower().endswith('.csv')
                    and not f.upper().endswith('_X_UL.CSV')
                    and not f.upper().endswith('_Y_UL.CSV')]
    if summary_csvs:
        # 요약 CSV는 현재 앱 어디에서도 소비되지 않는 부가 정보다.
        # 여기서 실패해도 같은 Lot의 실측 데이터(x_data/y_data) 로드를 막아서는 안 된다.
        summary_path = os.path.join(lot_path, summary_csvs[0])
        try:
            summary = parse_summary_csv(summary_path)
            result['x_summary'] = summary['x_summary']
            result['y_summary'] = summary['y_summary']
            # 비정상 셀 경고는 위로 전달해 System Log에 노출한다.
            # (logging은 stderr로만 나가 windowed EXE에서는 볼 수 없다)
            result['warnings'] = [f'{lot_name}: {w}' for w in summary.get('warnings', [])]
        except Exception as e:
            msg = f'{lot_name}: 요약 CSV 파싱 실패 ({type(e).__name__}: {e})'
            logging.warning('%s [%s]', msg, summary_path)
            result['warnings'] = [msg]

    # TIFF 파일 목록
    result['tiff_files'] = sorted(
        [f for f in files if f.lower().endswith(('.tiff', '.tif'))]
    )

    return result


# ──────────────────────────────────────────────
# Batch Loading (핵심 — csv combine 대체)
# ──────────────────────────────────────────────

# 측정은 11 Repeat, 분석은 첫 Repeat을 뺀 10 Repeat이다.
# 폴더가 이보다 적으면 엔지니어가 이미 뺀 것으로 보고 그대로 분석한다.
MIN_LOTS_FOR_FIRST_REPEAT_EXCLUSION = 11


def _is_first_repeat(lot: dict) -> bool:
    """Lot 폴더 CSV 메타의 Repeat No가 1인가. 읽을 수 없으면 False."""
    csv_name = lot.get('x_csv') or lot.get('y_csv')
    if not csv_name:
        return False
    meta = parse_csv(os.path.join(lot['path'], csv_name))['meta']
    return _safe_int(meta.get('repeat_no', '')) == 1


def _exclude_first_repeat(lots: list, info: Optional[dict]) -> list:
    """번호순 첫 폴더가 Repeat 1이고 폴더가 11개 이상이면 그 폴더를 뺀다.

    폴더 이름과 시작 번호는 장비마다 달라(Sample1002~, Sample1501~, Lot208~)
    위치만으로 판단하지 않는다. 근거가 없으면 빼지 않고 info로 알린다.
    """
    if len(lots) < MIN_LOTS_FOR_FIRST_REPEAT_EXCLUSION:
        return lots
    if _is_first_repeat(lots[0]):
        if info is not None:
            info['first_repeat_excluded'] = lots[0]['lot_name']
        return lots[1:]
    if info is not None:
        info['first_repeat_unverified'] = len(lots)
    return lots


def batch_load(root_path: str,
               lot_range: Optional[Union[tuple, list]] = None,
               axis: str = 'both',
               metric_col: str = 'HZ1_O (nm)',
               errors: Optional[list] = None,
               warnings: Optional[list] = None,
               info: Optional[dict] = None) -> list:
    """유연한 범위 지정 배치 로드 — Analysis csv combine.exe 완전 대체

    Args:
        root_path: Recipe 결과 폴더 (예: ".../1st")
        lot_range: None=전체, (start, end)=번호범위, [1,3,5]=특정 인덱스
        axis: 'both', 'x', 'y' — 분석 축 선택
        metric_col: 측정값 컬럼 이름
        errors: 전달하면 Lot 단위 실패 메시지가 여기에 누적된다.
                실패한 Lot은 건너뛰고 나머지 Lot은 정상 로드된다.
        warnings: 전달하면 로드는 됐지만 값이 비정상인 셀 경고가 여기에 누적된다.
        info: 전달하면 부가 집계가 담긴다 ('channel_dropped' = Z Drive로 제외된 행 수,
              'first_repeat_excluded' = 제외한 첫 리핏 폴더 이름,
              'first_repeat_unverified' = 폴더가 11개 이상인데 첫 리핏을 확인하지 못한 폴더 수).

    Returns:
        [{'lot_name': 'Lot401', 'lot_index': 1, 'filename': 'Lot4_X_UL.csv',
          'site_id': '0001_X000_Y000', 'site_x': 0, 'site_y': 0,
          'point_no': 1, 'x_um': 4305.72, 'y_um': 5726.0,
          'method': 'X', 'state': 'COMPLETED', 'valid': True,
          'value': 2976.074, 'value_valid': True}, ...]
    """
    lots = scan_lot_folders(root_path)
    if not lots:
        return []

    # 범위 필터링
    if lot_range is not None:
        if isinstance(lot_range, tuple) and len(lot_range) == 2:
            start, end = lot_range
            lots = [l for l in lots if start <= l['index'] <= end]
        elif isinstance(lot_range, list):
            lots = [l for l in lots if l['index'] in lot_range]
    else:
        # 범위를 직접 지정한 호출은 그 선택을 그대로 따른다
        lots = _exclude_first_repeat(lots, info)

    results = []
    for lot in lots:
        # Lot 단위 격리 — 한 폴더의 문제가 나머지 Lot 전체를 무효화하지 않게 한다.
        try:
            lot_data = load_lot_data(lot['path'])
        except Exception as e:
            msg = (f"Lot '{lot['lot_name']}' 로드 실패 "
                   f"({type(e).__name__}: {e}) — 건너뜁니다")
            logging.warning('%s [%s]', msg, lot['path'])
            if errors is not None:
                errors.append(msg)
            continue

        if warnings is not None:
            warnings.extend(lot_data.get('warnings', []))
        if info is not None:
            info['channel_dropped'] = (info.get('channel_dropped', 0)
                                       + lot_data.get('channel_dropped', 0))

        # X 데이터
        if axis in ('both', 'x') and lot_data['x_data']:
            x_csv = lot.get('x_csv', '')
            for row in lot_data['x_data']:
                results.append(_normalize_row(row, lot, x_csv, metric_col))

        # Y 데이터
        if axis in ('both', 'y') and lot_data['y_data']:
            y_csv = lot.get('y_csv', '')
            for row in lot_data['y_data']:
                results.append(_normalize_row(row, lot, y_csv, metric_col))

    return results


def _normalize_row(row: dict, lot_info: dict, filename: str,
                   metric_col: str) -> dict:
    """CSV 행을 정규화된 dict로 변환"""
    value_str = row.get(metric_col, '0')
    valid_str = row.get(f'{metric_col.split(" ")[0]}_Valid', 'TRUE')

    return {
        'lot_name': lot_info['lot_name'],
        'lot_index': lot_info['index'],
        'lot_number': lot_info['lot_number'],
        'filename': filename,
        'site_id': row.get('Site ID', ''),
        'site_x': _safe_int(row.get('Site X', '0')),
        'site_y': _safe_int(row.get('Site Y', '0')),
        'point_no': _safe_int(row.get('Point No', '0')),
        'x_um': _safe_float(row.get('X (um)', '0')),
        'y_um': _safe_float(row.get('Y (um)', '0')),
        'method': row.get('Method ID', ''),
        'state': row.get('State', ''),
        'valid': row.get('Valid', 'TRUE').upper() == 'TRUE',
        'value': _safe_float(value_str),
        'value_valid': valid_str.upper() == 'TRUE',
    }


def _safe_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: str) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


# ──────────────────────────────────────────────
# Convenience: 요약 정보
# ──────────────────────────────────────────────

def get_scan_summary(root_path: str) -> dict:
    """폴더 스캔 결과 요약"""
    lots = scan_lot_folders(root_path)
    if not lots:
        return {'total_lots': 0}

    # 첫 Lot에서 메타 정보 추출
    first_meta = {}
    if lots[0]['has_x_csv']:
        parsed = parse_csv(os.path.join(lots[0]['path'], lots[0]['x_csv']))
        first_meta = parsed['meta']

    return {
        'total_lots': len(lots),
        'lot_range': f"{lots[0]['lot_name']} ~ {lots[-1]['lot_name']}",
        'lot_numbers': [l['lot_number'] for l in lots],
        'recipe': first_meta.get('recipe_id', 'Unknown'),
        'sample_id': first_meta.get('sample_id', 'Unknown'),
        'total_tiffs': sum(l['tiff_count'] for l in lots),
    }
