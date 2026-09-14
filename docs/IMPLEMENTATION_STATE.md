# Implementation state

This file is the persistent state of the work, updated at every milestone. Chat memory is
not state. Stages and vocabulary come from the work-plan amendment
`predictor/docs/integracion_workplan_2026_09_10/09_ADOPCION_DATA_LAKE_DATA_WAREHOUSE_2026_09_14.md`.

States: `PENDING` | `IMPLEMENTED` | `PROVEN_DISPOSABLE` | `PUBLISHED` | `DEPLOYED` |
`PROVEN_PRODUCTION`.

| stage | criterion | state |
|---|---|---|
| 1. Create | repository with README, AGENTS.md, requirements, tests and persistent state, published at a real URL | `PUBLISHED` — https://github.com/harveybc/data-warehouse |
| 2. Implement | host installable as a wheel; provider installable from its own repository; discovery proven through a real install | `PROVEN_DISPOSABLE` — the clone-and-install check in `docs/PARITY.md` |
| 3. Integrate | parity with the adapter it replaces: tables/views, query behaviour, receipts, outcomes, idempotence | `PROVEN_DISPOSABLE` — 11/11 data routes identical, `docs/PARITY.md` |
| 4. Interface | AdminLTE configuration and inventory views, relation schema, bounded query, desktop and mobile | `PROVEN_DISPOSABLE` — `tests/test_console.py` (10) and `tools/console_screenshots.py`: eight pages driven in a real browser at 1440×900 and 390×844, every asset served by this host, zero horizontal overflow; receipt and PNGs in `docs/console/` |
| 5. Put into use | controlled transition over the same data and IDs; governed micro-run through both hosts with exact reconciliation | `PROVEN_PRODUCTION` on 2026-09-14: synthetic governed terminal, four metrics, idempotent replay, exact reconciliation; historical table counts unchanged |
| 6. Adopt | consumer configurations updated; new campaigns use this route by default | `PROVEN_PRODUCTION` for bounded synthetic runs of preprocessor, feature-eng, feature-extractor and predictor; offline DOIN integration remains pending |

The four-consumer production check on 2026-09-14 added twelve reconciled
terminals and 119 metrics through this host. Existing results were preserved.
This is transport/mechanics evidence, not a scientific approval.
[Acceptance and next work](https://github.com/harveybc/predictor/blob/master/docs/handoffs/MUSASHI_SYNTHETIC_CATALOG_AND_FOUR_CONSUMERS_ACCEPTANCE_2026_09_14.md).

## Requirements this host must satisfy

1. Serve, unchanged, the routes the governed consumers already call: `/api/v1/describe`,
   `/storage`, `/discover`, `/schema`, `/query`, `/api/v1/metrics` and `/api/v2/terminals`,
   with report and terminal idempotence (a stored digest answers 200 `already_stored`).
2. Resolve exactly one installed distribution for the configured entry point, or refuse
   with a named reason; record what was resolved.
3. Refuse an undeclared capability with 422 rather than approximating it.
4. Map a provider's refusal to the status the kernel already maps, and re-raise anything it
   cannot classify.
5. Hold no dataset knowledge: everything data-specific travels in `backend.settings`.

## Acceptance scenarios

| scenario | expected |
|---|---|
| unauthenticated request to any API route | 401 |
| `SELECT` query | 200 with rows and a truncation flag |
| anything that is not a single read-only statement | 400 |
| download route on a warehouse | 422, naming the kind |
| unknown relation in `/schema` | 404; a non-identifier relation | 400 |
| second identical report or terminal | 200 with `already_stored`, no duplicate row |
| terminal without a campaign | 400 |
| operation the backend does not declare | 422 |
| store unreachable | 503, never a refusal attributed to the store; the console states the failure instead of showing an empty list |
| console query | the result table is rendered and escaped, not a row count in a flash |
| console shows a secret | never; a redacted value cannot be saved back |
| two distributions registering the same entry point | startup refusal naming both |
| configured distribution not the owner of the entry point | startup refusal naming the real owner |

## Test matrix

| layer | file | what it proves |
|---|---|---|
| discovery | `tests/test_discovery.py` | the resolution rules and every refusal, with the identity recorded |
| contract | `tests/test_http_contract.py` | routes, status codes, read-only SQL, idempotence, capability refusals, the warehouse/lake boundary |
| console | `tests/test_console.py` | inventory and schema, an escaped query result table, refusals, and a *pending* save that does not move the active configuration |
| browser | `tools/console_screenshots.py` | desktop and mobile rendering, local assets only, no horizontal overflow |
| parity | `tools/compare_with_legacy_host.py` | the legacy adapter and this host answer identically on the same fixture |
| end to end | `data-gov/tools/verify_flow_v3_e2e.py --new-warehouse-python …` | a full governed campaign through this host |
