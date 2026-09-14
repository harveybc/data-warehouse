#!/usr/bin/env python3
"""Browser acceptance for the operator console: desktop and mobile, with evidence.

Starts the host with the disposable SQLite provider, drives a real browser over the
console pages at two viewports, and asserts what "usable on mobile" has to mean:

* every stylesheet and script the page asks for is served **by this host** and answers 200,
  so the console does not silently depend on a CDN;
* the page does not scroll horizontally at 390 px;
* the parts an operator needs are present in the rendered DOM, not just in the template.

It writes PNGs and a JSON receipt. Screenshots are evidence of what was rendered, not a
substitute for the assertions above.
"""

from __future__ import annotations

import argparse
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

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, deadline: float = 60.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as handle:
                if handle.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--chromium", default="/snap/bin/chromium",
                        help="a chromium binary; Playwright's own download is not required")
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    work = args.out / "work"
    work.mkdir(exist_ok=True)
    config = work / "console.json"
    config.write_text(json.dumps({
        "store_id": "console_demo", "title": "console demo", "web_port": port,
        "operator_config_path": str(work / "pending.json"),
        "backend": {"entry_point": "sqlite_store", "distribution": "data-warehouse-service",
                    "settings": {"store_id": "console_demo",
                                 "database": str(work / "console.sqlite")}}}))
    log = (args.out / "console.log").open("wb")
    service = subprocess.Popen([args.python, "-m", "data_warehouse_service.main", "--load_config", str(config)],
                               cwd=str(work), stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    receipt = {"schema": "console_browser_acceptance.v1", "base": "http://127.0.0.1:<port>",
               "pages": {}}
    try:
        if not wait_for(base + "/healthz"):
            raise SystemExit("the console host did not start: " + (args.out / "console.log").read_text()[-2000:])
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=args.chromium)
            for name, path, expected in [("inventory", "/", "gov_terminal"),
                                         ("schema", "/relation?relation=gov_terminal", "campaign_sha256"),
                                         ("query", "/query", "SELECT statement"),
                                         ("settings", "/settings", "pending")]:
                for label, viewport in VIEWPORTS.items():
                    context = browser.new_context(viewport=viewport)
                    page = context.new_page()
                    responses = []
                    page.on("response", lambda r: responses.append((r.url, r.status)))
                    page.goto(base + path, wait_until="load")
                    body = page.content()
                    overflow = page.evaluate(
                        "() => document.scrollingElement.scrollWidth - window.innerWidth")
                    external = [url for url, _ in responses if not url.startswith(base)]
                    failed = [(url, status) for url, status in responses if status >= 400]
                    shot = args.out / f"warehouse-{name}-{label}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    receipt["pages"][f"{name}-{label}"] = {
                        "viewport": viewport, "path": path, "requests": len(responses),
                        "external_requests": external, "failed_requests": failed,
                        "horizontal_overflow_px": overflow, "contains_expected": expected in body,
                        "screenshot": shot.name}
                    context.close()
            browser.close()
    finally:
        if service.poll() is None:
            os.killpg(os.getpgid(service.pid), signal.SIGTERM)
            try:
                service.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                os.killpg(os.getpgid(service.pid), signal.SIGKILL)
        log.close()
    problems = []
    for name, page in receipt["pages"].items():
        if page["external_requests"]:
            problems.append(f"{name}: requests outside this host {page['external_requests']}")
        if page["failed_requests"]:
            problems.append(f"{name}: failed requests {page['failed_requests']}")
        if page["horizontal_overflow_px"] > 0:
            problems.append(f"{name}: {page['horizontal_overflow_px']}px of horizontal overflow")
        if not page["contains_expected"]:
            problems.append(f"{name}: the page did not render what it is for")
    receipt["problems"] = problems
    receipt["ok"] = not problems
    (args.out / "CONSOLE_BROWSER_ACCEPTANCE.json").write_text(
        json.dumps(receipt, indent=1).replace(str(Path.home()), "~") + "\n", encoding="utf-8")
    print(json.dumps({"ok": receipt["ok"], "problems": problems,
                      "pages": sorted(receipt["pages"])}, indent=1))
    return 0 if receipt["ok"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
