"""In-house customer records over the existing `people` table (Phase 1 of the
order-entry / proposed-invoice build). The People directory already carries
email/phone/name/city/state for imported contacts; here we add a full shipping
address and the lookup/save helpers the order-entry form needs. Pure functions
over a sqlite connection (cx) for testability; the people + orders tables live in
the same LOG_DB."""
import json
import re
from datetime import datetime, timezone

from dashboard import dbwrite
from dashboard.name_case import normalize_name

# Address columns added to `people` (city/state/country/phone already exist).
_ADDR_COLS = ("address1", "address2", "zip")

# Columns the order-entry customer picker reads back.
PICKER_COLS = ("id", "name", "first_name", "last_name", "email", "phone",
               "address1", "address2", "city", "state", "zip", "country")


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_people_address_columns(cx):
    """Additively migrate `people` to carry a full shipping address. Idempotent."""
    for col in _ADDR_COLS:
        try:
            cx.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:
            pass  # already present
    cx.commit()


def _person_row(cx, person_id):
    cx.row_factory = __import__("sqlite3").Row
    return cx.execute("SELECT * FROM people WHERE id=?", (int(person_id),)).fetchone()


def get_person(cx, person_id):
    row = _person_row(cx, person_id)
    if row is None:
        return None
    d = dict(row)
    return {k: d.get(k, "") for k in PICKER_COLS}


def find_people(cx, query, limit=10):
    """Case-insensitive LIKE match over name/email/phone for the picker. Returns
    client/known contacts first (those with an order history or a saved address)."""
    q = (query or "").strip()
    if not q:
        return []
    cx.row_factory = __import__("sqlite3").Row
    like = f"%{q.lower()}%"
    rows = cx.execute(
        "SELECT * FROM people WHERE lower(name) LIKE ? OR lower(email) LIKE ? "
        "OR lower(coalesce(first_name,'')||' '||coalesce(last_name,'')) LIKE ? "
        "OR replace(coalesce(phone,''),' ','') LIKE ? "
        "ORDER BY order_count DESC, last_order_date DESC LIMIT ?",
        (like, like, like, like, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rec = {k: d.get(k, "") for k in PICKER_COLS}
        # A record can match a name search via first_name/last_name while the
        # `name` column is blank OR holds a placeholder email (imported contacts
        # sometimes have the email copied into `name`, e.g. Miriam Lynn Nelson =
        # "heritagecms@aol.com"). In both cases synthesize a real name from
        # first/last so the order-entry picker fills the Name field with a name,
        # not the email. Only substitute when we actually have a first/last to use
        # — never blank out a name we can't replace.
        # (See test_find_people_synthesizes_name_from_first_last_when_blank
        #  and test_find_people_replaces_email_in_name_column.)
        nm = (rec.get("name") or "").strip()
        first_last = (str(rec.get("first_name") or "") + " "
                      + str(rec.get("last_name") or "")).strip()
        if first_last and (not nm or "@" in nm):
            rec["name"] = first_last
        out.append(rec)
    return out


def upsert_person_address(cx, person_id, addr):
    """Save a shipping address back onto a person so it's on file next time.
    Only non-empty fields overwrite existing values."""
    addr = addr or {}
    field_map = {
        "address1": addr.get("address1") or addr.get("street") or "",
        "address2": addr.get("address2") or "",
        "city": addr.get("city") or "",
        "state": addr.get("state") or "",
        "zip": addr.get("zip") or addr.get("postal") or "",
        "country": (addr.get("country") or "").upper(),
        "phone": addr.get("phone") or "",
    }
    sets, vals = [], []
    for col, val in field_map.items():
        if str(val).strip():
            sets.append(f"{col}=?")
            vals.append(str(val).strip())
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(int(person_id))
    cx.execute(f"UPDATE people SET {', '.join(sets)} WHERE id=?", vals)
    cx.commit()
    return True


def _canonical(cx, email):
    """The survivor's address for a merged one (person merge spec, 2026-09-26). Falls
    back to the address itself when the alias table is missing or unreadable."""
    e = (email or "").strip().lower()
    try:
        from dashboard import person_aliases as _pal
        return _pal.canonical_email(cx, e)
    except Exception:
        return e


def find_or_create_by_email(cx, *, email, name="", phone="", source="order-entry"):
    """Return an existing person id for this email, or create a minimal record.
    Email is the unique key on `people`. `source` is FIRST-TOUCH only: it is written
    solely on creation, so an existing person keeps their original acquisition
    source (e.g. a client already on file who later submits a product review stays
    at their first source, not 'product-review')."""
    em = _canonical(cx, email)
    if not em:
        return None
    row = cx.execute("SELECT id FROM people WHERE lower(email)=?", (em,)).fetchone()
    if row:
        return row[0]
    new_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO people (email, name, phone, source, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (em, normalize_name((name or "").strip()), (phone or "").strip(),
         (source or "order-entry").strip(), _now(), _now()))
    cx.commit()
    return new_id


def rename_by_email(cx, email, *, name, first_name=None, last_name=None):
    """Correct a customer's display name across their people record AND every order
    they have — both keyed by email. The invoice bills to the ORDER name, so a
    person-only rename wouldn't reach it. Only non-blank values are written; all
    other fields are left untouched. Returns {people_updated, orders_updated}."""
    em = (email or "").strip().lower()
    nm = normalize_name((name or "").strip())
    if not em or not nm:
        raise ValueError("email and non-blank name required")
    ts = _now()
    people_updated = cx.execute(
        "UPDATE people SET name=?, updated_at=? WHERE lower(email)=?", (nm, ts, em)).rowcount
    if (first_name or "").strip():
        cx.execute("UPDATE people SET first_name=? WHERE lower(email)=?",
                   (normalize_name((first_name or "").strip()), em))
    if (last_name or "").strip():
        cx.execute("UPDATE people SET last_name=? WHERE lower(email)=?",
                   (normalize_name((last_name or "").strip(), leading_particle=True), em))
    orders_updated = cx.execute(
        "UPDATE orders SET name=?, updated_at=? WHERE lower(email)=?", (nm, ts, em)).rowcount
    cx.commit()
    return {"people_updated": people_updated, "orders_updated": orders_updated}


def _rollback(cx):
    try:
        cx.rollback()
    except Exception:
        pass


def _order_address_shape(a):
    # Normalise the orders address_json shape ({street,...}) to the people shape.
    return {
        "address1": a.get("street") or a.get("address1") or "",
        "address2": a.get("address2") or "",
        "city": a.get("city") or "", "state": a.get("state") or "",
        "zip": a.get("zip") or "", "country": a.get("country") or "US",
    }


def last_address_for(cx, email, accept=None):
    """The most recent shipping address this email shipped to (from orders), so a
    repeat customer without a saved people-address still autofills.

    Only an address with a street counts. A newer order saved with a blank street
    (a hand-off, a portal order) used to win because its JSON was not empty, and
    it hid the older order that had the real address.

    accept, when given, maps a candidate to the address to use or {} to keep
    looking at older orders (the ship-to fallback passes us_ship_ready)."""
    em = (email or "").strip().lower()
    if not em:
        return {}
    rows = cx.execute(
        "SELECT address_json FROM orders WHERE lower(email)=? AND address_json IS NOT NULL "
        "AND address_json NOT IN ('', '{}') ORDER BY created_at DESC, id DESC",
        (em,)).fetchall()
    for row in rows:
        try:
            a = json.loads(row[0] if not hasattr(row, "keys") else row["address_json"])
        except Exception:
            continue
        if isinstance(a, dict) and (a.get("street") or a.get("address1") or "").strip():
            shaped = _order_address_shape(a)
            if accept is None:
                return shaped
            got = accept(shaped)
            if got:
                return got
    return {}


# FileMaker country text -> the two-letter code orders carry. Hawai'i ships as US.
_FMP_COUNTRY = {
    "USA": "US", "U.S.A.": "US", "US": "US", "U.S.": "US", "UNITED STATES": "US",
    "HAWAII": "US", "KINGDOM OF HAWAI'I": "US", "KINGDOM OF HAWAII": "US",
    "CANADA": "CA", "AUSTRALIA": "AU", "GREECE": "GR", "U.K.": "GB", "UK": "GB",
    "ENGLAND": "GB", "GREAT-BRITAIN": "GB", "GREAT BRITAIN": "GB", "UNITED KINGDOM": "GB",
    "GERMANY": "DE", "SWITZERLAND": "CH", "MEXICO": "MX", "FRANCE": "FR", "SPAIN": "ES",
    "JAPAN": "JP", "HUNGARY": "HU", "SWEDEN": "SE", "PORTUGAL": "PT", "ITALY": "IT",
    "DENMARK": "DK", "BULGARIA": "BG", "NEW ZEALAND": "NZ", "NEW-ZEALAND": "NZ",
    "NETHERLANDS": "NL", "IRELAND": "IE", "FINLAND": "FI", "SOUTH AFRICA": "ZA",
    "ISRAEL": "IL", "INDIA": "IN", "PHILIPPINES": "PH",
}
_US_ZIP = re.compile(r"^\d{5}(-\d{4})?$")


def _fmp_country(country, postal_code):
    """Two-letter code, or None when it cannot be told safely. A blank country
    counts as US only with a US ZIP."""
    c = (country or "").strip().upper()
    if not c:
        return "US" if _US_ZIP.match((postal_code or "").strip()) else None
    return _FMP_COUNTRY.get(c)


_US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "HAWAI'I": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI",
    "WYOMING": "WY", "PUERTO RICO": "PR", "GUAM": "GU",
}
_US_CODES = set(_US_STATES.values())


def us_state_code(state):
    """Two-letter US state code, or "" when it cannot be told."""
    st = " ".join((state or "").strip().upper().replace(".", "").split())
    if st in _US_CODES:
        return st
    return _US_STATES.get(st, "")


def us_ship_ready(addr):
    """A fallback address counts only when it can ship and be priced: US, with
    street, city, a known two-letter state and a US ZIP. Returns the address with
    country and state normalised, or {}.

    Round 1 review, 2026-10-03: a non-US fill made the hand-off fail with "We ship
    to US addresses only", where a blank one lets Rae finish it. A spelled-out
    "Hawaii" dropped GET, which is charged only on "HI"."""
    a = dict(addr or {})
    street = (a.get("address1") or a.get("street") or "").strip()
    country = _fmp_country(a.get("country"), a.get("zip")) or ""
    state = us_state_code(a.get("state"))
    if (not street or not (a.get("city") or "").strip() or country != "US"
            or not state or not _US_ZIP.match((a.get("zip") or "").strip())):
        return {}
    a["country"], a["state"] = "US", state
    return a


def fmp_address_for(cx, email):
    """The FileMaker address for this email, only when it is unambiguous.

    FileMaker addresses carry no shipping or billing type, 191 clients have more
    than one street, and 386 emails sit on more than one client (local copy,
    2026-10-03). Glen, 2026-10-03: "same street only". Every street on file for the
    email must be the same, or this returns {} and the order stays blank for Rae."""
    em = (email or "").strip().lower()
    if not em:
        return {}
    try:
        rows = cx.execute(
            "SELECT a.street, a.city, a.province, a.postal_code, a.country "
            "FROM fmp_client_addresses a JOIN fmp_clients c ON c.id_pk = a.id_fk_client "
            "WHERE lower(c.email)=? AND trim(coalesce(a.street,''))<>''", (em,)).fetchall()
    except Exception:
        _rollback(cx)   # keep a Postgres transaction usable for the next lookup
        return {}   # projection tables not loaded
    found = {}
    for r in rows:
        street, city, state, postal, country = (
            (r[k] if hasattr(r, "keys") else r[i]) or ""
            for i, k in enumerate(("street", "city", "province", "postal_code", "country")))
        lines = [ln.strip() for ln in street.replace("\r", "\n").split("\n") if ln.strip()]
        if not lines:
            continue   # whitespace only: SQL trim() leaves tabs and line breaks
        key = (" ".join(" ".join(lines).lower().replace(".", " ").replace(",", " ").split()),
               "".join(ch for ch in postal if ch.isalnum()).lower()[:5])
        # The same street on two records: keep the fuller one, so a blank province on
        # one copy cannot hide an agreeing complete copy.
        filled = sum(bool(str(v).strip()) for v in (city, state, postal, country))
        if key not in found or filled > found[key][0]:
            found[key] = (filled, lines, city, state, postal, country)
    if len(found) != 1:
        return {}
    _, lines, city, state, postal, country = next(iter(found.values()))
    code = _fmp_country(country, postal)
    if not code:
        return {}
    if not state.strip() and (country or "").strip().upper() in (
            "HAWAII", "KINGDOM OF HAWAI'I", "KINGDOM OF HAWAII"):
        state = "HI"
    return {"address1": lines[0], "address2": ", ".join(lines[1:]),
            "city": city.strip(), "state": state.strip(), "zip": postal.strip(),
            "country": code}


def people_address_for(cx, email):
    """The shipping address saved on this email's people record, or {} when it has
    no street. Several records can share an email: the most recently updated with a
    street wins."""
    em = (email or "").strip().lower()
    if not em:
        return {}
    try:
        row = cx.execute(
            "SELECT address1, address2, city, state, zip, country FROM people "
            "WHERE lower(email)=? AND trim(coalesce(address1,''))<>'' "
            "ORDER BY coalesce(updated_at,'') DESC, id DESC LIMIT 1", (em,)).fetchone()
    except Exception:
        _rollback(cx)   # keep a Postgres transaction usable for the next lookup
        return {}   # an older people table without the address columns
    if not row:
        return {}
    cols = ("address1", "address2", "city", "state", "zip", "country")
    a = {c: (row[c] if hasattr(row, "keys") else row[i]) or "" for i, c in enumerate(cols)}
    a["country"] = a["country"] or "US"
    return a
