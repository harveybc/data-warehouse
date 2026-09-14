# Parity with the OLAP host this one replaces

`tools/compare_with_legacy_host.py` starts the legacy OLAP host and this host over separate
throwaway SQLite databases, with `PG*` removed from the environment, and compares status and
body for the thirteen routes the governed consumers use — including the second report and
the second terminal, where idempotence must answer 200 with `already_stored`.

Run on 2026-09-14 (`WAREHOUSE_HOST_PARITY.json` in the predictor evidence directory):

| | |
|---|---|
| routes compared | 13 |
| data routes identical (status and body) | 11 of 11 |
| identity routes (`describe`, `storage`) | status equal; bodies name their own host |
| differences | none |

The full Flow v3 campaign was then executed with this host receiving the governed terminal
and the governance kernel's configuration unchanged:
`data-gov/tools/verify_flow_v3_e2e.py --new-warehouse-python …`, reconciliation exact.
