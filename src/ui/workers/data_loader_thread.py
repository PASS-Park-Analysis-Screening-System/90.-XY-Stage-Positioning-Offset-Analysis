"""
data_loader_thread.py — 백그라운드 데이터 로딩 스레드
"""

import logging
import threading

from PySide6.QtCore import QThread, Signal
from core.checklist import summarize_runs
from core.csv_loader import ScanCancelled, ScanSession, session_key
from core.positioning_log import attach_analysis
from core.recipe_scanner import load_all_recipes, compare_recipes
from ui.workers.progress_throttle import Throttle


def planned_lots(recipes, round_name='1st'):
    """읽을 회차 폴더별 Lot 수 {경로 키: [폴더 하나의 Lot 수, 읽는 횟수]} (폴더 확인 결과로 미리 안다).

    load_all_recipes와 같은 규칙: 회차가 둘 이상이면 모두, 아니면 round_name(없으면 첫 라운드).
    """
    plan = {}
    for recipe in recipes or []:
        rounds = recipe.get('rounds') or []
        if len(rounds) < 2:
            rounds = [r for r in rounds if r.get('name') == round_name] or rounds[:1]
        for r in rounds:
            entry = plan.setdefault(session_key(r['path']), [int(r.get('lot_count') or 0), 0])
            entry[1] += 1
    return plan


class DataLoaderThread(QThread):
    finished = Signal(object, object, float)
    error = Signal(str, str)  # (다이얼로그용 요약, System Log용 traceback 전문)
    progress = Signal(str)    # 상태 표시줄 문구 (예: "데이터 로드 1/4: Vision Pattern 회차 3/14")
    cancelled = Signal()      # 사용자가 중지했다 (결과 없음)
    count = Signal(int, int)  # (읽은 Lot, 전체 Lot) — 모든 Workflow·회차 합. 전체를 모르면 0

    def __init__(self, folder, recipes=None, parent=None, session=None):
        super().__init__(parent)
        self.folder = folder
        self.recipes = recipes  # 주면 folder를 다시 스캔하지 않고 이 Recipe 목록을 읽는다
        # 폴더 확인 때 찾은 Lot 목록을 다시 읽지 않는다. 중지는 이 로드에만 걸린다
        self.session = (session if session is not None else ScanSession()).fork()
        self.session.lot_progress = self._on_lot_event
        # 회차 요약(summarize_runs)도 여기서 계산한다 (화면 갱신 시간을 줄인다).
        # (spec_limits, spec_deviation) — 화면 스레드가 로드 시작 때 넣는다. 없으면 화면이 계산한다
        self.run_specs = None
        self._plan = planned_lots(recipes)
        self.total_lots = sum(count * times for count, times in self._plan.values())
        self._done = {}         # 경로 키 → 읽은 Lot 수
        self._rounds = {}       # 경로 키 → 다 읽은 횟수
        self._lock = threading.Lock()
        self._throttle = Throttle(0.1)
        self.last_emitted = None    # 마지막으로 보낸 (읽은 Lot, 전체)

    def cancel(self):
        self.session.cancel()

    def _report_progress(self, index, total, text):
        self.progress.emit(f"데이터 로드 중 {index}/{total}: {text}")

    def _on_lot_event(self, event, path):
        """[Lot 작업 스레드들] Lot 하나를 읽었거나 회차 폴더를 다 읽었다. 진행 수는 줄지 않는다."""
        key = session_key(path)
        with self._lock:
            per_round, times = self._plan.get(key, (0, 0))
            cap = per_round * times
            if event == 'round':
                self._rounds[key] = self._rounds.get(key, 0) + 1
                # 첫 리핏 제외·측정 CSV 없는 폴더처럼 읽지 않은 Lot도 이 회차 몫은 끝났다
                floor = min(cap, self._rounds[key] * per_round)
                self._done[key] = max(self._done.get(key, 0), floor)
            else:
                self._done[key] = min(cap, self._done.get(key, 0) + 1)
            done = sum(self._done.values())
            # 회차 끝은 늘 보낸다 (줄인 Lot 알림 사이의 진행이 보이게). Lot은 초당 10번까지
            if event != 'round' and not self._throttle.ready():
                return
            self._emit_count(done)

    def _emit_count(self, done, final=False):
        done = min(done, self.total_lots)
        last = self.last_emitted[0] if self.last_emitted else None
        if last is not None and (done < last or (final and done == last)):
            return      # 늦게 도착한 작은 값·같은 끝 값은 버린다 (진행 막대가 뒤로 가지 않게)
        self.last_emitted = (done, self.total_lots)
        self.count.emit(done, self.total_lots)

    def run(self):
        import time
        import traceback
        t0 = time.perf_counter()
        try:
            results = load_all_recipes(self.folder, round_name='1st', axis='both',
                                       recipes=self.recipes,
                                       progress_cb=self._report_progress,
                                       session=self.session)
            with self._lock:
                self._emit_count(self.total_lots, final=True)
            # 비전 보정 로그 대조 (Positioning Log 탭·AI 분석용 리포트). 화면에 보일 회차만 읽는다.
            # 다른 회차는 그 회차를 볼 때 화면이 작업 스레드로 읽는다 (네트워크 왕복을 줄인다)
            self.progress.emit("비전 보정 로그 읽는 중…")
            for result in results:
                attach_analysis(result, self.session)
            if self.run_specs is not None and any(r.get('run_results') for r in results):
                self.progress.emit("회차 요약 계산 중…")
                spec_limits, spec_deviation = self.run_specs
                for result in results:
                    if not result.get('run_results'):
                        continue
                    try:
                        result['runs'] = summarize_runs(result, spec_limits, spec_deviation)
                    except Exception:
                        # 로드는 버리지 않는다. 'runs'가 없으면 화면 스레드가 다시 계산한다
                        logging.exception("회차 요약 계산 실패 (화면에서 다시 계산): %s",
                                          result.get('short_name', ''))
                        result.pop('runs', None)
            comparison = compare_recipes(results)
            elapsed = time.perf_counter() - t0
            self.finished.emit(results, comparison, elapsed)
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as e:
            # str(e)만 보내면 예외 타입도 파일 경로도 남지 않아 현장 추적이 불가능하다.
            # 요약은 다이얼로그에, traceback 전문은 System Log에 남긴다.
            self.error.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
