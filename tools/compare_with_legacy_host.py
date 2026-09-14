#!/usr/bin/env python3
"""Design step 6: the same fixture through the legacy OLAP host and the new warehouse host.

Both hosts serve a **throwaway SQLite database**, never the production cube: the OLAP
provider supports `sqlite_path`, so this comparison needs no PostgreSQL at all and cannot
reach `predictor_olap`. Ports are chosen free, and both processes are stopped at the end.

What is compared: status code and body — including the report/terminal idempotence the
campaign protocol depends on. A difference is reported, never explained away; the two
identity routes (`describe`, `storage`) are compared separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = "parity-warehouse-token"
CAMPAIGN = "d" * 64

READ_HEADERS = ("Content-Type",)

REPORT = {"report_sha256": "e" * 64, "experiment_key": "parity", "project_key": "predictor",
          "phase_key": "parity", "received_at": "2026-09-14T00:00:00.000000+00:00",
          "lineage": "VERIFIED", "metrics": [{"metric": "MAE", "value": 0.5,
                                              "split": "test", "horizon": 1}]}
TERMINAL = {"schema": "governed_terminal.v1", "campaign_sha256": CAMPAIGN, "unit_id": "u1",
            "generation": 1, "status": "COMPLETED", "started_at": "2026-09-14T00:00:00Z",
            "finished_at": "2026-09-14T00:00:01Z", "metrics": [], "artifacts": [],
            "deliveries": [], "costs": {"wall_seconds": 1.0}, "tags": {}}

ROUTES = [("describe", "GET", "/api/v1/describe", {}, None),
          ("storage", "GET", "/api/v1/storage", {}, None),
          ("discover", "GET", "/api/v1/discover", {}, None),
          ("query", "GET", "/api/v1/query", {"sql": "SELECT 1 AS one"}, None),
          ("query_write_refused", "GET", "/api/v1/query",
           {"sql": "DELETE FROM gov_metric"}, None),
          ("query_empty", "GET", "/api/v1/query", {"sql": ""}, None),
          ("metrics_first", "POST", "/api/v1/metrics", {}, REPORT),
          ("metrics_repeat", "POST", "/api/v1/metrics", {}, REPORT),
          ("metrics_not_an_object", "POST", "/api/v1/metrics", {}, "not-an-object"),
          ("terminal_first", "POST", "/api/v2/terminals", {}, TERMINAL),
          ("terminal_repeat", "POST", "/api/v2/terminals", {}, TERMINAL),
          ("terminal_list", "GET", "/api/v2/terminals", {"campaign_sha256": CAMPAIGN}, None),
          ("terminal_list_without_campaign", "GET", "/api/v2/terminals", {}, None)]

IDENTITY_ROUTES = {"describe", "storage"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port: int, method: str, path: str, params: dict, payload=None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as handle:
            raw = handle.read()
            return {"status": handle.status, "json": _json(raw, handle.headers.get("Content-Type")),
                    "sha256": hashlib.sha256(raw).hexdigest()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {"status": exc.code, "json": _json(raw, exc.headers.get("Content-Type")),
                "sha256": hashlib.sha256(raw).hexdigest()}


def _json(body: bytes, content_type):
    if content_type and "json" in content_type:
        try:
            return json.loads(body.decode())
        except ValueError:
            return None
    return None


def wait_for(port: int, deadline: float = 40.0):
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as handle:
                if handle.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def start(argv, cwd, env, log):
    handle = log.open("wb")
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=handle, stderr=subprocess.STDOUT,
                            start_new_session=True)
    return proc, handle


def stop(proc):
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-repo", type=Path, required=True, help="predictor/olap/lake")
    parser.add_argument("--legacy-python", default=sys.executable)
    parser.add_argument("--new-python", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    legacy_port, new_port = free_port(), free_port()
    legacy_db, new_db = work / "legacy.sqlite", work / "new.sqlite"
    legacy_config = work / "legacy.json"
    legacy_config.write_text(json.dumps({"web_port": legacy_port, "sqlite_path": str(legacy_db),
                                         "lake_id": "olap_cube", "holdout_start": "2025-01-01"}))
    new_config = work / "new.json"
    new_config.write_text(json.dumps({
        "store_id": "olap_cube", "web_port": new_port, "service_token": TOKEN,
        "backend": {"entry_point": "predictor_olap", "distribution": "predictor-olap-store",
                    "settings": {"sqlite_path": str(new_db), "lake_id": "olap_cube",
                                 "holdout_start": "2025-01-01"}}}))
    env = dict(os.environ, DATA_GOV_LAKE_TOKEN=TOKEN, OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env.pop("PYTHONPATH", None)
    for name in ("PGDATABASE", "PGUSER", "PGPASSWORD", "PGHOST", "PGPORT",
                 "PGUSER_WRITE", "PGPASSWORD_WRITE"):
        env.pop(name, None)  # the comparison must not be able to reach the production cube
    legacy, legacy_log = start([args.legacy_python, "-m", "app.main", "--load_config", str(legacy_config)],
                               args.legacy_repo, env, work / "legacy.log")
    new, new_log = start([args.new_python, "-m", "data_warehouse_service.main",
                          "--load_config", str(new_config)], work, env, work / "new.log")
    report = {"schema": "warehouse_host_parity.v1", "routes": {},
              "legacy_port": legacy_port, "new_port": new_port,
              "database": "throwaway sqlite; PG* removed from the environment"}
    try:
        if not wait_for(legacy_port):
            raise SystemExit(f"the legacy host did not start: {(work / 'legacy.log').read_text()[-2000:]}")
        if not wait_for(new_port):
            raise SystemExit(f"the new host did not start: {(work / 'new.log').read_text()[-2000:]}")
        for name, method, path, params, payload in ROUTES:
            legacy_answer = request(legacy_port, method, path, params, payload)
            new_answer = request(new_port, method, path, params, payload)
            same = {"status": legacy_answer["status"] == new_answer["status"],
                    "json": legacy_answer["json"] == new_answer["json"]}
            report["routes"][name] = {"path": path, "method": method, "params": params,
                                      "legacy": legacy_answer, "new": new_answer,
                                      "identity_route": name in IDENTITY_ROUTES,
                                      "identical": all(same.values()), "same": same}
    finally:
        stop(legacy)
        stop(new)
        legacy_log.close()
        new_log.close()
    data_routes = {k: v for k, v in report["routes"].items() if not v["identity_route"]}
    report["summary"] = {"routes_compared": len(report["routes"]),
                         "data_routes": len(data_routes),
                         "data_routes_identical": sum(1 for v in data_routes.values() if v["identical"]),
                         "identity_routes_status_equal": sum(1 for k, v in report["routes"].items()
                                                             if v["identity_route"] and v["same"]["status"]),
                         "differences": sorted(k for k, v in data_routes.items() if not v["identical"])}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=1))
    return 0 if not report["summary"]["differences"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
