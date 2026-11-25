"""Track files imported from the LR brochure at entry to a budget calculation."""

from functools import lru_cache, wraps
from hashlib import sha256
from pathlib import Path
from threading import Lock


_lock = Lock()
_revision = None


def refresh_catalog(directory):
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
        _revision = root, signature.digest()
        return signature.hexdigest()


def catalog_cache(maxsize, *, semantic_key=None):
    """A result already being calculated stays keyed to the revision that started it."""
    def decorate(fn):
        @lru_cache(maxsize=maxsize)
        def versioned(revision, rules, *args, **kwargs):
            return fn(*args, **kwargs)

        @wraps(fn)
        def cached(*args, **kwargs):
            return versioned(_revision, semantic_key() if semantic_key else None, *args, **kwargs)

        cached.cache_info = versioned.cache_info
        cached.cache_clear = versioned.cache_clear
        return cached
    return decorate
