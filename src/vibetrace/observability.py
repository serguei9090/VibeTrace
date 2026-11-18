"""Unified error formatting, structured step telemetry, and logging configuration."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_NOISY_LOGGERS = (
    "transformers",
    "huggingface_hub",
    "httpx",
    "httpcore",
    "urllib3",
    "filelock",
)

_HINT_MAP: tuple[tuple[type[BaseException], str], ...] = (
    (FileNotFoundError, "Run 'vibetrace init' or 'vibetrace index' first, or check the path."),
    (PermissionError, "Verify file permissions and ensure the target stays inside project root."),
    (
        sqlite3.OperationalError,
        "Database may be locked or uninitialized; run 'vibetrace index' or retry.",
    ),
    (ImportError, "Optional dependency missing; run 'uv sync' or install the required extra."),
    (RuntimeError, "Run with --verbose (-v) and inspect '.vibetrace/vibetrace.log' for details."),
)


def is_verbose_enabled(explicit: bool = False) -> bool:
    """Return True if verbose logging is requested via flag or VIBETRACE_VERBOSE env var."""
    if explicit:
        return True
    return os.environ.get("VIBETRACE_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "debug",
    )


def configure_logging(
    verbose: bool = False,
    project_root: Path | str | None = None,
) -> logging.Logger:
    """Configure the 'vibetrace' logger hierarchy on stderr and optional project log file."""
    eff_verbose = is_verbose_enabled(verbose)
    vt_logger = logging.getLogger("vibetrace")
    vt_logger.propagate = False
    vt_logger.setLevel(logging.DEBUG if eff_verbose else logging.WARNING)

    for h in list(vt_logger.handlers):
        vt_logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    fmt = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.DEBUG if eff_verbose else logging.WARNING)
    stderr_handler.setFormatter(fmt)
    vt_logger.addHandler(stderr_handler)

    if project_root is not None:
        vibetrace_dir = Path(project_root).resolve() / ".vibetrace"
        if vibetrace_dir.is_dir():
            try:
                file_handler = logging.FileHandler(
                    vibetrace_dir / "vibetrace.log", encoding="utf-8"
                )
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
                )
                vt_logger.addHandler(file_handler)
            except OSError:
                pass

    if os.environ.get("VIBETRACE_DEBUG_LIBS") != "1":
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TQDM_DISABLE", "1")
        for lib_name in _NOISY_LOGGERS:
            logging.getLogger(lib_name).setLevel(logging.ERROR)

    return vt_logger


def infer_error_hint(exc: BaseException) -> str | None:
    """Return an actionable hint for common exception types, or None."""
    for exc_cls, hint in _HINT_MAP:
        if isinstance(exc, exc_cls):
            return hint
    return None


def format_error_dict(
    exc: BaseException,
    context: str = "",
    verbose: bool = False,
) -> dict[str, Any]:
    """Log an exception and return a structured error dictionary."""
    eff_verbose = is_verbose_enabled(verbose)
    vt_logger = logging.getLogger("vibetrace")
    if context:
        vt_logger.error("%s: %s", context, exc, exc_info=eff_verbose)
    else:
        vt_logger.error("%s", exc, exc_info=eff_verbose)

    payload: dict[str, Any] = {
        "status": "error",
        "error_type": type(exc).__name__,
        "context": context,
        "message": str(exc),
    }
    hint = infer_error_hint(exc)
    if hint:
        payload["hint"] = hint
    if eff_verbose and sys.exc_info()[0] is not None:
        payload["traceback"] = traceback.format_exc()
    return payload


def format_error_text(
    exc: BaseException,
    context: str = "",
    verbose: bool = False,
) -> str:
    """Format an exception as human-readable text, preserving legacy Error (...) prefix."""
    payload = format_error_dict(exc, context=context, verbose=verbose)
    err_type = payload["error_type"]
    msg = f"Error ({err_type}): {context} - {exc}" if context else f"Error ({err_type}): {exc}"
    if "traceback" in payload:
        tb = str(payload["traceback"]).rstrip()
        if tb and tb != "NoneType: None":
            msg = f"{msg}\nTraceback:\n{tb}"
    return msg


class StepTracker:
    """Tracks timed pipeline stages for verbose stderr output and structured diagnostics."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = is_verbose_enabled(verbose)
        self._start = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self._logger = logging.getLogger("vibetrace.pipeline")

    def record(self, step: str, message: str, **metrics: Any) -> None:
        """Record a completed or active pipeline stage."""
        elapsed_ms = round((time.perf_counter() - self._start) * 1000.0, 2)
        self._logger.info("[%s] %s", step, message)
        if self.verbose:
            print(f"  [{step}] {message}", file=sys.stderr)
        self.events.append({"step": step, "message": message, "elapsed_ms": elapsed_ms, **metrics})

    def warn(self, step: str, message: str) -> None:
        """Record a pipeline warning."""
        elapsed_ms = round((time.perf_counter() - self._start) * 1000.0, 2)
        self._logger.warning("[%s] %s", step, message)
        if self.verbose:
            print(f"  [{step} WARN] {message}", file=sys.stderr)
        self.events.append(
            {"step": step, "level": "warning", "message": message, "elapsed_ms": elapsed_ms}
        )
