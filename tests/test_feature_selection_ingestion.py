"""Acceptance tests for owner-only phase-1 feature-selection ingestion."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math

import pytest

from data_warehouse_service.config import load
from data_warehouse_service.testing.sqlite_store import SqliteStore
from data_warehouse_service.web import create_app


TOKEN = "feature-selection-test-token"
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def sealed(row):
    result = copy.deepcopy(row)
    result["row_sha256"] = digest(result)
    return result


def envelope():
    document = {
        "schema_version": "feature_selection_envelope.v1",
        "run": {
            "run_id": "fs-phase1-20261005",
            "campaign_sha256": HEX_A,
            "code_sha256": HEX_B,
            "input_sha256": HEX_C,
            "inventory_sha256": HEX_D,
            "created_at": "2026-10-05T12:00:00Z",
        },
        "rows": {
            "sampling_quality": [sealed({
                "feature_id": "market.eth.close",
                "split": "train",
                "metric_name": "missing_timestamp_count",
                "metric_value": 2,
                "state": "MEASURED",
                "unit": "count",
            })],
            "variable_profiles": [sealed({
                "feature_id": "market.eth.close",
                "split": "train",
                "metric_name": "median",
                "metric_value": 2048.5,
                "state": "MEASURED",
                "unit": "USD",
            })],
            "information_metrics": [sealed({
                "feature_id": "market.eth.close",
                "target_id": "eth.return",
                "horizon": 24,
                "split": "train",
                "metric_name": "mutual_information",
                "metric_value": 0.21,
                "state": "MEASURED",
            })],
            "pair_relations": [sealed({
                "feature_id": "market.eth.close",
                "target_id": "eth.return",
                "horizon": 24,
                "split": "train",
                "lag": 1,
                "metric_name": "spearman",
                "metric_value": 0.17,
                "state": "MEASURED",
            })],
            "causal_evidence": [sealed({
                "feature_id": "market.eth.close",
                "target_id": "eth.return",
                "horizon": 24,
                "split": "train",
                "rung": 2,
                "estimand": "ATE",
                "estimator": "aipw",
                "state": "IDENTIFIED",
                "effect": 0.012,
                "lower": 0.004,
                "upper": 0.020,
                "support_n": 1200,
                "assumptions": ["exchangeability", "positivity"],
                "adjustment_set": ["hour", "day_of_week"],
                "evidence_sha256": HEX_A,
            })],
            "selection_decisions": [sealed({
                "feature_id": "market.eth.close",
                "target_id": "eth.return",
                "horizon": 24,
                "method": "causal_ladder_fdr",
                "score": 0.83,
                "rank": 1,
                "decision": "SELECTED",
                "rule": "q_value <= 0.05 and stable_sign",
                "evidence_sha256": HEX_A,
            })],
        },
    }
    document["envelope_sha256"] = digest(document)
    return document


@pytest.fixture()
def warehouse(tmp_path):
    backend = SqliteStore()
    backend.set_params(database=str(tmp_path / "cube.sqlite"), store_id="cube")
    app = create_app(
        load(None, {"service_token": TOKEN, "store_id": "cube"}),
        backend,
        {"capabilities": list(backend.capabilities())},
    )
    app.config.update(TESTING=True)
    return backend, app.test_client()


def post(client, document):
    return client.post(
        "/api/v2/feature-selection-envelopes",
        json={"document": document},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def scalar(backend, sql):
    return backend.query(sql)["rows"][0]["n"]


def test_one_envelope_atomically_loads_every_family_and_a_receipt(warehouse):
    backend, client = warehouse

    response = post(client, envelope())

    assert response.status_code == 201
    assert response.get_json() == {
        "already_stored": False,
        "envelope_sha256": envelope()["envelope_sha256"],
        "row_count": 6,
        "stored": True,
    }
    for relation in (
        "df_fact_sampling_quality",
        "df_fact_variable_profile",
        "df_fact_information_metric",
        "df_fact_pair_relation",
        "df_fact_feature_causal_evidence",
        "df_fact_feature_selection_decision",
        "df_fact_feature_selection_load_receipt",
    ):
        assert scalar(backend, f"SELECT count(*) AS n FROM {relation}") == 1


def test_identical_replay_is_a_noop_but_a_contradiction_rolls_back_everything(warehouse):
    backend, client = warehouse
    original = envelope()
    assert post(client, original).status_code == 201
    assert post(client, original).status_code == 200

    changed = copy.deepcopy(original)
    changed["rows"]["sampling_quality"].append(sealed({
        "feature_id": "market.eth.close",
        "split": "train",
        "metric_name": "duplicate_timestamp_count",
        "metric_value": 0,
        "state": "MEASURED",
        "unit": "count",
    }))
    row = changed["rows"]["variable_profiles"][0]
    row["metric_value"] = 999.0
    row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    changed["envelope_sha256"] = digest({k: v for k, v in changed.items() if k != "envelope_sha256"})
    response = post(client, changed)

    assert response.status_code == 400
    assert "contradicts" in response.get_json()["error"]
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_sampling_quality") == 1
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_variable_profile") == 1
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_feature_selection_load_receipt") == 1


def test_concurrent_identical_replays_commit_exactly_once(warehouse):
    backend, _ = warehouse
    document = envelope()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: backend.write_feature_selection_envelope(document), range(8)
        ))

    assert sum(result["stored"] for result in results) == 1
    assert sum(result["already_stored"] for result in results) == 7
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_feature_selection_load_receipt") == 1
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_feature_causal_evidence") == 1


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda doc: doc.update(schema_version="feature_selection_envelope.v0"), "schema_version"),
        (lambda doc: doc["run"].pop("inventory_sha256"), "inventory_sha256"),
        (lambda doc: doc["rows"].pop("causal_evidence"), "causal_evidence"),
        (lambda doc: doc["rows"]["variable_profiles"][0].update(metric_value=math.inf), "finite"),
        (lambda doc: doc["rows"]["causal_evidence"][0].update(rung=4), "rung"),
        (lambda doc: doc["rows"]["selection_decisions"][0].update(decision="MAYBE"), "decision"),
        (lambda doc: doc["rows"]["sampling_quality"][0].update(metric_value=None), "finite"),
        (lambda doc: doc["rows"]["sampling_quality"][0].update(secret_score=1), "unknown fields"),
        (lambda doc: doc["run"].update(created_at="2026-10-05 12:00:00"), "timezone"),
        (lambda doc: doc.update(envelope_sha256="0" * 64), "envelope_sha256"),
    ],
)
def test_invalid_envelopes_are_rejected_before_any_write(warehouse, mutate, expected):
    backend, client = warehouse
    document = envelope()
    mutate(document)
    response = post(client, document)

    assert response.status_code == 400
    assert expected in response.get_json()["error"]
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_feature_selection_load_receipt") == 0


def test_a_row_digest_must_cover_the_stored_row(warehouse):
    backend, client = warehouse
    document = envelope()
    document["rows"]["pair_relations"][0]["metric_value"] = -0.99
    document["envelope_sha256"] = digest(
        {k: v for k, v in document.items() if k != "envelope_sha256"}
    )

    response = post(client, document)

    assert response.status_code == 400
    assert "row_sha256" in response.get_json()["error"]
    assert scalar(backend, "SELECT count(*) AS n FROM df_fact_pair_relation") == 0


def test_dashboard_coverage_and_failure_views_are_read_only_and_queryable(warehouse):
    backend, client = warehouse
    assert post(client, envelope()).status_code == 201

    coverage = backend.query(
        "SELECT feature_id, profile_rows, causal_rows, decision_rows "
        "FROM df_feature_selection_coverage"
    )["rows"]
    assert coverage == [{
        "feature_id": "market.eth.close",
        "profile_rows": 4,
        "causal_rows": 1,
        "decision_rows": 1,
    }]
    dashboard = backend.query(
        "SELECT run_id, total_features, selected_features, failed_rows "
        "FROM df_feature_selection_dashboard"
    )["rows"]
    assert dashboard == [{
        "run_id": "fs-phase1-20261005",
        "total_features": 1,
        "selected_features": 1,
        "failed_rows": 0,
    }]
    assert backend.query("SELECT * FROM df_feature_selection_failures")["rows"] == []


def test_route_requires_auth_and_an_explicit_backend_capability(tmp_path):
    backend = SqliteStore()
    backend.set_params(database=str(tmp_path / "cube.sqlite"))
    app = create_app(load(None, {"service_token": TOKEN}), backend,
                     {"capabilities": list(backend.capabilities())})
    client = app.test_client()
    assert client.post("/api/v2/feature-selection-envelopes", json={}).status_code == 401

    class WithoutFeatureSelection(SqliteStore):
        declared_capabilities = tuple(
            item for item in SqliteStore.declared_capabilities
            if item != "write_feature_selection_envelope"
        )

    backend = WithoutFeatureSelection()
    backend.set_params(database=str(tmp_path / "other.sqlite"))
    app = create_app(load(None, {"service_token": TOKEN}), backend,
                     {"capabilities": list(backend.capabilities())})
    response = app.test_client().post(
        "/api/v2/feature-selection-envelopes",
        json={"document": envelope()},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 422
