"""The phase-2/3 feature-selection routes: owner-only write, paged readback, reconciliation.

The host owns authentication, capability gating and status codes; the backend owns the
rows. These tests use a recording backend, so they prove the contract of the routes and
nothing about any database. The backend semantics are proved in the predictor package
(`predictor_olap_store.fs_phase23_store`) on throwaway DuckDB files.
"""

from __future__ import annotations

import pytest

from data_warehouse_service.backends import CAPABILITIES, WarehouseBackendBase, check_capabilities
from data_warehouse_service.config import load
from data_warehouse_service.web import create_app

TOKEN = "fs-phase23-test-token"
ROUTES = ("write_fs_phase23_rows", "read_fs_phase23_rows", "reconcile_fs_phase23")


class RecordingBackend(WarehouseBackendBase):
    declared_capabilities = ("describe", *ROUTES)

    def __init__(self):
        super().__init__()
        self.calls = []

    def describe(self):
        return {"engine": "recording"}

    def write_fs_phase23_rows(self, document):
        self.calls.append(("write", document))
        if not isinstance(document, dict) or not document.get("rows"):
            raise ValueError("rows must be a non-empty list")
        if document.get("run_id") == "foreign":
            raise ValueError("foreign population identity: refused before any write")
        inserted = sum(1 for r in document["rows"] if not r.get("dup"))
        return {"schema": "fs_phase23.warehouse_receipt.v2", "run_id": document["run_id"],
                "table": document["table"], "row_count": len(document["rows"]), "inserted": inserted,
                "duplicates_ignored": len(document["rows"]) - inserted, "rows_sha256": "0" * 64,
                "receipt_sha256": "1" * 64, "backend": "recording"}

    def read_fs_phase23_rows(self, document):
        self.calls.append(("read", document))
        return {"rows": [{"row_key": "k1", "unit_id": document.get("unit_id")}], "count": 1, "next_after": None}

    def reconcile_fs_phase23(self, document):
        self.calls.append(("reconcile", document))
        return {"run_id": document["run_id"], "tables": {"feature_pair_metrics": {"count": 1, "rows_sha256": "0" * 64}}}


def make_client(backend):
    config = load(None, {"service_token": TOKEN, "store_id": "cube"})
    app = create_app(config, backend, {"distribution": "test", "version": "0",
                                       "capabilities": list(backend.capabilities())})
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def stack():
    backend = RecordingBackend()
    return backend, make_client(backend)


def test_the_three_capabilities_are_known_to_the_host():
    assert all(name in CAPABILITIES for name in ROUTES)
    assert check_capabilities(ROUTES) == ROUTES


def test_every_fs_phase23_route_requires_the_service_token(stack):
    _, client = stack
    assert client.post("/api/v2/fs-phase23/rows", json={}).status_code == 401
    assert client.get("/api/v2/fs-phase23/rows?run_id=r&table=t").status_code == 401
    assert client.post("/api/v2/fs-phase23/reconcile", json={}).status_code == 401


def test_a_store_without_the_capability_answers_422_not_500():
    class Bare(WarehouseBackendBase):
        declared_capabilities = ("describe",)

        def describe(self):
            return {}
    client = make_client(Bare())
    assert client.post("/api/v2/fs-phase23/rows", json={"run_id": "r"}, headers=auth()).status_code == 422
    assert client.get("/api/v2/fs-phase23/rows?run_id=r&table=t", headers=auth()).status_code == 422
    assert client.post("/api/v2/fs-phase23/reconcile", json={"run_id": "r"}, headers=auth()).status_code == 422


def test_write_answers_201_on_insert_200_on_pure_replay_and_passes_the_document_through(stack):
    backend, client = stack
    body = {"run_id": "phase2-eurusd:x", "table": "feature_pair_metrics", "host_role": "worker_b",
            "rows": [{"row_key": "a"}, {"row_key": "b"}]}
    response = client.post("/api/v2/fs-phase23/rows", json=body, headers=auth())
    assert response.status_code == 201
    receipt = response.get_json()
    assert receipt["inserted"] == 2 and receipt["run_id"] == "phase2-eurusd:x"
    assert backend.calls[-1] == ("write", body)
    replay = client.post("/api/v2/fs-phase23/rows", json={"document": dict(body, rows=[{"row_key": "a", "dup": 1}])},
                         headers=auth())
    assert replay.status_code == 200 and replay.get_json()["duplicates_ignored"] == 1


def test_a_refused_write_is_400_with_the_reason_and_the_class(stack):
    _, client = stack
    response = client.post("/api/v2/fs-phase23/rows", json={"run_id": "foreign", "table": "t", "rows": [{}]},
                           headers=auth())
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["class"] == "INVALID_INPUT" and "foreign population" in payload["error"]
    assert client.post("/api/v2/fs-phase23/rows", json={"run_id": "r", "table": "t", "rows": []},
                       headers=auth()).status_code == 400


def test_read_passes_query_parameters_and_omits_absent_ones(stack):
    backend, client = stack
    response = client.get("/api/v2/fs-phase23/rows", headers=auth(),
                          query_string={"run_id": "r", "table": "feature_pair_metrics", "unit_id": "u", "limit": "10"})
    assert response.status_code == 200
    assert response.get_json()["rows"][0]["unit_id"] == "u"
    assert backend.calls[-1] == ("read", {"run_id": "r", "table": "feature_pair_metrics", "unit_id": "u", "limit": "10"})


def test_reconcile_answers_the_backend_document(stack):
    backend, client = stack
    response = client.post("/api/v2/fs-phase23/reconcile", json={"run_id": "r"}, headers=auth())
    assert response.status_code == 200
    assert response.get_json()["tables"]["feature_pair_metrics"]["count"] == 1
    assert backend.calls[-1] == ("reconcile", {"run_id": "r"})


def test_the_host_still_refuses_downloads(stack):
    _, client = stack
    assert client.get("/api/v2/download?resource=x", headers=auth()).status_code == 422
