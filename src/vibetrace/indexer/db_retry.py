"""SQLite write retry mechanism with cross-process lock coordination and exponential backoff."""

from __future__ import annotations

import os
import random
import sqlite3
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any


@contextmanager
def interprocess_write_lock(
    db_path: str | Path | None, timeout: float = 30.0
) -> Generator[None, None, None]:
    """Cross-process advisory file lock around SQLite write transactions.

    Uses Windows msvcrt.locking or POSIX fcntl.flock on a sibling `.lock` file
    to serialize multi-process writers (MCP servers, CLI, background watcher)
    before hitting SQLite WAL lock contention.
    """
    if not db_path or str(db_path) == ":memory:" or "mode=memory" in str(db_path):
        yield
        return

    lock_path = Path(f"{db_path}.write.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return

    fd: int | None = None
    locked = False
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        except OSError:
            pass

        if fd is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(random.uniform(0.005, 0.025))
        yield
    finally:
        if locked and fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _resolve_db_path(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | Path | None:
    """Extract db_path from kwargs, db instance, or self argument."""
    if "db_path" in kwargs and kwargs["db_path"] is not None:
        return kwargs["db_path"]
    if "db" in kwargs and hasattr(kwargs["db"], "db_path"):
        return getattr(kwargs["db"], "db_path", None)
    for arg in args:
        if hasattr(arg, "db_path"):
            return getattr(arg, "db_path", None)
    return None


def with_write_retry(max_attempts: int = 7, initial_delay: float = 0.05) -> Callable[..., Any]:
    """Decorator to retry SQLite writes when locked or busy under concurrent agent access."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            db_path = _resolve_db_path(args, kwargs)
            last_err: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    with interprocess_write_lock(db_path):
                        return func(*args, **kwargs)
                except sqlite3.DatabaseError as err:
                    err_msg = str(err).lower()
                    if "locked" in err_msg or "busy" in err_msg:
                        last_err = err
                        if attempt < max_attempts - 1:
                            sleep_time = initial_delay * (2**attempt) + random.uniform(0.01, 0.05)
                            time.sleep(sleep_time)
                        continue
                    raise
            if last_err:
                raise last_err
            raise RuntimeError("with_write_retry: no attempts were made")

        return wrapper

    return decorator
