"""The invoice's join-and-save card quotes two real totals (Glen, 2026-10-22).

"Pay as you go: $X" and "Member: $Y" sit side by side, then one net line, the
benefits, the terms, and two equal buttons. X and Y are what the Total row would read,
priced by the membership toggle's own payload through the same reprice code, with
nothing written. The JS is extracted from static/invoice.html and run under node.
"""
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

FFS = ["paracleanse", "nerve-repair", "neuroceramides",
       "microbiome", "oxygen-cleanse", "macular-wellness-lycopene"]
EMAIL = "card-nonmember@example.com"


def _client(tmp_path, monkeypatch, items=None, *, normalise=True):
    """An unpaid non-member invoice. normalise=True saves it once through the
    membership toggle's remove action, so its stored total is the one the reprice
    gives, as an invoice saved by the editor would be."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O
    if items is None:
        items = [{"slug": s, "qty": 1, "name": s, "unit_cents": 7000, "line_cents": 7000}
                 for s in FFS]
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        O.upsert_order(cx, source="inhouse", external_ref="INH-CARD-1",
                       email=EMAIL, name="Card Tester", items=items,
                       total_cents=sum(i["line_cents"] for i in items), status="proposed")
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
    client = appmod.app.test_client()
    if normalise:
        r = client.post(f"/api/invoice/{token}/membership", json={"action": "remove"})
        assert r.status_code == 200, r.data
    return appmod, client, token, db


def _order(client, token):
    r = client.get(f"/api/invoice/{token}")
    assert r.status_code == 200, r.data
    return r.get_json()["order"]


def _snapshot(db):
    """Every table's rows, in full."""
    with sqlite3.connect(db) as cx:
        names = [r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: sorted(map(repr, cx.execute(f'SELECT * FROM "{n}"').fetchall()))
                for n in names}


# ── server ──────────────────────────────────────────────────────────────────

def test_an_unpaid_non_member_invoice_gets_both_totals(tmp_path, monkeypatch):
    appmod, client, token, _db = _client(tmp_path, monkeypatch)
    o = _order(client, token)
    offer = o["membership_offer"]
    assert offer["tier"] == "month" and offer["gross_cents"] == 9900
    assert offer["payg_total_cents"] == o["total_cents"]
    assert isinstance(offer["member_total_cents"], int)
    assert offer["member_total_cents"] != offer["payg_total_cents"]


def test_the_member_total_is_what_adding_the_line_produces(tmp_path, monkeypatch):
    appmod, client, token, _db = _client(tmp_path, monkeypatch)
    offer = _order(client, token)["membership_offer"]
    add = client.post(f"/api/invoice/{token}/membership", json={"action": "add", "tier": "month"})
    assert add.status_code == 200, add.data
    after = add.get_json()["order"]
    assert any(l["slug"] == "membership:month" for l in after["lines"])
    assert after["total_cents"] == offer["member_total_cents"]
    # And removing it again lands on the pay-as-you-go figure.
    rem = client.post(f"/api/invoice/{token}/membership", json={"action": "remove"})
    assert rem.get_json()["order"]["total_cents"] == offer["payg_total_cents"]


def test_a_membership_line_already_on_the_invoice_keeps_the_added_state(tmp_path, monkeypatch):
    appmod, client, token, _db = _client(tmp_path, monkeypatch)
    after = client.post(f"/api/invoice/{token}/membership",
                        json={"action": "add", "tier": "month"}).get_json()["order"]
    offer = after["membership_offer"]
    assert offer and not offer.get("included")
    assert offer["member_total_cents"] == after["total_cents"]
    html = _render(offer, lines=after["lines"])
    assert "Membership added to this order" in html and "Remove membership" in html
    assert "Keep pay as you go" not in html


def test_computing_the_offer_writes_nothing(tmp_path, monkeypatch):
    appmod, client, token, db = _client(tmp_path, monkeypatch)
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        order = O.get_order(cx, 1)
    before = _snapshot(db)
    offer = appmod._invoice_membership_offer(order)
    assert "member_total_cents" in offer
    after = _snapshot(db)
    for name, rows in before.items():
        assert after[name] == rows, f"table {name} changed"
    for name in set(after) - set(before):
        assert after[name] == [], f"new table {name} has rows"


def test_the_offer_stands_when_membership_saves_nothing(tmp_path, monkeypatch):
    # One bottle of one formula: the member rate needs a mix, so nothing is saved.
    items = [{"slug": "bone-builder", "qty": 1, "name": "Bone Builder",
              "unit_cents": 7000, "line_cents": 7000}]
    appmod, client, token, _db = _client(tmp_path, monkeypatch, items)
    o = _order(client, token)
    offer = o["membership_offer"]
    assert offer is not None and offer["savings_cents"] == 0
    assert offer["member_total_cents"] - offer["payg_total_cents"] == 9900
    html = _render(offer)
    assert "Membership adds $99.00 to this order." in html


def test_totals_are_withheld_when_the_reprice_disagrees_with_the_invoice(tmp_path, monkeypatch):
    """A stored total the toggle's own reprice does not reproduce (here: a shipping fee
    the stored invoice never charged) means a click moves the total for reasons beside
    membership. The card then gets no totals and keeps its savings wording."""
    appmod, client, token, _db = _client(tmp_path, monkeypatch, normalise=False)
    real = appmod._reprice_invoice

    def drift(order, lines_in, **kw):
        priced, ship, credit = real(order, lines_in, **kw)
        priced["total_cents"] += 1234
        return priced, ship, credit
    monkeypatch.setattr(appmod, "_reprice_invoice", drift)
    offer = _order(client, token)["membership_offer"]
    assert offer is not None and offer["savings_cents"] > 0
    assert "payg_total_cents" not in offer and "member_total_cents" not in offer


def test_add_is_still_refused_for_a_member(tmp_path, monkeypatch):
    appmod, client, token, db = _client(tmp_path, monkeypatch)
    from datetime import datetime, timedelta
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO memberships (id, email, granted_at, expires_at, source) "
                   "VALUES (?,?,?,?,?)",
                   ("m-card-1", EMAIL, datetime.utcnow().isoformat(),
                    (datetime.utcnow() + timedelta(days=30)).isoformat(), "membership_month"))
        cx.commit()
    assert _order(client, token)["membership_offer"] is None
    r = client.post(f"/api/invoice/{token}/membership", json={"action": "add", "tier": "month"})
    assert r.status_code == 409


# ── page ────────────────────────────────────────────────────────────────────

PAGE = Path(__file__).resolve().parent.parent / "static" / "invoice.html"


def _segment():
    """From the card's constants through the end of renderMembershipOffer."""
    src = PAGE.read_text()
    i = src.index("const MEM_BENEFITS=")
    k = src.index("function renderMembershipOffer(")
    d, j = 0, src.index("{", k)
    while True:
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            break
        j += 1
    return src[i:j + 1]


def _helper(name):
    src = PAGE.read_text()
    start = src.index(name)
    return src[start:src.index("\n", start)]


def _run(offer, steps, lines=None):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = """
      const el = {style:{}, innerHTML:''};
      const $ = () => el;
      %s
      %s
      let FETCHES = 0;
      globalThis.fetch = () => { FETCHES++; return Promise.reject(new Error('no')); };
      const ORDER = {pay_status:'unpaid', lines:%s, membership_offer:%s};
      %s
      const out = [];
      renderMembershipOffer(); out.push(el.innerHTML);
      %s
      console.log(JSON.stringify({out, fetches: FETCHES}));
    """ % (_helper("const money ="), _helper("function esc("), json.dumps(lines or []),
           json.dumps(offer), _segment(), steps)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _render(offer, lines=None):
    return _run(offer, "", lines)["out"][0]


OFFER = {"tier": "month", "gross_cents": 9900, "savings_cents": 5400,
         "net_add_cents": 4500, "net_savings_cents": 5400}


@pytest.mark.parametrize("payg,member,line", [
    (50000, 47000, "Membership saves you $30.00 on this order."),
    (40000, 44500, "Membership adds $45.00 to this order."),
    (40000, 40000, "Membership costs nothing extra on this order."),
])
def test_the_net_line_reads_from_the_two_totals(payg, member, line):
    html = _render({**OFFER, "payg_total_cents": payg, "member_total_cents": member})
    assert line in html
    for other in ("saves you", "adds $", "costs nothing extra"):
        if other not in line:
            assert other not in html


def test_the_card_sets_out_both_totals_benefits_terms_and_buttons():
    html = _render({**OFFER, "payg_total_cents": 41982, "member_total_cents": 46482})
    assert "Pay as you go: <b>$419.82</b>" in html
    assert "Member: <b>$464.82</b>, including $99 for one month of membership" in html
    benefits = ["Weekly live group coaching", "Your protocol re-matched every month",
                "Your AI ally", "Terrain Restore", "Biofield Analysis at $200 instead of $300"]
    at = [html.index("<li>" + b + "</li>") for b in benefits]
    assert at == sorted(at)
    terms = html.index("Membership is $99 for one month. It does not renew, so there is nothing to cancel.")
    add = html.index(">Add membership</button>")
    keep = html.index(">Keep pay as you go</button>")
    assert at[-1] < terms < add < keep
    assert "toggleInvoiceMembership('add')" in html
    # Same weight: neither button is the primary one, and neither total is marked out.
    assert 'class="primary"' not in html
    assert html.count('<button class="ghost"') == 2


def test_the_terms_use_the_tiers_own_price():
    html = _render({**OFFER, "gross_cents": 12900,
                    "payg_total_cents": 40000, "member_total_cents": 52900})
    assert "Membership is $129 for one month." in html
    assert "including $129 for one month" in html


def test_without_totals_the_card_keeps_the_savings_wording():
    html = _render(OFFER)
    assert "Pay as you go:" not in html and "Member:" not in html
    assert "$54.00" in html and "$45.00" in html
    assert "Membership is $99 for one month." in html and "Keep pay as you go" in html


def test_keep_pay_as_you_go_folds_the_card_without_a_server_call():
    res = _run({**OFFER, "payg_total_cents": 40000, "member_total_cents": 44500},
               "keepPayAsYouGo(true); out.push(el.innerHTML);"
               "keepPayAsYouGo(false); out.push(el.innerHTML);")
    full, folded, reopened = res["out"]
    assert res["fetches"] == 0
    assert "Paying as you go." in folded and "Show membership option" in folded
    assert "keepPayAsYouGo(false)" in folded
    assert "Add membership" not in folded and "Pay as you go:" not in folded
    assert reopened == full


def test_the_card_stays_out_of_the_print_root():
    src = PAGE.read_text()
    i = src.index("const root = document.getElementById('print-root');")
    # The print builder's own body, up to its next top-level function.
    body = src[i:src.index("\nfunction ", i)]
    assert "membership_offer" not in body and "renderMembershipOffer" not in body
