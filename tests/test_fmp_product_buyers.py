"""POST /api/console/fmp-product-buyers: buyer counts across all FMP clients.

Legacy Invoices.fmp12 rows carry status 'Historical'; everything else is the
current FMP file. The route must return numbers only, never an email.
"""
import json
import sqlite3

import pytest

from dashboard import fmp_orders as fo


def _seed(cx):
    fo.ensure_tables(cx)
    cx.executemany("INSERT INTO fmp_clients (id_pk,name_first,name_last,company,email,phone_res,phone_cell,phone_business) VALUES (?,?,?,?,?,?,?,?)", [
        ("1", "Ann", "A", "", "ann@x.com", "", "", ""),
        ("2  ", "Bo", "B", "", "BO@x.com ", "", "", ""),   # padded id, mixed-case email
        ("3", "Cy", "C", "", "", "", "", ""),               # no email
    ])
    cx.executemany("INSERT INTO fmp_invoices (id_pk,id_fk_client,invoice_date,status,subtotal,total,shipping,outstanding) VALUES (?,?,?,?,?,?,?,?)", [
        ("10", "1", "2001-01-01", "Historical", "", "", "", ""),
        ("11", "2.0", "2002-01-01", "Historical", "", "", "", ""),
        ("12", "3", "2003-01-01", "Historical", "", "", "", ""),
        ("13", "99", "2004-01-01", "Historical", "", "", "", ""),  # client missing
        ("500", "1", "2026-01-01", "Active", "", "", "", ""),
    ])
    cx.executemany("INSERT INTO fmp_invoice_items (id_pk,id_fk_invoice,id_fk_product,description,qty,price,ext_price) VALUES (?,?,?,?,?,?,?)", [
        ("a", "10", "", "Clear the Way 30V Scar Solution", "1", "", ""),
        ("b", "11", "", "Clear the Way - Scar Silk", "1", "", ""),
        ("c", "12", "", "Fibrolysis Factors 30V", "1", "", ""),
        ("d", "13", "", "Fibrolysis Factors", "1", "", ""),
        ("e", "500", "", "clear the way", "1", "", ""),
        ("f", "404", "", "Clear the Way", "1", "", ""),           # invoice missing
        ("g", "10", "", "Lens-Zyme", "1", "", ""),
    ])
    cx.commit()


PRODUCTS = {"Clear the Way": ["clear the way"], "Scar Silk": ["scar silk"],
            "Fibrolysis Factors": ["fibrolysis"]}
GROUPS = {"scar": ["Clear the Way", "Scar Silk"], "fibroid": ["Fibrolysis Factors"]}


def _counts(**kw):
    cx = sqlite3.connect(":memory:")
    _seed(cx)
    return fo.product_buyer_counts(cx, PRODUCTS, GROUPS, **kw)


def test_coverage_splits_legacy_from_current_and_counts_links():
    cov = _counts()["coverage"]
    assert cov["legacy"] == {"invoices": 4, "linked_to_client": 3, "client_has_email": 2}
    assert cov["current"] == {"invoices": 1, "linked_to_client": 1, "client_has_email": 1}


def test_product_counts_per_source():
    p = _counts()["products"]
    assert p["Clear the Way"]["legacy"] == {"invoices": 2, "clients": 2, "emails": 2}
    assert p["Clear the Way"]["current"] == {"invoices": 1, "clients": 1, "emails": 1}
    assert p["Clear the Way"]["lines_without_invoice"] == 1
    assert p["Scar Silk"]["legacy"]["emails"] == 1          # one line names two products
    assert p["Fibrolysis Factors"]["legacy"] == {"invoices": 2, "clients": 1, "emails": 0}


def test_groups_union_and_overlap():
    g = _counts(compare={"scar": {"store": ["Ann@x.com", "new@y.com"]}})["groups"]
    assert g["scar"]["fmp_emails"] == 2                      # ann (both files) + bo
    assert g["scar"]["legacy_emails"] == 2 and g["scar"]["current_emails"] == 1
    assert g["scar"]["overlap_with"] == {"store": 1}
    assert g["scar"]["total_unique_emails"] == 3
    assert g["fibroid"]["fmp_clients_without_email"] == 1
    assert g["fibroid"]["overlap_with"] == {}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    import dashboard as _dashboard
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(_dashboard, "CONSOLE_SECRET", "test-secret")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _seed(cx)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_route_requires_console_key(client):
    r = client.post("/api/console/fmp-product-buyers", json={"products": PRODUCTS})
    assert r.status_code == 401


def test_route_rejects_bad_input(client):
    h = {"X-Console-Key": "test-secret"}
    assert client.post("/api/console/fmp-product-buyers", json={}, headers=h).status_code == 400
    assert client.post("/api/console/fmp-product-buyers",
                       json={"products": {"x": ["a"]}}, headers=h).status_code == 400


def test_route_returns_counts_and_no_email(client):
    r = client.post("/api/console/fmp-product-buyers", headers={"X-Console-Key": "test-secret"},
                    json={"products": PRODUCTS, "groups": GROUPS,
                          "compare": {"scar": {"store": ["ann@x.com"]}}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["groups"]["scar"]["overlap_with"] == {"store": 1}
    assert "@" not in json.dumps(body)
