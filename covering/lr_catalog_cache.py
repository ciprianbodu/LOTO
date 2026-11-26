"""Track files imported from the LR brochure at entry to a budget calculation."""

from functools import lru_cache, wraps
from hashlib import sha256
from pathlib import Path
from threading import Lock


_lock = Lock()
_revision = None
_certificates = None


def _load_certificates(root, *, refresh=False):
    global _certificates
    path = root / "expanded_certificates.json"
    try:
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        stamp = None
    except OSError:
        _certificates = None
        return sha256(b"unreadable").hexdigest(), b"unreadable"
    if refresh or _certificates is None or _certificates[:2] != (root, stamp):
        try:
            data = path.read_bytes() if stamp is not None else b""
        except OSError:
            _certificates = None
            return sha256(b"unreadable").hexdigest(), b"unreadable"
        _certificates = root, stamp, sha256(data).hexdigest(), data
    return _certificates[2:]


def certificate_snapshot(directory):
    """Read the bytes once per revision, including an in-place replacement."""
    with _lock:
        return _load_certificates(Path(directory))


def refresh_catalog(directory):
    from covering import lr_portfolio
    from loto_enterprise.core.lotteries import games_for_country

    global _revision
    with _lock:
        root = Path(directory)
        signature = sha256()
        for path in sorted(root.iterdir()) if root.is_dir() else ():
            if (path.name.startswith("LR_") and path.suffix == ".txt") or path.name in (
                "index_oficial.json", "app_same_size.json", "optimal_offline.json",
            ):
                signature.update(path.name.encode())
                signature.update(sha256(path.read_bytes()).digest())
        certificate_hash, _ = _load_certificates(root, refresh=True)
        signature.update(bytes.fromhex(certificate_hash))
        tariffs = [(lot.game_id, lot.price, lot.per_ticket, lot.draw_n, lot.pick_n,
                    lot.max_n, lot.geo.second.max_n if lot.geo.second else 0)
                   for lot in games_for_country("RO")]
        signature.update(repr((tariffs, lr_portfolio.FEE_BANI)).encode("ascii"))
        _revision = root, signature.digest()
        return signature.hexdigest()


class _SkipMemoization(Exception):
    """lru_cache records normal returns only."""


def catalog_cache(maxsize, *, semantic_key=None, cache_if=None):
    """A result already being calculated stays keyed to the revision that started it."""
    def decorate(fn):
        @lru_cache(maxsize=maxsize)
        def versioned(revision, rules, *args, **kwargs):
            value = fn(*args, **kwargs)
            if cache_if is not None and not cache_if(value):
                raise _SkipMemoization(value)
            return value

        @wraps(fn)
        def cached(*args, **kwargs):
            try:
                return versioned(_revision, semantic_key() if semantic_key else None, *args, **kwargs)
            except _SkipMemoization as skipped:
                return skipped.args[0]

        cached.cache_info = versioned.cache_info
        cached.cache_clear = versioned.cache_clear
        return cached
    return decorate
