"""
troubleshoot_launcher.py — 3D 트러블슈팅 보고서·사례 라이브러리를 기본 브라우저로 열기

보고서는 Diagnosis 탭이 만든 진단(window._xy_diagnosis, pass.xy.diagnosis/1)으로 만든다.
HTML 생성·저장은 core.troubleshoot_report가 하고, 이 모듈은 확인 메시지와 브라우저 열기만 한다.
"""

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from core import troubleshoot_report

REPORT_TITLE = '3D 트러블슈팅 보고서'
LIBRARY_TITLE = '사례 라이브러리'
NO_DIAGNOSIS_MESSAGE = '먼저 Diagnosis 탭에서 진단을 실행하세요.'


def _log(window, text):
    logger = getattr(window, 'logger', None)
    if logger is not None and hasattr(logger, 'info'):
        logger.info(text)


def _open_in_browser(window, title, path) -> bool:
    if QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path))):
        return True
    QMessageBox.warning(window, title, f'브라우저로 열지 못했습니다. 파일을 직접 여세요:\n{path}')
    return False


def open_troubleshoot_report(window):
    """진단으로 보고서를 만들어 기본 브라우저로 연다. 만든 HTML 경로 (못 만들면 None)."""
    diag = getattr(window, '_xy_diagnosis', None)
    if diag is not None and hasattr(diag, 'to_dict'):
        diag = diag.to_dict()
    if not diag:
        QMessageBox.information(window, REPORT_TITLE, NO_DIAGNOSIS_MESSAGE)
        return None
    try:
        path = troubleshoot_report.write_report(diag)
    except Exception as e:     # 디스크·권한·틀 누락: 앱은 계속 쓸 수 있어야 한다
        QMessageBox.warning(window, REPORT_TITLE, f'보고서를 만들지 못했습니다: {e}')
        return None
    _log(window, f'3D 트러블슈팅 보고서 저장: {path}')
    _open_in_browser(window, REPORT_TITLE, path)
    return path


def open_case_library(window):
    """번들된 사례 라이브러리 목록을 기본 브라우저로 연다. 연 index.html 경로 (없으면 None)."""
    try:
        path = troubleshoot_report.browsable_case_library()
    except OSError as e:
        QMessageBox.warning(window, LIBRARY_TITLE, f'사례 라이브러리를 준비하지 못했습니다: {e}')
        return None
    if not os.path.isfile(path):
        QMessageBox.warning(window, LIBRARY_TITLE, f'사례 라이브러리를 찾을 수 없습니다:\n{path}')
        return None
    _open_in_browser(window, LIBRARY_TITLE, path)
    return path
