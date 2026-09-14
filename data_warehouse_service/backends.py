"""The backend interface of a warehouse host.

A warehouse serves queries and reports, not file bodies. A backend that cannot answer an
operation says so; the host never fabricates a download endpoint for a warehouse.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from .errors import UnsupportedError

CAPABILITIES = (
    "describe",          # identity and metadata of the store
    "storage",           # size on the host
    "discover",          # the tables/views exposed
    "schema",            # the columns of one exposed relation
    "query",             # read-only SQL
    "write_metrics",     # append a governed report
    "write_terminal",    # append a governed terminal
    "terminal_digests",  # what terminals a campaign already stored
)


@runtime_checkable
class WarehouseBackend(Protocol):
    def capabilities(self) -> Iterable[str]: ...

    def set_params(self, **settings) -> None: ...

    def describe(self) -> dict: ...


class WarehouseBackendBase:
    """Optional base class: declares nothing, refuses everything, records its settings."""

    declared_capabilities: tuple = ()
    backend_params: dict = {}

    def __init__(self):
        self.params = dict(self.backend_params)

    def set_params(self, **settings):
        self.params.update(settings)

    def capabilities(self):
        return tuple(self.declared_capabilities)

    def source_identity(self) -> dict:
        return {}

    def _refuse(self, name):
        raise UnsupportedError(f"this backend does not support {name}")

    def sweep(self):
        return None

    def describe(self):
        self._refuse("describe")

    def storage(self):
        self._refuse("storage")

    def discover(self):
        self._refuse("discover")

    def schema(self, relation):
        self._refuse("schema")

    def query(self, sql: str):
        self._refuse("query")

    def write_metrics(self, report: dict):
        self._refuse("write_metrics")

    def write_terminal(self, terminal: dict):
        self._refuse("write_terminal")

    def terminal_digests(self, campaign_sha256: str):
        self._refuse("terminal_digests")


def check_capabilities(declared) -> tuple:
    declared = tuple(declared or ())
    unknown = [c for c in declared if c not in CAPABILITIES]
    if unknown:
        raise UnsupportedError(f"a backend declares unknown capabilities: {sorted(unknown)}")
    return declared
