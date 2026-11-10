"""
folder_scan_thread.py — 폴더 확인(스캔 대상 판별, Workflow 대화상자 초기값)을 화면 스레드 밖에서 한다

네트워크 폴더는 목록 조회·파일 열기 한 번이 수십 ms라, 화면 스레드에서 확인하면
창이 '응답 없음'이 된다. 확인할 일(job)은 호출하는 쪽이 정하고, 이 스레드는 진행 문구와
중지를 맡는다. 중지하면 ScanCancelled로 멈추고 cancelled를 보낸다 (화면은 바꾸지 않는다).
"""

import os
import time
import traceback

from PySide6.QtCore import QThread, Signal

from core.csv_loader import ScanCancelled


def progress_text(done: int, total: int, path: str) -> str:
    """상태 표시줄 문구 — '폴더 확인 중 37/140 — Vision Pattern/7' (확인 중인 Lot의 상위 두 단계)."""
    parent = os.path.dirname(os.path.normpath(path))
    tail = '/'.join(p for p in (os.path.basename(os.path.dirname(parent)),
                                os.path.basename(parent)) if p)
    return f"폴더 확인 중 {done}/{total} — {tail}"


class FolderScanThread(QThread):
    done = Signal(object, float)    # (job 결과, 걸린 초)
    failed = Signal(str, str)       # (대화상자용 요약, System Log용 traceback)
    cancelled = Signal()
    progress = Signal(str)          # 상태 표시줄 문구

    def __init__(self, job, session, parent=None):
        super().__init__(parent)
        self._job = job
        # 캐시는 함께 쓰고 진행 표시·중지는 이 스레드 것만 갖는다
        self.session = session.fork(progress=self._report)

    def _report(self, done, total, path):
        self.progress.emit(progress_text(done, total, path))

    def cancel(self):
        self.session.cancel()

    def run(self):
        t0 = time.perf_counter()
        try:
            result = self._job(self.session)
        except ScanCancelled:
            self.cancelled.emit()
            return
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
            return
        self.done.emit(result, time.perf_counter() - t0)
