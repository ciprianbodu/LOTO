"""Snapshot local și verificat al ultimului istoric Re-Bench publicat.

Doar JSON tipizat, niciun pickle sau calcul WF. SQLite serializează publicarea
între interfețe, iar pornirea deschide exclusiv read-only baza existentă.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from runtime_paths import RUNTIME_ROOT

STORE_FILE = RUNTIME_ROOT / "rebench_history.sqlite3"
_MAX_BYTES = 128 * 1024 * 1024
_VERSION = 1


def _pack(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("număr ne-finit în istoric")
        return value
    if isinstance(value, Path):
        return {"$path": str(value)}
    if isinstance(value, dict):
        if any(type(k) not in (str, int) for k in value):
            raise TypeError("cheie nesuportată în istoric")
        return {"$dict": [[_pack(k), _pack(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [_pack(v) for v in value]
    if isinstance(value, tuple):
        return {"$tuple": [_pack(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"$set": [_pack(v) for v in sorted(value)]}
    # Scalarii și vectorii NumPy au reprezentări Python fără pierdere.
    if type(value).__module__.startswith("numpy") and hasattr(value, "tolist"):
        return _pack(value.tolist())
    raise TypeError(f"tip nesuportat în istoric: {type(value).__name__}")


def _unpack(value, depth=0):
    if depth > 80:
        raise ValueError("istoric imbricat excesiv")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_unpack(v, depth + 1) for v in value]
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("tip JSON invalid în istoric")
    tag, data = next(iter(value.items()))
    if tag == "$path" and isinstance(data, str):
        return Path(data)
    if tag in ("$tuple", "$set") and isinstance(data, list):
        values = [_unpack(v, depth + 1) for v in data]
        return tuple(values) if tag == "$tuple" else set(values)
    if tag == "$dict" and isinstance(data, list):
        result = {}
        for pair in data:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("pereche invalidă în istoric")
            key, item = (_unpack(v, depth + 1) for v in pair)
            if type(key) not in (str, int) or key in result:
                raise ValueError("cheie invalidă în istoric")
            result[key] = item
        return result
    raise ValueError("tip JSON necunoscut în istoric")


def _connect_write():
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(STORE_FILE), timeout=10)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS latest (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL, started_ns INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)")
    except Exception:
        db.close()
        raise
    return db


def mark_launch(run_id: str, started_ns: int | None = None) -> None:
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("identitate Re-Bench invalidă")
    now = time.time_ns() if started_ns is None else int(started_ns)
    db = _connect_write()
    try:
        db.execute("INSERT INTO latest VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id, started_ns=excluded.started_ns WHERE excluded.started_ns >= latest.started_ns", (run_id, now))
        db.commit()
    finally:
        db.close()


def _records_to_wire(records):
    from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
    row_fields = {f.name for f in fields(WalkForwardResult)}
    wire = []
    for record in records:
        item = dict(record)
        rows = item.pop("flat", [])
        item["flat"] = [{name: getattr(row, name, None) for name in row_fields} for row in rows]
        wire.append(item)
    return wire


def save(run_id: str, records: list[dict], status: str, *, complete: bool, provenance: dict) -> bool:
    """Publică numai pași existenți și numai pentru ultima rulare lansată."""
    if not any(r.get("flat") for r in records):
        return False
    payload = {"version": _VERSION, "run_id": run_id, "status": status,
               "complete": bool(complete), "provenance": provenance,
               "records": _records_to_wire(records)}
    text = json.dumps(_pack(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # Nu înlocuim vechiul snapshot cu unul care nu poate fi citit ulterior.
    _validate_payload(_unpack(json.loads(text)), run_id)
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("istoricul depășește limita de stocare locală")
    digest = hashlib.sha256(encoded).hexdigest()
    db = _connect_write()
    try:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT run_id FROM latest WHERE id=1").fetchone()
        if not latest or latest[0] != run_id:
            db.rollback()
            return False
        db.execute("INSERT INTO history VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id, payload=excluded.payload, digest=excluded.digest", (run_id, text, digest))
        db.commit()
        return True
    finally:
        db.close()


def load():
    """Întoarce snapshotul validat și marcajul «precedent», fără scrieri."""
    if not STORE_FILE.is_file():
        return None
    uri = STORE_FILE.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        row = db.execute("SELECT run_id, payload, digest FROM history WHERE id=1").fetchone()
        latest = db.execute("SELECT run_id FROM latest WHERE id=1").fetchone()
    finally:
        db.close()
    if not row:
        return None
    if not isinstance(row[1], str) or len(row[1].encode("utf-8")) > _MAX_BYTES:
        raise ValueError("conținut sau dimensiune invalidă în istoricul local")
    if hashlib.sha256(row[1].encode("utf-8")).hexdigest() != row[2]:
        raise ValueError("integritate istoric invalidă")
    payload = _validate_payload(_unpack(json.loads(row[1])), row[0])
    return payload, bool(latest and latest[0] != row[0])


def _validate_payload(payload, run_id):
    if not isinstance(payload, dict) or payload.get("version") != _VERSION or payload.get("run_id") != run_id:
        raise ValueError("versiune sau identitate istoric invalidă")
    if not isinstance(payload.get("records"), list) or not isinstance(payload.get("status"), str) or type(payload.get("complete")) is not bool or not isinstance(payload.get("provenance"), dict):
        raise ValueError("structură istoric invalidă")
    from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
    from loto_enterprise.core import lotteries
    known = {f.name for f in fields(WalkForwardResult)}
    for record in payload["records"]:
        if not isinstance(record, dict) or type(record.get("simulated")) is not bool or not isinstance(record.get("name"), str):
            raise ValueError("joc invalid în istoric")
        spec = lotteries.lottery_by_id(str(record.get("game_id") or ""))
        if spec is None or spec.geometry != record.get("game") or type(record.get("pool")) is not int or not 1 <= record["pool"] <= spec.max_n:
            raise ValueError("identitate sau pool invalid în istoric")
        if type(record.get("guarantee")) is not int or not 3 <= record["guarantee"] <= spec.pick_n or record.get("depth") not in (30, 100):
            raise ValueError("setări invalide în istoric")
        if "error" in record:
            if not isinstance(record["error"], str):
                raise ValueError("eroare invalidă în istoric")
            continue
        if not isinstance(record.get("meta"), dict) or not isinstance(record.get("flat"), list):
            raise ValueError("pași invalizi în istoric")
        restored = []
        for data in record["flat"]:
            if not isinstance(data, dict) or set(data) != known or type(data.get("draw_index")) is not int or data["draw_index"] < 0:
                raise ValueError("extragere invalidă în istoric")
            if not isinstance(data.get("variant"), list) or any(type(n) is not int for n in data["variant"]):
                raise ValueError("variantă invalidă în istoric")
            if any(type(data.get(k)) is not int or not 0 <= data[k] <= 6 for k in ("hits", "hits_union")):
                raise ValueError("hituri invalide în istoric")
            if type(data.get("virtual_full_system")) is not bool or data["virtual_full_system"] == record["simulated"]:
                raise ValueError("mod fizic/virtual inconsistent")
            if data.get("variant_prize_groups") is not None and not isinstance(data["variant_prize_groups"], dict):
                raise ValueError("grupe de premii invalide")
            if data.get("ticket_context") is not None and not isinstance(data["ticket_context"], dict):
                raise ValueError("context de extragere invalid")
            restored.append(WalkForwardResult(**data))
        record["flat"] = restored
    if not any(record.get("flat") for record in payload["records"]):
        raise ValueError("istoric fără pași")
    return payload
