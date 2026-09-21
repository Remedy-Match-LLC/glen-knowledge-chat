"""Two live $39.97 pages published formulas that were not the product.

A T33_FORMULAS row's Name cell holds several alternate names, one per line, and 202 of
329 rows do. The catalog matched a LATER line and took that row's whole formula. Nine of
the 30 products sourced from t33 matched only an alternate name.

msm-powder carried fmp_id 90, which FileMaker types "Pure Powders", and published ten
ingredients at gram doses taken from FOR000092, whose first name is "old - Comfort
Complex". Glen ruled it on 2026-09-20: "msm-powder is only MSM".

Spec: production/05 Formulations/msm-powder/2026-09-20/msm-powder-is-only-msm.html
"""
import io
import json
import re
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "data" / "products.json"


def _catalog():
    return json.loads(io.open(CATALOG, encoding="utf-8").read())["products"]


def test_msm_powder_publishes_only_msm():
    p = _catalog()["msm-powder"]
    assert [i["name"] for i in p["ingredients"]] == ["MSM (Methylsulfonylmethane)"]
    assert p["ingredients_source"] == "glen-ruling-2026-09-20"


def test_msm_powder_states_no_dose_because_none_is_sourced():
    """FileMaker 90 gives a 200 g bottle and a 1 scoop dose and never a scoop weight,
    so any milligram figure on this line would be invented."""
    assert _catalog()["msm-powder"]["ingredients"][0]["dose"] == ""


def test_msm_powder_carries_filemakers_own_directions():
    """Served deterministically, outside the cached AI draft, so a rewrite cannot drop
    the dosing the way it did on the fibrolysis-factors page in September."""
    assert (_catalog()["msm-powder"]["directions"]
            == "Take 1 scoop 2 times daily in a drink, or as guided.")


def test_msm_powder_keeps_its_identity():
    """The ruling changes what is in the jar, not what the jar is or costs."""
    p = _catalog()["msm-powder"]
    assert p["name"] == "MSM Powder"
    assert p["price_cents"] == 3997
    assert p["fmp_id"] == "90"


def test_no_description_states_a_price_its_own_row_contradicts():
    """msm-powder's description opened "Price: $69.97." on a $39.97 product, copied from
    MSM Syntropy Powder, which really is that price. 37 descriptions carry a hardcoded
    "Price: $" and nothing keeps any of them in step with price_cents. This asserts the
    whole catalog rather than the one row, because the next copy-paste is the same bug."""
    bad = []
    for slug, p in _catalog().items():
        m = re.search(r"Price:\s*\$([0-9][0-9,]*\.?\d*)", p.get("description") or "")
        if not m:
            continue
        cents = p.get("price_cents")
        if not isinstance(cents, (int, float)):
            continue
        stated = float(m.group(1).replace(",", ""))
        if abs(stated - cents / 100.0) > 0.005:
            bad.append(f"{slug}: says ${stated:.2f}, price_cents is ${cents / 100:.2f}")
    assert not bad, "descriptions publishing a price their own row contradicts: " + "; ".join(bad)
