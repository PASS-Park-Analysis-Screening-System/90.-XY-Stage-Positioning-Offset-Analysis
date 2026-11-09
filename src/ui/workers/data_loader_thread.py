"""
data_loader_thread.py — 백그라운드 데이터 로딩 스레드
"""

from PySide6.QtCore import QThread, Signal
from core.recipe_scanner import load_all_recipes, compare_recipes


class DataLoaderThread(QThread):
    finished = Signal(object, object, float)
    error = Signal(str, str)  # (다이얼로그용 요약, System Log용 traceback 전문)

    def __init__(self, folder, recipes=None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.recipes = recipes  # 주면 folder를 다시 스캔하지 않고 이 Recipe 목록을 읽는다

    def run(self):
        import time
        import traceback
        t0 = time.perf_counter()
        try:
            results = load_all_recipes(self.folder, round_name='1st', axis='both',
                                       recipes=self.recipes)
            comparison = compare_recipes(results)
            elapsed = time.perf_counter() - t0
            self.finished.emit(results, comparison, elapsed)
        except Exception as e:
            # str(e)만 보내면 예외 타입도 파일 경로도 남지 않아 현장 추적이 불가능하다.
            # 요약은 다이얼로그에, traceback 전문은 System Log에 남긴다.
            self.error.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
