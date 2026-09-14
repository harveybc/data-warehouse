# AGENTS.md — data-warehouse

Guidance for AI coding agents working in this repository. See [agents.md](https://agents.md).

## Project overview

`data-warehouse` is a **host**: it serves the warehouse HTTP contract and resolves the
structured store behind it from a Python entry point. It contains no schema, no cube, no
ETL and no governance decision. Policy, deliveries, receipts and accounting live in
[data-gov](https://github.com/harveybc/data-gov); the schema and its tables live in a
*provider* distribution such as `predictor-olap-store`.

A warehouse is not a lake. This host has no file delivery, and the download routes exist
only to say so with 422.

## Quickstart

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install .
python -m data_warehouse_service.main --load_config examples/config/sqlite_demo.json --print-identity
python -m data_warehouse_service.main --load_config examples/config/sqlite_demo.json
curl -H "Authorization: Bearer $DATA_GOV_LAKE_TOKEN" \
  "http://127.0.0.1:5069/api/v1/query?sql=SELECT+1+AS+one"
```

`sqlite_demo.json` uses `sqlite_store`, the disposable provider shipped with the host, so a
fresh install serves something without any other distribution. `predictor_olap.json` is the
real shape and needs `predictor-olap-store` installed:

```bash
pip install "git+https://github.com/harveybc/predictor.git#subdirectory=olap/store"
```

## Tests

```bash
python -m pytest tests -q
python tools/compare_with_legacy_host.py --help
```

The parity harness runs both hosts over a **throwaway SQLite database** and strips every
`PG*` variable from their environment, so it cannot reach a production cube. Keep it that
way: a test that can reach the real cube is a defect, not a stronger test.

## Layout

| Path | Purpose |
|---|---|
| `data_warehouse_service/web.py` | the HTTP contract: query, schema, metrics, terminals, and the 422 for downloads |
| `data_warehouse_service/backends.py` | the backend interface and the capability vocabulary |
| `data_warehouse_service/discovery.py` | entry-point resolution, refusals and the identity recorded |
| `data_warehouse_service/config.py` | host configuration; provider settings pass through opaquely |
| `data_warehouse_service/testing/` | the disposable SQLite provider used by the host's own tests |
| `tools/` | the legacy/new parity harness |

## Conventions and constraints

- **The contract is not ours to change.** The governed consumers already call these routes
  and depend on report and terminal idempotence: a digest stored once answers 200 with
  `already_stored`, not 201.
- **Read-only SQL.** The host exposes no route that could write outside the two governed
  append paths. Never add one, and never relax a provider's statement check to make a test
  pass.
- **503 is not a refusal.** An unreachable store is 503; a refusal attributed to a store
  that never answered is a lie in the receipts.
- **A capability is declared, never assumed**; anything else is 422.
- **No cube knowledge here.** Schemas, connection settings and ETL belong to the provider.

## Do not touch

- **The production cube.** Never run against `predictor_olap`, never truncate, never
  `reset_olap`. Throwaway databases only, and drop them.
- **Running services**, including the OLAP loader and Metabase. Do not restart anything as
  part of a change here.
- **Secrets.** No database passwords, machine names or private paths in this repository.
