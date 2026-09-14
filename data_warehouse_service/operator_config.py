"""Project and validate an operator's configuration, without touching the running one.

The rule this module exists to enforce: **a pending file is not an authorization and not a
deployment**. Saving writes a JSON file next to the service; activation remains the
service's configuration load, which happens at start. Nothing here mutates the live
configuration, starts a provider or reloads a route.

What the console may edit is deliberately small: the service's own settings and the backend
selection with its opaque provider settings. Secrets are never projected into the browser.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

SECRET_KEYS = {"service_token", "service_token_file", "secret_key", "password", "api_key",
               "lake_service_token", "token"}
SERVICE_FIELDS = ("store_id", "title", "description", "kind", "engine", "transport",
                  "web_host", "web_port", "max_rows")


def _redact(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in SECRET_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def editable_config(config: dict) -> dict:
    """The part of the configuration an operator may edit, with secrets removed."""
    backend = copy.deepcopy(config.get("backend") or {})
    projected = {field: config.get(field) for field in SERVICE_FIELDS if field in config}
    projected["backend"] = {"entry_point": backend.get("entry_point"),
                            "distribution": backend.get("distribution"),
                            "settings": _redact(backend.get("settings") or {})}
    return projected


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def pending_config(config: dict, raw: str) -> dict:
    """Parse and validate the operator's text, or raise ValueError with a usable reason."""
    try:
        proposed = json.loads(raw, object_pairs_hook=_pairs,
                              parse_constant=lambda v: (_ for _ in ()).throw(
                                  ValueError(f"non-finite JSON value: {v}")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(proposed, dict):
        raise ValueError("the configuration must be a JSON object")
    unknown = set(proposed) - set(SERVICE_FIELDS) - {"backend"}
    if unknown:
        raise ValueError(f"fields this console does not edit: {sorted(unknown)}")
    backend = proposed.get("backend")
    if not isinstance(backend, dict):
        raise ValueError("backend must be an object")
    if not backend.get("entry_point") or not backend.get("distribution"):
        raise ValueError("backend must name both entry_point and distribution")
    if not isinstance(backend.get("settings", {}), dict):
        raise ValueError("backend.settings must be an object")
    port = proposed.get("web_port", config.get("web_port"))
    if port is not None and not (isinstance(port, int) and 0 < port < 65536):
        raise ValueError("web_port must be a port number")
    for field in ("max_rows",):
        if field in proposed and not (isinstance(proposed[field], int) and proposed[field] > 0):
            raise ValueError(f"{field} must be a positive integer")
    redacted = [k for k, v in (backend.get("settings") or {}).items() if v == "<redacted>"]
    if redacted:
        raise ValueError(f"these values were redacted for the browser and cannot be saved "
                         f"from it: {sorted(redacted)}")
    return proposed


def write_pending(path: str | os.PathLike, proposed: dict) -> Path:
    """Persist atomically. A partial write must never become a pending configuration."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(destination.parent), prefix=".pending-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(proposed, out, indent=1, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination
