"""Search in the client portal header, step 1 (plan 2026-11-06-portal-header-site-search).

One list, ranked by match, from three sources:

- "records": the signed-in client's own orders, reports and portal messages. The caller
  passes the email that the portal token (or sign-in session) resolved to, and every
  query here filters on that one email. Nothing in this module ever reads another
  client's rows, and nothing takes an email from the request.
- "store": products and programs, by the store's own listing rule
  (shop_catalog.listable), and nothing else. Glen, 6 Nov 2026, on the do-not-recommend
  products the store already lists (Electrolyte Mineral Manna): "it can list them". Price and "can be bought now"
  come from the caller's orderability predicate (practitioner_portal.is_orderable).
- "page": public pages this app serves, read once per process from their static files.

Pure, no Flask. Snippets are the source's own wording, escaped first and then bolded.
"""
import html as _html
import json
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

from dashboard.order_destination import destination_for
from dashboard.shop_catalog import listable

PAGE_SIZE = 10
MIN_QUERY_CHARS = 2
MAX_TERMS = 8
SNIPPET_CHARS = 200

RECORDS, STORE, PAGE = "records", "store", "page"
SOURCE_LABELS = {RECORDS: "Your records", STORE: "Store", PAGE: "Page"}
_SOURCE_ORDER = {RECORDS: 0, STORE: 1, PAGE: 2}

# Retired destinations (Glen): a result naming one is never shown, whatever its source.
RETIRED_NAMES = ("practice better", "practicebetter", "skool", "clientclub", "client club")

# Public pages served by this app on illtowell.com and myhealingoasis.com, with the
# static file each one serves. Every path here is a GET route with no arguments that
# returns this file unchanged (tests/test_portal_search.py checks both halves). A page
# behind a flag names it in `gate`; the app supplies the callable and a closed gate
# leaves the page out of the index.
PAGES = (
    {"path": "/begin/match", "file": "begin-match.html"},
    {"path": "/begin/path", "file": "begin-path.html"},
    {"path": "/begin/ascend", "file": "begin-ascend.html"},
    {"path": "/begin/quiz", "file": "begin-quiz.html"},
    {"path": "/program-guides", "file": "program-guides.html"},
    {"path": "/shipping/international", "file": "shipping-international.html"},
    {"path": "/certification", "file": "certification.html"},
    {"path": "/affiliate", "file": "affiliate.html"},
    {"path": "/reorder", "file": "reorder.html"},
    {"path": "/learn/glossary", "file": "clinical-glossary-index.html"},
    {"path": "/learn/patterns", "file": "patterns-index.html"},
    {"path": "/body-map", "file": "body-map.html"},
    {"path": "/terms", "file": "terms.html"},
    {"path": "/privacy", "file": "privacy.html"},
    {"path": "/shop", "file": "shop.html", "gate": "shop"},
    {"path": "/product-review", "file": "product-review.html", "gate": "product_review"},
)

# Paths that get the header icon: the portal home by token or /portal/me, and the
# membership program page. Sign-in and other one-word routes under /portal are not
# client portal pages.
_NOT_A_PORTAL = frozenset({
    "login", "login-verify", "login-request", "password-login", "password-reset",
    "password-reset-request", "logout", "claim", "auth", "offer"})
_PORTAL_PAGE_RE = re.compile(r"^/portal/([^/]+)(?:/program)?/?$")

SCRIPT_TAG = '<script id="portal-search-assets" defer src="/static/js/portal-search.js?v=20261106"></script>'


# ── query and matching ───────────────────────────────────────────────────────

def terms_for(q):
    """Lower-cased words of the query, or [] when it is shorter than MIN_QUERY_CHARS."""
    q = (q or "").strip().lower()
    if len(q) < MIN_QUERY_CHARS:
        return []
    out = []
    for t in q.split():
        if t not in out:
            out.append(t)
    return out[:MAX_TERMS]


def names_retired(text):
    low = (text or "").lower()
    return any(n in low for n in RETIRED_NAMES)


def _score(title, body, terms):
    """None unless every term appears in title or body; else a match score."""
    t, b = (title or "").lower(), (body or "").lower()
    score = 0
    for term in terms:
        in_t, n_b = term in t, b.count(term)
        if not in_t and not n_b:
            return None
        score += (5 if in_t else 0) + min(n_b, 5)
    phrase = " ".join(terms)
    if len(terms) > 1:
        score += 4 if phrase in t else (2 if phrase in b else 0)
    if t == phrase:
        score += 5
    return score


def bold_html(text, terms):
    """Escape `text`, wrapping each case-insensitive match of a term in <b>. The text
    is split on matches first and each piece escaped on its own, so a term can never
    land inside an entity and no raw text is ever emitted."""
    text = text or ""
    words = sorted({t for t in terms if t}, key=len, reverse=True)
    if not words:
        return _html.escape(text)
    pat = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    out, last = [], 0
    for m in pat.finditer(text):
        out.append(_html.escape(text[last:m.start()]))
        out.append("<b>" + _html.escape(m.group(0)) + "</b>")
        last = m.end()
    out.append(_html.escape(text[last:]))
    return "".join(out)


def snippet_html(body, terms, size=SNIPPET_CHARS):
    """About `size` characters of `body` around its first match, bolded."""
    body = re.sub(r"\s+", " ", body or "").strip()
    low = body.lower()
    hits = [low.find(t) for t in terms if t and low.find(t) >= 0]
    pos = min(hits) if hits else 0
    start = max(0, pos - size // 3)
    if start:
        sp = body.rfind(" ", 0, start)
        start = sp + 1 if sp >= 0 and start - sp < 30 else start
    end = min(len(body), start + size)
    if end < len(body):
        sp = body.find(" ", end)
        end = sp if 0 <= sp - end < 30 else end
    piece = body[start:end]
    return (("… " if start else "") + bold_html(piece, terms)
            + (" …" if end < len(body) else ""))


# ── the client's own records ─────────────────────────────────────────────────
#
# Each source is the SAME selection the portal's own tab uses, handed in by the caller:
# the Orders tab's app._published_invoices_for / app._past_invoices_for, the report
# reads api_client_portal makes, and app._portal_chat_thread (the last 100 messages,
# exactly what the Messages card shows). Search never shows more than the portal shows.

def _safe_read(cx, fn, default):
    """fn(cx), or `default` when the table is missing. A failed read on Postgres aborts
    the transaction, so roll back before the next read."""
    try:
        return fn(cx)
    except Exception as e:
        print(f"[portal-search] read failed: {e!r}", flush=True)
        try:
            cx.rollback()
        except Exception:
            pass
        return default


def invoice_item_names(order):
    """Item names from the order an invoice link opens (app._invoice_order_for_token),
    which is what the invoice page shows to whoever holds that link."""
    names = []
    for it in (order or {}).get("items") or []:
        n = (it.get("name") or it.get("title") or it.get("slug") or "").strip() \
            if isinstance(it, dict) else str(it or "").strip()
        if n:
            names.append(n)
    return names


def order_docs(published, past, items_for_token):
    """Orders as the Orders tab lists them. `items_for_token(tok)` returns the item
    names the invoice page shows for that link, or [] when it shows none (an expired
    link, another client's order). A modern receipt with no link has no item names on
    the tab, so only its date and amount are searched. A FileMaker receipt's lines are
    shown on the tab, so they are searched."""
    docs = []
    for inv in published or []:
        tok = inv.get("token") or ""
        docs.append({"source": RECORDS, "kind": "order",
                     "title": f"Invoice to pay, ${inv.get('amount_dollars') or '0.00'}",
                     "body": ", ".join(items_for_token(tok) if tok else []), "url": "#orders"})
    for inv in past or []:
        tok = inv.get("token") or ""
        amount, when = f"${inv.get('amount_dollars') or '0.00'}", inv.get("when") or ""
        word = "Order receipt" if inv.get("paid") else "Invoice"
        title = f"{word}, {when}, {amount}" if when else f"{word}, {amount}"
        if tok:
            names = items_for_token(tok)
        else:
            names = [(it.get("description") or it.get("product_name") or "").strip()
                     for it in inv.get("items") or [] if isinstance(it, dict)]
        docs.append({"source": RECORDS, "kind": "order", "title": title,
                     "body": ", ".join(n for n in names if n), "url": "#orders"})
    return docs


def _report_body(content, show_remedies):
    content = content or {}
    parts = [content.get("greeting") or ""]
    for L in content.get("layers") or []:
        if not isinstance(L, dict):
            continue
        parts += [L.get("title") or "", L.get("meaning") or ""]
        if show_remedies:
            parts.append(L.get("remedy") or "")
    return " ".join(str(p) for p in parts if p)


def _report_title(kind, date):
    from dashboard import portal_biofield_reports as _pbr
    word = "Remedy Match" if kind == _pbr.REMEDY_MATCH else "Biofield report"
    return f"{word}, {date}" if date else word


def report_docs(cx, email, *, show_remedies, portal_content=None):
    """The client's published reports, from the reads api_client_portal makes: confirmed
    stored reports and scan reveals, never a date the owner has put on hold. Remedy
    names are searched only when `show_remedies`, because the portal blurs them
    otherwise. The caller computes that BEFORE opening `cx`."""
    from dashboard import biofield_reveals as _brv
    from dashboard import portal_biofield_reports as _pbr
    from dashboard import portal_view as _pv
    from dashboard import report_holds as _rh
    email = (email or "").strip().lower()
    if not email:
        return []
    held = set(_safe_read(cx, lambda c: _rh.held_dates(c, email), set()) or [])
    dates = _safe_read(cx, lambda c: _pbr.list_report_dates(c, email), [])
    docs, seen = [], set()
    for d in dates:
        rep = _safe_read(cx, lambda c: _pbr.get_report(c, email, d), None) or {}
        seen.add(d)   # a draft or held report hides a reveal of the same date too
        if d in held or (rep.get("status") or "confirmed") != "confirmed":
            continue
        content = rep.get("content") or {}
        kind = rep.get("report_kind") or _pbr.effective_kind(None, content)
        docs.append({"source": RECORDS, "kind": "report", "title": _report_title(kind, d),
                     "body": _report_body(content, show_remedies), "url": "#biofield",
                     "scan_date": d})
    reveals = _safe_read(cx, lambda c: _brv.list_for_email(c, email), [])
    for r in reveals:
        d = r.get("scan_date") or ""
        if not d or d in seen or d in held:
            continue
        seen.add(d)
        content = _pv._reveal_as_report_content(r)
        docs.append({"source": RECORDS, "kind": "report",
                     "title": _report_title(_pbr.REMEDY_MATCH, d),
                     "body": _report_body(content, show_remedies), "url": "#biofield",
                     "scan_date": d})
    # No dated report and no reveal: the portal shows the report held on the portal
    # record itself, with its own biofield_status. A hold on its current_scan_date
    # keeps it out too, so a held report can never be found here.
    if not dates and not reveals and portal_content:
        pc = portal_content or {}
        cur = (pc.get("current_scan_date") or "").strip()
        if (pc.get("layers") and (pc.get("biofield_status") or "confirmed") == "confirmed"
                and not (cur and cur in held)):
            docs.append({"source": RECORDS, "kind": "report",
                         "title": _report_title(_pbr.effective_kind(None, pc), ""),
                         "body": _report_body(pc, show_remedies), "url": "#biofield"})
    return docs


def message_docs(messages):
    """The Messages card's thread (app._portal_chat_thread)."""
    from dashboard import portal_chat as _pchat
    docs = []
    for m in messages or []:
        when = (m.get("created_at") or "")[:10]
        who = "you" if m.get("role") == _pchat.CLIENT else (m.get("author") or "Dr. Glen and the team")
        title = f"Message from {who}" + (f", {when}" if when else "")
        docs.append({"source": RECORDS, "kind": "message", "title": title,
                     "body": m.get("content") or "", "url": "#chat"})
    return docs


# A built corpus is kept briefly so typing and paging do not reload everything. Keyed by
# the email the portal token resolved to, and by nothing else.
CORPUS_TTL_S = 60
_CORPUS_MAX = 256
_CORPUS_LOCK = threading.Lock()
_CORPUS = {}


def cached(key, build, ttl=None):
    ttl = CORPUS_TTL_S if ttl is None else ttl
    now = time.monotonic()
    with _CORPUS_LOCK:
        hit = _CORPUS.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _CORPUS_LOCK:
        if len(_CORPUS) >= _CORPUS_MAX:
            for k, _v in sorted(_CORPUS.items(), key=lambda kv: kv[1][0])[:_CORPUS_MAX // 4]:
                _CORPUS.pop(k, None)
        _CORPUS[key] = (now, value)
    return value


def reset_caches():
    with _CORPUS_LOCK:
        _CORPUS.clear()
    reset_page_index()


# ── the store ────────────────────────────────────────────────────────────────

def _ingredient_text(p):
    items = p.get("ingredients") or []
    if not isinstance(items, list):
        return str(items)
    return " ".join(str(i.get("name") if isinstance(i, dict) else i) for i in items)


def product_docs(products, get_product, is_orderable):
    """Every product the store lists, by shop_catalog.listable: inactive, info_only and
    service records stay out. Do-not-recommend products the store lists are searched
    too. Glen, 6 Nov 2026: "it can list them." Listing is not recommending.
    `products` is the raw catalog {slug: record}; `get_product` follows a retired slug
    to its successor."""
    seen, docs = set(), []
    for slug in (products or {}):
        p = get_product(slug)
        if not listable(p):
            continue
        real = p.get("slug") or slug
        if real in seen:
            continue
        seen.add(real)
        name = (p.get("name") or real).strip()
        body = " ".join((visible_text(p.get("description") or ""), _ingredient_text(p)))
        orderable = bool(is_orderable(real, p))
        cents = int(p.get("price_cents") or 0)
        docs.append({"source": STORE, "kind": "product", "title": name, "body": body,
                     "url": destination_for(real), "slug": real, "orderable": orderable,
                     "price": f"${cents / 100:.2f}" if orderable and cents > 0 else ""})
    return docs


# ── public pages ─────────────────────────────────────────────────────────────

class _TextParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        elif tag in self._SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in self._SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.text.append(data)


def _parse(markup):
    p = _TextParser()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:
        pass
    return p


def visible_text(markup):
    if "<" not in (markup or ""):
        return re.sub(r"\s+", " ", markup or "").strip()
    return re.sub(r"\s+", " ", " ".join(_parse(markup).text)).strip()


def build_page_index(static_dir, gates=None, pages=PAGES):
    """[{source, title, body, url}] for every listed page whose gate is open and whose
    text names no retired destination."""
    gates = gates or {}
    out = []
    for pg in pages:
        gate = pg.get("gate")
        if gate:
            try:
                if not gates.get(gate, lambda: False)():
                    continue
            except Exception:
                continue
        try:
            markup = (Path(static_dir) / pg["file"]).read_text(encoding="utf-8")
        except OSError as e:
            print(f"[portal-search] page {pg['path']} unreadable: {e!r}", flush=True)
            continue
        parsed = _parse(markup)
        title = re.sub(r"\s+", " ", "".join(parsed.title)).strip() or pg["path"]
        body = re.sub(r"\s+", " ", " ".join(parsed.text)).strip()
        if names_retired(title + " " + body):
            continue
        out.append({"source": PAGE, "kind": "page", "title": title, "body": body,
                    "url": pg["path"]})
    return out


_INDEX_LOCK = threading.Lock()
_INDEX = None


def page_index(static_dir, gates=None):
    """The page index, built once per process: a deploy starts a new process."""
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = build_page_index(static_dir, gates)
        return _INDEX


def reset_page_index():
    global _INDEX
    with _INDEX_LOCK:
        _INDEX = None


# ── ranking and paging ───────────────────────────────────────────────────────

def search(docs, terms, page=1, page_size=PAGE_SIZE):
    """Rank every doc matching all terms, then return one page of results.

    Paging is by offset over a fresh ranking on each request. If the client's records
    change between two page requests a result can shift by one place across a page
    boundary. That is accepted for a 10-per-page search of one client's own records
    (review round 2, declined)."""
    hits = []
    for d in docs if terms else []:
        if names_retired(d.get("title", "") + " " + d.get("body", "")):
            continue
        s = _score(d.get("title"), d.get("body"), terms)
        if s is None:
            continue
        hits.append((s, d))
    hits.sort(key=lambda h: (-h[0], _SOURCE_ORDER.get(h[1]["source"], 9),
                             (h[1].get("title") or "").lower()))
    total = len(hits)
    pages = max(1, -(-total // page_size))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    results = []
    for s, d in hits[(page - 1) * page_size: page * page_size]:
        r = {"source": d["source"], "label": SOURCE_LABELS[d["source"]], "kind": d["kind"],
             "title": d.get("title") or "", "url": d.get("url") or "",
             "snippet_html": snippet_html(d.get("body") or d.get("title") or "", terms),
             "score": s}
        if d["source"] == STORE:
            r["price"] = d.get("price") or ""
            r["orderable"] = bool(d.get("orderable"))
        if d.get("scan_date"):
            r["scan_date"] = d["scan_date"]
        results.append(r)
    return {"total": total, "page": page, "pages": pages, "page_size": page_size,
            "results": results}


# ── page injection ───────────────────────────────────────────────────────────

def is_portal_page(path):
    m = _PORTAL_PAGE_RE.match(path or "")
    return bool(m) and m.group(1) not in _NOT_A_PORTAL


def inject_html(markup):
    """Add the search script before </head>. Idempotent; no-op without </head>."""
    if 'id="portal-search-assets"' in (markup or "") or "</head>" not in (markup or ""):
        return markup
    return markup.replace("</head>", SCRIPT_TAG + "\n</head>", 1)
