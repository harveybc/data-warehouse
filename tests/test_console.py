"""The operator console: inventory, schema, a bounded query, and a pending configuration."""

from __future__ import annotations

import json

import pytest

from data_warehouse_service.config import load
from data_warehouse_service.operator_config import editable_config, write_pending
from data_warehouse_service.testing.sqlite_store import SqliteStore
from data_warehouse_service.web import create_app

SECRET = "do-not-render-this"


@pytest.fixture()
def app(tmp_path):
    backend = SqliteStore()
    backend.set_params(database=str(tmp_path / "cube.sqlite"), store_id="cube")
    config = load(None, {"service_token": SECRET, "store_id": "cube", "title": "cube console",
                         "operator_config_path": str(tmp_path / "pending.json"),
                         "backend": {"entry_point": "sqlite_store",
                                     "distribution": "data-warehouse-service",
                                     "settings": {"database": str(tmp_path / "cube.sqlite")}}})
    application = create_app(config, backend, {"entry_point": "sqlite_store",
                                               "distribution": "data-warehouse-service",
                                               "version": "0.1.0", "module": "m:backend",
                                               "capabilities": list(backend.capabilities()),
                                               "settings_sha256": "b" * 64})
    application.config.update(TESTING=True)
    return application, config, tmp_path


def test_the_inventory_lists_the_relations_and_names_the_provider(app):
    application, _config, _tmp = app
    body = application.test_client().get("/").get_data(as_text=True)
    assert "gov_terminal" in body and "data-warehouse-service" in body
    assert 'name="viewport"' in body and "width=device-width" in body


def test_the_schema_page_shows_columns_and_refuses_an_unknown_relation(app):
    application, _config, _tmp = app
    client = application.test_client()
    body = client.get("/relation?relation=gov_terminal").get_data(as_text=True)
    assert "campaign_sha256" in body and "terminal_sha256" in body
    missing = client.get("/relation?relation=absent").get_data(as_text=True)
    assert "unknown relation" in missing


def test_a_query_shows_its_result_table_not_only_a_row_count(app):
    application, _config, _tmp = app
    page = application.test_client().post("/query", data={"sql": "SELECT 1 AS one, 'x' AS two"})
    body = page.get_data(as_text=True)
    assert "<td" in body and ">1<" in body and ">x<" in body
    assert "1 row(s)" in body


def test_a_write_statement_is_refused_in_the_console_too(app):
    application, _config, _tmp = app
    body = application.test_client().post("/query", data={"sql": "DELETE FROM gov_metric"}).get_data(as_text=True)
    assert "only SELECT is served" in body


def test_a_query_result_is_escaped(app):
    application, _config, _tmp = app
    body = application.test_client().post(
        "/query", data={"sql": "SELECT '<script>alert(1)</script>' AS payload"}).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_console_never_renders_a_secret(app):
    application, _config, _tmp = app
    for path in ("/", "/settings", "/relation?relation=gov_metric", "/query"):
        assert SECRET not in application.test_client().get(path).get_data(as_text=True), path


def test_saving_writes_a_pending_file_and_changes_nothing_active(app):
    application, config, tmp_path = app
    before = json.dumps(config, sort_keys=True, default=str)
    proposal = editable_config(config)
    proposal["max_rows"] = 25
    page = application.test_client().post("/settings", data={"configuration": json.dumps(proposal)})
    assert "pending" in page.get_data(as_text=True)
    assert json.loads((tmp_path / "pending.json").read_text())["max_rows"] == 25
    assert json.dumps(config, sort_keys=True, default=str) == before


def test_invalid_configuration_is_refused_and_writes_nothing(app):
    application, _config, tmp_path = app
    client = application.test_client()
    for text, reason in [("{", "invalid JSON"),
                         ('{"backend": {"entry_point": "a"}}', "entry_point and distribution"),
                         ('{"backend": {"entry_point": "a", "distribution": "b"}, "max_rows": -1}',
                          "max_rows must be a positive integer")]:
        assert reason in client.post("/settings", data={"configuration": text}).get_data(as_text=True)
    assert not (tmp_path / "pending.json").exists()


def test_the_pending_write_is_atomic(tmp_path):
    destination = tmp_path / "nested" / "pending.json"
    write_pending(destination, {"a": 1})
    assert json.loads(destination.read_text()) == {"a": 1}
    assert not list(destination.parent.glob(".pending-*"))


def test_an_unreachable_store_is_reported_on_the_page(tmp_path):
    class Broken(SqliteStore):
        def discover(self):
            raise RuntimeError("cube unreachable")

    backend = Broken()
    backend.set_params(database=str(tmp_path / "x.sqlite"))
    application = create_app(load(None, {"store_id": "broken"}), backend,
                             {"capabilities": list(backend.capabilities())})
    application.config.update(TESTING=True)
    assert "cube unreachable" in application.test_client().get("/").get_data(as_text=True)
