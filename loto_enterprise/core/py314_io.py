"""I/O helpers folosind stdlib Python 3.14 (PEP 784: compression.zstd).

Cache-urile pickle pe disc sunt comprimate cu Zstandard la scriere; la citire
acceptăm și fișiere legacy necomprimate (pickle brut).
"""

from __future__ import annotations

import os
import pickle
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
    """Scriere atomică tmp + os.replace (walk-forward cache)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pickle_dump_bytes(obj))
    os.replace(tmp, path)
