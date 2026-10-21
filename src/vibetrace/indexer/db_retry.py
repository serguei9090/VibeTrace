"""SQLite write retry mechanism with exponential backoff and jitter."""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable
from functools import wraps
from typing import Any


def with_write_retry(max_attempts: int = 5, initial_delay: float = 0.05) -> Callable[..., Any]:
    """Decorator to retry SQLite writes when locked under concurrent agent access."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as err:
                    err_msg = str(err).lower()
                    if "locked" in err_msg or "busy" in err_msg:
                        last_err = err
                        sleep_time = initial_delay * (2**attempt) + random.uniform(0.01, 0.05)
                        time.sleep(sleep_time)
                        continue
                    raise
            if last_err:
                raise last_err

        return wrapper

    return decorator
