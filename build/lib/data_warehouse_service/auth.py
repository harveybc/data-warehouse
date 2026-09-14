"""Bearer authentication of the store API. The token is the governance kernel's, not the host's."""

from __future__ import annotations

import hmac
import os
from pathlib import Path


def load_token(config: dict | None = None) -> str | None:
    """The expected service token: configuration, then environment, then a token file."""
    if config and config.get("service_token"):
        return str(config["service_token"]).strip() or None
    env = os.getenv("DATA_GOV_LAKE_TOKEN")
    if env:
        return env.strip() or None
    explicit = os.getenv("DATA_GOV_LAKE_TOKEN_FILE")
    if explicit and Path(explicit).is_file():
        return Path(explicit).read_text(encoding="utf-8").strip() or None
    if config and config.get("service_token_file"):
        path = Path(config["service_token_file"])
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
    return None


def check_bearer(header: str | None, expected: str | None) -> bool:
    if not expected:
        return False
    given = (header or "").strip()
    if not given.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(given[7:].strip(), expected)
