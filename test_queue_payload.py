"""Teste pack/decode rezultat job (pickle+zstd+b64 + fallback legacy)."""
from __future__ import annotations

import base64
import json
import pickle

from ui_shared import (
    ENCODING_PICKLE_B64,
    ENCODING_PICKLE_ZSTD_B64,
    decode_queue_result,
    pack_queue_result,
)


def test_pack_decode_zstd_roundtrip():
    payload = ({"games": {"loto_6_49": {"pool": [1, 2, 3]}}}, {"cpu": 42.0})
    raw = pack_queue_result(payload)
    assert decode_queue_result(raw) == payload
    data = json.loads(raw)
    assert data["encoding"] == ENCODING_PICKLE_ZSTD_B64


def test_decode_legacy_pickle_b64():
    obj = {"ok": True, "n": 7}
    blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    legacy = json.dumps({
        "encoding": ENCODING_PICKLE_B64,
        "payload": base64.b64encode(blob).decode("ascii"),
    })
    assert decode_queue_result(legacy) == obj


def test_decode_empty_payload():
    assert decode_queue_result("{}") is None
    assert decode_queue_result('{"encoding":"pickle+b64","payload":""}') is None
