"""Resolve the configured backend to one entry point of one installed distribution.

Identical rules to the lake host, over the `datawarehouse.backends` group.

Rules, all refusals rather than fallbacks:

* a repository name in configuration is a provenance reference, never an import path;
* the entry point must belong to the distribution the configuration names;
* two installed distributions offering the same entry-point name is a refusal, not a
  race the host resolves by order;
* nothing is installed, downloaded or imported outside the declared entry-point group;
* what was resolved is recorded — distribution, version, module, the provider's own
  source identity and the SHA-256 of the settings actually applied.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import distribution, entry_points

from .backends import check_capabilities
from .errors import DiscoveryRefusal

GROUP = "datawarehouse.backends"


def settings_sha256(settings: dict) -> str:
    return hashlib.sha256(json.dumps(settings or {}, sort_keys=True, default=str).encode()).hexdigest()


def _entries(group: str, source=None):
    if source is not None:
        return list(source)
    return list(entry_points(group=group))


def _distribution_of(entry):
    dist = getattr(entry, "dist", None)
    if dist is not None and getattr(dist, "name", None):
        return dist.name, getattr(dist, "version", None)
    return None, None


def resolve(config: dict, group: str = GROUP, source=None) -> dict:
    """Return {"backend", "identity"} for `config["backend"]`, or refuse with a named reason."""
    spec = (config or {}).get("backend") or {}
    name = spec.get("entry_point")
    wanted = spec.get("distribution")
    settings = spec.get("settings") or {}
    if not name:
        raise DiscoveryRefusal("configuration names no backend entry_point")
    if not wanted:
        raise DiscoveryRefusal(f"configuration names no distribution for entry point {name!r}")
    matches = [e for e in _entries(group, source) if e.name == name]
    if not matches:
        raise DiscoveryRefusal(
            f"no installed distribution registers {name!r} in {group}; install the provider first")
    owners = {}
    for entry in matches:
        dist_name, version = _distribution_of(entry)
        owners.setdefault(dist_name, []).append((entry, version))
    if len(owners) > 1:
        raise DiscoveryRefusal(
            f"entry point {name!r} is registered by more than one distribution "
            f"({sorted(str(o) for o in owners)}); the host does not choose between them")
    (dist_name, pairs), = owners.items()
    if dist_name != wanted:
        raise DiscoveryRefusal(
            f"entry point {name!r} belongs to distribution {dist_name!r}, "
            f"but the configuration names {wanted!r}")
    if len(pairs) > 1:
        raise DiscoveryRefusal(
            f"distribution {dist_name!r} registers {name!r} more than once in {group}")
    entry, version = pairs[0]
    if version is None:
        try:
            version = distribution(dist_name).version
        except Exception:  # pragma: no cover - a distribution without metadata
            version = None
    factory = entry.load()
    backend = factory() if callable(factory) else factory
    if hasattr(backend, "set_params"):
        backend.set_params(**settings)
    declared = check_capabilities(backend.capabilities() if hasattr(backend, "capabilities") else ())
    if not declared:
        raise DiscoveryRefusal(f"backend {name!r} declares no capability; a host serves nothing by default")
    identity = {"group": group, "entry_point": name, "distribution": dist_name, "version": version,
                "module": getattr(entry, "value", None), "capabilities": list(declared),
                "settings_sha256": settings_sha256(settings),
                "source_identity": backend.source_identity() if hasattr(backend, "source_identity") else {}}
    return {"backend": backend, "identity": identity}
