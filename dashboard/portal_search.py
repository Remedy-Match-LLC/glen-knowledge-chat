"""Search in the client portal header, step 1 (plan 2026-11-06-portal-header-site-search).

One list, ranked by match, from three sources:

- "records": the signed-in client's own orders, reports and portal messages. The caller
  passes the email that the portal token (or sign-in session) resolved to, and every
  query here filters on that one email. Nothing in this module ever reads another
  client's rows, and nothing takes an email from the request.
- "store": products and programs, by the store's own listing rule
  (shop_catalog.listable), less the never-recommend list. Price and "can be bought now"
  come from the caller's orderability predicate (practitioner_portal.is_orderable).
- "page": public pages this app serves, read once per process from their static files.

Pure, no Flask. Snippets are the source's own wording, escaped first and then bolded.
"""
import html as _html
import json
import re
import threading
from html.parser import HTMLParser
from pathlib import Path

from dashboard.order_destination import destination_for
from dashboard.related_products import DO_NOT_RECOMMEND
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

def _order_item_names(items_json):
    try:
        items = json.loads(items_json or "[]")
    except (TypeError, ValueError):
        return []
    names = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            n = (it.get("name") or it.get("title") or it.get("slug") or "").strip()
        else:
            n = str(it or "").strip()
        if n:
            names.append(n)
    return names


def order_docs(cx, email):
    """The client's orders as the Orders tab shows them: paid receipts, and published
    invoices still to pay. Same filters as app._past_invoices_for and
    app._published_invoices_for. Legacy FileMaker invoices are not searched."""
    email = (email or "").strip().lower()
    if not email:
        return []
    try:
        rows = cx.execute(
            "SELECT total_cents, COALESCE(invoice_token,''), COALESCE(items_json,'[]'), "
            "COALESCE(paid_at, updated_at, created_at, ''), COALESCE(pay_status,''), "
            "COALESCE(status,''), COALESCE(portal_published,0) FROM orders "
            "WHERE lower(coalesce(email,''))=? AND coalesce(status,'')<>'cancelled' "
            "ORDER BY id DESC", (email,)).fetchall()
    except Exception as e:
        print(f"[portal-search] orders read failed: {e!r}", flush=True)
        try:
            cx.rollback()
        except Exception:
            pass
        return []
    docs = []
    for total, tok, items_json, when, pay, status, published in rows:
        amount = f"${(total or 0) / 100:.2f}"
        when = (when or "")[:10]
        if pay == "paid":
            title = f"Order receipt, {when}, {amount}" if when else f"Order receipt, {amount}"
        elif int(published or 0) == 1 and tok and status not in ("delivered", "done"):
            title = f"Invoice to pay, {amount}"
        else:
            continue
        docs.append({"source": RECORDS, "kind": "order", "title": title,
                     "body": ", ".join(_order_item_names(items_json)), "url": "#orders"})
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


def report_docs(cx, email, *, reports_unlocked, portal_content=None):
    """The client's published reports: confirmed stored reports and scan reveals, never
    a date the owner has put on hold. Remedy names are searched only when
    `reports_unlocked()` is true, because the portal blurs them otherwise."""
    from dashboard import biofield_reveals as _brv
    from dashboard import portal_biofield_reports as _pbr
    from dashboard import portal_view as _pv
    from dashboard import report_holds as _rh
    email = (email or "").strip().lower()
    if not email:
        return []
    _pbr.init_table(cx)
    _rh.init_table(cx)
    held = set(_rh.held_dates(cx, email) or [])
    show = None

    def _show():
        nonlocal show
        if show is None:
            try:
                show = bool(reports_unlocked())
            except Exception:
                show = False
        return show

    docs, seen = [], set()
    dates = _pbr.list_report_dates(cx, email)
    for d in dates:
        rep = _pbr.get_report(cx, email, d) or {}
        if d in held or (rep.get("status") or "confirmed") != "confirmed":
            seen.add(d)   # a draft or held report hides a reveal of the same date too
            continue
        content = rep.get("content") or {}
        kind = rep.get("report_kind") or _pbr.effective_kind(None, content)
        seen.add(d)
        docs.append({"source": RECORDS, "kind": "report", "title": _report_title(kind, d),
                     "body": _report_body(content, _show()), "url": "#biofield",
                     "scan_date": d})
    try:
        _brv.init_table(cx)
        reveals = _brv.list_for_email(cx, email)
    except Exception as e:
        print(f"[portal-search] reveals read failed: {e!r}", flush=True)
        reveals = []
    for r in reveals:
        d = r.get("scan_date") or ""
        if not d or d in seen or d in held:
            continue
        seen.add(d)
        content = _pv._reveal_as_report_content(r)
        docs.append({"source": RECORDS, "kind": "report",
                     "title": _report_title(_pbr.REMEDY_MATCH, d),
                     "body": _report_body(content, _show()), "url": "#biofield",
                     "scan_date": d})
    # A portal with no dated report shows the report held on the portal record itself.
    if not dates and not reveals and portal_content:
        pc = portal_content or {}
        if pc.get("layers") and (pc.get("biofield_status") or "confirmed") == "confirmed":
            docs.append({"source": RECORDS, "kind": "report",
                         "title": _report_title(_pbr.effective_kind(None, pc), ""),
                         "body": _report_body(pc, _show()), "url": "#biofield"})
    return docs


def message_docs(cx, email):
    from dashboard import portal_chat as _pchat
    email = (email or "").strip().lower()
    if not email:
        return []
    docs = []
    for m in _pchat.list_messages(cx, email, limit=100000):
        when = (m.get("created_at") or "")[:10]
        who = "you" if m.get("role") == _pchat.CLIENT else (m.get("author") or "Dr. Glen and the team")
        title = f"Message from {who}" + (f", {when}" if when else "")
        docs.append({"source": RECORDS, "kind": "message", "title": title,
                     "body": m.get("content") or "", "url": "#chat"})
    return docs


def record_docs(cx, email, *, reports_unlocked, portal_content=None):
    return (order_docs(cx, email)
            + report_docs(cx, email, reports_unlocked=reports_unlocked,
                          portal_content=portal_content)
            + message_docs(cx, email))


# ── the store ────────────────────────────────────────────────────────────────

def _ingredient_text(p):
    items = p.get("ingredients") or []
    if not isinstance(items, list):
        return str(items)
    return " ".join(str(i.get("name") if isinstance(i, dict) else i) for i in items)


def product_docs(products, get_product, is_orderable):
    """Every product the store lists, less DO_NOT_RECOMMEND. `products` is the raw
    catalog {slug: record}; `get_product` follows a retired slug to its successor."""
    seen, docs = set(), []
    for slug in (products or {}):
        if slug in DO_NOT_RECOMMEND:
            continue
        p = get_product(slug)
        if not listable(p):
            continue
        real = p.get("slug") or slug
        if real in seen or real in DO_NOT_RECOMMEND:
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
    """Rank every doc matching all terms, then return one page of results."""
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
