"""Limitele și validarea istoricelor încărcate din interfață."""

import csv
import io
from pathlib import Path

import pandas as pd
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.lotteries import Lottery

MAX_FILE_BYTES = 1024 * 1024
MAX_BATCH_BYTES = 4 * MAX_FILE_BYTES
MAX_FILES = 13
MAX_ROWS = 50_000
MAX_COLUMNS = 32
UPLOAD_LIMITS = "CSV · 1 MiB/fișier · 4 MiB/lot · max. 13 fișiere"
_CSV_CONTENT_TYPES = {
    "",
    "application/csv",
    "application/octet-stream",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
    "text/x-csv",
}
# Rezervă pentru delimitatori multipart, nume de fișiere și antete.
_MAX_BODY_BYTES = MAX_BATCH_BYTES + 64 * 1024


class LimitHistoryUploadBody:
    """Limitează POST-ul NiceGUI înainte de parsarea multipart."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        request_path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and request_path.startswith(root_path + "/"):
            request_path = request_path[len(root_path):]
        path = request_path.split("/")
        if len(path) != 6 or path[1:3] != ["_nicegui", "client"] or path[4] != "upload":
            await self.app(scope, receive, send)
            return

        too_large = PlainTextResponse(UPLOAD_LIMITS, status_code=413)
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    size = int(value)
                except ValueError:
                    continue
                if size > _MAX_BODY_BYTES:
                    await too_large(scope, receive, send)
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > _MAX_BODY_BYTES:
                await too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        buffered: bytes | None = bytes(body)
        del body

        async def receive_body():
            nonlocal buffered
            if buffered is None:
                return await receive()
            message = {"type": "http.request", "body": buffered, "more_body": False}
            buffered = None
            return message

        await self.app(scope, receive_body, send)


def validate_upload_batch(files) -> None:
    if len(files) > MAX_FILES:
        raise ValueError(f"Sunt permise cel mult {MAX_FILES} fișiere per lot.")

    total = 0
    for file in files:
        if Path(file.name).suffix.lower() != ".csv":
            raise ValueError(f"{file.name}: extensia trebuie să fie .csv.")
        content_type = file.content_type.partition(";")[0].strip().lower()
        if content_type not in _CSV_CONTENT_TYPES:
            raise ValueError(f"{file.name}: tip de fișier incompatibil cu CSV.")
        size = file.size()
        if size > MAX_FILE_BYTES:
            raise ValueError(f"{file.name}: fișierul depășește 1 MiB.")
        total += size

    if total > MAX_BATCH_BYTES:
        raise ValueError("Lotul depășește 4 MiB.")


def read_history_csv(content: bytes, game: Lottery) -> pd.DataFrame:
    with io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        for row in reader:
            if len(row) > MAX_COLUMNS:
                raise ValueError(f"Sunt permise cel mult {MAX_COLUMNS} coloane.")
        stream.seek(0)
        frame = pd.read_csv(stream, nrows=MAX_ROWS + 1)

    if len(frame) > MAX_ROWS:
        raise ValueError(f"Sunt permise cel mult {MAX_ROWS:,} de extrageri.".replace(",", "."))
    if frame.empty:
        raise ValueError("Fișierul nu conține extrageri.")
    if not isinstance(frame.index, pd.RangeIndex):
        raise ValueError("Există rânduri cu mai multe câmpuri decât antetul CSV.")

    columns = [f"n{i}" for i in range(1, game.draw_n + 1)]
    _, valid = valid_draw_matrix(frame, columns, draw_n=game.draw_n, max_num=game.max_n)
    if not valid.all():
        raise ValueError(f"{int((~valid).sum())} rânduri invalide pentru {game.display}.")
    return frame
