"""Find a tracking email's recipient by the parcel's SHIP-TO ADDRESS.

Glen, 2026-09-22 (relayed by fulfillment): tracking emails send as soon as the number
exists; a partial name match auto-sends only when the ship-to address also matches; and
recipients "are likely somewhere accessible to be matched and entered". The watcher used
to match on NAME only. This module is the address step it now runs first.

Order of standing:
  1. an open board order at the same address. Its email is what the customer typed for
     this purchase, so it wins over FileMaker.
  2. a FileMaker client at the same address whose name agrees with the label.
Two agreeing FileMaker clients at one address with different emails are a conflict for
Rae, never the first row. A different surname never agrees, even at the right address.

Pure reads. A missing table resolves to nothing rather than raising, so the watcher
falls back to its name path instead of failing the parcel.
"""
import json
import re
import unicodedata

# Board orders that can still be this parcel's order. 'cancelled', 'delivered' and
# 'done' are settled records, the same exclusion link_shipment_to_orders makes.
_OPEN = ("new", "packed", "shipped")

_STREET_WORDS = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr",
    "lane": "ln", "boulevard": "blvd", "court": "ct", "place": "pl", "circle": "cir",
    "highway": "hwy", "parkway": "pkwy", "terrace": "ter", "trail": "trl",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "apartment": "apt", "suite": "ste", "unit": "apt", "number": "apt",
}

# Short or familiar first names that do not share an initial with the formal one.
# Same-initial pairs (Judy/Judith) already agree by initial.
_ALIASES = [
    {"william", "bill", "billy", "will", "liam"}, {"robert", "bob", "bobby", "rob"},
    {"richard", "dick", "rick", "rich"}, {"margaret", "peggy", "maggie", "meg"},
    {"john", "jack", "johnny"}, {"edward", "ted", "ed", "eddie", "ned"},
    {"sarah", "sally", "sara"}, {"mary", "polly", "molly", "mae"},
    {"henry", "hank", "harry"}, {"charles", "chuck", "charlie"},
    {"elizabeth", "beth", "liz", "betty", "betsy", "lisa", "eliza"},
    {"katherine", "kathryn", "catherine", "kate", "kathy", "cathy", "kitty"},
    {"susan", "sue", "suzy"}, {"anthony", "tony"}, {"james", "jim", "jimmy"},
    {"patricia", "pat", "patty", "trish"}, {"theodore", "ted", "teddy"},
]


def _ascii(value):
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).lower()


def _alnum(value):
    return re.sub(r"[^a-z0-9]", "", _ascii(value))


def zip5(value):
    return re.sub(r"\D", "", str(value or ""))[:5]


def street_key(street):
    """'12 Ocean View Drive, Apt. 3' -> '12oceanviewdrapt3'. Common words shortened so a
    typed label and an exported record compare equal."""
    words = re.findall(r"[a-z0-9#]+", _ascii(street))
    return "".join(_STREET_WORDS.get(w.replace("#", ""), w.replace("#", "apt"))
                   for w in words)


def _first_names_agree(a, b):
    a, b = _alnum(a), _alnum(b)
    if not a or not b:
        return False
    for group in _ALIASES:
        if a in group and b in group:
            return True
    # The same first initial is enough, because the caller has already required the
    # exact surname: Judy/Judith, J/Judith. It never lets Robert agree with Judith.
    return a[0] == b[0]


def names_agree(label_name, first, last):
    """Does the label's name agree with a record's first and last name?

    The surname must match exactly once spaces, hyphens and apostrophes are gone
    ('Roemer Brown' == 'Roemer-Brown'). The label's surname may span its last one to
    three words, since a label does not mark where a double surname starts."""
    tokens = re.findall(r"[a-z0-9']+", _ascii(label_name))
    tokens = [t for t in tokens if _alnum(t)]
    want_last = _alnum(last)
    if len(tokens) < 2 or not want_last:
        return False
    for span in (1, 2, 3):
        if span >= len(tokens):
            break
        if _alnum("".join(tokens[-span:])) == want_last:
            return _first_names_agree(tokens[0], first)
    return False


def _rows(cx, sql, params=()):
    try:
        cur = cx.execute(sql, params)
    except Exception:
        # On Postgres a failed statement aborts the transaction, and the watcher's
        # next write on this connection would fail with it. Nothing of ours is
        # pending here (reads only), so rolling back is safe.
        try:
            cx.rollback()
        except Exception:
            pass
        return []
    rows = cur.fetchall()
    if not rows:
        # Return before touching `description`: the Postgres cursor wrapper
        # (dashboard.db._PgCursor) has none, and reading it raised on every empty
        # result, which made the whole address step fail in production (#1790).
        return []
    if hasattr(rows[0], "keys"):          # sqlite3.Row, and Postgres HybridRow
        return [{k: r[k] for k in r.keys()} for r in rows]
    cols = [d[0] for d in (getattr(cur, "description", None) or [])]
    return [dict(zip(cols, r)) for r in rows]


def _result(email=None, source=None, conflict=False, reason="", candidates=()):
    return {"email": email, "source": source, "conflict": conflict, "reason": reason,
            "candidates": sorted(set(candidates))}


def _one_email(found):
    """found: list of (email, agrees). Unique email -> it; several -> conflict."""
    emails = {e.strip().lower() for e, _ in found if (e or "").strip()}
    if len(emails) == 1:
        return emails.pop(), False
    return None, len(emails) > 1


def resolve_by_address(cx, shipment):
    """{email, source: 'board'|'fmp'|None, conflict, reason, candidates}."""
    name = shipment.get("recipient_name") or ""
    z, sk = zip5(shipment.get("zip")), street_key(shipment.get("street"))
    if not z or not sk:
        return _result(reason="label has no street or ZIP")

    # 1. Board orders at this address.
    marks = ",".join("?" for _ in _OPEN)
    board = []
    for o in _rows(cx, f"SELECT email, name, address_json FROM orders "
                       f"WHERE status IN ({marks})", _OPEN):
        try:
            a = json.loads(o.get("address_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(a, dict):
            continue
        if zip5(a.get("zip") or a.get("postal_code")) == z and \
                street_key(a.get("street") or a.get("address1")) == sk:
            board.append(o)
    if board:
        email, clash = _one_email([(o.get("email"), True) for o in board])
        if email:
            return _result(email, "board", reason="open board order at the ship-to address")
        # Several emails at one address (a household): narrow by the label's name.
        named = [o for o in board
                 if names_agree(name, *(((o.get("name") or "").split(" ", 1) + [""])[:2]))]
        email, clash = _one_email([(o.get("email"), True) for o in named])
        if email:
            return _result(email, "board", reason="board order at the address, name agrees")
        return _result(conflict=True, reason="several board orders at the address",
                       candidates=[(o.get("email") or "").lower() for o in board])

    # 2. FileMaker clients at this address whose name agrees.
    agree = []
    for c in _rows(cx, "SELECT c.name_first, c.name_last, c.email, a.street, a.postal_code "
                       "FROM fmp_client_addresses a JOIN fmp_clients c "
                       "ON c.id_pk = a.id_fk_client WHERE a.postal_code LIKE ?", (z + "%",)):
        if zip5(c.get("postal_code")) != z or street_key(c.get("street")) != sk:
            continue
        if names_agree(name, c.get("name_first"), c.get("name_last")):
            agree.append(c)
    if not agree:
        return _result(reason="no board order or agreeing FileMaker client at the address")
    email, clash = _one_email([(c.get("email"), True) for c in agree])
    if email:
        return _result(email, "fmp", reason="FileMaker client at the address, name agrees")
    return _result(conflict=clash,
                   reason=("several agreeing FileMaker clients at the address" if clash
                           else "agreeing FileMaker client has no email"),
                   candidates=[(c.get("email") or "").lower() for c in agree if c.get("email")])
