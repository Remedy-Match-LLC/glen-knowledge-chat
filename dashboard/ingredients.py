"""Ingredient resolver for the ingredient page. Maps a URL slug to an ingredient
name + its FMP record, the formulations that use it, and its research studies."""
import json
import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FMP = _ROOT / "data" / "fmp-ingredient-content.json"
_PRODUCTS = _ROOT / "data" / "products.json"
_GROUPS = _ROOT / "data" / "ingredient_formulations.json"


def slugify(name):
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")[:40]


@lru_cache(maxsize=1)
def _fmp_records():
    """Return the ingredients sub-dict from fmp-ingredient-content.json.
    The file has top-level keys _source and ingredients; we want the latter."""
    try:
        raw = json.loads(_FMP.read_text())
        if isinstance(raw, dict):
            return raw.get("ingredients", {}) or {}
        return {}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _name_index():
    """{slug: canonical_name} over all known ingredient names (FMP + products)."""
    idx = {}
    for rec in _fmp_records().values():
        if not isinstance(rec, dict):
            continue
        nm = (rec.get("name") or "").strip().replace("\n", " ")
        if nm:
            idx.setdefault(slugify(nm), nm)
    try:
        prods = json.loads(_PRODUCTS.read_text()).get("products", {})
    except Exception:
        prods = {}
    for p in prods.values():
        for ing in (p.get("ingredients") or []):
            nm = (ing.get("name") if isinstance(ing, dict) else ing) or ""
            nm = nm.strip()
            if nm:
                idx.setdefault(slugify(nm), nm)
    return idx


def _fmp_for(name):
    try:
        from dashboard import ingredient_content
        return ingredient_content.get(name) or {}
    except Exception:
        return {}


def resolve(slug):
    name = _name_index().get(slug)
    if not name:
        return None
    return {"slug": slug, "name": name, "fmp": _fmp_for(name)}


_GROUPS_CACHE = None


def _groups_by_page():
    """{page slug: group} from ingredient_formulations.json, built from the formula data.
    Cached only after a good read, so one failed read is retried rather than kept."""
    global _GROUPS_CACHE
    if _GROUPS_CACHE is None:
        try:
            groups = json.loads(_GROUPS.read_text()).get("groups", {}) or {}
        except Exception:
            return {}
        _GROUPS_CACHE = {pg: g for g in groups.values() for pg in (g.get("pages") or [])}
    return _GROUPS_CACHE


def formulations_with(name):
    """Products to link from an ingredient page. An ingredient Glen asked to link by formula
    data (ingredient_formulations.json) lists every live product whose formula contains it,
    in the file's order. Any other ingredient keeps the exact-name match."""
    target = slugify(name)
    out = []
    try:
        prods = json.loads(_PRODUCTS.read_text()).get("products", {})
    except Exception:
        return out
    group = _groups_by_page().get(target)
    if group:
        for item in group.get("products") or []:
            p = prods.get(item.get("slug"))
            if not p or p.get("inactive"):
                continue
            row = {"slug": item["slug"], "name": p.get("name", item["slug"])}
            if item.get("note"):
                row["note"] = item["note"]
            out.append(row)
        return out
    for pslug, p in prods.items():
        for ing in (p.get("ingredients") or []):
            nm = (ing.get("name") if isinstance(ing, dict) else ing) or ""
            if slugify(nm) == target:
                out.append({"slug": pslug, "name": p.get("name", pslug)})
                break
    return out


def research_studies(name, k=12):
    try:
        from dashboard import product_content
        return product_content._research_sources(name, k=k) or []
    except Exception:
        return []
