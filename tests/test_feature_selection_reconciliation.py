"""Acceptance tests for authenticated phase-1 warehouse reconciliation."""

from __future__ import annotations

import copy

import pytest

from data_warehouse_service.config import load
from data_warehouse_service.feature_selection import digest
from data_warehouse_service.testing.sqlite_store import SqliteStore
from data_warehouse_service.web import create_app


TOKEN = "feature-selection-test-token"
HEX_E = "e" * 64
HEX_F = "f" * 64


def sealed(row):
    result = copy.deepcopy(row)
    result["row_sha256"] = digest(result)
    return result


def envelope():
    document = {
        "schema_version": "feature_selection_envelope.v1",
        "run": {
            "run_id": "fs-phase1-reconcile-20261005",
            "campaign_sha256": "a" * 64,
            "code_sha256": "b" * 64,
            "input_sha256": "c" * 64,
            "inventory_sha256": "d" * 64,
            "created_at": "2026-10-05T12:00:00Z",
        },
        "rows": {
            "sampling_quality": [sealed({
                "feature_id": "market.eth.close", "split": "train",
                "metric_name": "missing_timestamp_count", "metric_value": 0,
                "state": "MEASURED", "unit": "count",
            })],
            "variable_profiles": [sealed({
                "feature_id": "market.eth.close", "split": "train",
                "metric_name": "median", "metric_value": 2048.5,
                "state": "MEASURED", "unit": "USD",
            })],
            "information_metrics": [],
            "pair_relations": [],
            "causal_evidence": [],
            "selection_decisions": [],
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


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def identity(document, *, feature_id="market.eth.close"):
    return {
        "feature_id": feature_id,
        "feature_key": feature_id.replace(".", "_"),
        "terminal_state": "COMPLETED",
        "terminal_sha256": HEX_E,
        "envelope_sha256": document["envelope_sha256"],
        "warehouse_receipt_sha256": HEX_F,
    }


def request_for(identities):
    document = {
        "schema": "phase1.warehouse_reconciliation_request.v1",
        "population_id": "ETH",
        "plan_sha256": "1" * 64,
        "expected_count": len(identities),
        "identities_sha256": digest(identities),
        "identities": identities,
        "authentication_profile": "data-gov-service-token",
    }
    document["request_sha256"] = digest(document)
    return document


def duplicate_identity(request):
    request["identities"].append(copy.deepcopy(request["identities"][0]))
    request["expected_count"] = len(request["identities"])
    request["identities_sha256"] = digest(request["identities"])


def post(client, document, *, authenticated=True):
    return client.post(
        "/api/v2/feature-selection-reconcile",
        json=document,
        headers=auth() if authenticated else {},
    )


def test_reconciliation_queries_the_store_and_binds_the_complete_request(warehouse):
    backend, client = warehouse
    stored = envelope()
    backend.write_feature_selection_envelope(stored)
    request = request_for([identity(stored)])

    response = post(client, request)

    assert response.status_code == 200
    body = response.get_json()
    assert body["schema"] == "phase1.warehouse_reconciliation.v1"
    assert body["state"] == "RECONCILED"
    assert body["population_id"] == request["population_id"]
    assert body["plan_sha256"] == request["plan_sha256"]
    assert body["expected_count"] == request["expected_count"]
    assert body["identities_sha256"] == request["identities_sha256"]
    assert body["authentication_profile"] == request["authentication_profile"]
    assert body["request_sha256"] == request["request_sha256"]
    assert body["observed_count"] == 1
    assert body["observed_identities"] == request["identities"]
    assert body["observed_identities_sha256"] == digest(request["identities"])
    assert body["complete"] is True
    assert body["contradictions"] == []
    assert body["reconciliation_sha256"] == digest(
        {key: value for key, value in body.items() if key != "reconciliation_sha256"}
    )


def test_reconciliation_requires_auth_and_an_explicit_capability(warehouse, tmp_path):
    _, client = warehouse
    assert post(client, request_for([]), authenticated=False).status_code == 401

    class WithoutReconciliation(SqliteStore):
        declared_capabilities = tuple(
            capability
            for capability in SqliteStore.declared_capabilities
            if capability != "reconcile_feature_selection"
        )

    backend = WithoutReconciliation()
    backend.set_params(database=str(tmp_path / "other.sqlite"))
    app = create_app(
        load(None, {"service_token": TOKEN}),
        backend,
        {"capabilities": list(backend.capabilities())},
    )
    response = app.test_client().post(
        "/api/v2/feature-selection-reconcile",
        json=request_for([]),
        headers=auth(),
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda request: request.update(schema="wrong"), "schema"),
        (lambda request: request.update(expected_count=True), "expected_count"),
        (lambda request: request.update(expected_count=0, identities=[]), "positive"),
        (lambda request: request.update(expected_count=2), "expected_count"),
        (lambda request: request.update(identities_sha256="0" * 64), "identities_sha256"),
        (lambda request: request.update(request_sha256="0" * 64), "request_sha256"),
        (lambda request: request.update(authentication_profile=""), "authentication_profile"),
        (duplicate_identity, "duplicates"),
    ],
)
def test_invalid_or_duplicate_expected_populations_are_rejected(
    warehouse, mutate, expected
):
    _, client = warehouse
    document = envelope()
    request = request_for([identity(document)])
    mutate(request)
    if "request_sha256" not in expected:
        request["request_sha256"] = digest(
            {key: value for key, value in request.items() if key != "request_sha256"}
        )

    response = post(client, request)

    assert response.status_code == 400
    assert expected in response.get_json()["error"]


def test_missing_envelope_rejects_an_incomplete_population(warehouse):
    _, client = warehouse
    request = request_for([identity(envelope())])

    response = post(client, request)

    assert response.status_code == 400
    assert "incomplete" in response.get_json()["error"]


def test_feature_that_is_not_in_the_claimed_envelope_is_a_contradiction(warehouse):
    backend, client = warehouse
    stored = envelope()
    backend.write_feature_selection_envelope(stored)
    request = request_for([identity(stored, feature_id="market.eur.close")])

    response = post(client, request)

    assert response.status_code == 400
    assert "contradiction" in response.get_json()["error"]


def test_unavailable_terminal_is_bound_without_claiming_a_stored_envelope(warehouse):
    _, client = warehouse
    unavailable = {
        "feature_id": "fred.unavailable",
        "feature_key": "fred_unavailable",
        "terminal_state": "UNAVAILABLE",
        "terminal_sha256": HEX_E,
        "envelope_sha256": None,
        "warehouse_receipt_sha256": None,
    }

    response = post(client, request_for([unavailable]))

    assert response.status_code == 200
    observed = response.get_json()["observed_identities"][0]
    assert observed == unavailable
