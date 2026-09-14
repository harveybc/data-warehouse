"""The warehouse HTTP contract, unchanged from the OLAP host it is ported from.

Routes, status codes and bodies match what data-gov's `http_lake` and the governed
consumers already call: `/api/v1/describe`, `/storage`, `/discover`, `/query`,
`/api/v1/metrics` and `/api/v2/terminals`. What is new: the store behind them is a
provider resolved from an entry point, an undeclared operation is 422, and a store that
cannot be reached is 503 — never a refusal attributed to the store.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .auth import check_bearer, load_token
from .operator_config import editable_config, pending_config, write_pending
from .errors import HoldoutError, StorageUnreachable, UnsupportedError, WarehouseError


def create_app(config: dict, backend, identity: dict | None = None) -> Flask:
    here = Path(__file__).resolve().parent
    app = Flask(__name__, template_folder=str(here / "templates"),
                static_folder=str(here / "static"), static_url_path="/static")
    app.secret_key = config.get("secret_key") or "x"
    identity = dict(identity or {})
    capabilities = set(identity.get("capabilities") or backend.capabilities())
    if hasattr(backend, "sweep"):
        backend.sweep()

    def _auth():
        if check_bearer(request.headers.get("Authorization"), load_token(config)):
            return None
        return jsonify({"error": "unauthenticated"}), 401

    def _needs(capability):
        if capability in capabilities:
            return None
        return jsonify({"error": f"this store does not support {capability}"}), 422

    @app.get("/healthz")
    def healthz():
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/api/v1/host")
    def api_host():
        denied = _auth()
        if denied:
            return denied
        return jsonify({"kind": config.get("kind") or "warehouse",
                        "transport": config.get("transport") or "http",
                        "store_id": config.get("store_id"), "backend": identity,
                        "capabilities": sorted(capabilities)})

    @app.get("/api/v1/describe")
    def api_describe():
        denied = _auth() or _needs("describe")
        if denied:
            return denied
        meta = dict(backend.describe())
        meta.setdefault("lake_id", config.get("store_id"))
        meta.setdefault("kind", config.get("kind") or "warehouse")
        meta.setdefault("transport", config.get("transport") or "http")
        return jsonify(meta)

    @app.get("/api/v1/storage")
    def api_storage():
        denied = _auth() or _needs("storage")
        if denied:
            return denied
        return jsonify(backend.storage())

    @app.get("/api/v1/discover")
    def api_discover():
        denied = _auth() or _needs("discover")
        if denied:
            return denied
        try:
            return jsonify({"resources": backend.discover()})
        except StorageUnreachable as exc:
            return jsonify({"error": str(exc), "resources": []}), 503
        except Exception as exc:  # the store answered, badly: still not a refusal
            return jsonify({"error": str(exc), "resources": []}), 503

    @app.get("/api/v1/schema")
    def api_schema():
        denied = _auth() or _needs("schema")
        if denied:
            return denied
        try:
            return jsonify(backend.schema(request.args.get("relation") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError:
            return jsonify({"error": "unknown relation"}), 404
        except StorageUnreachable as exc:
            return jsonify({"error": str(exc)}), 503

    @app.get("/api/v1/query")
    def api_query():
        denied = _auth() or _needs("query")
        if denied:
            return denied
        try:
            return jsonify(backend.query(request.args.get("sql") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except (PermissionError, HoldoutError):
            return jsonify({"error": "holdout"}), 403
        except UnsupportedError as exc:
            return jsonify({"error": str(exc)}), 422
        except StorageUnreachable as exc:
            return jsonify({"error": str(exc)}), 503

    @app.post("/api/v1/metrics")
    def api_metrics():
        denied = _auth() or _needs("write_metrics")
        if denied:
            return denied
        report = request.get_json(silent=True)
        if not isinstance(report, dict):
            return jsonify({"error": "report must be a JSON object"}), 400
        try:
            result = backend.write_metrics(report)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"database error: {exc}"}), 503
        result["report_sha256"] = report.get("report_sha256")
        return jsonify(result), (201 if result.get("stored") else 200)

    @app.route("/api/v2/terminals", methods=["GET", "POST"])
    def api_terminals():
        denied = _auth()
        if denied:
            return denied
        if request.method == "GET":
            denied = _needs("terminal_digests")
            if denied:
                return denied
            try:
                return jsonify({"terminals": backend.terminal_digests(
                    request.args.get("campaign_sha256") or "")})
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except Exception as exc:
                return jsonify({"error": f"database error: {exc}"}), 503
        denied = _needs("write_terminal")
        if denied:
            return denied
        terminal = request.get_json(silent=True)
        if not isinstance(terminal, dict):
            return jsonify({"error": "terminal must be a JSON object"}), 400
        try:
            result = backend.write_terminal(terminal)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"database error: {exc}"}), 503
        return jsonify(result), (201 if result.get("stored") else 200)

    @app.get("/api/v1/download")
    @app.get("/api/v2/download")
    def api_download():
        """A warehouse has no file body to deliver, and says so instead of inventing one."""
        denied = _auth()
        if denied:
            return denied
        return jsonify({"error": "this store is a warehouse; it delivers query results, not files"}), 422

    # ---- operator console -------------------------------------------------
    # Inventory, schema, a bounded read-only query and a *pending* configuration. Saving
    # prepares; only the service's configuration load activates. No secret is rendered.
    def _page_context():
        return {"store_id": config.get("store_id"), "kind": config.get("kind") or "warehouse",
                "transport": config.get("transport") or "http", "identity": identity}

    @app.get("/")
    def console_home():
        meta, relations, error = {}, [], None
        try:
            if "describe" in capabilities:
                meta = backend.describe()
            if "discover" in capabilities:
                relations = backend.discover()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        return render_template("dashboard.html", meta=meta, relations=relations, error=error,
                               **_page_context())

    @app.get("/relation")
    def console_relation():
        relation = request.args.get("relation") or ""
        columns, error = [], None
        try:
            if "schema" in capabilities:
                columns = backend.schema(relation).get("columns") or []
            else:
                error = "this store does not expose schema metadata"
        except FileNotFoundError:
            error = f"unknown relation {relation!r}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        return render_template("relation.html", relation=relation, columns=columns, error=error,
                               **_page_context())

    @app.route("/query", methods=["GET", "POST"])
    def console_query():
        sql, result, error, columns = "", None, None, []
        if request.method == "POST":
            sql = request.form.get("sql") or ""
            try:
                if "query" not in capabilities:
                    raise UnsupportedError("this store does not serve queries")
                result = backend.query(sql)
                rows = result.get("rows") or []
                columns = list(rows[0].keys()) if rows else []
            except (ValueError, WarehouseError) as exc:
                error = str(exc)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        return render_template("query.html", sql=sql, result=result, columns=columns,
                               error=error, **_page_context())

    @app.route("/settings", methods=["GET", "POST"])
    def console_settings():
        pending_path = config.get("operator_config_path")
        error = saved = None
        text = json.dumps(editable_config(config), indent=1, sort_keys=True)
        if request.method == "POST":
            text = request.form.get("configuration") or ""
            try:
                proposed = pending_config(config, text)
                if not pending_path:
                    raise ValueError("this service has no operator_config_path, so a pending "
                                     "configuration has nowhere to go")
                write_pending(pending_path, proposed)
                saved = True
            except ValueError as exc:
                error = str(exc)
        return render_template("settings.html", configuration=text, error=error, saved=saved,
                               pending_path=pending_path,
                               pending_exists=bool(pending_path and Path(pending_path).is_file()),
                               **_page_context())

    return app



def serve(config: dict, backend, identity: dict | None = None) -> int:
    app = create_app(config, backend, identity)
    host = config.get("web_host") or "127.0.0.1"
    port = int(config.get("web_port") or 5061)
    print(f"data-warehouse host ({config.get('store_id')}) → http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    return 0
