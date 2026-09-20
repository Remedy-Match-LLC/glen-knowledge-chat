"""Product links name the new store, not the retired GrooveKart one.

Glen, 2026-09-19: "look for any links still pointing to the old store and plan to update
them all", then "We still want to go ahead with pointing at the new store links".

Measured that day: of the 304 remedymatch.com urls in products.json, 290 answered 200 and
14 returned 404; in the glossary, 60 of 71 were already dead. The store also carries only
part of the catalog and clients have failed to check out on it (see
dashboard.legacy_store_links), so a live old link is still the wrong destination.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "data" / "products.json").read_text())["products"]
ALIASES = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
NEW_STORE = "https://myhealingoasis.com"


def test_no_product_links_at_the_old_store():
    bad = [(s, p["url"]) for s, p in CATALOG.items()
           if "remedymatch.com" in (p.get("url") or "")]
    assert bad == [], bad


def test_each_product_url_is_its_own_page_on_the_store_host():
    """The url is derived from the record's own slug, so it cannot drift onto another
    product the way an inherited GrooveKart address did."""
    wrong = [(s, p["url"]) for s, p in CATALOG.items()
             if (p.get("url") or "").startswith(NEW_STORE)
             and p["url"] != f"{NEW_STORE}/begin/product/{s}"]
    assert wrong == [], wrong


def test_the_chat_link_table_points_at_the_new_store():
    """app.py prefers canonical_url over url when it injects a product link."""
    bad = [(k, v.get("canonical_url") or v.get("url")) for k, v in ALIASES.items()
           if "remedymatch.com" in ((v.get("canonical_url") or "") + (v.get("url") or ""))]
    assert bad == [], bad


def test_every_chat_link_names_a_real_product_or_the_shop():
    for name, info in ALIASES.items():
        u = info.get("canonical_url") or info.get("url") or ""
        if "/begin/product/" in u:
            slug = u.rsplit("/", 1)[-1]
            assert slug in CATALOG, (name, slug)
        elif u.startswith(NEW_STORE):
            assert u.endswith("/shop"), (name, u)


def test_the_old_address_is_kept_so_old_links_still_resolve():
    """The trap this batch fell into, caught by CI on 2026-09-19.

    dashboard.legacy_store_links builds its {old id -> new page} map from the catalog's
    OLD GrooveKart addresses: an old link in knowledge text or a chat answer carries only
    that numeric id. Repointing `url` erased the map, and every old link fell back to the
    shop. The old address now lives in `legacy_store_url`."""
    from dashboard import legacy_store_links as L
    kept = [s for s, p in CATALOG.items() if "remedymatch.com" in (p.get("legacy_store_url") or "")]
    assert len(kept) > 300, len(kept)
    m = L.build_map(CATALOG)
    assert m.get("85") == "/begin/product/nous-energy", m.get("85")
    assert len(m) > 300, len(m)


def _atlas(name):
    return json.loads((ROOT / "data" / name).read_text())["concepts"]


def test_no_atlas_concept_links_at_the_old_store():
    """Glen, 2026-09-19: take on the Atlas links too. Both files carry them: the seed is
    the source, and the live graph is what /atlas/data serves."""
    for name in ("atlas-concepts.json", "atlas-seed-input.json"):
        bad = [(c["id"], l.get("title"), l.get("url")) for c in _atlas(name)
               for l in (c.get("links") or []) if "remedymatch.com" in (l.get("url") or "")]
        assert bad == [], (name, bad[:5])


def test_every_atlas_product_link_names_a_real_product_or_the_shop():
    for name in ("atlas-concepts.json", "atlas-seed-input.json"):
        for c in _atlas(name):
            for l in (c.get("links") or []):
                u = l.get("url") or ""
                if "/begin/product/" in u:
                    assert u.rsplit("/", 1)[-1] in CATALOG, (name, c["id"], u)
                elif u.startswith(NEW_STORE):
                    assert u.endswith("/shop"), (name, c["id"], u)
