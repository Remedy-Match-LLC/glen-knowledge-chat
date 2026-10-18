"""A Biofield invoice is editable by its client (Glen, 2026-10-13).

Linda Maher's order 225 opened with lines she could not change. This test order
carries the same products and quantities, and no client data.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

LINES = [("biofield-analysis", 1, 30000, True), ("crystalline-clarity", 1, 5600, False),
         ("moisturize", 1, 5600, False), ("clarity", 1, 5600, False),
         ("golden-book", 1, 5600, False), ("nous-energy", 1, 5600, False),
         ("ginger-lily-flower-essence-in-terrain-restore", 1, 7000, False),
         ("heart-health", 1, 5600, False), ("lymph-flow", 1, 5600, False),
         ("ei5-lymphaticsbladder-meridian", 0, 4000, False),
         ("immune-modulation", 0, 5600, False), ("transform", 1, 5600, False),
         ("microbiome", 1, 5600, False)]


@pytest.fixture
def inv(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O
    items = [{"slug": s, "name": s, "qty": q, "unit_cents": u, "line_cents": u * q,
              "source": "self" if svc else "biofield", **({"service": True} if svc else {})}
             for s, q, u, svc in LINES]
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        O.upsert_order(cx, source="in-house", external_ref="INH-TEST-225",
                       email="biofield-test@example.com", name="Test Client", items=items,
                       total_cents=89700, status="confirmed")
        cx.commit()
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: None)   # never mail Glen
    from dashboard import practitioner_portal as PP
    monkeypatch.setattr(PP, "_LOG_DB", Path(db))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), PP.create_order_invoice_token(1)


def test_the_page_offers_editing(inv):
    c, tok = inv
    o = c.get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["editable"] is True


def test_a_quantity_change_is_accepted(inv):
    c, tok = inv
    o = c.get(f"/api/invoice/{tok}").get_json()["order"]
    lines = [{"slug": l["slug"], "qty": l["qty"], "format": "bottle"} for l in o["lines"]]
    for l in lines:
        if l["slug"] == "immune-modulation":
            l["qty"] = 1
    r = c.post(f"/api/invoice/{tok}/update", json={"lines": lines})
    assert r.status_code == 200, r.get_json()
    got = {l["slug"]: l["qty"] for l in r.get_json()["order"]["lines"]}
    assert got["immune-modulation"] == 1


def test_a_product_can_be_added(inv):
    c, tok = inv
    o = c.get(f"/api/invoice/{tok}").get_json()["order"]
    lines = [{"slug": l["slug"], "qty": l["qty"], "format": "bottle"} for l in o["lines"]]
    lines.append({"slug": "vitality", "qty": 1, "format": "bottle"})
    r = c.post(f"/api/invoice/{tok}/update", json={"lines": lines})
    assert r.status_code == 200, r.get_json()
