"""Teste pentru py314_io.py — pickle_store_path() scria neatomic; acum
delega la pickle_store_path_atomic (tmp unic + fsync + os.replace)."""

from __future__ import annotations

from loto_enterprise.core.py314_io import (
    pickle_load_path,
    pickle_store_path,
    pickle_store_path_atomic,
)


def test_pickle_store_path_roundtrips(tmp_path):
    p = tmp_path / "sub" / "fold.pkl"
    pickle_store_path(p, {"hits": [1, 2, 3]})
    assert pickle_load_path(p) == {"hits": [1, 2, 3]}


def test_pickle_store_path_leaves_no_tmp_file_behind(tmp_path):
    p = tmp_path / "fold.pkl"
    pickle_store_path(p, {"a": 1})
    leftovers = [f for f in tmp_path.iterdir() if f.name != "fold.pkl"]
    assert leftovers == []


def test_pickle_store_path_is_now_the_atomic_implementation(tmp_path, monkeypatch):
    """pickle_store_path trebuie sa delege la varianta atomica -- nu doar sa
    produca acelasi rezultat, ci sa foloseasca EFECTIV tmp+fsync+os.replace."""
    calls = []
    import loto_enterprise.core.py314_io as io_mod

    def _spy(path, obj):
        calls.append((path, obj))

    monkeypatch.setattr(io_mod, "pickle_store_path_atomic", _spy)
    io_mod.pickle_store_path(tmp_path / "x.pkl", {"v": 1})
    assert calls == [(tmp_path / "x.pkl", {"v": 1})]


def test_pickle_store_path_atomic_cleans_up_tmp_on_failure(tmp_path, monkeypatch):
    import loto_enterprise.core.py314_io as io_mod

    def _boom(obj):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(io_mod, "pickle_dump_bytes", _boom)
    p = tmp_path / "fold.pkl"
    try:
        pickle_store_path_atomic(p, {"a": 1})
    except RuntimeError:
        pass
    assert not p.exists()
    assert list(tmp_path.iterdir()) == []
