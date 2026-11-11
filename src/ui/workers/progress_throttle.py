"""
progress_throttle.py — 작업 스레드의 진행 알림을 초당 10번 정도로 줄인다

Lot 하나마다 시그널을 보내면 네트워크가 빠를 때(캐시) 화면 스레드가 진행 표시만 그리느라
바빠진다. 마지막 값(끝)은 호출하는 쪽이 따로 보낸다.
"""

import threading
import time


class Throttle:
    def __init__(self, interval: float = 0.1, clock=time.monotonic):
        self.interval = interval
        self._clock = clock
        self._last = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        """지난번 True 뒤로 interval이 지났으면 True (처음은 언제나 True). 여러 스레드에서 불러도 된다."""
        with self._lock:
            now = self._clock()
            if self._last is None or now - self._last >= self.interval:
                self._last = now
                return True
            return False
