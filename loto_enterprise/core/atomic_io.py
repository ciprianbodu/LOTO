"""Scriere atomică pe disc — previne coruperea fișierelor de stare la crash /
sleep / sync OneDrive în mijlocul unei scrieri.

Pattern: scriem într-un `<file>.tmp` în ACELAȘI director, flush + os.fsync,
(opțional) copiem fișierul curent într-un `.bak`, apoi `os.replace(tmp, file)`
— `os.replace` e atomic pe POSIX și Windows, deci cititorii văd fie versiunea
veche completă, fie cea nouă completă, niciodată una trunchiată.

Înlocuiește `Path.write_text(json.dumps(...))` / `json.dump(open(w))` care
trunchiau fișierul in-place.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_text(path, text: str, encoding: str = "utf-8", keep_backup: bool = True) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    # Backup NEdistructiv (copiere, nu mutare) — originalul rămâne intact până la
    # `os.replace` de mai jos, deci nu există fereastră în care fișierul lipsește.
    if keep_backup and path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError as exc:
            logger.debug("[atomic_io] backup eșuat pentru %s: %s", path, exc)
    os.replace(tmp, path)  # atomic


def atomic_write_json(path, obj: Any, *, indent: int = 2, ensure_ascii: bool = False,
                      encoding: str = "utf-8", keep_backup: bool = True) -> None:
    atomic_write_text(
        path,
        json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii),
        encoding=encoding,
        keep_backup=keep_backup,
    )
