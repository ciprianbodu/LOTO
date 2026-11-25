"""Serialize local CSV writers while they recheck and replace a history file."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile


@contextmanager
def history_write_lock():
    # All writers under this user share a short SQLite reservation. SQLite
    # releases it even if a launcher is terminated during the write.
    lock_path = Path(tempfile.gettempdir()) / "loto_history_writes.sqlite3"
    try:
        with closing(sqlite3.connect(lock_path, timeout=30)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            yield
    except sqlite3.Error as exc:
        raise OSError(f"Blocarea istoricului a eșuat: {exc}") from exc
