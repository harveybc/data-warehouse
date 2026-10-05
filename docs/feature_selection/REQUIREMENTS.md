# Phase-1 feature-selection warehouse contract

## Scope

This repository owns the authenticated HTTP boundary and delegates storage to exactly one
configured backend. It does not give workers a DuckDB path or connection. The feature-selection
producer submits one `feature_selection_envelope.v1` document to the owner process.

## Requirements

| ID | Observable requirement |
|---|---|
| FSWH-01 | `POST /api/v2/feature-selection-envelopes` requires the service token and an explicitly declared backend capability. |
| FSWH-02 | One accepted envelope commits sampling, profile, information, pair-relation, causal-evidence and final-decision rows plus one receipt atomically. |
| FSWH-03 | The envelope, run and every fact row have content-derived SHA-256 identities; a false digest is rejected before writing. |
| FSWH-04 | An identical replay is a no-op; the same immutable natural identity with different content is a contradiction and rolls back the entire envelope. |
| FSWH-05 | Non-finite metrics, invalid causal rungs and non-final decisions are rejected. Missing evidence is represented by an explicit state, not by omitting a required family. |
| FSWH-06 | Read-only relations expose profile facts, causal evidence, decisions, per-feature coverage, failures and a run dashboard. |
| FSWH-07 | The production provider owns its DuckDB schema, transaction and verified snapshot. This host owns only the capability and HTTP contract. |
| FSWH-08 | Tests use only the disposable SQLite provider and never open or mutate the live warehouse. |
| FSWH-09 | `POST /api/v2/feature-selection-reconcile` requires the service token and the explicit `reconcile_feature_selection` capability. |
| FSWH-10 | A `phase1.warehouse_reconciliation_request.v1` binds a positive expected count, one unique identity per feature, the plan, population, authentication profile and canonical request/identity digest. |
| FSWH-11 | Reconciliation queries owner-retained envelope receipts; a missing envelope or a feature absent from its claimed envelope rejects the population. |
| FSWH-12 | A successful `phase1.warehouse_reconciliation.v1` binds every request field, the observed identities/count/digest, completeness and contradictions under `reconciliation_sha256`. |
| FSWH-13 | An `UNAVAILABLE` terminal may carry no envelope or receipt; it remains request-bound and is never presented as an independently stored warehouse payload. |

## Envelope

Required top-level fields are `schema_version`, `run`, `rows` and `envelope_sha256`.
`envelope_sha256` is the canonical JSON SHA-256 of the document without that field. Every row
similarly carries `row_sha256`, computed without `row_sha256`.

`run` requires `run_id`, `campaign_sha256`, `code_sha256`, `input_sha256`,
`inventory_sha256` and `created_at`. `rows` requires all six arrays, even when one is empty:

- `sampling_quality`
- `variable_profiles`
- `information_metrics`
- `pair_relations`
- `causal_evidence`
- `selection_decisions`

The exact executable validation is in `data_warehouse_service.feature_selection`; executable
validation, not this prose, decides acceptance.

## Provider integration boundary

A production backend must:

1. Declare `write_feature_selection_envelope`.
2. Implement `write_feature_selection_envelope(document)` and defensively call the shared
   `validate_envelope()` before opening its transaction. The HTTP host validates first too;
   this second check protects direct provider use.
3. Own normalized equivalents of the seven disposable-provider relations:
   `df_fact_sampling_quality`, `df_fact_variable_profile`,
   `df_fact_information_metric`, `df_fact_pair_relation`,
   `df_fact_feature_causal_evidence`, `df_fact_feature_selection_decision` and
   `df_fact_feature_selection_load_receipt`, plus a run dimension.
4. Enforce `row_identity_sha256` uniqueness, compare the retained `row_sha256` on conflict,
   and commit every family and the receipt in one transaction.
5. Publish read-only equivalents of `df_feature_profile_current`,
   `df_feature_causal_ladder_current`, `df_feature_selection_current`,
   `df_feature_selection_coverage`, `df_feature_selection_failures` and
   `df_feature_selection_dashboard`.
6. Extend its verified snapshot manifest with table row counts and deterministic content
   digests for the run dimension, all seven facts/receipts and all six views. A snapshot cannot
   claim phase-1 coverage when any relation is absent.

The DuckDB provider and its snapshot implementation live in `predictor/olap/store` and
`predictor/tools/olap_duckdb_migrate.py`; they are intentionally not modified by this change.

## Authenticated reconciliation

The predictor orchestrator posts `phase1.warehouse_reconciliation_request.v1` directly to
`POST /api/v2/feature-selection-reconcile`. The host authenticates the HTTP request, validates
all identities and digest claims, then delegates to `reconcile_feature_selection(request)`.

The backend must query retained feature-selection load receipts. For each `COMPLETED` identity,
the named envelope must exist and its retained feature set must contain the named feature. A
missing envelope is incomplete evidence; a mismatched feature is contradictory evidence. Both
are refusals, not partially successful reconciliations. `UNAVAILABLE` identities have no
warehouse payload and are bound from the authenticated request without claiming independent
warehouse observation.

A successful response has schema `phase1.warehouse_reconciliation.v1`, state `RECONCILED`, all
request identity fields, `observed_count`, `observed_identities`,
`observed_identities_sha256`, `complete: true`, an empty `contradictions` list and a canonical
`reconciliation_sha256`. Production providers must declare `reconcile_feature_selection` and
implement this same behavior before the orchestrator can open its phase-2 gate.
