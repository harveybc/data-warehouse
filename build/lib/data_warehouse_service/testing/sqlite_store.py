"""A disposable warehouse provider on SQLite: enough to build and test the host alone.

Read-only SQL, an append-only report table and an append-only terminal table with the
same idempotence rule the production cube applies (a stored digest is stored once).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from ..backends import WarehouseBackendBase
from ..errors import StorageUnreachable

FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "copy")


class SqliteStore(WarehouseBackendBase):
    declared_capabilities = ("describe", "storage", "discover", "schema", "query",
                             "write_metrics", "write_terminal", "terminal_digests")
    backend_params = {"store_id": "sqlite", "database": ":memory:", "max_rows": 10000}

    def __init__(self):
        super().__init__()
        self._connection = None

    def source_identity(self):
        return {"kind": "in_repository", "module": __name__, "distribution": "data-warehouse-service"}

    def _db(self):
        if self._connection is None:
            try:
                self._connection = sqlite3.connect(self.params.get("database") or ":memory:",
                                                   check_same_thread=False)
            except sqlite3.Error as exc:
                raise StorageUnreachable(str(exc)) from exc
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(
                "CREATE TABLE IF NOT EXISTS gov_metric (report_sha256 TEXT PRIMARY KEY, body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS gov_terminal (terminal_sha256 TEXT PRIMARY KEY,"
                " campaign_sha256 TEXT NOT NULL, body TEXT NOT NULL);")
            self._connection.commit()
        return self._connection

    def sweep(self):
        self._db()

    def describe(self):
        return {"lake_id": self.params.get("store_id"), "title": "disposable sqlite warehouse",
                "kind": "warehouse", "engine": "sqlite", "transport": "http",
                "database": self.params.get("database")}

    def storage(self):
        rows = self._db().execute("SELECT page_count * page_size AS bytes FROM pragma_page_count(),"
                                  " pragma_page_size()").fetchone()
        return {"host_total": 0, "host_used": 0, "host_free": 0,
                "warehouse_bytes": rows["bytes"] if rows else 0,
                "root": self.params.get("database")}

    def discover(self):
        rows = self._db().execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                                  " ORDER BY name").fetchall()
        return [{"resource_id": r["name"], "kind": "relation"} for r in rows]

    def schema(self, relation):
        if not relation or not relation.replace("_", "").isalnum():
            raise ValueError("relation must be a plain identifier")
        rows = self._db().execute(f"PRAGMA table_info({relation})").fetchall()
        if not rows:
            raise FileNotFoundError(relation)
        return {"relation": relation,
                "columns": [{"name": r["name"], "type": r["type"]} for r in rows]}

    def query(self, sql: str):
        text = (sql or "").strip().rstrip(";")
        if not text:
            raise ValueError("sql is required")
        lowered = text.lower()
        if not lowered.startswith(("select", "with")):
            raise ValueError("only SELECT is served")
        if ";" in text or any(word in lowered.split() for word in FORBIDDEN):
            raise ValueError("only a single read-only statement is served")
        limit = int(self.params.get("max_rows") or 10000)
        rows = [dict(r) for r in self._db().execute(text).fetchmany(limit + 1)]
        truncated = len(rows) > limit
        return {"sql": text, "rows": rows[:limit], "truncated": truncated}

    def _append(self, table, key_column, key, body):
        db = self._db()
        existing = db.execute(f"SELECT 1 FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
        if existing:
            return {"stored": False, "already_stored": True}
        if table == "gov_terminal":
            db.execute("INSERT INTO gov_terminal (terminal_sha256, campaign_sha256, body) VALUES (?,?,?)",
                       (key, body.get("campaign_sha256") or "", json.dumps(body, sort_keys=True)))
        else:
            db.execute("INSERT INTO gov_metric (report_sha256, body) VALUES (?,?)",
                       (key, json.dumps(body, sort_keys=True)))
        db.commit()
        return {"stored": True, "already_stored": False}

    @staticmethod
    def _digest(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def write_metrics(self, report: dict):
        if not report:
            raise ValueError("report must not be empty")
        return self._append("gov_metric", "report_sha256",
                            report.get("report_sha256") or self._digest(report), report)

    def write_terminal(self, terminal: dict):
        if not terminal.get("campaign_sha256"):
            raise ValueError("a terminal names its campaign")
        return self._append("gov_terminal", "terminal_sha256",
                            terminal.get("terminal_sha256") or self._digest(terminal), terminal)

    def terminal_digests(self, campaign_sha256: str):
        if not campaign_sha256:
            raise ValueError("campaign_sha256 is required")
        rows = self._db().execute("SELECT terminal_sha256 FROM gov_terminal WHERE campaign_sha256 = ?"
                                  " ORDER BY terminal_sha256", (campaign_sha256,)).fetchall()
        return [r["terminal_sha256"] for r in rows]


def backend():
    return SqliteStore()
