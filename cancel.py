"""Cancellation primitives for cooperative stop in long-running tasks."""


class AnalysisCancelled(Exception):
    """Raised when the user requests stopping the active analysis."""


# State global pentru a preveni rularea multipla a aceleasi operatiuni grele pe server.
# Stocam SessionID-ul care "detine" procesorul.
_ACTIVE_ENGINE_SESSION_ID = None


def is_engine_busy() -> bool:
    global _ACTIVE_ENGINE_SESSION_ID
    return _ACTIVE_ENGINE_SESSION_ID is not None


def lock_engine(session_id: str):
    global _ACTIVE_ENGINE_SESSION_ID
    _ACTIVE_ENGINE_SESSION_ID = session_id


def unlock_engine():
    global _ACTIVE_ENGINE_SESSION_ID
    _ACTIVE_ENGINE_SESSION_ID = None


def get_locker_session_id() -> str | None:
    return _ACTIVE_ENGINE_SESSION_ID

