"""One background queue reader shared by every browser tab."""

import logging
import threading
import time


class JobStatusProbe:
    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._last_start = 0.0
        self._job = None
        self._snapshot = None
        self._error = False

    def request(self, job, read, *, interval=1.0):
        now = time.monotonic()
        with self._lock:
            if self._busy or (job == self._job and now - self._last_start < interval):
                return False
            if job != self._job:
                self._snapshot, self._error = None, False
            self._job, self._last_start, self._busy = job, now, True

        def collect():
            try:
                snapshot = read(job)
                with self._lock:
                    self._snapshot, self._error = snapshot, False
            except Exception as exc:
                logging.getLogger(__name__).warning("Starea jobului #%s: %s", job, exc)
                with self._lock:
                    self._error = True
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=collect, name="job-status", daemon=True).start()
        return True

    def view(self, job):
        with self._lock:
            if self._job != job:
                return None, False
            slow = self._busy and time.monotonic() - self._last_start > 1.5
            return self._snapshot, self._error or slow
