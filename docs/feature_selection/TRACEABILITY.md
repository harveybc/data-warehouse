# Phase-1 feature-selection traceability

| Requirement | Test evidence | Implementation |
|---|---|---|
| FSWH-01 | `test_route_requires_auth_and_an_explicit_backend_capability` | `web.create_app`, backend capability vocabulary |
| FSWH-02 | `test_one_envelope_atomically_loads_every_family_and_a_receipt` | `SqliteStore.write_feature_selection_envelope` |
| FSWH-03 | `test_a_row_digest_must_cover_the_stored_row`, invalid-envelope cases | `feature_selection.validate_envelope` |
| FSWH-04 | `test_identical_replay_is_a_noop_but_a_contradiction_rolls_back_everything`, `test_concurrent_identical_replays_commit_exactly_once` | immutable row identity plus serialized owner transaction |
| FSWH-05 | parameterized invalid-envelope cases | typed row-family validators |
| FSWH-06 | `test_dashboard_coverage_and_failure_views_are_read_only_and_queryable` | disposable provider views |
| FSWH-07 | document inspection | provider integration boundary in `REQUIREMENTS.md` |
| FSWH-08 | full test command and packaging test | temporary SQLite fixtures only |
