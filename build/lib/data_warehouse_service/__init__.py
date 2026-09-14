"""A reusable warehouse host: the HTTP contract and the backend seam, with no data of its own."""

from .backends import CAPABILITIES, WarehouseBackendBase
from .errors import (BackendRefusal, DiscoveryRefusal, HoldoutError, StorageUnreachable,
                     UnsupportedError, WarehouseError)

__all__ = ["CAPABILITIES", "WarehouseBackendBase", "WarehouseError", "HoldoutError",
           "UnsupportedError", "BackendRefusal", "DiscoveryRefusal", "StorageUnreachable",
           "__version__"]
__version__ = "0.1.0"
