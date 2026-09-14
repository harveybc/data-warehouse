# data-warehouse

A reusable **warehouse host**: the HTTP contract, the configuration and the provider interface
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
pip install "git+https://github.com/harveybc/data-warehouse.git"                       # the host
pip install "git+https://github.com/harveybc/predictor.git#subdirectory=olap/store"    # a provider
python -m data_warehouse_service.main --load_config host.json --print-identity
python -m data_warehouse_service.main --load_config host.json
```

A fresh install can serve something immediately: `examples/config/sqlite_demo.json` uses
`sqlite_store`, the disposable provider shipped with the host.
`examples/config/predictor_olap.json` is the real shape, and needs `predictor-olap-store`.

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

## Operator console

| page | what it is for |
|---|---|
| `/` | store identity, the provider resolved at start, and the tables and views exposed |
| `/relation?relation=…` | the columns of one relation |
| `/query` | a bounded read-only query whose **result table** is rendered and escaped, not summarised as a row count |
| `/settings` | the effective configuration, secrets redacted, and a **pending** save |

A pending file is not an authorization and not a deployment: activation remains the
service's configuration load. Desktop and mobile are proven in a real browser by
`tools/console_screenshots.py` — eight pages at 1440×900 and 390×844, every asset served by
this host, no horizontal overflow — with PNGs and a receipt in [docs/console/](docs/console/).

## Tests

The suite under `tests/` covers discovery, the HTTP contract and the console, and
`tools/compare_with_legacy_host.py` compares this host with the OLAP host it replaces.

That parity harness runs both hosts over a **throwaway SQLite database** and removes every
`PG*` variable from their environment, so it cannot reach the production cube.

## Status

This repository is public. The first external provider is
[predictor/olap/store](https://github.com/harveybc/predictor/tree/master/olap/store),
now on that repository's default branch. The host was deployed on 2026-09-14
against the existing PostgreSQL cube without a database migration or historical
table-count changes. A governed, non-scientific synthetic micro-run added one
terminal, four metrics, one input receipt and one artifact; replay did not duplicate
them. [Deployment receipt](https://github.com/harveybc/predictor/blob/master/docs/handoffs/MUSASHI_STORE_HOSTS_PRODUCTION_ACCEPTANCE_2026_09_14.md).

## Use with a coding agent

Read [AGENTS.md](AGENTS.md), the implementation state and
[data-gov's integration guide](https://github.com/harveybc/data-gov/blob/master/docs/INTEGRATION_EXAMPLES.md).
Use the SQLite demo or a disposable PostgreSQL database for tests. Install the
provider separately and report its exact identity. Check inventory, relation
schemas, query behavior, reporting, duplicate submission and reconciliation.
Do not reset the real cube or treat historical rows as test fixtures. Report
consumer adoption separately from host deployment: an available endpoint alone
does not prove every experiment is reporting through it.

## Work plan

Stage by stage in [docs/IMPLEMENTATION_STATE.md](docs/IMPLEMENTATION_STATE.md), which is
the persistent state of this work. Design, migration sequence and scope exclusions:
[data-gov/docs/STORE_PACKAGES_DESIGN.md](https://github.com/harveybc/data-gov/blob/master/docs/STORE_PACKAGES_DESIGN.md).
