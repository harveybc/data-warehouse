"""A disposable warehouse provider on SQLite: enough to build and test the host alone.

Read-only SQL, an append-only report table and an append-only terminal table with the
same idempotence rule the production cube applies (a stored digest is stored once).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

from ..backends import WarehouseBackendBase
from ..errors import StorageUnreachable
from ..feature_selection import ROW_FAMILIES, canonical_json, digest, row_identity, validate_envelope
from ..feature_selection_reconciliation import (
    reconciliation_response,
    validate_reconciliation_request,
)

FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "copy")


class SqliteStore(WarehouseBackendBase):
    declared_capabilities = ("describe", "storage", "discover", "schema", "query",
                             "write_metrics", "write_terminal", "terminal_digests",
                             "write_feature_selection_envelope", "reconcile_feature_selection")
    backend_params = {"store_id": "sqlite", "database": ":memory:", "max_rows": 10000}

    def __init__(self):
        super().__init__()
        self._connection = None
        self._write_lock = threading.RLock()

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
            self._connection.executescript(_SCHEMA)
            columns = {
                row["name"] for row in self._connection.execute(
                    "PRAGMA table_info(df_fact_feature_selection_load_receipt)"
                ).fetchall()
            }
            if "feature_ids_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE df_fact_feature_selection_load_receipt "
                    "ADD COLUMN feature_ids_json TEXT"
                )
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

    def write_feature_selection_envelope(self, document: dict):
        """Validate and commit all phase-1 fact families in one owner transaction."""
        document = validate_envelope(document)
        run = document["run"]
        envelope_sha256 = document["envelope_sha256"]
        db = self._db()
        with self._write_lock:
            existing = db.execute(
                "SELECT 1 FROM df_fact_feature_selection_load_receipt "
                "WHERE envelope_sha256 = ?", (envelope_sha256,),
            ).fetchone()
            if existing:
                return {"stored": False, "already_stored": True,
                        "envelope_sha256": envelope_sha256,
                        "row_count": sum(len(document["rows"][name]) for name in ROW_FAMILIES)}

            try:
                db.execute("BEGIN IMMEDIATE")
                self._insert_run(db, run)
                for family in ROW_FAMILIES:
                    for row in document["rows"][family]:
                        self._insert_feature_row(db, run["run_id"], family, row)
                row_count = sum(len(document["rows"][name]) for name in ROW_FAMILIES)
                feature_ids = sorted({
                    row["feature_id"]
                    for family in ROW_FAMILIES
                    for row in document["rows"][family]
                })
                db.execute(
                    "INSERT INTO df_fact_feature_selection_load_receipt "
                    "(envelope_sha256, run_id, schema_version, row_count, body_sha256, "
                    "feature_ids_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (envelope_sha256, run["run_id"], document["schema_version"], row_count,
                     digest(document), canonical_json(feature_ids)),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"stored": True, "already_stored": False,
                "envelope_sha256": envelope_sha256, "row_count": row_count}

    def reconcile_feature_selection(self, request: dict):
        """Verify every expected envelope against owner-retained receipt metadata."""
        request = validate_reconciliation_request(request)
        db = self._db()
        observed = []
        missing = []
        contradictions = []
        for identity in request["identities"]:
            if identity["terminal_state"] == "UNAVAILABLE":
                observed.append(identity)
                continue
            row = db.execute(
                "SELECT feature_ids_json FROM df_fact_feature_selection_load_receipt "
                "WHERE envelope_sha256 = ?",
                (identity["envelope_sha256"],),
            ).fetchone()
            if row is None:
                missing.append(identity["feature_id"])
                continue
            feature_ids = json.loads(row["feature_ids_json"] or "[]")
            if identity["feature_id"] not in feature_ids:
                contradictions.append(identity["feature_id"])
                continue
            observed.append(identity)
        if missing:
            raise ValueError(
                f"incomplete warehouse population; missing envelopes for {sorted(missing)}"
            )
        if contradictions:
            raise ValueError(
                "warehouse population contradiction; claimed feature is absent from its "
                f"envelope: {sorted(contradictions)}"
            )
        if len(observed) != request["expected_count"]:
            raise ValueError("incomplete warehouse population after reconciliation")
        return reconciliation_response(request, observed)

    @staticmethod
    def _insert_run(db, run):
        run_sha256 = digest(run)
        existing = db.execute(
            "SELECT run_sha256 FROM df_dim_feature_selection_run WHERE run_id = ?",
            (run["run_id"],),
        ).fetchone()
        if existing:
            if existing["run_sha256"] != run_sha256:
                raise ValueError(f"run_id {run['run_id']!r} contradicts its stored identity")
            return
        db.execute(
            "INSERT INTO df_dim_feature_selection_run "
            "(run_id, run_sha256, campaign_sha256, code_sha256, input_sha256, "
            "inventory_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run["run_id"], run_sha256, run["campaign_sha256"], run["code_sha256"],
             run["input_sha256"], run["inventory_sha256"], run["created_at"]),
        )

    @staticmethod
    def _insert_feature_row(db, run_id, family, row):
        table = _FAMILY_TABLES[family]
        identity = row_identity(run_id, family, row)
        existing = db.execute(
            f"SELECT row_sha256 FROM {table} WHERE row_identity_sha256 = ?", (identity,),
        ).fetchone()
        if existing:
            if existing["row_sha256"] != row["row_sha256"]:
                raise ValueError(
                    f"{family} identity {identity} contradicts its stored row"
                )
            return

        if family in {"sampling_quality", "variable_profiles"}:
            db.execute(
                f"INSERT INTO {table} (row_identity_sha256, row_sha256, run_id, feature_id, "
                "split, metric_name, metric_value, state, unit, population_id, fold) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, row["row_sha256"], run_id, row["feature_id"], row["split"],
                 row["metric_name"], row.get("metric_value"), row["state"], row.get("unit"),
                 row.get("population_id"), row.get("fold")),
            )
        elif family == "information_metrics":
            db.execute(
                f"INSERT INTO {table} (row_identity_sha256, row_sha256, run_id, feature_id, "
                "target_id, horizon, split, metric_name, metric_value, state, population_id, fold) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, row["row_sha256"], run_id, row["feature_id"], row["target_id"],
                 row["horizon"], row["split"], row["metric_name"], row.get("metric_value"),
                 row["state"], row.get("population_id"), row.get("fold")),
            )
        elif family == "pair_relations":
            db.execute(
                f"INSERT INTO {table} (row_identity_sha256, row_sha256, run_id, feature_id, "
                "target_id, horizon, split, lag, metric_name, metric_value, state, "
                "population_id, fold) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, row["row_sha256"], run_id, row["feature_id"], row["target_id"],
                 row["horizon"], row["split"], row["lag"], row["metric_name"],
                 row.get("metric_value"), row["state"], row.get("population_id"), row.get("fold")),
            )
        elif family == "causal_evidence":
            db.execute(
                f"INSERT INTO {table} (row_identity_sha256, row_sha256, run_id, feature_id, "
                "target_id, horizon, split, rung, estimand, estimator, state, effect, lower_bound, "
                "upper_bound, support_n, assumptions_json, adjustment_set_json, evidence_sha256, "
                "population_id, fold) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, row["row_sha256"], run_id, row["feature_id"], row["target_id"],
                 row["horizon"], row["split"], row["rung"], row["estimand"], row["estimator"],
                 row["state"], row.get("effect"), row.get("lower"), row.get("upper"),
                 row["support_n"], canonical_json(row["assumptions"]),
                 canonical_json(row["adjustment_set"]), row["evidence_sha256"],
                 row.get("population_id"), row.get("fold")),
            )
        else:
            db.execute(
                f"INSERT INTO {table} (row_identity_sha256, row_sha256, run_id, feature_id, "
                "target_id, horizon, method, score, rank, decision, rule, evidence_sha256, "
                "population_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, row["row_sha256"], run_id, row["feature_id"], row["target_id"],
                 row["horizon"], row["method"], row.get("score"), row.get("rank"),
                 row["decision"], row["rule"], row["evidence_sha256"],
                 row.get("population_id")),
            )


def backend():
    return SqliteStore()


_FAMILY_TABLES = {
    "sampling_quality": "df_fact_sampling_quality",
    "variable_profiles": "df_fact_variable_profile",
    "information_metrics": "df_fact_information_metric",
    "pair_relations": "df_fact_pair_relation",
    "causal_evidence": "df_fact_feature_causal_evidence",
    "selection_decisions": "df_fact_feature_selection_decision",
}


_COMMON_PROFILE_COLUMNS = """
    row_identity_sha256 TEXT PRIMARY KEY,
    row_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    feature_id TEXT NOT NULL,
    split TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    state TEXT NOT NULL,
    unit TEXT,
    population_id TEXT,
    fold TEXT
"""


_SCHEMA = f"""
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS gov_metric (
    report_sha256 TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gov_terminal (
    terminal_sha256 TEXT PRIMARY KEY,
    campaign_sha256 TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS df_dim_feature_selection_run (
    run_id TEXT PRIMARY KEY,
    run_sha256 TEXT NOT NULL UNIQUE,
    campaign_sha256 TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    inventory_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS df_fact_sampling_quality ({_COMMON_PROFILE_COLUMNS});
CREATE TABLE IF NOT EXISTS df_fact_variable_profile ({_COMMON_PROFILE_COLUMNS});
CREATE TABLE IF NOT EXISTS df_fact_information_metric (
    row_identity_sha256 TEXT PRIMARY KEY,
    row_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    feature_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    split TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    state TEXT NOT NULL,
    population_id TEXT,
    fold TEXT
);
CREATE TABLE IF NOT EXISTS df_fact_pair_relation (
    row_identity_sha256 TEXT PRIMARY KEY,
    row_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    feature_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    split TEXT NOT NULL,
    lag INTEGER NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    state TEXT NOT NULL,
    population_id TEXT,
    fold TEXT
);
CREATE TABLE IF NOT EXISTS df_fact_feature_causal_evidence (
    row_identity_sha256 TEXT PRIMARY KEY,
    row_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    feature_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    split TEXT NOT NULL,
    rung INTEGER NOT NULL CHECK (rung BETWEEN 1 AND 3),
    estimand TEXT NOT NULL,
    estimator TEXT NOT NULL,
    state TEXT NOT NULL,
    effect REAL,
    lower_bound REAL,
    upper_bound REAL,
    support_n INTEGER NOT NULL,
    assumptions_json TEXT NOT NULL,
    adjustment_set_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    population_id TEXT,
    fold TEXT
);
CREATE TABLE IF NOT EXISTS df_fact_feature_selection_decision (
    row_identity_sha256 TEXT PRIMARY KEY,
    row_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    feature_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    method TEXT NOT NULL,
    score REAL,
    rank INTEGER,
    decision TEXT NOT NULL CHECK (decision IN ('SELECTED','REJECTED','NEUTRAL','UNAVAILABLE')),
    rule TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    population_id TEXT
);
CREATE TABLE IF NOT EXISTS df_fact_feature_selection_load_receipt (
    envelope_sha256 TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES df_dim_feature_selection_run(run_id),
    schema_version TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    body_sha256 TEXT NOT NULL,
    feature_ids_json TEXT,
    stored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW IF NOT EXISTS df_feature_profile_current AS
SELECT run_id, feature_id, split, metric_name, metric_value, state, 'sampling_quality' AS family,
       row_sha256
FROM df_fact_sampling_quality
UNION ALL
SELECT run_id, feature_id, split, metric_name, metric_value, state, 'variable_profiles', row_sha256
FROM df_fact_variable_profile
UNION ALL
SELECT run_id, feature_id, split, metric_name, metric_value, state, 'information_metrics', row_sha256
FROM df_fact_information_metric
UNION ALL
SELECT run_id, feature_id, split, metric_name, metric_value, state, 'pair_relations', row_sha256
FROM df_fact_pair_relation;

CREATE VIEW IF NOT EXISTS df_feature_causal_ladder_current AS
SELECT * FROM df_fact_feature_causal_evidence;
CREATE VIEW IF NOT EXISTS df_feature_selection_current AS
SELECT * FROM df_fact_feature_selection_decision;

CREATE VIEW IF NOT EXISTS df_feature_selection_coverage AS
WITH profile AS (
    SELECT run_id, feature_id, count(*) AS profile_rows FROM df_feature_profile_current
    GROUP BY run_id, feature_id
), causal AS (
    SELECT run_id, feature_id, count(*) AS causal_rows FROM df_fact_feature_causal_evidence
    GROUP BY run_id, feature_id
), decisions AS (
    SELECT run_id, feature_id, count(*) AS decision_rows FROM df_fact_feature_selection_decision
    GROUP BY run_id, feature_id
), features AS (
    SELECT run_id, feature_id FROM profile UNION SELECT run_id, feature_id FROM causal
    UNION SELECT run_id, feature_id FROM decisions
)
SELECT f.run_id, f.feature_id, coalesce(p.profile_rows, 0) AS profile_rows,
       coalesce(c.causal_rows, 0) AS causal_rows,
       coalesce(d.decision_rows, 0) AS decision_rows
FROM features f LEFT JOIN profile p USING (run_id, feature_id)
LEFT JOIN causal c USING (run_id, feature_id)
LEFT JOIN decisions d USING (run_id, feature_id);

CREATE VIEW IF NOT EXISTS df_feature_selection_failures AS
SELECT run_id, 'sampling_quality' AS family, feature_id, state, row_sha256
FROM df_fact_sampling_quality WHERE state IN ('FAILED','ERROR','INVALID')
UNION ALL SELECT run_id, 'variable_profiles', feature_id, state, row_sha256
FROM df_fact_variable_profile WHERE state IN ('FAILED','ERROR','INVALID')
UNION ALL SELECT run_id, 'information_metrics', feature_id, state, row_sha256
FROM df_fact_information_metric WHERE state IN ('FAILED','ERROR','INVALID')
UNION ALL SELECT run_id, 'pair_relations', feature_id, state, row_sha256
FROM df_fact_pair_relation WHERE state IN ('FAILED','ERROR','INVALID')
UNION ALL SELECT run_id, 'causal_evidence', feature_id, state, row_sha256
FROM df_fact_feature_causal_evidence WHERE state IN ('FAILED','ERROR','INVALID');

CREATE VIEW IF NOT EXISTS df_feature_selection_dashboard AS
SELECT r.run_id,
       (SELECT count(DISTINCT feature_id) FROM df_feature_selection_coverage c
        WHERE c.run_id = r.run_id) AS total_features,
       (SELECT count(DISTINCT feature_id) FROM df_fact_feature_selection_decision d
        WHERE d.run_id = r.run_id AND d.decision = 'SELECTED') AS selected_features,
       (SELECT count(*) FROM df_feature_selection_failures f
        WHERE f.run_id = r.run_id) AS failed_rows
FROM df_dim_feature_selection_run r;
"""
