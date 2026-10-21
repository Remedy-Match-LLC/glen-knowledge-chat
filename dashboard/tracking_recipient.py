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


def _result(email=None, source=None, conflict=False, reason="", candidates=(),
            dropship=False, name_agrees=False, to_client=False):
    return {"email": email, "source": source, "conflict": conflict, "reason": reason,
            "candidates": sorted(set(candidates)), "dropship": dropship,
            "name_agrees": name_agrees, "to_client": to_client}


def _one_email(found):
    """found: list of (email, agrees). Unique email -> it; several -> conflict."""
    emails = {e.strip().lower() for e, _ in found if (e or "").strip()}
    if len(emails) == 1:
        return emails.pop(), False
    return None, len(emails) > 1


def _street_after_business_line(shipment):
    """The label's street without its first line, when that first line looks like a
    business or care-of name: "Fukushima Health & Wellbeing / 32703 RAY CT" -> "32703 RAY
    CT". None unless the label has two or more street lines and the rest starts with a
    house number, so "12 Main St / Apt 3" is never cut down to "Apt 3"."""
    lines = [ln.strip() for ln in str(shipment.get("address_block") or "").split(" / ")
             if ln.strip()]
    street_lines = lines[:-1]            # the last line is city, state and ZIP
    if len(street_lines) < 2:
        return None
    rest = " ".join(street_lines[1:])
    first = re.findall(r"[a-z0-9#]+", _ascii(rest))[:1]
    return rest if first and first[0][:1].isdigit() else None


def resolve_by_address(cx, shipment):
    """{email, source: 'board'|'fmp'|None, conflict, reason, candidates, dropship,
    name_agrees}. `dropship` is True when the email came from a drop-ship order;
    `name_agrees` when a board order's own name agrees with the label.

    A label's business line is joined onto its street, and then the street matches
    nothing on file: Fukushima's 6 labels on 2026-10-14 became 6 blank drafts. When the
    whole street finds nobody, it is tried once more without that line, and then a board
    order must ALSO agree on the name, because a shortened street is a weaker key.

    A failure is rolled back before it is raised. On Postgres the aborted transaction
    would otherwise make the watcher's record_shipment fail, and an unrecorded box is
    drafted again on every run."""
    try:
        return _resolve_by_address(cx, shipment)
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        raise


def _resolve_by_address(cx, shipment):
    name = shipment.get("recipient_name") or ""
    z = zip5(shipment.get("zip"))
    sk = street_key(shipment.get("street"))
    if not z or not sk:
        return _result(reason="label has no street or ZIP")
    found = _resolve_at(cx, name, z, sk, require_name=False)
    if found["email"] or found["conflict"]:
        return found
    alt = _street_after_business_line(shipment)
    if alt and street_key(alt) != sk:
        again = _resolve_at(cx, name, z, street_key(alt), require_name=True)
        if again["email"] or again["conflict"]:
            again["reason"] += " (street read without the label's first line)"
            return again
    return found


def _dropship_columns(cx):
    """SELECT list for the drop-ship columns. Older databases may lack them; select
    NULL rather than fail, because a failed read here empties the whole board step."""
    from dashboard import db as _db
    return ", ".join(c if _db.column_exists(cx, "orders", c) else f"NULL AS {c}"
                     for c in ("source", "practitioner_id", "recipient_email"))


def _switched_on(cx):
    from dashboard import practitioner_settings as _ps
    return _ps.pids_with_client_review_emails(cx)


def _ship_to(order):
    try:
        a = json.loads(order.get("address_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return a if isinstance(a, dict) else {}


def _split_name(full):
    return ((full or "").split(" ", 1) + [""])[:2]


def _name_words(full):
    return [w for w in (_alnum(t) for t in re.findall(r"[a-z0-9'-]+", _ascii(full))) if w]


def client_name_agrees(label_name, ship_name):
    """Does a label name the client on a drop-ship ship-to? Used only together with a
    matching address. The full names agree once normalised, or the first initials
    agree and they share a later word, so a middle or a double surname still agrees:
    'Mary Ann Smith' with 'Mary Smith', 'Maria Garcia Lopez' with 'Maria Garcia'."""
    a, b = _name_words(label_name), _name_words(ship_name)
    if not a or not b:
        return False
    if "".join(a) == "".join(b) or names_agree(label_name, *_split_name(ship_name)):
        return True
    return a[0][0] == b[0][0] and bool(set(a[1:]) & set(b[1:]))


def _street_keys(a):
    """The keys an order's ship-to can be matched by: street with address2 (the unit,
    as a label prints it) and street alone."""
    street = a.get("street") or a.get("address1") or ""
    unit = a.get("address2") or a.get("street2") or ""
    keys = {street_key(f"{street} {unit}"), street_key(street)}
    keys.discard("")
    return keys


def shipment_from_block(recipient_name, address_block):
    """A recorded shipment row as the label dict the hold reads. The block is
    'street lines / CITY ST 12345-6789 US', as the watcher stored it."""
    lines = [ln.strip() for ln in str(address_block or "").split(" / ") if ln.strip()]
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", lines[-1]) if lines else None
    return {"recipient_name": recipient_name or "", "street": " ".join(lines[:-1]),
            "zip": m.group(1) if m else "", "address_block": address_block or ""}


def _order_name(order):
    """The person the parcel is for. A drop-ship's `name` is the practitioner's, so
    its client's name is read from the ship-to instead."""
    if order.get("source") == "dropship":
        return _ship_to(order).get("name") or ""
    return order.get("name") or ""


def _dropship_to(order, switched_on):
    """A drop-ship order's tracking address: the client only when that practitioner
    turned client mail on and the order names a client; otherwise the practitioner."""
    pid = str(order.get("practitioner_id") or "").strip()
    client = (order.get("recipient_email") or "").strip()
    if pid and pid in switched_on and client:
        return client
    return order.get("email")


def dropship_holds(cx):
    """Every drop-ship client whose practitioner has client mail off, from orders in
    any status: [{order_id, prac, client, name, zip, streets}]. `client` is '' when the
    practitioner typed no email, so the hold also works by ship-to address and name.
    A client of two practitioners is held while either one has the switch off.

    Read fresh for every send, so a switch turned off mid-run holds the next email.
    None when the read fails: the caller then drafts rather than send blind. A
    database without the drop-ship columns has no drop-ship clients and returns []."""
    from dashboard import db as _db
    try:
        if not _db.column_exists(cx, "orders", "source"):
            return []
        rows = cx.execute(
            f"SELECT id, email, address_json, {_dropship_columns(cx)} FROM orders "
            "WHERE source = 'dropship'").fetchall()
        switched_on = _switched_on(cx)
    except Exception:
        try:
            cx.rollback()      # on Postgres a failed read blocks the next statement
        except Exception:
            pass
        return None
    holds = []
    for order_id, prac, address_json, _source, pid, client in rows:
        if str(pid or "").strip() in switched_on:
            continue
        a = _ship_to({"address_json": address_json})
        holds.append({"order_id": order_id,
                      "prac": (prac or "").strip().lower(),
                      "client": (client or "").strip().lower(),
                      "name": a.get("name") or "",
                      "zip": zip5(a.get("zip") or a.get("postal_code")),
                      "streets": _street_keys(a)})
    return holds


def holds_touching(holds, shipment, to=None):
    """The holds this parcel cannot be told apart from: the held client's own email, or
    the same full ship-to address AND a name that agrees. A name alone never holds:
    surnames repeat across the country, and a stranger would lose their tracking."""
    z = zip5(shipment.get("zip"))
    streets = {street_key(shipment.get("street"))}
    alt = _street_after_business_line(shipment)
    if alt:
        streets.add(street_key(alt))
    streets.discard("")
    label = shipment.get("recipient_name") or ""
    to = (to or "").strip().lower()
    out = []
    for h in holds or []:
        if (h["client"] and h["client"] == to) or \
                (z and h["zip"] == z and h["streets"] & streets
                 and client_name_agrees(label, h["name"])):
            out.append(h)
    return out


def email_is_held(cx, email, holds=None):
    """Is this email a held drop-ship client for mail that is not about one parcel,
    such as the plan-choice email? True when it is a held client's typed email, or
    when any shipment recorded for it went to a held ship-to address and name. The
    same address-plus-name rule as the watcher. Raises when the holds cannot be read,
    so the caller sends nothing rather than send blind."""
    if holds is None:
        holds = dropship_holds(cx)
    if holds is None:
        raise RuntimeError("drop-ship holds could not be read")
    em = (email or "").strip().lower()
    if any(h["client"] and h["client"] == em for h in holds):
        return True
    if not holds:
        return False
    rows = cx.execute("SELECT recipient_name, address_block FROM shipments "
                      "WHERE lower(resolved_email) = ?", (em,)).fetchall()
    return any(holds_touching(holds, shipment_from_block(r[0], r[1])) for r in rows)


def _resolve_at(cx, name, z, sk, require_name):
    # 1. Board orders at this address.
    marks = ",".join("?" for _ in _OPEN)
    board = []
    for o in _rows(cx, f"SELECT email, name, address_json, {_dropship_columns(cx)} "
                       f"FROM orders WHERE status IN ({marks})", _OPEN):
        try:
            a = json.loads(o.get("address_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(a, dict):
            continue
        if zip5(a.get("zip") or a.get("postal_code")) == z and sk in _street_keys(a):
            board.append(o)

    def agrees(o):
        if o.get("source") == "dropship":
            return client_name_agrees(name, _order_name(o))
        return names_agree(name, *_split_name(_order_name(o)))

    # Glen, 2026-10-21: a drop-ship client gets mail from us only when that
    # practitioner turns it on. Drop-ship orders are weighed before the name filter
    # below, by the client's name on the ship-to, never the practitioner's.
    dropship = [o for o in board if o.get("source") == "dropship"]
    if dropship:
        own = [o for o in board if o.get("source") != "dropship" and agrees(o)]
        ours = [o for o in dropship if agrees(o)]
        if ours and not own:
            email, clash = _one_email([(_dropship_to(o, _switched_on(cx)), True)
                                       for o in ours])
            if email:
                pracs = {(o.get("email") or "").strip().lower() for o in ours}
                return _result(email, "board", dropship=True, to_client=email not in pracs,
                               reason="open drop-ship order at the ship-to address")
        elif own and not ours:
            # A housemate's own order: the label names them, not the drop-ship client.
            email, clash = _one_email([(o.get("email"), True) for o in own])
            if email:
                return _result(email, "board", name_agrees=True,
                               reason="own order at a drop-ship address, name agrees")
        return _result(conflict=True,
                       reason="drop-ship order at the address; the label name does not settle it",
                       candidates=[(o.get("email") or "").lower() for o in board])

    if require_name:
        board = [o for o in board if agrees(o)]
    if board:
        email, clash = _one_email([(o.get("email"), True) for o in board])
        if email:
            return _result(email, "board", name_agrees=any(agrees(o) for o in board),
                           reason="open board order at the ship-to address")
        # Several emails at one address (a household): narrow by the label's name.
        named = [o for o in board if agrees(o)]
        email, clash = _one_email([(o.get("email"), True) for o in named])
        if email:
            return _result(email, "board", name_agrees=True,
                           reason="board order at the address, name agrees")
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
