"""Retire the phantom "Aller-Free Aid" listing, and rename the drops.

Spec: production/02 Products/aller-free-aid-retire-2026-09-25.md (Glen approved it and
its addendum on 2026-09-25). `aller-free-aid` was never sold. The real product is
`allerfree-homeoenergetic-drops`, now named "Aller-Free HomeoEnergetic Drops".
"""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

OLD = "aller-free-aid"
NEW = "allerfree-homeoenergetic-drops"
NEW_NAME = "Aller-Free HomeoEnergetic Drops"
OLD_DROPS_NAME = "AllerFree HomeoEnergetic Drops"
PHANTOM = "Aller-Free Aid for Inhalant Allergies"
MARKER = "MARKER-OLD-SLUG-DRAFT-7f3a"

ROOT = Path(__file__).resolve().parent.parent


def _catalog():
    return json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["products"]


# --- catalogue -----------------------------------------------------------------------

def test_phantom_listing_is_retired_to_the_drops():
    p = _catalog()[OLD]
    assert p["inactive"] is True
    assert p["superseded_by"] == NEW
    assert p["name"] == PHANTOM  # old orders and reports still read


def test_drops_renamed_keeping_slug_and_pinecone_title():
    p = _catalog()[NEW]
    assert p["name"] == NEW_NAME
    # Knowledge re-ingested under the new title 2026-09-30 (Glen ran it), so the exact
    # Pinecone filter now uses the new name. The old title holds no chunks.
    assert p["pinecone_title"] == NEW_NAME
    assert not p.get("inactive")


def test_upsell_pairings_drop_the_phantom():
    pairings = json.loads((ROOT / "data" / "upsell-pairings.json").read_text(encoding="utf-8"))
    assert OLD not in pairings["pairings"]


def test_product_alias_catalog_name_is_the_new_name():
    aliases = json.loads((ROOT / "data" / "product-aliases.json").read_text(encoding="utf-8"))
    assert aliases["aliases"]["AllerFree"]["catalog_name"] == NEW_NAME


# --- routes --------------------------------------------------------------------------

def _reload_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", "true")
    import app as appmod
    importlib.reload(appmod)
    assert appmod._SALES_AI_COPY_ENABLED is True  # the flag is on, so nothing passes vacuously
    monkeypatch.setattr(appmod, "_RELATED_PRODUCTS_ENABLED", False, raising=False)
    # The drops have no pinned copy, so page-data would generate a card: never call out.
    monkeypatch.setattr(appmod, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    return appmod


def _seed_marker(appmod, slug):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, slug, sec, f"{MARKER} {sec}")


def _rows_for(appmod, slug):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return sp.get_page(cx, slug)


@pytest.mark.parametrize("old,new", [(OLD, NEW), ("aces-eyedrops", "aces-eye-drops")])
def test_superseded_product_page_302s_keeping_the_query_string(monkeypatch, tmp_path, old, new):
    appmod = _reload_app(monkeypatch, tmp_path)
    r = appmod.app.test_client().get(f"/begin/product/{old}?utm_source=x&utm_campaign=a%20b&z=1")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.endswith(f"/begin/product/{new}?utm_source=x&utm_campaign=a%20b&z=1"), loc


def test_superseded_product_page_without_query_has_no_question_mark(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    r = appmod.app.test_client().get(f"/begin/product/{OLD}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/begin/product/{NEW}")


def test_live_product_page_is_not_redirected(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    r = appmod.app.test_client().get(f"/begin/product/{NEW}?utm_source=x")
    assert r.status_code == 200
    assert NEW_NAME in r.get_data(as_text=True)



def _unpin_survivor(appmod, monkeypatch):
    """The drops now carry Glen's pinned copy (2026-09-30). These two tests exercise the AI
    draft path under the survivor's slug, so they run with the pin taken off."""
    entry = dict(appmod._PRODUCTS["products"][NEW])
    entry.pop("copy_pinned", None)
    monkeypatch.setitem(appmod._PRODUCTS["products"], NEW, entry)

def test_page_data_for_old_slug_serves_the_survivor_not_the_old_draft(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    _unpin_survivor(appmod, monkeypatch)
    _seed_marker(appmod, OLD)
    r = appmod.app.test_client().get(f"/begin/product-page-data/{OLD}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert MARKER not in body
    data = r.get_json()
    assert data["slug"] == NEW
    assert NEW in data["cta_url"] and OLD not in data["cta_url"]
    # The narrative sections were read under the survivor's slug (no draft there yet).
    nar = [s for s in data["sections"] if s["id"] in ("intro", "description", "research")]
    assert nar and all(s.get("ai") == "pending" for s in nar)


class _Stream:
    def __init__(self, toks): self._toks = toks
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def text_stream(self):
        yield from self._toks


class _Messages:
    def __init__(self): self.prompts = []
    def stream(self, **kw):
        self.prompts.append(kw)
        return _Stream(["Survivor ", "copy."])


class _FakeCl:
    def __init__(self): self.messages = _Messages()


def test_page_gen_for_old_slug_never_serves_or_writes_the_old_draft(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    _unpin_survivor(appmod, monkeypatch)
    _seed_marker(appmod, OLD)
    fake = _FakeCl()
    monkeypatch.setattr(appmod, "_cl", fake)
    seen = []
    from dashboard import sales_copy as sc
    real = sc.build_section_prompt

    def _spy(section, prod):
        seen.append(prod.get("slug"))
        return real(section, prod)
    monkeypatch.setattr(sc, "build_section_prompt", _spy)
    before = _rows_for(appmod, OLD)
    body = appmod.app.test_client().get(f"/begin/product-page-gen/{OLD}/intro").get_data(as_text=True)
    assert MARKER not in body
    assert "Survivor " in body
    assert seen == [NEW]  # never asked to generate under the old slug
    assert len(fake.messages.prompts) == 1
    assert _rows_for(appmod, OLD) == before  # nothing new written under the old slug
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sp.get_section(cx, NEW, "intro") == "Survivor copy."


# --- chat ----------------------------------------------------------------------------

def _app():
    import app as appmod
    return appmod


def test_chat_prompt_names_the_drops_and_drops_the_phantom():
    prompt = _app().get_system_prompt("self-healing")
    assert "Aller-Free Aid" not in prompt
    rule = [l for l in prompt.splitlines() if l.startswith("- SELLABLE BUT NOT RECOMMENDED")]
    assert len(rule) == 1
    assert NEW_NAME in rule[0]
    assert '"AllerFree"' in rule[0]  # the old spelling is still recognised
    assert "Immune Modulation" in rule[0]
    assert "NEVER tell anyone AllerFree is retired" in rule[0]


@pytest.mark.parametrize("name", [NEW_NAME, OLD_DROPS_NAME, "AllerFree", PHANTOM])
def test_chat_link_resolves_every_name_to_the_drops(name):
    url, source = _app()._resolve_remedy_url(name)
    assert url and NEW in url and OLD not in url, (name, url)
    assert source == "catalog"


# --- never recommend -----------------------------------------------------------------

def test_both_slugs_are_never_recommended():
    from dashboard.related_products import DO_NOT_RECOMMEND, guardrail_ok
    assert OLD in DO_NOT_RECOMMEND and NEW in DO_NOT_RECOMMEND
    products = _catalog()
    assert guardrail_ok(NEW, "immune-modulation", products) is False


# --- invoices ------------------------------------------------------------------------

@pytest.mark.parametrize("name", [OLD_DROPS_NAME, NEW_NAME, "allerfree homeoenergetic drops "])
def test_invoice_line_resolves_old_and_new_drops_name(name):
    from dashboard.biofield_invoice import resolve_line_slug
    catalog = [{"slug": s, **p} for s, p in _catalog().items()]
    assert resolve_line_slug(name, catalog) == NEW


# --- dosing --------------------------------------------------------------------------

def _snap(product_name):
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE fmp_snap_products (id_pk INTEGER, product_name TEXT, dosage TEXT, "
               "dosage_freq TEXT, dosage_timing TEXT)")
    cx.execute("INSERT INTO fmp_snap_products VALUES (105, ?, '10 drops', '3 times a day', 'or as needed')",
               (product_name,))
    return cx


@pytest.mark.parametrize("snap_name", [OLD_DROPS_NAME, NEW_NAME])
@pytest.mark.parametrize("asked", [NEW_NAME, PHANTOM, OLD_DROPS_NAME])
def test_dose_found_under_either_snapshot_spelling(snap_name, asked):
    from dashboard import biofield_authoring as ba
    d = ba.remedy_dosing(_snap(snap_name), asked)
    assert d["dosage"] == "10 drops", (snap_name, asked, d)
    assert d["frequency"] == "3 times a day"


@pytest.mark.parametrize("snap_name", [OLD_DROPS_NAME, NEW_NAME])
def test_dose_row_swaps_spelling(snap_name):
    from dashboard import biofield_authoring as ba
    other = NEW_NAME if snap_name == OLD_DROPS_NAME else OLD_DROPS_NAME
    r = ba._dose_row(_snap(snap_name), other)
    assert r is not None and r["dosage"] == "10 drops"


def test_dose_swap_leaves_other_products_alone():
    from dashboard import biofield_authoring as ba
    assert ba._dose_row(_snap(OLD_DROPS_NAME), "Immune Modulation") is None
