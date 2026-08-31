"""Cancellation primitives for cooperative stop in long-running tasks."""

# State global pentru a preveni rularea multipla a aceleasi operatiuni grele pe server.
# Stocam SessionID-ul care "detine" procesorul.
_ACTIVE_ENGINE_SESSION_ID = None


def lock_engine(session_id: str):
    global _ACTIVE_ENGINE_SESSION_ID
    _ACTIVE_ENGINE_SESSION_ID = session_id


def unlock_engine():
    global _ACTIVE_ENGINE_SESSION_ID
    _ACTIVE_ENGINE_SESSION_ID = None
