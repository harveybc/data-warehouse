"""Host configuration of a warehouse. Connection strings and schemas belong to the provider."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "store_id": "warehouse",
    "title": "data-warehouse",
    "description": "Reusable warehouse host",
    "kind": "warehouse",
    "engine": None,
    "transport": "http",
    "web_host": "127.0.0.1",
    "web_port": 5061,
    "secret_key": "change-me",
    "max_rows": 10000,
    "service_token": None,
    "service_token_file": None,
    "backend": {"entry_point": None, "distribution": None, "settings": {}},
}


def load(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    if path:
        file_config = json.loads(Path(path).read_text(encoding="utf-8"))
        backend = dict(config["backend"], **(file_config.get("backend") or {}))
        config.update(file_config)
        config["backend"] = backend
    if overrides:
        backend = dict(config["backend"], **(overrides.get("backend") or {}))
        config.update(overrides)
        config["backend"] = backend
    return config
