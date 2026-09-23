"""Glen's standing product rules, enforced on every reveal a client can open.

Until 2026-09-23 a free member saw a reveal only after Glen's first approval, and that
approval was the human check on these rules. #1796 removes it, so the rules must hold in
code. They were enforced for related products, condition triage and old-store links, but
nothing on the reveal path read them.

Rules (root CLAUDE.md and memory feedback_do_not_recommend_products):
  - Electrolyte Mineral Manna: never recommended.
  (AllerFree is not listed: the read path already swaps it for Immune Modulation,
   biofield_reveals.REMEDY_SUBSTITUTIONS, before this screen runs.)
  - Bioavailability Blend: an adjunct, never a stand-alone or a Biofield reveal item.
  - Fungifuge: only following a Candida Cleanse, so only when the same report carries it.

Applied where a client reads or orders from a reveal (app._biofield_verify_token). The
console, where Glen reviews raw matches, is not screened. This is a second line: the
vault matcher (02 Skills/e4l_synthesis.py REVEAL_EXCLUDED_SLUGS) already drops two of
these, but its candidate pool (data/ff-candidates.json) still marks them includable and
has no Candida Cleanse condition for Fungifuge, so the matcher alone is one list edit
away from sending them.
"""
import copy
import re

NEVER = {
    "electrolyte-mineral-manna": "Electrolyte Mineral Manna",
    "bioavailability-blend": "Bioavailability Blend",
    "bioavailability-blend-powder": "Bioavailability Blend",
}
_NEVER_NAMES = ("electrolyte mineral manna", "bioavailability blend")
FUNGIFUGE = "fungifuge"
CANDIDA_CLEANSE = "candida-cleanse"


def _key(remedy):
    if not isinstance(remedy, dict):
        return "", ""
    slug = (remedy.get("slug") or "").strip().lower()
    name = re.sub(r"\s+", " ", (remedy.get("name") or "").strip().lower())
    return slug, name


def _is_never(remedy):
    slug, name = _key(remedy)
    return slug in NEVER or any(name.startswith(n) for n in _NEVER_NAMES)


def _is_fungifuge(remedy):
    slug, name = _key(remedy)
    return slug == FUNGIFUGE or name.startswith("fungifuge")


def _is_candida_cleanse(remedy):
    slug, name = _key(remedy)
    return slug == CANDIDA_CLEANSE or name.startswith("candida cleanse")


def screen(row):
    """A copy of a biofield_reveals row with barred remedies removed. Never raises;
    on anything unexpected the row comes back unchanged rather than breaking the page."""
    if not isinstance(row, dict):
        return row
    try:
        out = copy.deepcopy(row)
        layers = out.get("layers") or []
        remedies = out.get("remedies") or []
        every = [L.get("remedy") for L in layers if isinstance(L, dict)] + list(remedies)
        has_cc = any(_is_candida_cleanse(r) for r in every)

        def barred(r):
            return _is_never(r) or (_is_fungifuge(r) and not has_cc)

        for L in layers:
            if isinstance(L, dict) and barred(L.get("remedy")):
                L["remedy"] = None
        out["remedies"] = [r for r in remedies if not barred(r)]
        return out
    except Exception:
        return row
