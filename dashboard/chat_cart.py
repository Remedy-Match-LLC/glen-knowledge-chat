"""Deterministic parsing for explicit chat-to-basket commands."""
import re


_INTENT = re.compile(r"\b(?:add|order|buy|purchase|put|want|need|get|send|take)\b", re.I)
_NEGATED = re.compile(
    r"\b(?:do\s+not|don't|dont|no|not)\s+(?:add|order|buy|purchase|put|get|send|take)\b",
    re.I,
)
_QUESTION = re.compile(r"^\s*(?:do|does|did|should|could|would|can|is|are|what|which|why|how)\b", re.I)
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _norm(value, *, plus=True):
    """Lowercase words. A "+" reads as the word "plus" (plus=True), so "OcuHeal+ Eye
    Drops" and "OcuHeal Eye Drops" stay two products (2026-09-24: the collapse put the
    new 10% DMSO drops in the basket of a buyer asking for the original)."""
    text = str(value or "").lower()
    if plus:
        text = text.replace("+", " plus ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def explicit_cart_items(message, catalog, *, max_qty=99):
    """Return exact catalog items only when the user explicitly orders them."""
    if (not _INTENT.search(message or "") or _NEGATED.search(message or "")
            or _QUESTION.search(message or "")):
        return []
    text = _norm(message)
    products, primary = [], {}
    for product in catalog or []:
        slug = str(product.get("slug") or "").strip().lower()
        name = str(product.get("name") or product.get("title") or slug).strip()
        if not slug or not name:
            continue
        own = {_norm(name), _norm(slug)} - {""}
        products.append((slug, name, own))
        for alias in own:
            primary.setdefault(alias, set()).add(slug)
    choices = []
    for slug, name, own in products:
        # The name with its "+" dropped, as typed before this change ("Neuro Eye
        # Drops", "Air & Surface PRO"), stays an alias only while no OTHER product
        # claims it: "ocuheal eye drops" is OcuHeal's own name, never OcuHeal+'s.
        loose = _norm(name, plus=False)
        if loose and loose not in own and primary.get(loose, {slug}) <= {slug}:
            own = own | {loose}
        for alias in own:
            choices.append((len(alias), alias, slug, name))
    choices.sort(reverse=True)

    occupied, out, seen = [], [], set()
    number = r"(?P<qty>\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
    for _length, alias, slug, name in choices:
        if slug in seen:
            continue
        pattern = re.compile(
            rf"(?:{number}\s+(?:(?:bottles?|boxes?|packs?|units?)\s+(?:of\s+)?)?)?"
            rf"(?P<product>\b{re.escape(alias)}\b)")
        match = pattern.search(text)
        if not match:
            continue
        span = match.span("product")
        if any(span[0] < b and a < span[1] for a, b in occupied):
            continue
        raw_qty = match.group("qty")
        qty = int(raw_qty) if raw_qty and raw_qty.isdigit() else _NUMBER_WORDS.get(raw_qty or "", 1)
        occupied.append(span)
        seen.add(slug)
        out.append({"slug": slug, "name": name,
                    "qty": max(1, min(int(max_qty), qty))})
    return out
