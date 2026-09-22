"""Shared cancellation context and publication boundary (no app initialization)."""
from contextvars import ContextVar
from functools import wraps
from threading import RLock

publication_lock = RLock()
ingestion_lock = RLock()
current_upload_task: ContextVar[str | None] = ContextVar("current_upload_task", default=None)

class TaskCancelledError(Exception):
    pass

class CleanupError(Exception):
    pass

def publication_guard(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with publication_lock:
            return fn(*args, **kwargs)
    return wrapped

def ingestion_guard(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with ingestion_lock:
            return fn(*args, **kwargs)
    return wrapped
