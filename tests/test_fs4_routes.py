"""The phase-4 extractibility routes: owner-only write, paged readback, reconciliation.

The host owns authentication, capability gating and status codes; the backend owns the
terminals. These tests use a recording backend, so they prove the contract of the routes and
nothing about any database. The backend semantics are proved in the predictor package
(`predictor_olap_store.fs4_store`) on throwaway DuckDB files.
"""

from __future__ import annotations

import pytest

from data_warehouse_service.backends import CAPABILITIES, WarehouseBackendBase, check_capabilities
from data_warehouse_service.config import load
from data_warehouse_service.web import create_app

TOKEN = "fs4-test-token"
ROUTES = ("write_fs4_terminals", "read_fs4_terminals", "reconcile_fs4")
PLAN = "f" * 64


class RecordingBackend(WarehouseBackendBase):
    declared_capabilities = ("describe", *ROUTES)

    def __init__(self):
        super().__init__()
        self.calls = []

    def describe(self):
        return {"engine": "recording"}

    def write_fs4_terminals(self, document):
        self.calls.append(("write", document))
        if not isinstance(document, dict) or not document.get("terminals"):
            raise ValueError("terminals must be a non-empty list")
        if document.get("plan_sha256") == "foreign":
            raise ValueError("foreign plan identity: refused before any write")
        inserted = sum(1 for t in document["terminals"] if not t.get("dup"))
        return {"schema": "fs4.warehouse_receipt.v1", "plan_sha256": document["plan_sha256"],
                "terminal_count": len(document["terminals"]), "inserted": inserted,
                "duplicates_ignored": len(document["terminals"]) - inserted, "terminals_sha256": "0" * 64,
                "receipt_sha256": "1" * 64, "backend": "recording"}

    def read_fs4_terminals(self, document):
        self.calls.append(("read", document))
        return {"terminals": [{"task": {"task_id": document.get("task_id") or "t1"}, "result": {}}],
                "count": 1, "next_after": None}

    def reconcile_fs4(self, document):
        self.calls.append(("reconcile", document))
        return {"plan_sha256": document["plan_sha256"], "stored": {"total": 3}, "complete": True}


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


def test_the_host_lists_the_capabilities_it_serves(stack):
    _, client = stack
    body = client.get("/api/v1/host", headers=auth()).get_json()
    assert all(name in body["capabilities"] for name in ROUTES)


def test_every_fs4_route_requires_the_service_token(stack):
    _, client = stack
    assert client.post("/api/v2/fs4/terminals", json={}).status_code == 401
    assert client.get(f"/api/v2/fs4/terminals?plan_sha256={PLAN}").status_code == 401
    assert client.post("/api/v2/fs4/reconcile", json={}).status_code == 401


def test_a_store_without_the_capability_answers_422_not_500():
    class Bare(WarehouseBackendBase):
        declared_capabilities = ("describe",)

        def describe(self):
            return {}
    client = make_client(Bare())
    assert client.post("/api/v2/fs4/terminals", json={"plan_sha256": PLAN}, headers=auth()).status_code == 422
    assert client.get(f"/api/v2/fs4/terminals?plan_sha256={PLAN}", headers=auth()).status_code == 422
    assert client.post("/api/v2/fs4/reconcile", json={"plan_sha256": PLAN}, headers=auth()).status_code == 422


def test_write_answers_201_on_insert_200_on_pure_replay_and_passes_the_document_through(stack):
    backend, client = stack
    body = {"plan_sha256": PLAN, "host_role": "coordinator",
            "terminals": [{"task": {"task_id": "a"}}, {"task": {"task_id": "b"}}]}
    response = client.post("/api/v2/fs4/terminals", json=body, headers=auth())
    assert response.status_code == 201
    receipt = response.get_json()
    assert receipt["inserted"] == 2 and receipt["plan_sha256"] == PLAN
    assert backend.calls[-1] == ("write", body)
    replay = client.post("/api/v2/fs4/terminals",
                         json={"document": dict(body, terminals=[{"task": {"task_id": "a"}, "dup": 1}])},
                         headers=auth())
    assert replay.status_code == 200 and replay.get_json()["duplicates_ignored"] == 1


def test_a_refused_write_is_400_with_the_reason_and_the_class(stack):
    _, client = stack
    response = client.post("/api/v2/fs4/terminals", json={"plan_sha256": "foreign", "terminals": [{}]}, headers=auth())
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["class"] == "INVALID_INPUT" and "foreign plan" in payload["error"]
    assert client.post("/api/v2/fs4/terminals", json={"plan_sha256": PLAN, "terminals": []},
                       headers=auth()).status_code == 400


def test_read_passes_query_parameters_and_omits_absent_ones(stack):
    backend, client = stack
    response = client.get("/api/v2/fs4/terminals", headers=auth(),
                          query_string={"plan_sha256": PLAN, "task_id": "t9", "arm": "RAW", "limit": "10"})
    assert response.status_code == 200
    assert response.get_json()["terminals"][0]["task"]["task_id"] == "t9"
    assert backend.calls[-1] == ("read", {"plan_sha256": PLAN, "task_id": "t9", "arm": "RAW", "limit": "10"})


def test_reconcile_answers_the_backend_document(stack):
    backend, client = stack
    expected = {"total": 6495, "by_population": {"EURUSD": 5325, "ETH": 1170}}
    response = client.post("/api/v2/fs4/reconcile", json={"plan_sha256": PLAN, "expected": expected}, headers=auth())
    assert response.status_code == 200
    assert response.get_json()["stored"]["total"] == 3
    assert backend.calls[-1] == ("reconcile", {"plan_sha256": PLAN, "expected": expected})


def test_the_phase23_routes_are_untouched(stack):
    _, client = stack
    # the recording backend declares no phase-2/3 capability: the host must answer 422, not 404/500
    assert client.post("/api/v2/fs-phase23/rows", json={"run_id": "r"}, headers=auth()).status_code == 422


def test_the_host_still_refuses_downloads(stack):
    _, client = stack
    assert client.get("/api/v2/download?resource=x", headers=auth()).status_code == 422
