"""Regresie pentru limitele de încărcare și formatul istoricelor CSV."""

import asyncio
from dataclasses import dataclass

import pytest

from loto_enterprise.core.lotteries import GAMES, LOTTERIES, PROJECT_ROOT
from ui_history_upload import (
    MAX_BATCH_BYTES,
    MAX_COLUMNS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_ROWS,
    LimitHistoryUploadBody,
    read_history_csv,
    validate_upload_batch,
)


@dataclass
class UploadedFile:
    name: str = "istoric.CSV"
    content_type: str = "application/vnd.ms-excel"
    length: int = MAX_FILE_BYTES

    def size(self) -> int:
        return self.length


def test_accepts_exact_batch_limit():
    validate_upload_batch([UploadedFile()] * (MAX_BATCH_BYTES // MAX_FILE_BYTES))


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ([UploadedFile(name="istoric.txt")], "extensia"),
        ([UploadedFile(content_type="image/png")], "tip de fișier"),
        ([UploadedFile(length=MAX_FILE_BYTES + 1)], "1 MiB"),
        ([UploadedFile()] * (MAX_BATCH_BYTES // MAX_FILE_BYTES + 1), "4 MiB"),
        ([UploadedFile(length=1)] * (MAX_FILES + 1), "13 fișiere"),
    ],
)
def test_rejects_unsupported_batch(files, error):
    with pytest.raises(ValueError, match=error):
        validate_upload_batch(files)


_HEADER = b"n1,n2,n3,n4,n5,n6\n"
_DRAW = b"1,2,3,4,5,6\n"


@pytest.mark.parametrize("game", GAMES, ids=lambda game: game.game_id)
def test_accepts_registry_history(game):
    path = PROJECT_ROOT / game.csv
    assert not read_history_csv(path.read_bytes(), game).empty


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (_HEADER, "nu conține extrageri"),
        (_HEADER + _DRAW * (MAX_ROWS + 1), "50.000"),
        (_HEADER + b"1," * MAX_COLUMNS + b"1\n", "coloane"),
        (_HEADER + b"1,2,3,4,5,6,7\n", "decât antetul"),
        (_HEADER + b"9,9,1,2,3,4\n", "rânduri invalide"),
        (_HEADER + b"1,2,3,4,5,49\n", "rânduri invalide"),
    ],
)
def test_rejects_invalid_history(content, error):
    # La 5/40 se extrag șase numere, chiar dacă varianta are cinci.
    with pytest.raises(ValueError, match=error):
        read_history_csv(content, LOTTERIES["5/40"])


def test_accepts_six_drawn_numbers_for_540():
    history = read_history_csv(_HEADER + _DRAW, LOTTERIES["5/40"])
    assert history.iloc[0].tolist() == list(range(1, 7))


@pytest.mark.parametrize("root_path", ["", "/loto"])
@pytest.mark.parametrize(
    ("declared_size", "chunks", "called", "status"),
    [
        (4, [b"12", b"34"], [b"1234"], 200),
        (8 * MAX_FILE_BYTES, [], [], 413),
        (None, [b"x" * (4 * MAX_FILE_BYTES), b"x" * MAX_FILE_BYTES], [], 413),
    ],
)
def test_bounds_upload_body_before_multipart(root_path, declared_size, chunks, called, status):
    forwarded = []
    responses = []
    messages = iter(
        {"type": "http.request", "body": data, "more_body": i < len(chunks) - 1}
        for i, data in enumerate(chunks)
    )

    async def receive():
        return next(messages)

    async def send(message):
        responses.append(message)

    async def endpoint(scope, receive, send):
        forwarded.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200})

    headers = []
    if declared_size is not None:
        headers = [(b"content-length", str(declared_size).encode())]
    scope = {
        "type": "http",
        "method": "POST",
        "root_path": root_path,
        "path": f"{root_path}/_nicegui/client/example/upload/1",
        "headers": headers,
    }
    asyncio.run(LimitHistoryUploadBody(endpoint)(scope, receive, send))
    assert forwarded == called
    assert responses[0]["status"] == status
