"""DOIN archive projection routes: owner-only write, readback and reorg revocation.

A recording backend proves the host contract only (auth, capability gate, status codes).
The store semantics are proved in predictor_olap_store.doin_projection_store on throwaway
SQLite and DuckDB files.
"""

from __future__ import annotations

from data_warehouse_service.backends import CAPABILITIES, WarehouseBackendBase, check_capabilities
from data_warehouse_service.config import load
from data_warehouse_service.web import create_app

TOKEN = "doin-test-token"
ROUTES = ("write_doin_projection", "read_doin_projection", "revoke_doin_projection")


class RecordingBackend(WarehouseBackendBase):
    declared_capabilities = ("describe", *ROUTES)

    def __init__(self):
        super().__init__()
        self.calls = []

    def describe(self):
        return {"engine": "recording"}

    def write_doin_projection(self, document):
        self.calls.append(("write", document))
        if document.get("schema") != "i6n.doin_projection.write.v1":
            raise ValueError("schema must be i6n.doin_projection.write.v1")
        state = document.get("expect", "PROJECTED")
        return {"state": state, "projection_key": "1" * 64, "metrics_inserted": 2 if state == "PROJECTED" else 0}

    def read_doin_projection(self, document):
        self.calls.append(("read", document))
        return {"projections": [{"report_sha256": document.get("report_sha256"), "state": "PROJECTED"}],
                "events": [], "metrics": []}

    def revoke_doin_projection(self, document):
        self.calls.append(("revoke", document))
        return {"block_hash": document["block_hash"], "revoked": 1}


def make_client(backend):
    config = load(None, {"service_token": TOKEN, "store_id": "cube"})
    app = create_app(config, backend, {"distribution": "test", "version": "0",
                                       "capabilities": list(backend.capabilities())})
    app.config.update(TESTING=True)
    return app.test_client()


AUTH = {"Authorization": "Bearer " + TOKEN}


def test_capabilities_are_declared_vocabulary():
    for name in ROUTES:
        assert name in CAPABILITIES
    assert check_capabilities(RecordingBackend.declared_capabilities)


def test_routes_require_token_and_capability():
    client = make_client(RecordingBackend())
    assert client.post("/api/v2/doin/projections", json={}).status_code == 401
    assert client.get("/api/v2/doin/projections?report_sha256=x").status_code == 401
    assert client.post("/api/v2/doin/projections/revoke", json={}).status_code == 401

    class Bare(WarehouseBackendBase):
        declared_capabilities = ("describe",)

        def describe(self):
            return {}

    bare = make_client(Bare())
    assert bare.post("/api/v2/doin/projections", json={}, headers=AUTH).status_code == 422
    assert bare.get("/api/v2/doin/projections?report_sha256=x", headers=AUTH).status_code == 422
    assert bare.post("/api/v2/doin/projections/revoke", json={}, headers=AUTH).status_code == 422


def test_write_read_revoke_status_codes():
    backend = RecordingBackend()
    client = make_client(backend)
    created = client.post("/api/v2/doin/projections", json={"schema": "i6n.doin_projection.write.v1"},
                          headers=AUTH)
    assert created.status_code == 201 and created.get_json()["state"] == "PROJECTED"
    again = client.post("/api/v2/doin/projections",
                        json={"schema": "i6n.doin_projection.write.v1", "expect": "ALREADY_PROJECTED"},
                        headers=AUTH)
    assert again.status_code == 200
    refused = client.post("/api/v2/doin/projections", json={"schema": "other"}, headers=AUTH)
    assert refused.status_code == 400
    read = client.get("/api/v2/doin/projections?report_sha256=" + "a" * 64, headers=AUTH)
    assert read.status_code == 200 and read.get_json()["projections"][0]["report_sha256"] == "a" * 64
    revoked = client.post("/api/v2/doin/projections/revoke", json={"block_hash": "b" * 64}, headers=AUTH)
    assert revoked.status_code == 200 and revoked.get_json()["revoked"] == 1
    assert [c[0] for c in backend.calls] == ["write", "write", "write", "read", "revoke"]
