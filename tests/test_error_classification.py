"""L4 (predictor order, 2026-09-17): at the HTTP boundary a document the store cannot read is a
typed 400/422, a defect inside the store is a 500 named as such, and only a store that is
unreachable or locked is a 503. Nothing is disguised, the message is bounded and clean."""
from __future__ import annotations

import pytest

from data_warehouse_service.config import load
from data_warehouse_service.errors import (StorageUnreachable, UnsupportedError, bounded,
                                           classify)
from data_warehouse_service.testing.sqlite_store import SqliteStore
from data_warehouse_service.web import create_app


class Faulty(SqliteStore):
    """A backend whose envelope writer fails the way the rule asks."""
    mode = "defect"

    def capabilities(self):
        return set(super().capabilities()) | {"write_terminal"}

    def write_foundation_envelope(self, document):
        if self.mode == "invalid":
            raise ValueError("data_consumed.datasets[0] must be an object")
        if self.mode == "refusal":
            raise SystemExit("REFUSED: data_consumed.datasets[0] must be an object")
        if self.mode == "defect":
            return document["data_consumed"]["datasets"][0].get("id")   # AttributeError on str
        if self.mode == "unreachable":
            raise StorageUnreachable("could not connect to the cube")
        if self.mode == "locked":
            raise RuntimeError("IO Error: Could not set lock on file: conflicting lock is held")
        if self.mode == "secret":
            raise RuntimeError("token Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ leaked")
        return {"units": 0}


@pytest.fixture()
def faulty(tmp_path):
    backend = Faulty()
    backend.set_params(database=str(tmp_path / "cube.sqlite"), store_id="cube")
    config = load(None, {"service_token": "t", "store_id": "cube"})
    app = create_app(config, backend, {"distribution": "data-warehouse-service",
                                       "version": "0.1.0",
                                       "capabilities": list(backend.capabilities())})
    return backend, app.test_client()


def post(client, doc):
    return client.post("/api/v2/foundation-envelopes", json={"document": doc},
                       headers={"Authorization": "Bearer t"})


def test_classify_separates_input_defect_and_unavailability():
    assert classify(ValueError("x"))[:2] == (400, "INVALID_INPUT")
    assert classify(SystemExit("REFUSED: x"))[:2] == (400, "INVALID_INPUT")
    assert classify(UnsupportedError("x"))[:2] == (422, "UNSUPPORTED")
    assert classify(StorageUnreachable("x"))[:2] == (503, "STORE_UNAVAILABLE")
    assert classify(RuntimeError("database is locked"))[:2] == (503, "STORE_UNAVAILABLE")
    assert classify(AttributeError("'str' object has no attribute 'get'"))[:2] == (500, "INTERNAL_DEFECT")
    assert classify(KeyError("identity"))[:2] == (500, "INTERNAL_DEFECT")


def test_bounded_messages_carry_no_bearer_token_and_are_short():
    text = bounded("Bearer " + "A" * 60 + " and " + "x" * 500)
    assert "Bearer <redacted>" in text and "A" * 60 not in text and len(text) <= 300


def test_an_invalid_document_is_a_permanent_400(faulty):
    backend, client = faulty
    backend.mode = "invalid"
    answer = post(client, {"data_consumed": {"datasets": ["u"]}})
    assert answer.status_code == 400
    assert answer.get_json()["class"] == "INVALID_INPUT"


def test_an_internal_defect_is_a_500_named_as_such_never_a_503(faulty):
    backend, client = faulty
    backend.mode = "defect"
    answer = post(client, {"data_consumed": {"datasets": ["u"]}})
    assert answer.status_code == 500
    body = answer.get_json()
    assert body["class"] == "INTERNAL_DEFECT" and "AttributeError" in body["error"]


@pytest.mark.parametrize("mode", ["unreachable", "locked"])
def test_real_unavailability_stays_a_503(faulty, mode):
    backend, client = faulty
    backend.mode = mode
    answer = post(client, {"data_consumed": {}})
    assert answer.status_code == 503
    assert answer.get_json()["class"] == "STORE_UNAVAILABLE"


def test_a_diagnosis_never_leaks_a_secret(faulty):
    backend, client = faulty
    backend.mode = "secret"
    answer = post(client, {})
    assert answer.status_code == 500
    assert "abcdefghijklmnopqrstuvwxyz" not in answer.get_json()["error"]


def test_the_loaders_typed_refusal_a_systemexit_is_a_400_not_a_dropped_connection(faulty):
    backend, client = faulty
    backend.mode = "refusal"
    answer = post(client, {})
    assert answer.status_code == 400 and answer.get_json()["class"] == "INVALID_INPUT"
