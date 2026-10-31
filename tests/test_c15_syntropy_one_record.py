"""C15 Syntropy keeps one record (Glen, 2026-10-31, sales tab: "yes" to "keep C15 Syntropy and
redirect the old page").

c15-syntropy is the FileMaker 880 record and stays. c15-syntropy-pentadecanoic-acid, the old
GrooveKart 533 record, retires with `inactive: true` plus `superseded_by`, the same rule as
the 2026-09-16 duplicate pairs. It is never deleted: order history and the old store id 533
still name it. GrooveKart 533 itself is unchanged.
"""
import importlib
import json
import pathlib

import pytest

from dashboard import legacy_store_links as lsl
from dashboard import products as pm

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEEP, RETIRE = "c15-syntropy", "c15-syntropy-pentadecanoic-acid"


@pytest.fixture(scope="module")
def products():
    return json.loads((ROOT / "data" / "products.json").read_text())["products"]


def test_the_old_record_retires_onto_the_filemaker_record(products):
    r = products[RETIRE]
    assert r["inactive"] is True and r["superseded_by"] == KEEP
    assert products[KEEP]["fmp_id"] == "880" and not products[KEEP].get("inactive")
    assert pm.superseded_slug(RETIRE, products) == KEEP


def test_old_store_id_533_reaches_the_kept_page(products):
    assert lsl.build_map(products)["533"] == lsl._page_for(KEEP, products)
    assert lsl._page_for(KEEP, products)


def test_the_chat_link_table_names_only_the_kept_page():
    """The truly.vip short link keeps its own address; the page it lands on is the kept one."""
    a = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    # Plain C15 (c15-pentadecanoic-acid) is a different product and is not touched.
    c15 = {k: v for k, v in a.items() if "c15 syntropy" in json.dumps(v).lower()}
    assert set(c15) == {"C15 Syntropy", "C15 Syntropy: Pentadecanoic Acid"}
    page = "https://myhealingoasis.com/begin/product/c15-syntropy"
    assert c15["C15 Syntropy"]["canonical_url"] == page
    assert c15["C15 Syntropy: Pentadecanoic Acid"]["url"] == page
    assert c15["C15 Syntropy"]["url"] == "https://truly.vip/" + RETIRE
    assert a["C15: Pentadecanoic Acid"]["url"] == (
        "https://myhealingoasis.com/begin/product/c15-pentadecanoic-acid")
    assert RETIRE not in json.dumps(c15).replace("truly.vip/" + RETIRE, "")


def test_plain_c15_is_untouched(products):
    p = products["c15-pentadecanoic-acid"]
    assert not p.get("inactive") and not p.get("superseded_by")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a.app.test_client()


def test_the_old_page_redirects_and_keeps_its_query(client):
    r = client.get(f"/begin/product/{RETIRE}?utm_source=x")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/begin/product/{KEEP}?utm_source=x")
    assert client.get(f"/begin/product/{KEEP}").status_code == 200


def test_filemaker_880_and_the_related_list_name_the_kept_slug():
    """Round 1: an order-history line copies the map's slug, and the page looks up its
    related list by its own slug, so both must name the kept record (as wholomega did)."""
    resolved = json.loads((ROOT / "data" / "fmp_slug_map.json").read_text())["resolved"]
    assert resolved["880"] == KEEP
    related = json.loads((ROOT / "data" / "related-harvested.json").read_text())
    assert related.get(KEEP) == ["wholomega"] and RETIRE not in related
