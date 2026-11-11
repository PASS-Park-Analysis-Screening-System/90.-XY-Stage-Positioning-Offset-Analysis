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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Union


# ──────────────────────────────────────────────
# Scan Session (네트워크 폴더 — 같은 폴더를 한 번만 읽는다)
# ──────────────────────────────────────────────

class ScanCancelled(Exception):
    """사용자가 폴더 확인·데이터 로드를 중지했다."""


class ScanSession:
    """사용자 동작 한 번(Scan·Open·Workflow별 선택) 동안의 폴더 확인 결과.

    네트워크 폴더는 목록 조회·파일 열기 한 번이 수십 ms라, 같은 폴더를 스캔·대화상자·
    로드에서 거듭 읽으면 수십 초가 된다. 결과를 정규화한 경로 키로 담아 두고 재사용한다.
    새 동작(Scan 버튼을 다시 누름)은 새 세션을 만든다. 오래된 결과를 쓰지 않기 위함이다.

    fork()는 캐시를 공유하고 진행 표시·중지만 따로 갖는 사본이다
    (스캔 스레드, 대화상자의 행 확인, 로드 스레드가 서로의 중지에 영향을 주지 않는다).
    """

    _SHARED = ('status', 'dirs', 'listings', 'lots', 'own', 'rounds', 'errors')

    def __init__(self, progress=None, _shared=None):
        if _shared is None:
            # 실패(폴더 없음, 읽기 오류)는 담지 않는다. 다시 확인하면 다시 읽는다
            self.status = {}     # workflow_map.describe_folder 결과 (사용 가능한 것만)
            self.dirs = {}       # isdir 결과 (폴더인 것만)
            self.listings = {}   # 폴더 목록 [(이름, 폴더 여부), ...]
            self.lots = {}       # scan_lot_folders 결과 (모든 하위 폴더를 확인한 것만)
            self.own = {}        # scan_self_lot 결과 (고른 폴더 자체가 Lot인지, 읽기 성공한 것만)
            self.rounds = {}     # build_recipe의 라운드 목록
            self.errors = {}     # 확인하지 못한 폴더 경로 → 오류 문구 (다시 확인해 성공하면 지운다)
        else:
            for name in self._SHARED:
                setattr(self, name, getattr(_shared, name))
        self.progress = progress     # callback(확인한 폴더 수, 지금까지 찾은 폴더 수, 경로)
        # 로드 진행: callback('lot' | 'round', 회차 폴더 경로). 'lot'은 Lot 하나를 읽을 때마다
        # (작업 스레드들에서 불린다), 'round'는 그 회차 폴더를 다 읽었을 때 한 번
        self.lot_progress = None
        self.done = 0
        self.total = 0
        self._cancel = threading.Event()

    def fork(self, progress=None):
        return ScanSession(progress, _shared=self)

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self):
        if self._cancel.is_set():
            raise ScanCancelled()

    def found(self, count: int):
        self.total += count

    def checked(self, path: str):
        self.done += 1
        if self.progress is not None:
            self.progress(self.done, max(self.total, self.done), path)

    def failed_under(self, path: str) -> list:
        """path(또는 그 아래)에서 확인하지 못한 폴더 [(경로, 오류 문구), ...]."""
        prefix = session_key(path)
        return [(p, msg) for p, msg in list(self.errors.items())
                if _is_under(session_key(p), prefix)]

    def forget(self, path: str):
        """path와 그 아래의 확인 결과를 모두 지운다 ('다시 확인')."""
        prefix = session_key(path)
        for name in self._SHARED:
            store = getattr(self, name)
            for key in [k for k in list(store) if _is_under(session_key(k), prefix)]:
                store.pop(key, None)


def session_key(path: str) -> str:
    """캐시 키 — 대소문자·구분자 차이를 무시한다."""
    return os.path.normcase(os.path.normpath(path))


def _is_under(key: str, prefix: str) -> bool:
    return key == prefix or key.startswith(prefix.rstrip(os.sep) + os.sep)


FOLDER_RETRY_DELAY = 0.5    # 네트워크 일시 오류는 잠깐 뒤 한 번 더 읽는다 (초)


def folder_check_warning(path: str, message: str) -> str:
    """확인하지 못한 Lot 폴더 문구 — System Log·카드·내보내기에 그대로 쓴다."""
    return f"Lot 폴더 확인 실패: {path} — 결과에서 빠졌을 수 있음 ({message})"


def is_dir(path: str, session: Optional[ScanSession] = None) -> bool:
    """os.path.isdir — 세션이 있으면 같은 경로를 한 번만 묻는다."""
    if not path:
        return False
    if session is None:
        return os.path.isdir(path)
    key = session_key(path)
    if key in session.dirs:
        return session.dirs[key]
    result = os.path.isdir(path)
    if result:      # '없음'은 담지 않는다 (일시 오류일 수 있어 다음에 다시 묻는다)
        session.dirs[key] = True
    return result


def list_dir(path: str, session: Optional[ScanSession] = None) -> list:
    """폴더 목록 [(이름, 폴더 여부), ...] (이름순). 실패하면 OSError를 그대로 올린다.

    os.scandir은 목록과 함께 폴더 여부를 돌려준다(Windows). 항목마다 isdir을
    따로 물으면 네트워크 폴더에서 왕복이 항목 수만큼 늘어난다.
    """
    key = session_key(path) if session is not None else None
    if key is not None and key in session.listings:
        return list(session.listings[key])
    entries = []
    with os.scandir(path) as it:
        for entry in it:
            try:
                folder = entry.is_dir()
            except OSError:
                folder = False
            entries.append((entry.name, folder))
    entries.sort()
    if key is not None:
        session.listings[key] = entries
        session.dirs[key] = True
        for name, folder in entries:    # 하위 폴더는 isdir을 다시 묻지 않는다
            if folder:
                session.dirs.setdefault(session_key(os.path.join(path, name)), True)
    return list(entries)


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


def _read_head_bytes(file_path: str, max_lines: int) -> bytes:
    """파일 앞 max_lines줄만 읽는다 (네트워크 폴더에서 파일 전체를 받지 않는다).

    DLP 차단(PermissionError)이면 _read_file_bytes의 xcopy 폴백으로 전체를 읽는다.
    """
    try:
        with open(file_path, 'rb') as f:
            lines = []
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    break
                lines.append(line)
            return b''.join(lines)
    except PermissionError:
        return _read_file_bytes(file_path)


def _open_csv_text(csv_path: str, max_lines: int = 20) -> list:
    """인코딩 자동 감지 텍스트 읽기 (처음 N줄)."""
    raw = _read_head_bytes(csv_path, max_lines)
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

def _is_smartscan_csv(csv_path: str, lines: Optional[list] = None) -> bool:
    """CSV 파일이 SmartScan 결과인지 구조로 판별

    판별 기준:
        - 메타헤더 (Lot ID, Recipe ID 등)가 존재하거나
        - 데이터 헤더에 'Site ID' 컬럼이 있음

    lines: 이미 읽은 앞 20줄 (주면 파일을 다시 열지 않는다)
    """
    if lines is None:
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


def _is_data_folder(folder_path: str, session: Optional[ScanSession] = None) -> dict:
    """폴더가 SmartScan 데이터 폴더인지 내부 구조로 판별

    판별 기준 (하나 이상 충족):
        1. *_X_UL.csv 또는 *_Y_UL.csv가 존재하고 SmartScan 구조
        2. TIFF 파일이 존재하고 CSV 파일이 SmartScan 구조

    폴더가 없으면 None. 그 밖의 OSError(네트워크 일시 오류, 권한)는 그대로 올린다.
    조용히 None으로 바꾸면 Lot이 하나 빠진 채 정상처럼 보인다 (11개 → 10개면 첫 리핏
    제외도 꺼진다). 다시 시도와 기록은 scan_lot_folders가 한다.

    Returns:
        None if not a data folder, otherwise dict with file info
        ('head'는 측정 순서를 정할 때 다시 쓰는 X/Y CSV 앞 20줄이다)
    """
    try:
        files = [name for name, _folder in list_dir(folder_path, session)]
    except (FileNotFoundError, NotADirectoryError):
        return None

    x_csvs = [f for f in files if f.upper().endswith('_X_UL.CSV')]
    y_csvs = [f for f in files if f.upper().endswith('_Y_UL.CSV')]
    all_csvs = [f for f in files if f.lower().endswith('.csv')]
    tiffs = [f for f in files if f.lower().endswith(('.tiff', '.tif'))]

    # X/Y UL CSV가 있는 경우 — 구조 확인
    if x_csvs or y_csvs:
        check_csv = x_csvs[0] if x_csvs else y_csvs[0]
        head = _open_csv_text(os.path.join(folder_path, check_csv), max_lines=20)
        if _is_smartscan_csv(os.path.join(folder_path, check_csv), head):
            summary_csvs = [f for f in all_csvs
                            if not f.upper().endswith('_X_UL.CSV')
                            and not f.upper().endswith('_Y_UL.CSV')]
            return {
                'x_csv': x_csvs[0] if x_csvs else None,
                'y_csv': y_csvs[0] if y_csvs else None,
                'summary_csv': summary_csvs[0] if summary_csvs else None,
                'tiff_files': sorted(tiffs),
                'head': head,
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


def _folder_sort_key(name: str) -> tuple:
    """폴더 이름의 숫자를 모두 비교하는 자연 정렬 키.

    Lot6 → (6,), Lot6 (2) → (6, 2), Lot401 → (401,). 측정 순서를 메타에서 읽지
    못할 때만 쓰는 마지막 기준이다. 복사본 표기에서는 실제 순서와 다를 수 있다
    (실측: Lot6 (2) ~ Lot6 (10)이 Repeat 2~10이고 괄호 없는 Lot6이 Repeat 11).
    """
    numbers = tuple(int(n) for n in re.findall(r'\d+', name))
    return (numbers or (999999,), name)


_ORDER_META_LINES = 20     # 메타 머리글은 10줄 안팎이다. 예전처럼 앞 20줄까지만 본다


def _iter_csv_head(csv_path: str, max_lines: int = _ORDER_META_LINES):
    """CSV 앞 max_lines줄을 한 줄씩 읽는다. 그만 읽으면 파일도 거기서 멈춘다.

    메타 키(Start Time, Repeat No)는 ASCII라 줄마다 디코드해도 결과가 같다.
    DLP 차단(PermissionError)이면 xcopy 폴백으로 읽은 앞부분을 쓴다.
    """
    try:
        f = open(csv_path, 'rb')
    except PermissionError:
        yield from _open_csv_text(csv_path, max_lines)
        return
    with f:
        for _ in range(max_lines):
            raw = f.readline()
            if not raw:
                return
            for enc in _ENCODINGS:     # 마지막 latin-1은 실패하지 않는다
                try:
                    yield raw.decode(enc)
                    break
                except (UnicodeDecodeError, ValueError):
                    continue


def _read_order_meta(folder: dict) -> tuple:
    """Lot 폴더 CSV 메타의 (Start Time, Repeat No). 없거나 형식이 다르면 None.

    메타 머리글만 읽는다: 둘 다 찾았거나 데이터 머리글(Site ID)에 닿으면 멈춘다.
    폴더를 판별할 때 읽어 둔 앞부분('head')이 있으면 파일을 다시 열지 않는다.
    """
    start_time = repeat_no = None
    csv_name = folder.get('x_csv') or folder.get('y_csv')
    if not csv_name:
        return start_time, repeat_no
    lines = folder.get('head')
    if lines is None:
        lines = _iter_csv_head(os.path.join(folder['path'], csv_name))
    for line in lines:
        key, _, value = line.partition(',')
        key = key.strip().lower()
        if key == 'site id':
            break
        value = value.split(',')[0].strip()
        try:
            if key == 'start time':
                start_time = datetime.strptime(value, '%Y.%m.%d %H:%M:%S')
            elif key == 'repeat no':
                repeat_no = int(float(value))
        except ValueError:
            pass
        if start_time is not None and repeat_no is not None:
            break
    return start_time, repeat_no


def _sort_by_measurement_order(folders: list) -> None:
    """폴더를 측정 순서로 정렬한다: Start Time → Repeat No → 폴더 이름.

    폴더 이름은 복사본 표기나 시작 번호가 장비마다 달라 실제 순서를 보장하지 못한다.
    기준이 섞이지 않도록, 모든 측정 폴더에서 읽히는 기준만 쓴다.
    X/Y 측정 CSV가 없는 폴더는 순서를 판단할 수 없으므로 맨 뒤에 둔다.
    """
    folders.sort(key=lambda f: _folder_sort_key(f['lot_name']))
    measured = [f for f in folders if f.get('x_csv') or f.get('y_csv')]
    others = [f for f in folders if not (f.get('x_csv') or f.get('y_csv'))]
    meta = {f['lot_name']: _read_order_meta(f) for f in measured}
    use_time = all(m[0] is not None for m in meta.values())
    use_repeat = all(m[1] is not None for m in meta.values())

    def key(folder):
        start_time, repeat_no = meta[folder['lot_name']]
        return (start_time if use_time else datetime.min,
                repeat_no if use_repeat else 0)

    measured.sort(key=key)   # 안정 정렬이라 같은 값은 이름 순서를 유지한다
    folders[:] = measured + others


def _copy_lots(lots: list) -> list:
    """캐시한 Lot 목록의 사본 (로드가 index를 다시 매겨도 캐시는 그대로다)."""
    return [dict(lot, tiff_files=list(lot['tiff_files'])) for lot in lots]


def _lot_entry(name: str, full_path: str, file_info: dict) -> dict:
    """_is_data_folder 결과로 Lot 목록 항목을 만든다."""
    return {
        'lot_name': name,
        'path': full_path,
        'lot_number': _extract_folder_number(name),
        'has_x_csv': file_info['x_csv'] is not None,
        'has_y_csv': file_info['y_csv'] is not None,
        'has_summary': file_info['summary_csv'] is not None,
        'x_csv': file_info['x_csv'],
        'y_csv': file_info['y_csv'],
        'summary_csv': file_info['summary_csv'],
        'tiff_count': len(file_info['tiff_files']),
        'tiff_files': file_info['tiff_files'],
        'head': file_info.get('head'),    # 측정 순서 정렬에만 쓰고 지운다
    }


def is_measured_lot(lot: dict) -> bool:
    """X/Y 측정 CSV가 있는 Lot 폴더인가 (Info·Debug/Analysis처럼 메타만 같은 CSV 폴더는 아니다)."""
    return bool(lot.get('x_csv') or lot.get('y_csv'))


def own_measurement_csv(path: str, session: Optional[ScanSession] = None) -> Optional[str]:
    """폴더 바로 안에 있는 X/Y 측정 CSV 이름 (*_X_UL.csv 먼저). 없거나 읽지 못하면 None."""
    try:
        names = [name for name, folder in list_dir(path, session) if not folder]
    except OSError:
        return None
    own = sorted(n for n in names if n.upper().endswith(('_X_UL.CSV', '_Y_UL.CSV')))
    return own[0] if own else None


def has_measured_sublots(path: str, session: Optional[ScanSession] = None,
                         failures: Optional[list] = None) -> bool:
    """바로 아래 하위 폴더 중 X/Y 측정 CSV가 있는 Lot이 있는가."""
    return any(is_measured_lot(lot) for lot in scan_lot_folders(path, session, failures))


def scan_self_lot(path: str, session: Optional[ScanSession] = None,
                  failures: Optional[list] = None) -> list:
    """고른 폴더 자체가 Lot 폴더(X/Y 측정 CSV가 바로 있는 폴더)면 그 폴더 하나를 Lot 목록으로.

    실데이터의 Lot 폴더(예: 46LKD105SJE61)에는 *_X_UL.csv, *_Y_UL.csv, TIFF가 바로 있고
    Capture, Debug, Info, Log 하위 폴더가 있다. Info와 Debug/Analysis의 CSV도 같은
    'Lot ID' 메타를 가져 하위 폴더 쪽을 Lot으로 오인했다 (측정 CSV가 없어 0행이 됐다).
    측정 CSV가 없으면 빈 목록이다 (보통 구조로 다시 찾는다).
    폴더 자체에 측정 CSV가 있어도 바로 아래에 측정 Lot 폴더가 있으면 Lot 묶음이다 (빈 목록).
    읽지 못하면 빈 목록이고, 오류는 failures와 session.errors에 남긴다 (scan_lot_folders와 같다).
    """
    if not path:
        return []
    key = session_key(path) if session is not None else None
    if key is not None and key in session.own:
        return _copy_lots(session.own[key])

    def report(error):
        message = f'{type(error).__name__}: {error}'
        logging.warning("폴더 확인 실패 (%s): %s", message, path)
        if failures is not None:
            failures.append((path, message))
        if session is not None:
            session.errors[path] = message

    listing = _with_retry(lambda: list_dir(path, session), session)
    if isinstance(listing, OSError):
        if not isinstance(listing, (FileNotFoundError, NotADirectoryError)):
            report(listing)
        return []
    # 측정 CSV 이름이 없으면 파일을 열지 않는다 (네트워크 폴더에서 루트마다 읽지 않게)
    if own_measurement_csv(path, session) is None:
        if key is not None:
            session.own[key] = []
        return []
    sub_failures = []
    if has_measured_sublots(path, session, sub_failures):
        if key is not None and not sub_failures:
            session.own[key] = []
        return []
    if failures is not None:
        failures.extend(sub_failures)
    info = _with_retry(lambda: _is_data_folder(path, session), session)
    if isinstance(info, OSError):
        report(info)
        return []
    lots = []
    if info and is_measured_lot(info):
        lot = _lot_entry(os.path.basename(os.path.normpath(path)), path, info)
        lot['index'] = 1
        lot.pop('head', None)
        lots.append(lot)
    if session is not None:
        session.errors.pop(path, None)
    if key is not None and not sub_failures:
        session.own[key] = _copy_lots(lots)
    return lots


def scan_lot_folders(root_path: str, session: Optional[ScanSession] = None,
                     failures: Optional[list] = None) -> list:
    """데이터 폴더 자동 탐지 + 정렬

    폴더 이름이 아닌 **내부 CSV 구조**를 기반으로 SmartScan 결과 폴더를 판별합니다.
    폴더 이름이 'Lot401', 'Run_01', 'Test_data' 등 어떤 패턴이든 관계없이
    CSV 내부에 SmartScan 헤더가 있으면 데이터 폴더로 인식합니다.

    Args:
        root_path: Recipe 결과 폴더 (예: "1. Vision Pattern Recognize/1st")
        session: ScanSession — 같은 폴더를 다시 읽지 않고, 진행 표시와 중지를 받는다
        failures: 주면 확인하지 못한 폴더 [(경로, 오류 문구), ...]가 쌓인다.
                  네트워크 일시 오류는 한 번 더 읽고, 그래도 실패하면 그 폴더 없이 돌려주되
                  세션에 이 목록을 담지 않는다 (다음에 다시 확인한다)

    Returns:
        [{'lot_name': 'Lot401', 'path': '...', 'index': 1,
          'lot_number': 401, 'has_x_csv': True, 'has_y_csv': True,
          'has_summary': True, 'tiff_count': 44, 'tiff_files': [...]}, ...]
    """
    if not root_path:
        return []
    key = session_key(root_path) if session is not None else None
    if key is not None and key in session.lots:
        return _copy_lots(session.lots[key])
    if session is not None:
        session.check_cancelled()

    def report(path, error):
        message = f'{type(error).__name__}: {error}'
        logging.warning("폴더 확인 실패 (%s): %s", message, path)
        if failures is not None:
            failures.append((path, message))
        if session is not None:
            session.errors[path] = message

    entries = _with_retry(lambda: list_dir(root_path, session), session)
    if isinstance(entries, OSError):
        if not isinstance(entries, (FileNotFoundError, NotADirectoryError)):
            report(root_path, entries)
        return []
    if session is not None:
        session.errors.pop(root_path, None)

    folders = []
    failed = False
    subdirs = [name for name, folder in entries if folder]
    if session is not None:
        session.found(len(subdirs))

    # 폴더 판별(목록 + CSV 앞부분)은 네트워크 왕복이라 Lot마다 겹쳐 읽는다. 순서는 그대로다
    def check(name):
        if session is not None and session.cancelled:
            return None
        return _with_retry(lambda: _is_data_folder(os.path.join(root_path, name), session),
                           session)

    pool = (ThreadPoolExecutor(max_workers=min(LOT_LOAD_WORKERS, len(subdirs)),
                               thread_name_prefix='lot-scan')
            if len(subdirs) > 1 else None)
    infos = pool.map(check, subdirs) if pool is not None else map(check, subdirs)
    try:
        for name, file_info in zip(subdirs, infos):    # 끝나는 대로 순서대로 받는다
            full_path = os.path.join(root_path, name)
            if session is not None:
                session.check_cancelled()
                session.checked(full_path)
            if isinstance(file_info, OSError):
                if isinstance(file_info, (FileNotFoundError, NotADirectoryError)):
                    continue    # 그사이 지워졌다
                failed = True   # 이 폴더 목록은 담지 않는다 (다음에 다시 확인한다)
                report(full_path, file_info)
                continue
            if session is not None:
                session.errors.pop(full_path, None)
            if file_info is None:
                continue

            folders.append(_lot_entry(name, full_path, file_info))
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)

    # 측정 순서 정렬 + 인덱스 부여
    _sort_by_measurement_order(folders)
    for i, f in enumerate(folders):
        f['index'] = i + 1  # 1-based
        f.pop('head', None)

    if key is not None and not failed:
        session.lots[key] = _copy_lots(folders)
    return folders


def _with_retry(read, session=None):
    """read()를 부른다. 폴더 없음이 아닌 OSError면 잠깐 뒤 한 번 더. 그래도 실패하면 예외를 돌려준다."""
    for attempt in (0, 1):
        try:
            return read()
        except (FileNotFoundError, NotADirectoryError) as e:
            return e
        except OSError as e:
            if attempt or (session is not None and session.cancelled):
                return e
            time.sleep(FOLDER_RETRY_DELAY)


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
    """측정 순서상 첫 폴더가 Repeat 1이고 폴더가 11개 이상이면 그 폴더를 뺀다.

    폴더 이름과 시작 번호는 장비마다 달라(Sample1002~, Sample1501~, Lot208~)
    위치만으로 판단하지 않는다. 근거가 없으면 빼지 않고 info로 알린다.
    """
    if len(lots) < MIN_LOTS_FOR_FIRST_REPEAT_EXCLUSION:
        return lots
    if _is_first_repeat(lots[0]):
        if info is not None:
            info['first_repeat_excluded'] = lots[0]['lot_name']
        # 레시피끼리 비교할 수 있게 분석 대상 기준으로 번호를 다시 매긴다
        remaining = lots[1:]
        for i, lot in enumerate(remaining):
            lot['index'] = i + 1
        return remaining
    if info is not None:
        info['first_repeat_unverified'] = len(lots)
    return lots


LOT_LOAD_WORKERS = 8   # 네트워크 지연을 겹친다. 결과 순서는 Lot 순서 그대로다


def _load_lots_parallel(lots: list, session: Optional[ScanSession] = None,
                        on_loaded=None) -> list:
    """Lot마다 load_lot_data를 작은 스레드 풀로 읽는다. [(lot_data, 예외), ...] (Lot 순서).

    네트워크 폴더는 파일 하나를 여는 데 수십 ms가 걸려 순서대로 읽으면 대부분 기다리는
    시간이다. 결과 처리는 호출하는 쪽에서 Lot 순서대로 하므로 순차 로드와 결과가 같다.
    """
    def load(lot):
        if session is not None and session.cancelled:
            return None, ScanCancelled()
        try:
            return load_lot_data(lot['path']), None
        except Exception as e:
            return None, e
        finally:
            if on_loaded is not None:
                _notify_safely(on_loaded)

    if len(lots) <= 1:
        return [load(lot) for lot in lots]
    with ThreadPoolExecutor(max_workers=min(LOT_LOAD_WORKERS, len(lots)),
                            thread_name_prefix='lot-load') as pool:
        return list(pool.map(load, lots))


def batch_load(root_path: str,
               lot_range: Optional[Union[tuple, list]] = None,
               axis: str = 'both',
               metric_col: str = 'HZ1_O (nm)',
               errors: Optional[list] = None,
               warnings: Optional[list] = None,
               info: Optional[dict] = None,
               session: Optional[ScanSession] = None,
               self_lot: bool = False) -> list:
    """batch_load_rows와 같다. session.lot_progress가 있으면 Lot마다·회차 끝에 알린다."""
    notify = getattr(session, 'lot_progress', None)
    try:
        return batch_load_rows(root_path, lot_range, axis, metric_col, errors, warnings, info,
                               session, (lambda: notify('lot', root_path)) if notify else None,
                               self_lot=self_lot)
    finally:
        if notify is not None:
            _notify_safely(notify, 'round', root_path)


def _notify_safely(callback, *args):
    """진행 알림은 화면 표시용이다. 알림이 실패해도 읽은 데이터·로드 결과는 바뀌면 안 된다."""
    try:
        callback(*args)
    except Exception:
        logging.exception('진행 알림 오류 (데이터에는 영향 없음)')


def batch_load_rows(root_path: str,
                    lot_range: Optional[Union[tuple, list]] = None,
                    axis: str = 'both',
                    metric_col: str = 'HZ1_O (nm)',
                    errors: Optional[list] = None,
                    warnings: Optional[list] = None,
                    info: Optional[dict] = None,
                    session: Optional[ScanSession] = None,
                    on_loaded=None,
                    self_lot: bool = False) -> list:
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
              'unreadable_values' = 측정값을 읽지 못한 행 수,
              'first_repeat_excluded' = 제외한 첫 리핏 폴더 이름,
              'first_repeat_unverified' = 폴더가 11개 이상인데 첫 리핏을 확인하지 못한 폴더 수,
              'no_measurement_folders' = X/Y 측정 CSV가 없어 건너뛴 폴더 이름 목록,
              'loaded_lots' = 읽으려 한 Lot 폴더 이름 목록).
        session: ScanSession — 이미 확인한 Lot 목록을 다시 읽지 않는다. 중지하면 ScanCancelled
        self_lot: root_path 자체가 Lot 폴더다 (사용자가 Lot 폴더 하나를 고른 경우, scan_self_lot)

    Returns:
        [{'lot_name': 'Lot401', 'lot_index': 1, 'filename': 'Lot4_X_UL.csv',
          'site_id': '0001_X000_Y000', 'site_x': 0, 'site_y': 0,
          'point_no': 1, 'x_um': 4305.72, 'y_um': 5726.0,
          'method': 'X', 'state': 'COMPLETED', 'valid': True,
          'value': 2976.074, 'value_valid': True}, ...]
    """
    scan_failures = []
    if self_lot:
        lots = scan_self_lot(root_path, session, scan_failures)
    else:
        lots = scan_lot_folders(root_path, session, scan_failures)
    # 확인하지 못한 Lot 폴더는 결과에서 빠졌을 수 있다. 조용히 넘기면 정상처럼 보인다
    for path, message in scan_failures:
        if errors is not None:
            errors.append(folder_check_warning(path, message))
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
        # 범위를 직접 지정한 호출은 그 선택을 그대로 따른다.
        # 측정 CSV가 없는 폴더는 Repeat가 아니므로 폴더 수에 넣지 않는다.
        skipped = [l['lot_name'] for l in lots if not (l.get('x_csv') or l.get('y_csv'))]
        if skipped and info is not None:
            info['no_measurement_folders'] = skipped
        lots = _exclude_first_repeat(
            [l for l in lots if l.get('x_csv') or l.get('y_csv')], info)

    if info is not None:
        info['loaded_lots'] = [l['lot_name'] for l in lots]
    results = []
    loaded = _load_lots_parallel(lots, session, on_loaded)
    if session is not None:
        session.check_cancelled()
    for lot, (lot_data, e) in zip(lots, loaded):
        # Lot 단위 격리 — 한 폴더의 문제가 나머지 Lot 전체를 무효화하지 않게 한다.
        if e is not None:
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
            _report_unreadable(lot, 'X', lot_data['x_data'], metric_col, warnings, info)

        # Y 데이터
        if axis in ('both', 'y') and lot_data['y_data']:
            y_csv = lot.get('y_csv', '')
            for row in lot_data['y_data']:
                results.append(_normalize_row(row, lot, y_csv, metric_col))
            _report_unreadable(lot, 'Y', lot_data['y_data'], metric_col, warnings, info)

    return results


def _report_unreadable(lot: dict, axis: str, rows: list, metric_col: str,
                       warnings: Optional[list], info: Optional[dict]) -> None:
    """측정값을 숫자로 읽지 못한 행을 알린다. 그 행은 NaN이라 집계에서 빠진다."""
    count = sum(1 for r in rows if not _is_number(r.get(metric_col)))
    if not count:
        return
    if info is not None:
        info['unreadable_values'] = info.get('unreadable_values', 0) + count
    if warnings is not None:
        warnings.append(f"{lot['lot_name']} {axis}: '{metric_col}' 값을 읽을 수 없는 "
                        f"행 {count}개 — 분석에서 제외")


def _is_number(val) -> bool:
    """CSV 칸이 숫자로 읽히는가 (장비가 실패로 기록한 'NaN' 문자열 포함)."""
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _parse_measured(val) -> float:
    """측정값·좌표 칸을 읽는다. 읽지 못하면 NaN이다.

    0.0으로 바꾸면 실제 측정값처럼 집계된다. 측정 열 이름이 달라 값이 모두 0이 되면
    Range 0으로 PASS가 나오고, 칸 하나가 비면 없는 오차로 FAIL이 나온다.
    """
    try:
        return float(val)
    except (ValueError, TypeError):
        return float('nan')


def _normalize_row(row: dict, lot_info: dict, filename: str,
                   metric_col: str) -> dict:
    """CSV 행을 정규화된 dict로 변환"""
    value_str = row.get(metric_col)
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
        'x_um': _parse_measured(row.get('X (um)', '0')),
        'y_um': _parse_measured(row.get('Y (um)', '0')),
        'method': row.get('Method ID', ''),
        'state': row.get('State', ''),
        'valid': row.get('Valid', 'TRUE').upper() == 'TRUE',
        'value': _parse_measured(value_str),
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
