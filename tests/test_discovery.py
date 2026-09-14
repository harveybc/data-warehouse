"""Plugin discovery: one named distribution, or a refusal. No fallback, ever."""

from __future__ import annotations

import pytest

from data_warehouse_service.discovery import resolve, settings_sha256
from data_warehouse_service.errors import DiscoveryRefusal
from data_warehouse_service.testing.sqlite_store import SqliteStore


class FakeDist:
    def __init__(self, name, version):
        self.name, self.version = name, version


class FakeEntry:
    def __init__(self, name, dist, version="0.1.0", value="pkg.module:Backend", factory=SqliteStore):
        self.name = name
        self.dist = FakeDist(dist, version)
        self.value = value
        self._factory = factory

    def load(self):
        return self._factory


def config(entry_point="predictor_olap", distribution="predictor-olap-store", **settings):
    return {"backend": {"entry_point": entry_point, "distribution": distribution, "settings": settings}}


def test_a_resolved_backend_records_what_was_installed():
    entries = [FakeEntry("predictor_olap", "predictor-olap-store", "2.1.0")]
    resolved = resolve(config(database="/var/lib/cube.sqlite"), source=entries)
    identity = resolved["identity"]
    assert identity["distribution"] == "predictor-olap-store"
    assert identity["version"] == "2.1.0"
    assert identity["module"] == "pkg.module:Backend"
    assert identity["settings_sha256"] == settings_sha256({"database": "/var/lib/cube.sqlite"})
    assert "query" in identity["capabilities"]
    assert resolved["backend"].params["database"] == "/var/lib/cube.sqlite"


def test_a_missing_provider_is_refused_not_guessed():
    with pytest.raises(DiscoveryRefusal, match="no installed distribution registers"):
        resolve(config(), source=[FakeEntry("something_else", "other-store")])


def test_the_entry_point_must_belong_to_the_named_distribution():
    entries = [FakeEntry("predictor_olap", "someone-elses-store")]
    with pytest.raises(DiscoveryRefusal, match="belongs to distribution"):
        resolve(config(), source=entries)


def test_two_distributions_with_the_same_name_are_a_refusal():
    entries = [FakeEntry("predictor_olap", "predictor-olap-store"),
               FakeEntry("predictor_olap", "predictor-olap-store-fork")]
    with pytest.raises(DiscoveryRefusal, match="more than one distribution"):
        resolve(config(), source=entries)


def test_one_distribution_registering_the_name_twice_is_a_refusal():
    entries = [FakeEntry("predictor_olap", "predictor-olap-store"),
               FakeEntry("predictor_olap", "predictor-olap-store")]
    with pytest.raises(DiscoveryRefusal, match="more than once"):
        resolve(config(), source=entries)


def test_configuration_without_a_distribution_is_refused():
    with pytest.raises(DiscoveryRefusal, match="names no distribution"):
        resolve({"backend": {"entry_point": "predictor_olap"}},
                source=[FakeEntry("predictor_olap", "predictor-olap-store")])


def test_configuration_without_an_entry_point_is_refused():
    with pytest.raises(DiscoveryRefusal, match="names no backend entry_point"):
        resolve({"backend": {"distribution": "predictor-olap-store"}}, source=[])


def test_a_backend_declaring_nothing_serves_nothing():
    class Silent(SqliteStore):
        declared_capabilities = ()

    with pytest.raises(DiscoveryRefusal, match="declares no capability"):
        resolve(config(), source=[FakeEntry("predictor_olap", "predictor-olap-store", factory=Silent)])


def test_an_unknown_capability_is_refused():
    from data_warehouse_service.errors import UnsupportedError

    class Inventive(SqliteStore):
        declared_capabilities = ("describe", "teleport")

    with pytest.raises(UnsupportedError, match="unknown capabilities"):
        resolve(config(), source=[FakeEntry("predictor_olap", "predictor-olap-store", factory=Inventive)])


def test_the_settings_hash_does_not_depend_on_key_order():
    assert settings_sha256({"a": 1, "b": 2}) == settings_sha256({"b": 2, "a": 1})
