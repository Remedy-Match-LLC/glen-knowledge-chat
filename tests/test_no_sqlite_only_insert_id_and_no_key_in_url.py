"""Two defects #1753 and #1754 flagged, fixed together on 2026-09-19.

1. `SELECT last_insert_rowid()` is SQLite-only. On prod Postgres it fails, which is how
   household creation returned 500. Two more routes used it: the household merge queue
   and adding a todo step. Every insert that needs its id now uses RETURNING id.
2. dashboard/biofield_invoice.py sent the master console key as `?key=` as well as in the
   X-Console-Key header, so every Biofield invoice wrote the key into Render's access log.
   Every receiving route reads the header first, so the URL copy is dropped.
"""
import ast
import json
import pathlib

from dashboard import biofield_invoice as bi

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _string_literals(path):
    tree = ast.parse((ROOT / path).read_text())
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_no_executable_sql_asks_for_last_insert_rowid():
    """Parsed, so a comment explaining the rule does not count as a use of it."""
    for path in ("app.py", "dashboard/biofield_invoice.py", "dashboard/household.py"):
        hits = [s for s in _string_literals(path) if "last_insert_rowid()" in s.lower()
                and "select" in s.lower()]
        assert hits == [], (path, hits)


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_invoice_calls_carry_the_key_in_the_header_only(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, req.headers))
        return _Resp({"ok": True, "products": [], "order_id": 1, "url": "x", "invoice_url": "x"})

    monkeypatch.setattr(bi, "_console", lambda: ("https://example.test", "SECRET-KEY"))
    monkeypatch.setattr(bi.urllib.request, "urlopen", fake_urlopen)

    bi.default_fetch_catalog()
    bi.default_create_order({"email": "a@b.c"}, [])
    bi.default_invoice_link(7)
    bi.default_publish_invoice(7)
    paths = [u.split("example.test", 1)[1] for u, _ in seen]
    assert paths == ["/api/console/biofield-portal/catalog", "/api/orders/manual",
                     "/api/console/order/7/invoice-link",
                     "/api/console/order/7/publish-to-portal"], paths
    for url, headers in seen:
        assert "SECRET-KEY" not in url and "key=" not in url, url
        assert headers.get("X-console-key") == "SECRET-KEY", headers
