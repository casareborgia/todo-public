"""백그라운드 스캔 루프 및 기한 도래 알림 스케줄러."""

import threading
import time
from typing import Callable, List, Optional

from core.database import DatabaseManager
from core.models import TaskItem


class AppScheduler:
    """주기적인 폴더 자동 스캔과 마감 기한 도래 알림 체크를 수행하는 스케줄러."""

    def __init__(
        self,
        db: DatabaseManager,
        scan_callback: Callable[[], int],
        reminder_callback: Callable[[List[TaskItem]], None],
        scan_interval_sec: int = 15,
        reminder_interval_sec: int = 30,
        fast_callback: Optional[Callable[[], int]] = None,
        fast_interval_sec: int = 3,
    ):
        self.db = db
        self.scan_callback = scan_callback
        self.reminder_callback = reminder_callback
        self.scan_interval_sec = max(5, scan_interval_sec)
        self.reminder_interval_sec = max(10, reminder_interval_sec)
        # 쪽지 창처럼 금방 닫힐 수 있는 대상은 폴더보다 자주 확인
        self.fast_callback = fast_callback
        self.fast_interval_sec = max(1, fast_interval_sec)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        last_scan = 0.0
        last_reminder = 0.0
        last_fast = 0.0

        while not self._stop_event.is_set():
            now = time.time()

            if now - last_scan >= self.scan_interval_sec:
                last_scan = now
                try:
                    self.scan_callback()
                except Exception:
                    pass

            if self.fast_callback and now - last_fast >= self.fast_interval_sec:
                last_fast = now
                try:
                    self.fast_callback()
                except Exception:
                    pass

            if now - last_reminder >= self.reminder_interval_sec:
                last_reminder = now
                try:
                    due_tasks = self.db.get_due_tasks_for_reminder()
                    if due_tasks:
                        self.reminder_callback(due_tasks)
                except Exception:
                    pass

            self._stop_event.wait(1.0)
