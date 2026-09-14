# data-warehouse

A reusable **warehouse host**: the HTTP contract, the configuration and the backend seam
for structured stores. It holds no schema, no cube and no governance decision.

```json
{
  "store_id": "olap_cube",
  "web_port": 5067,
  "backend": {
    "entry_point": "predictor_olap",
    "distribution": "predictor-olap-store",
    "settings": {"schema": "public", "holdout_start": "2025-01-01"}
  }
}
```

```bash
pip install .                          # the host
pip install ../predictor/olap/store    # a provider, as its own distribution
python -m data_warehouse_service.main --load_config host.json --print-identity
```

## What the host guarantees

* The same discovery rules as `data-lake`, over the `datawarehouse.backends` group: one
  named, installed distribution or a refusal, and the resolved identity recorded.
* The contract the governed consumers already call: `/api/v1/describe`, `/storage`,
  `/discover`, `/schema`, `/query`, `/api/v1/metrics` and `/api/v2/terminals`, with the
  report and terminal idempotence the campaign protocol depends on (a stored digest stores
  once, and says so).
* **A warehouse is not a lake.** `/api/v1/download` and `/api/v2/download` exist only to
  answer 422: this host will not fabricate a file delivery. An undeclared capability is
  422; an unreachable store is 503, never a refusal attributed to the store.
* Read-only SQL. The provider decides what a query may do; the host offers no route that
  could truncate anything.

## Tests

The suite under `tests/` covers discovery and the HTTP contract (18 tests), and
`tools/compare_with_legacy_host.py` compares this host with the OLAP host it replaces.

That parity harness runs both hosts over a **throwaway SQLite database** and removes every
`PG*` variable from their environment, so it cannot reach the production cube.

## Status

Implemented and proven against a disposable SQLite provider and against the installed
`predictor-olap-store`; **not deployed**. Migration sequence and scope exclusions:
`data-gov/docs/STORE_PACKAGES_DESIGN.md`.
