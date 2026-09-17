"""Refusals of a warehouse host, mapped to the status codes the governance kernel expects."""

from __future__ import annotations

import re


class WarehouseError(RuntimeError):
    """A refusal that carries a reason the caller can act on."""


class HoldoutError(WarehouseError):
    """The query reaches into the declared holdout (403)."""


class UnsupportedError(WarehouseError):
    """The backend cannot serve this operation (422)."""


class BackendRefusal(WarehouseError):
    """The backend refused to start or to answer: configuration, not a caller error."""


class DiscoveryRefusal(BackendRefusal):
    """The configured backend could not be resolved to one installed, named distribution."""


class StorageUnreachable(WarehouseError):
    """The structured store is unreachable (503) — never reported as a refusal by the store."""


# --- classification at the HTTP boundary (predictor L4, 2026-09-17) --------------------------
#
# Three things were one: a document the store cannot read (the client's fault, permanent), a
# defect inside the store (ours, permanent until fixed) and a store that is unreachable or
# locked (nobody's fault, transient). A single `except Exception -> 503 "database error"`
# turned an AttributeError on a malformed envelope into an outage the loader retried forever.

#: exception class names that mean the structured store could not be reached or is busy
_TRANSIENT_NAMES = ("OperationalError", "InterfaceError", "DisconnectionError",
                    "ConnectionError", "ConnectionRefusedError", "TimeoutError",
                    "IOException", "BrokenPipeError")
_TRANSIENT_HINTS = ("could not connect", "connection refused", "lock", "locked", "timeout",
                    "unable to open", "no such file", "busy", "database is locked",
                    "conflicting lock")


def classify(exc: BaseException) -> tuple:
    """(status, class, message) for an exception raised while serving a write or a read.

    400 INVALID_INPUT      ValueError / SystemExit: the document was looked at and refused
    422 UNSUPPORTED        UnsupportedError / BackendRefusal / HoldoutError / PermissionError
    503 STORE_UNAVAILABLE  StorageUnreachable, driver connection/lock/timeout errors
    500 INTERNAL_DEFECT    anything else: a programming defect, named, never disguised
    """
    if isinstance(exc, (ValueError, SystemExit)):
        return 400, "INVALID_INPUT", str(exc)
    if isinstance(exc, (UnsupportedError, BackendRefusal, HoldoutError, PermissionError)):
        return 422, "UNSUPPORTED", str(exc)
    if isinstance(exc, StorageUnreachable):
        return 503, "STORE_UNAVAILABLE", str(exc)
    name = type(exc).__name__
    text = str(exc).lower()
    if name in _TRANSIENT_NAMES or any(h in text for h in _TRANSIENT_HINTS):
        return 503, "STORE_UNAVAILABLE", f"{name}: {exc}"
    return 500, "INTERNAL_DEFECT", f"{name}: {exc}"


def bounded(message: str, limit: int = 300) -> str:
    """A diagnosis a client may see: bounded, without bearer tokens or long secrets."""
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", str(message))
    text = re.sub(r"[A-Za-z0-9+/_-]{40,}", "<redacted>", text)
    return text[:limit]
