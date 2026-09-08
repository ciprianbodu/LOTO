"""Teste pentru loto_enterprise/benchmark/disabled.py — verificare globala.

`add_disabled()` (merge-only, IREVERSIBIL — §4.3) nu avea `file_lock` la
read-modify-write, risc de "lost update" intre doi scriitori concurenti, si
inghitea silentios esecul de scriere, intorcand setul in-memory ca si cum
ar fi fost scris pe disc (apelantii raportau succes fals)."""
from __future__ import annotations

import json
import threading

import pytest

from loto_enterprise.benchmark import disabled as dis


def test_add_disabled_is_merge_only(tmp_path, monkeypatch):
    path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(dis, "_PATH", path)

    dis.add_disabled(["m1"], reason="prima")
    final = dis.add_disabled(["m2"], reason="a doua")

    assert final == {"m1", "m2"}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data["disabled"]) == {"m1", "m2"}


def test_add_disabled_raises_instead_of_swallowing_write_failure(tmp_path, monkeypatch):
    """Inainte: esecul de scriere era prins si logat, iar functia intorcea
    tacut setul in-memory -- apelantul (prune_methods.py) afisa "APLICAT"
    desi fisierul n-a fost atins. Acum: excepția se propaga."""
    path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(dis, "_PATH", path)

    def _boom(_path, _payload):
        raise OSError("disc plin (simulat)")

    monkeypatch.setattr(dis, "_atomic_write_json", _boom)

    with pytest.raises(OSError, match="disc plin"):
        dis.add_disabled(["m1"])
    assert not path.exists()


def test_add_disabled_never_loses_a_tombstone_under_concurrent_writers(tmp_path, monkeypatch):
    """Mai multi "scriitori" concurenti (thread-uri, simuland UI + worker),
    fiecare face read-modify-write pe acelasi fisier -- fara lock in jurul
    intregii secvente citire+scriere, un interleaving nefavorabil poate
    suprascrie tombstone-ul altui thread (lost update). Cu `_file_lock`,
    scrierile se serializeaza, deci toate numele trebuie sa supravietuiasca
    indiferent de ordinea de scheduling."""
    path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(dis, "_PATH", path)

    names = [f"method_{i}" for i in range(8)]
    threads = [threading.Thread(target=dis.add_disabled, args=([n],)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    final = dis.load_disabled()
    assert final == set(names), f"tombstone(e) pierdut(e): {set(names) - final}"
