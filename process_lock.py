"""Process locks for files shared by the UI, queue worker and maintenance commands."""

import errno
import os
import time
from pathlib import Path


class file_lock:
    """Own a kernel lock until exit or process death. The inode stays in place."""

    def __init__(self, target, timeout=10.0):
        self.lockpath = str(target) + ".lock"
        self.timeout = max(0.0, float(timeout))
        self._fd = None

    def __enter__(self):
        Path(self.lockpath).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lockpath, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return self
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Resursa este ocupată: {self.lockpath}") from exc
                    time.sleep(min(0.05, remaining))
        except BaseException:
            os.close(fd)
            raise

    def __exit__(self, *exc):
        os.close(self._fd)
        self._fd = None
