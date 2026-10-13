"""An invoice carrying a Biofield Analysis says membership is included (Glen, 2026-10-13).

The Biofield fee includes a month of membership, and its remedies are already quoted at
member pricing. The card used to offer "Add membership" on such an order anyway.
"""
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

FFS = ["paracleanse", "nerve-repair", "microbiome"]


def _client(tmp_path, monkeypatch, *, biofield=True, membership_line=False):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O
    items = [{"slug": s, "qty": 1, "name": s, "unit_cents": 5600, "line_cents": 5600}
             for s in FFS]
    if biofield:
        items.insert(0, {"slug": "biofield-analysis", "qty": 1, "name": "Biofield Analysis",
                         "unit_cents": 30000, "line_cents": 30000, "service": True})
    if membership_line:
        items.append({"slug": "membership:month", "qty": 1, "name": "Membership",
                      "unit_cents": 2900, "line_cents": 2900, "kind": "membership"})
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        O.upsert_order(cx, source="inhouse", external_ref="INH-BFM-1",
                       email="bf-nonmember@example.com", name="Bio Tester", items=items,
                       total_cents=sum(i["line_cents"] for i in items), status="confirmed")
        cx.commit()
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    from dashboard import practitioner_portal as PP
    monkeypatch.setattr(PP, "_LOG_DB", Path(db))
    token = PP.create_order_invoice_token(1)
    appmod.app.config["TESTING"] = True
    return appmod, appmod.app.test_client(), token


def _offer(client, token):
    r = client.get(f"/api/invoice/{token}")
    assert r.status_code == 200, r.data
    return r.get_json()["order"]["membership_offer"]


def test_a_biofield_invoice_says_membership_is_included_with_its_saving(tmp_path, monkeypatch):
    appmod, client, token = _client(tmp_path, monkeypatch)
    o = _offer(client, token)
    lines = [{"slug": s, "qty": 1} for s in FFS]
    sub = lambda ls: sum(i["line_cents"] for i in appmod._price_inhouse_invoice(
        ls, email="bf-nonmember@example.com", pickup=True, ship=None)["items_rec"]
        if i.get("slug") in FFS)
    expected = sub(lines) - sub(lines + [{"slug": "biofield-analysis", "qty": 1}])
    assert expected > 0
    assert o == {"included": True, "savings_cents": expected}


def test_an_order_without_biofield_keeps_the_join_offer(tmp_path, monkeypatch):
    appmod, client, token = _client(tmp_path, monkeypatch, biofield=False)
    o = _offer(client, token)
    assert o and not o.get("included") and o["savings_cents"] > 0


def test_a_membership_line_keeps_its_remove_button(tmp_path, monkeypatch):
    appmod, client, token = _client(tmp_path, monkeypatch, membership_line=True)
    o = _offer(client, token)
    assert o and not o.get("included")


PAGE = Path(__file__).resolve().parent.parent / "static" / "invoice.html"


def _render(offer):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    src = PAGE.read_text()
    i = src.index("function renderMembershipOffer(")
    d, j = 0, src.index("{", i)
    while True:
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            break
        j += 1
    js = """
      const el = {style:{}, innerHTML:''};
      const $ = () => el;
      const money = c => '$' + (c/100).toFixed(0);
      const ORDER = {pay_status:'unpaid', lines:[], membership_offer:%s};
      %s
      renderMembershipOffer();
      console.log(JSON.stringify(el.innerHTML));
    """ % (json.dumps(offer), src[i:j + 1])
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_included_card_has_no_button_and_names_the_saving():
    html = _render({"included": True, "savings_cents": 12600})
    assert "A month of membership is included" in html
    assert "Your Biofield Analysis fee includes one month of membership." in html
    assert "Member pricing saves you $126 on this order." in html
    assert "<button" not in html


def test_the_included_card_drops_the_saving_when_there_is_none():
    html = _render({"included": True, "savings_cents": 0})
    assert "saves you" not in html and "<button" not in html
