"""Rewrite old GrooveKart store links (remedymatch.com) in chatbot answers.

The knowledge base still holds store-page text with remedymatch.com product links, and until
2026-09-14 only a prompt rule kept the chat from repeating them. Glen: "Update chatbot to the
new product pages." GrooveKart is the backup store; the primary store is /begin/product/<slug>.

Rules:
  * a remedymatch.com product link whose id maps to a live product becomes that product's
    new page, bare or inside a markdown link;
  * any other remedymatch.com page link (retired, unknown or do-not-recommend product, home,
    category, info): a markdown link keeps its text only, and a bare link becomes the browse
    entry page, so the sentence still reads and still works;
  * the brand name with no path, email addresses such as support@remedymatch.com, and
    go.remedymatch.com (GoHighLevel, not the store) are left alone.

The join is the numeric id at the start of the old URL's last path segment, e.g.
/remedies/syntropy/85-nous-energy -> 85. It is unique across the catalog. A retired record
follows `superseded_by` through dashboard.products.superseded_slug, the app's one walk. Only
for an id the catalog does not carry, the segment minus its "<id>-" may name a live slug
exactly. Products on dashboard.related_products.DO_NOT_RECOMMEND are never linked, whatever
their inactive flag says.

Answers stream, so `rewrite_stream` holds back only a tail that could still become a store
link (a trailing token that is or could grow into one, or an open markdown link) and
rewrites each released segment whole.
"""
import re

# Where a bare store link with no product page goes: the public store's browse page,
# now that it exists (Task 3). Was the guided RemedyMatch conversation, /begin/match.
BROWSE_ENTRY_PATH = "/shop"

_HOST = r"(?:www\.)?remedymatch\.com"
_TAIL = r"(?:[/?#][^\s<>\"'\]\)]*)?"
# Scheme form, or a bare host that is immediately followed by a path, query or fragment.
# The look-behinds keep email addresses (@), subdomains such as go. (.) and mid-word hits out.
_URL = re.compile(
    r"(?:(?<![\w@./-])https?://" + _HOST + r"|(?<![\w@./:-])" + _HOST + r"(?=[/?#]))" + _TAIL,
    re.I)
_MD = re.compile(r"\[([^\]\n]*)\]\(\s*([^)\s]+)\s*\)")
_ID = re.compile(r"^(\d+)-")
_TRAILING = ".,;:!?"
_TARGETS = ("https://www.remedymatch.com", "http://www.remedymatch.com",
            "https://remedymatch.com", "http://remedymatch.com",
            "www.remedymatch.com", "remedymatch.com")
_OPEN_MD = (re.compile(r"\[[^\]\n]*"), re.compile(r"\[[^\]\n]*\]"),
            re.compile(r"\[[^\]\n]*\]\([^)\s]*"))
_MAX_HOLD = 600


def is_legacy_store_url(url):
    """True for any remedymatch.com link. The match card uses this to refuse one outright."""
    return "remedymatch.com" in (url or "").lower()


def _product_id(url):
    path = re.split(r"[?#]", url or "", maxsplit=1)[0].rstrip("/")
    last = path.rsplit("/", 1)[-1] if "/" in path else ""
    m = _ID.match(last)
    return m.group(1) if m else None


def _page_for(slug, products):
    """The new page path for a catalog slug, or None when it must not be linked."""
    from dashboard.order_destination import destination_for
    from dashboard.products import superseded_slug
    from dashboard.related_products import DO_NOT_RECOMMEND

    live = superseded_slug(slug, products)
    rec = products.get(live) or {}
    if not rec or rec.get("inactive"):
        return None
    if slug in DO_NOT_RECOMMEND or live in DO_NOT_RECOMMEND:
        return None
    return destination_for(live)


def build_map(products):
    """{old store id: new page path, or None when there is no linkable product}."""
    out = {}
    for slug, p in (products or {}).items():
        url = (p or {}).get("url") or ""
        if "remedymatch.com" not in url.lower():
            continue
        pid = _product_id(url)
        if not pid:
            continue
        new = _page_for(slug, products)
        if out.get(pid):
            continue  # a linkable record already claimed this id
        out[pid] = new
    return out


_CACHE = {"products": None, "map": {}}


def _map_for(products):
    if products is None:
        from dashboard import products as _products
        products = _products._cached_products()
    if _CACHE["products"] is not products:
        _CACHE["map"] = build_map(products)
        _CACHE["products"] = products
    return _CACHE["map"], products


def _slug_fallback(url, products):
    """Second step, only for an id the catalog does not carry: the last path segment minus
    its leading "<id>-", when that is EXACTLY a catalog slug. 11 of knowledge's 18 id misses
    name a live product this way (73-microbiome). Never fuzzier than an exact slug."""
    path = re.split(r"[?#]", url or "", maxsplit=1)[0].rstrip("/")
    slug = re.sub(r"^\d+-", "", path.rsplit("/", 1)[-1]).lower()
    if not slug or slug not in products:
        return None
    return _page_for(slug, products)


def _new_url(url, base_url, mp, products):
    pid = _product_id(url)
    if not pid:
        return None
    path = mp[pid] if pid in mp else _slug_fallback(url, products)
    return (base_url or "").rstrip("/") + path if path else None


def rewrite_text(text, base_url, products=None):
    if not text:
        return ""
    mp, products = _map_for(products)
    entry = (base_url or "").rstrip("/") + BROWSE_ENTRY_PATH

    def _markdown(m):
        label, target = m.group(1), m.group(2)
        if not _URL.fullmatch(target):
            return m.group(0)
        new = _new_url(target, base_url, mp, products)
        return f"[{label}]({new})" if new else label

    def _bare(m):
        url, trail = m.group(0), ""
        while url and url[-1] in _TRAILING:
            url, trail = url[:-1], url[-1] + trail
        return (_new_url(url, base_url, mp, products) or entry) + trail

    return _URL.sub(_bare, _MD.sub(_markdown, text))


def _hold_from(buf):
    """Index from which `buf` must be held back because a store link may still be forming."""
    n = len(buf)
    hold = n
    j = max(buf.rfind(c) for c in (" ", "\n", "\t", "\r")) + 1
    tok = buf[j:].lower()
    if tok:
        if "remedymatch" in tok:
            hold = j
        else:
            for k in range(len(tok)):
                s = tok[k:]
                if any(t.startswith(s) for t in _TARGETS):
                    hold = j + k
                    break
        if hold < n and "](" in tok:
            k = buf.rfind("[", 0, j)
            if k != -1 and "\n" not in buf[k:j] and j - k <= _MAX_HOLD:
                hold = min(hold, k)
    i = buf.rfind("[")
    if i != -1 and n - i <= _MAX_HOLD and any(p.fullmatch(buf, i) for p in _OPEN_MD):
        hold = min(hold, i)
    return hold


def rewrite_stream(deltas, base_url, products=None):
    """Yield rewritten text for a stream of text deltas. Drains the input fully."""
    buf = ""
    for d in deltas:
        buf += d or ""
        h = _hold_from(buf)
        if h > 0:
            out = rewrite_text(buf[:h], base_url, products)
            buf = buf[h:]
            if out:
                yield out
    if buf:
        out = rewrite_text(buf, base_url, products)
        if out:
            yield out
