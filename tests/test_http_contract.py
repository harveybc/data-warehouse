"""The warehouse HTTP contract, served by the new host over a disposable SQLite provider."""

from __future__ import annotations

import pytest

from data_warehouse_service.config import load
from data_warehouse_service.testing.sqlite_store import SqliteStore
from data_warehouse_service.web import create_app

TOKEN = "test-token"
CAMPAIGN = "a" * 64


@pytest.fixture()
def client(tmp_path):
    backend = SqliteStore()
    backend.set_params(database=str(tmp_path / "cube.sqlite"), store_id="cube")
    config = load(None, {"service_token": TOKEN, "store_id": "cube"})
    app = create_app(config, backend, {"distribution": "data-warehouse-service", "version": "0.1.0",
                                       "capabilities": list(backend.capabilities())})
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_every_api_route_requires_the_service_token(client):
    for path in ("/api/v1/describe", "/api/v1/storage", "/api/v1/discover", "/api/v1/host",
                 "/api/v1/query?sql=select+1", "/api/v2/terminals?campaign_sha256=" + CAMPAIGN):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/v1/metrics", json={}).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_describe_names_a_warehouse_not_a_lake(client):
    payload = client.get("/api/v1/describe", headers=auth()).get_json()
    assert payload["kind"] == "warehouse" and payload["engine"] == "sqlite"
    assert client.get("/api/v1/host", headers=auth()).get_json()["kind"] == "warehouse"


def test_a_warehouse_refuses_to_pretend_it_delivers_files(client):
    for path in ("/api/v1/download?resource=anything", "/api/v2/download?resource=anything"):
        response = client.get(path, headers=auth())
        assert response.status_code == 422
        assert "warehouse" in response.get_json()["error"]


def test_only_read_only_sql_is_served(client):
    assert client.get("/api/v1/query?sql=select+1+as+one", headers=auth()).get_json()["rows"] == [{"one": 1}]
    for sql in ("delete from gov_metric", "select 1; drop table gov_metric", "insert into gov_metric values (1,2)"):
        assert client.get("/api/v1/query", query_string={"sql": sql}, headers=auth()).status_code == 400
    assert client.get("/api/v1/query?sql=", headers=auth()).status_code == 400


def test_discover_and_schema(client):
    resources = client.get("/api/v1/discover", headers=auth()).get_json()["resources"]
    assert {r["resource_id"] for r in resources} == {"gov_metric", "gov_terminal"}
    columns = client.get("/api/v1/schema?relation=gov_terminal", headers=auth()).get_json()["columns"]
    assert [c["name"] for c in columns] == ["terminal_sha256", "campaign_sha256", "body"]
    assert client.get("/api/v1/schema?relation=absent", headers=auth()).status_code == 404
    assert client.get("/api/v1/schema?relation=drop%20table", headers=auth()).status_code == 400


def test_a_report_is_appended_once(client):
    report = {"report_sha256": "b" * 64, "metrics": [{"key": "MAE", "value": 1.0}]}
    first = client.post("/api/v1/metrics", json=report, headers=auth())
    assert first.status_code == 201 and first.get_json()["stored"] is True
    second = client.post("/api/v1/metrics", json=report, headers=auth())
    assert second.status_code == 200 and second.get_json()["already_stored"] is True
    assert client.post("/api/v1/metrics", data="not json", headers=auth()).status_code == 400


def test_a_terminal_is_appended_once_and_listed_by_campaign(client):
    terminal = {"terminal_sha256": "c" * 64, "campaign_sha256": CAMPAIGN, "status": "COMPLETED"}
    assert client.post("/api/v2/terminals", json=terminal, headers=auth()).status_code == 201
    assert client.post("/api/v2/terminals", json=terminal, headers=auth()).status_code == 200
    listed = client.get(f"/api/v2/terminals?campaign_sha256={CAMPAIGN}", headers=auth()).get_json()
    assert listed["terminals"] == ["c" * 64]
    assert client.post("/api/v2/terminals", json={"status": "COMPLETED"}, headers=auth()).status_code == 400
    assert client.get("/api/v2/terminals", headers=auth()).status_code == 400


def test_an_undeclared_operation_is_422(tmp_path):
    class ReadOnly(SqliteStore):
        declared_capabilities = ("describe", "query")

    backend = ReadOnly()
    backend.set_params(database=str(tmp_path / "ro.sqlite"))
    app = create_app(load(None, {"service_token": TOKEN}), backend,
                     {"capabilities": list(backend.capabilities())})
    app.config.update(TESTING=True)
    client = app.test_client()
    assert client.post("/api/v1/metrics", json={"a": 1}, headers=auth()).status_code == 422
    assert client.post("/api/v2/terminals", json={"campaign_sha256": CAMPAIGN}, headers=auth()).status_code == 422
    assert client.get("/api/v1/query?sql=select+1", headers=auth()).status_code == 200
