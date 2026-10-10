"""A blank courtesy price publishes at store prices (Glen, 2026-10-09).

"It should publish to portal when I leave the special pricing blank, which will be the
case for most new clients." The price box stopped silently on a blank entry, so a
report never published. And a publish with no price stored 0 on every reorder item,
which the portal read as a $0 price: 30 portals carried 239 such items.
"""
import sqlite3

import pytest

CLIENT = "dana.hale@example.com"


@pytest.fixture
def a(monkeypatch, tmp_path):
    import app as mod
    monkeypatch.setattr(mod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "_mix_match_member", lambda e: False)
    monkeypatch.setattr(mod, "_is_paid_member", lambda e: False)
    from dashboard import client_prices as cp
    cx = sqlite3.connect(str(mod.LOG_DB))
    cp.init_table(cx)
    cx.commit()
    cx.close()
    return mod


def _formula(a):
    P = a._PRODUCTS["products"]
    return next(s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
                and not p.get("inactive") and not p.get("info_only")
                and int(p.get("price_cents") or 0) == 7000)


@pytest.mark.parametrize("stored,expected", [
    (None, None), ("", None), (0, None), ("0", None), (-100, None), ("junk", None),
    (5000, 5000), ("5000", 5000)])
def test_only_a_positive_curated_price_counts(a, stored, expected):
    item = {"slug": "x"} if stored is None else {"slug": "x", "price_cents": stored}
    assert a._curated_unit_cents(item) == expected


def test_a_stored_zero_is_charged_the_store_price(a):
    slug = _formula(a)
    _l, items, _sub = a._portal_priced_lines(
        [{"slug": slug, "qty": 1, "price_cents": 0}], email=CLIENT)
    assert items[0]["unit_cents"] == 7000


def test_a_real_courtesy_price_still_applies(a):
    slug = _formula(a)
    _l, items, _sub = a._portal_priced_lines(
        [{"slug": slug, "qty": 1, "price_cents": 5000}], email=CLIENT)
    assert items[0]["unit_cents"] == 5000


def test_the_button_publishes_a_blank_and_explains_a_bad_entry():
    from dashboard import biofield_report_html as brh
    import inspect
    src = inspect.getsource(brh)
    assert "if(!cents)return;" not in src
    assert "Leave blank for store prices." in src
    assert "if(t==='')return 0;" in src
    assert "Nothing was published." in src


def _portal(a, items):
    from dashboard import client_portal as cp
    with a.db.connect(a.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        tok, _ = cp.upsert_portal(cx, CLIENT, "Dana Hale", {"reorder_items": items})
    return tok


def test_the_portal_shows_the_store_price_for_a_stored_zero(a):
    slug = _formula(a)
    tok = _portal(a, [{"slug": slug, "qty": 1, "price_cents": 0}])
    data = a.app.test_client().get(f"/api/portal/{tok}").get_json()
    row = next(r for r in data["reorder_items"] if r["slug"] == slug)
    assert row["price_cents"] == 7000 and not row["is_special"]


def test_the_portal_still_shows_a_real_courtesy_price(a):
    slug = _formula(a)
    tok = _portal(a, [{"slug": slug, "qty": 1, "price_cents": 5000}])
    data = a.app.test_client().get(f"/api/portal/{tok}").get_json()
    row = next(r for r in data["reorder_items"] if r["slug"] == slug)
    assert row["price_cents"] == 5000 and row["is_special"]


def test_the_cart_payload_prices_a_stored_zero_at_the_store_price(a):
    slug = _formula(a)
    portal = {"email": CLIENT, "content": {"reorder_items": [
        {"slug": slug, "qty": 1, "price_cents": 0}]}}
    with a.db.connect(a.LOG_DB) as cx:
        a._cart_store.init_cart_tables(cx)
        tok = a._cart_store.get_or_create(cx, "t-blank-price", CLIENT)
        a._cart_store.add_item(cx, tok, slug, 1)
        payload = a._portal_cart_payload(cx, tok, portal)
    row = next(i for i in payload["items"] if i["slug"] == slug)
    assert row["price_cents"] == 7000


def _node_js():
    import shutil
    from dashboard import biofield_report_html as brh
    import inspect
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = inspect.getsource(brh)
    i = src.index("    portal_pub = (")
    j = src.index("stresses_section = \"\"", i)
    ns = {"tid": "a99", "courtesy_cents": None}
    exec("\n".join(l[4:] for l in src[i:j].splitlines()), ns)
    js = ns["portal_pub"]
    return js[js.index("<script>") + 8:js.index("</script>")]


@pytest.mark.parametrize("entry,cents", [
    ("", 0), ("  ", 0), ("50", 5000), ("$50", 5000), ("50.00", 5000), ("$ 50.5", 5050),
    ("1,250", 125000), ("1,50", None), ("5$0", None), ("$", None), (",", None), ("abc", None),
    ("-5", None), ("1e3", None), ("50.", None), ("50.123", None)])
def test_the_price_box_reads_only_whole_entries(entry, cents):
    import json, subprocess
    js = _node_js() + f"\nconsole.log(JSON.stringify(courtesyCents({json.dumps(entry)})));"
    out = subprocess.run(["node", "-e", "var document={};" + js], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert json.loads(out) == cents, entry


def _page(courtesy_cents):
    from dashboard.biofield_report_html import render_report_html
    return render_report_html({"client": {"name": "Dana Hale", "email": CLIENT},
                               "date": "2026-10-09", "layers": []},
                              courtesy_cents=courtesy_cents)


def test_the_box_opens_with_the_last_published_price():
    assert "Leave blank for store prices.','50.00');" in _page(5000)


@pytest.mark.parametrize("prev", [None, 0])
def test_the_box_opens_empty_when_no_price_was_published(prev):
    assert "Leave blank for store prices.','');" in _page(prev)


_RUN = """
var el={textContent:''}, posted=null;
var document={getElementById:function(){return el;}};
function prompt(){return ANSWER;}
async function fetch(u,o){posted=JSON.parse(o.body);
  return {json:async function(){return {ok:true,url:'https://example.com/portal/t'};}};}
"""


@pytest.mark.parametrize("answer,posted", [
    ("''", 0), ("'  '", 0), ("'50'", 5000), ("null", None), ("'0'", None), ("'0.00'", None),
    ("'abc'", None), ("'5000'", None)])
def test_the_button_publishes_blank_and_refuses_zero(answer, posted):
    import json, subprocess
    js = (_RUN.replace("ANSWER", answer) + _node_js()
          + "\npublishPortal().then(function(){console.log(JSON.stringify("
            "{posted:posted&&posted.special_price_cents, msg:el.textContent}));});")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True,
                                    check=True).stdout.strip())
    assert out["posted"] == posted, (answer, out)
    if posted is None:
        assert out["msg"], answer   # never a silent stop
