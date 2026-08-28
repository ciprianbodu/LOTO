"""I/O helpers folosind stdlib Python 3.14 (PEP 784: compression.zstd).

Cache-urile pickle pe disc sunt comprimate cu Zstandard la scriere; la citire
acceptăm și fișiere legacy necomprimate (pickle brut).
"""

from __future__ import annotations

import os
import pickle
import uuid
from pathlib import Path
from typing import Any

from compression import zstd

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_ZSTD_LEVEL = 3


def pickle_load_bytes(data: bytes) -> Any:
    if data.startswith(_ZSTD_MAGIC):
        data = zstd.decompress(data)
    return pickle.loads(data)


def pickle_dump_bytes(obj: Any) -> bytes:
    raw = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    return zstd.compress(raw, _ZSTD_LEVEL)


def pickle_load_path(path: Path) -> Any:
    return pickle_load_bytes(path.read_bytes())


def pickle_store_path(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle_dump_bytes(obj))


def pickle_store_path_atomic(path: Path, obj: Any) -> None:
    """Scriere atomică tmp UNIC (+fsync) + os.replace (walk-forward cache).

    Numele tmp e unic per scriere (pid+uuid): cu ".tmp" fix, doi scriitori
    concurenți pe aceeași cheie își truncau reciproc tmp-ul (WF rulează ~25
    procese). fsync înainte de replace: altfel un crash putea promova un
    pickle parțial sub numele final."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(pickle_dump_bytes(obj))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
