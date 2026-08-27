"""Collapse client-facing checkout detail into Rae's two QBO sales buckets."""

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .hawaii_counties import category_for_zip


_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "products.json"


def _catalog_indexes():
    try:
        products = json.loads(_CATALOG_PATH.read_text()).get("products", {})
    except Exception:
        products = {}
    by_key = {}
    for slug, product in products.items():
        is_service = bool(product.get("service") or product.get("digital"))
        by_key[str(slug).strip().lower()] = is_service
        name = str(product.get("name") or "").strip().lower()
        if name:
            by_key[name] = is_service
    return by_key


_SERVICE_BY_KEY = _catalog_indexes()
_SERVICE_SOURCES = {
    "biofield", "membership", "continuous_care_monthly", "certification",
    "course", "module", "consultation",
}


def _cents(amount):
    return int((Decimal(str(amount or 0)) * 100).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def _is_service_line(line, source=""):
    explicit = str(line.get("sale_category") or "").strip().lower()
    if explicit in ("digital", "service", "digital_services"):
        return True
    if explicit in ("physical", "physical_goods"):
        return False
    if str(source or "").strip().lower() in _SERVICE_SOURCES:
        return True
    name = str(line.get("name") or "").strip().lower()
    if name.startswith("shipping") or "usps shipping" in name:
        return False
    if name in _SERVICE_BY_KEY:
        return _SERVICE_BY_KEY[name]
    return any(word in name for word in (
        "membership", "biofield analysis", "consultation", "tuition",
        "course", "coaching", "digital", "download",
    ))


def in_house_breakdown(lines, total_cents, *, source=""):
    """Return the net physical/service split retained for internal reporting.

    The original lines remain stored for Stripe/client display. Any cart-level
    discount is already reflected in total_cents and is allocated proportionally
    across physical vs digital/service gross sales.
    """
    gross = {"physical": 0, "service": 0}
    for line in lines or []:
        qty = max(1, int(line.get("qty", 1) or 1))
        bucket = "service" if _is_service_line(line, source) else "physical"
        gross[bucket] += max(0, _cents(line.get("amount")) * qty)
    target = max(0, int(total_cents or 0))
    gross_total = gross["physical"] + gross["service"]
    if target <= 0 or gross_total <= 0:
        return {"physical_goods_cents": 0, "digital_services_cents": 0}
    if gross["physical"] and gross["service"]:
        physical = (target * gross["physical"] + gross_total // 2) // gross_total
        physical = min(target, physical)
        allocated = {"physical": physical, "service": target - physical}
    elif gross["service"]:
        allocated = {"physical": 0, "service": target}
    else:
        allocated = {"physical": target, "service": 0}
    return {"physical_goods_cents": allocated["physical"],
            "digital_services_cents": allocated["service"]}


def summarize(lines, total_cents, *, source=""):
    """Return Rae's single net QBO line; classification stays in-house."""
    target = max(0, int(total_cents or 0))
    if target <= 0 or not lines:
        return []
    return [{"name": "Order Total", "description": "RemedyMatch order total",
             "amount": target / 100.0, "qty": 1}]


def in_house_tracking(lines, total_cents, *, address=None, source=""):
    """Derive internal tax/product reporting without sending it to QBO."""
    address = address or {}
    postal = address.get("zip") or address.get("postal") or address.get("postcode")
    return {
        "sales_area": category_for_zip(postal),
        **in_house_breakdown(lines, total_cents, source=source),
    }


def classify_in_house_lines(lines, total_cents, *, address=None, source=""):
    """Classify each retained checkout line for internal sales-tax reporting.

    Net order value (after discounts/credits) is allocated proportionally across
    the original lines. Only physical lines receive the ZIP-derived sales area;
    digital/service lines deliberately receive ``None``.
    """
    address = address or {}
    postal = address.get("zip") or address.get("postal") or address.get("postcode")
    sales_area = category_for_zip(postal)
    prepared = []
    gross_total = 0
    for line in lines or []:
        qty = max(1, int(line.get("qty", 1) or 1))
        gross_cents = max(0, _cents(line.get("amount")) * qty)
        if not gross_cents:
            continue
        kind = "digital_services" if _is_service_line(line, source) else "physical_goods"
        prepared.append((line, kind, gross_cents))
        gross_total += gross_cents
    target = max(0, int(total_cents or 0))
    if not prepared or gross_total <= 0 or target <= 0:
        return []
    allocated_so_far = 0
    out = []
    for index, (line, kind, gross_cents) in enumerate(prepared):
        if index == len(prepared) - 1:
            net_cents = target - allocated_so_far
        else:
            net_cents = (target * gross_cents + gross_total // 2) // gross_total
            net_cents = min(target - allocated_so_far, net_cents)
            allocated_so_far += net_cents
        out.append({
            "name": line.get("name") or "",
            "qty": max(1, int(line.get("qty", 1) or 1)),
            "sales_type": kind,
            "net_cents": net_cents,
            "sales_area": sales_area if kind == "physical_goods" else None,
        })
    return out
