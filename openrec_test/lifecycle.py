"""Distinguish failed state restoration from a normal product assertion failure."""
from contextlib import contextmanager


class CleanupError(RuntimeError):
    pass


@contextmanager
def cleanup_guard():
    try:
        yield
    except Exception as error:
        raise CleanupError(f"state restoration failed: {error}") from error
