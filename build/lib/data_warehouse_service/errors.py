"""Refusals of a warehouse host, mapped to the status codes the governance kernel expects."""

from __future__ import annotations


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
