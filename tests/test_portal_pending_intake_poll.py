"""A pending Biofield Analysis must not tear down an active Intake form."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
PORTAL_HTML = STATIC_DIR / "client-portal.html"

PORTAL = {
    "name": "Portal Test",
    "biofield_status": "pending",
    "scan_history_enabled": True,
    "scan_dates": [],
    "layers": [],
    "findings": [],
    "reorder_items": [],
    "messages": [],
    "tos_agreed": True,
    "element_backdrop_enabled": False,
}
VIEW = {
    "hub_enabled": True,
    "biofield": {"visible": False},
    "health_profile": {"enabled": False},
    "oasis": {"enabled": False},
    "cart": {"enabled": False, "count": 0},
    "remedies": {"enabled": False},
}
FORM = {
    "sections": [{
        "title": "Personal Information",
        "fields": [{"id": "favorite_color", "type": "text",
                    "label": "Describe your favorite color"}],
    }],
}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/portal/"):
            self._send(200, PORTAL_HTML.read_bytes(), "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            asset = STATIC_DIR / path.removeprefix("/static/")
            if asset.is_file():
                self._send(200, asset.read_bytes(), "application/octet-stream")
            else:
                self._send(404, {})
        elif path == "/api/intake/form":
            self._send(200, FORM)
        elif path == "/api/intake/state":
            self._send(200, {"submitted": False, "status": "none", "answers": {}})
        elif path.endswith("/view"):
            self._send(200, VIEW)
        elif path.endswith("/recommendations"):
            self._send(200, None)
        elif path.startswith("/api/portal/"):
            self._send(200, PORTAL)
        else:
            self._send(200, {})

    def do_POST(self):
        self._send(200, {"ok": True})

    def log_message(self, *_args):
        pass


@pytest.fixture()
def portal_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()


def test_pending_poll_preserves_active_intake_values(portal_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{portal_server}/portal/test-token")
        page.get_by_role("button", name="My Intake Build or continue your clinical health profile").click()
        field = page.locator('#portal-intake-host input[type="text"]')
        field.fill("Ocean blue")

        # Reproduce the six-second timer callback directly. Before the fix this
        # called load(), replaced #app, and detached/reset the live field.
        page.evaluate("pollPending()")
        page.wait_for_timeout(250)

        assert page.locator('[data-panel="intake"]').is_visible()
        assert field.input_value() == "Ocean blue"
        browser.close()


def test_pending_poll_resumes_after_leaving_intake(portal_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{portal_server}/portal/test-token")
        page.get_by_role("button", name="My Intake Build or continue your clinical health profile").click()
        before = page.evaluate("_ppPolls")
        page.evaluate("pollPending()")
        assert page.evaluate("_ppPolls") == before

        page.get_by_role("button", name="‹ Back to hub").click()
        page.evaluate("pollPending()")
        page.wait_for_timeout(250)
        assert page.evaluate("_ppPolls") == before + 1
        browser.close()
