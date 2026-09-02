"""Console practitioner-admin — logic layer.

Backs the /console/practitioners page: a console-gated "add practitioner" form
and a roster (grouped Coaches / Practitioners) with per-row stats and edit
actions. The Supabase reads/writes live here too, but the pure pieces — input
validation, SQLite activity aggregation, and the row-merge that feeds the UI —
are split out so they're testable without a database.

Reuses dashboard.practitioner_portal for the SQLite order tables, magic-link
tokens, and the geocoder where possible.
"""
from __future__ import annotations

import sqlite3
from dashboard import db
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from dashboard import practitioner_portal as pp

VALID_ROLES = ("coach", "licensed")
N_MODULES = 12


# ── validation ──────────────────────────────────────────────────────────────────

def validate_new_practitioner(payload: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Pure validation for the add-practitioner form. Returns (clean, None) or
    (None, error_message). Cert level (0-12) is independent of wholesale access."""
    email = (payload.get("email") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    role = (payload.get("role") or "").strip().lower()
    if "@" not in email or "." not in email:
        return None, "A valid email is required."
    if not name:
        return None, "Your name is required."
    if role not in VALID_ROLES:
        return None, "Choose a role: coach or licensed practitioner."
    raw_level = payload.get("level", 0)
    try:
        level = int(raw_level)
    except (TypeError, ValueError):
        return None, "Cert level must be a number 0–12."
    level = max(0, min(N_MODULES, level))
    return {
        "email": email,
        "name": name,
        "portal_role": role,
        "credentials": (payload.get("credentials") or "").strip() or None,
        "wholesale_access": bool(payload.get("wholesale_access")),
        "level": level,
        "list_in_finder": bool(payload.get("list_in_finder")),
        "city": (payload.get("city") or "").strip() or None,
        "state": (payload.get("state") or "").strip().upper() or None,
        "country": (payload.get("country") or "").strip().upper() or "US",
        "send_invite": bool(payload.get("send_invite")),
    }, None


# ── SQLite activity aggregation ──────────────────────────────────────────────────

def aggregate_activity(db_path: str) -> dict:
    """Per-practitioner order + dispensary aggregates, keyed by practitioner_id.
    Returns {pid: {orders, spent_cents, last_order, disp_count, disp_credit_cents,
    disp_bottles}}. A practitioner with no activity is simply absent."""
    out: dict = {}
    with db.connect(db_path) as cx:
        pp._ensure_orders_table(cx)
        pp._ensure_dispensary_table(cx)
        for pid, n, spent, last in cx.execute(
            "SELECT practitioner_id, COUNT(*), COALESCE(SUM(total_cents),0), MAX(created_at) "
            "FROM wholesale_orders GROUP BY practitioner_id"
        ).fetchall():
            out.setdefault(pid, {}).update(
                {"orders": n, "spent_cents": int(spent or 0), "last_order": last})
        for pid, n, credit, bottles in cx.execute(
            "SELECT practitioner_id, COUNT(*), COALESCE(SUM(credit_earned_cents),0), "
            "COALESCE(SUM(bottles),0) FROM dispensary_orders GROUP BY practitioner_id"
        ).fetchall():
            out.setdefault(pid, {}).update(
                {"disp_count": n, "disp_credit_cents": int(credit or 0),
                 "disp_bottles": int(bottles or 0)})
    return out


# ── row merge (feeds the roster UI) ──────────────────────────────────────────────

_ACTIVITY_DEFAULTS = {
    "orders": 0, "spent_cents": 0, "last_order": None,
    "disp_count": 0, "disp_credit_cents": 0, "disp_bottles": 0,
}

_LIST_COLS = (
    "id, name, email, portal_role, credentials, modules_completed, "
    "wallet_balance_cents, wholesale_unlocked_at, application_status, "
    "show_contact, city, state, country, tier"
)
_TIER_FOR_ROLE = {"coach": "panel_in_cert", "licensed": "org_member"}


# ── Supabase reads/writes (thin; monkeypatched in route tests) ───────────────────

def list_practitioners(q: Optional[str] = None) -> List[dict]:
    """Every portal practitioner (portal_role set), newest-active first. Optional
    free-text filter across name/email/credentials/city."""
    from db_supabase import supabase_cursor
    sql = f"SELECT {_LIST_COLS} FROM practitioners WHERE portal_role IS NOT NULL"
    params: list = []
    term = (q or "").strip()
    if term:
        sql += (" AND (name ILIKE %s OR email ILIKE %s OR credentials ILIKE %s "
                "OR city ILIKE %s)")
        params += [f"%{term}%"] * 4
    sql += " ORDER BY name NULLS LAST"
    with supabase_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in (cur.fetchall() or [])]


def create_or_update_practitioner(clean: dict, *, now=None) -> str:
    """Insert or link (by email) a portal practitioner from the add form. Cert level
    and wholesale access are set independently. Returns the practitioner_id."""
    from db_supabase import supabase_cursor
    ts = now or datetime.now(timezone.utc)
    unlocked = ts if clean["wholesale_access"] else None
    tier = _TIER_FOR_ROLE.get(clean["portal_role"], "org_member")
    with supabase_cursor() as cur:
        row = pp.find_row_for_email(cur, clean["email"], "id, tier")
        if row:
            pid = row["id"]
            cert_tier = pp.cert_tier_for_level(dict(row).get("tier"), clean["level"])
            cur.execute(
                "UPDATE practitioners SET portal_role=%s, credentials=COALESCE(%s, credentials), "
                "modules_completed=%s, tier=COALESCE(%s, tier), "
                "wholesale_unlocked_at=CASE WHEN %s THEN COALESCE(wholesale_unlocked_at, %s) "
                "ELSE NULL END, "
                "show_contact=%s, city=COALESCE(%s, city), state=COALESCE(%s, state), "
                "name=COALESCE(NULLIF(name,''), %s), updated_at=now() WHERE id=%s",
                (clean["portal_role"], clean["credentials"], clean["level"], cert_tier,
                 clean["wholesale_access"], ts, clean["list_in_finder"],
                 clean["city"], clean["state"], clean["name"], pid))
        else:
            cur.execute(
                "INSERT INTO practitioners (tier, name, email, portal_role, credentials, "
                "modules_completed, wholesale_unlocked_at, show_contact, city, state) "
                "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s "
                + pp.NO_PORTAL_ROW_GUARD + " RETURNING id",
                (tier, clean["name"], clean["email"], clean["portal_role"],
                 clean["credentials"], clean["level"], unlocked,
                 clean["list_in_finder"], clean["city"], clean["state"],
                 clean["email"]))
            pid = pp._inserted_id_or_refuse(cur, clean["email"])
    return str(pid)


def set_level_and_access(pid: str, level: int, wholesale_access: bool, *, now=None) -> Optional[str]:
    """Set cert level (0-12) and toggle wholesale access independently. Granting
    access keeps any existing unlock timestamp; revoking clears it.

    Finishing the programme also promotes the practitioner: a certification row is
    moved to panel_certified at 12 modules and back to panel_in_cert below it. Any
    other tier is a scraped directory practitioner's and is left untouched (see
    practitioner_portal.cert_tier_for_level). Returns the resulting tier so the
    console can report the certification state back to the operator."""
    from db_supabase import supabase_cursor
    ts = now or datetime.now(timezone.utc)
    lvl = max(0, min(N_MODULES, int(level)))
    with supabase_cursor() as cur:
        cur.execute("SELECT tier FROM practitioners WHERE id=%s LIMIT 1", (str(pid),))
        row = cur.fetchone()
        stored = dict(row).get("tier") if row else None
        tier = pp.cert_tier_for_level(stored, lvl)
        cur.execute(
            "UPDATE practitioners SET modules_completed=%s, tier=COALESCE(%s, tier), "
            "wholesale_unlocked_at=CASE WHEN %s THEN COALESCE(wholesale_unlocked_at, %s) "
            "ELSE NULL END, updated_at=now() WHERE id=%s",
            (lvl, tier, bool(wholesale_access), ts, str(pid)))
    return tier or stored


def set_credentials(pid: str, credentials) -> None:
    """Set a practitioner's classification/credentials (blank clears it)."""
    from db_supabase import supabase_cursor
    cred = (credentials or "").strip() or None
    with supabase_cursor() as cur:
        cur.execute("UPDATE practitioners SET credentials=%s, updated_at=now() WHERE id=%s",
                    (cred, str(pid)))


def set_finder_visibility(pid: str, show: bool) -> None:
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute("UPDATE practitioners SET show_contact=%s, updated_at=now() WHERE id=%s",
                    (bool(show), str(pid)))


def geocode_and_set_location(pid: str, city: Optional[str], state: Optional[str],
                             country: Optional[str] = None) -> None:
    """Set city/state/country and (best-effort) city-level lat/lng via Mapbox so the
    practitioner places in the finder. country is an ISO-2 code (defaults US); it
    biases the geocoder and is stored. lat/lng/quality only set if geocoding hits."""
    from db_supabase import supabase_cursor
    from scrapers.practitioner_finder.geocode import geocode_place
    city = (city or "").strip() or None
    state = (state or "").strip().upper() or None
    cc = (country or "").strip().upper() or "US"
    lat = lng = None
    if city or state:
        place = ", ".join([p for p in (city, state, cc) if p])
        try:
            lat, lng = geocode_place(place, cc)
        except Exception:
            lat = lng = None
    with supabase_cursor() as cur:
        if lat is not None and lng is not None:
            cur.execute(
                "UPDATE practitioners SET city=%s, state=%s, country=%s, lat=%s, lng=%s, "
                "geocode_quality='city', updated_at=now() WHERE id=%s",
                (city, state, cc, lat, lng, str(pid)))
        else:
            cur.execute(
                "UPDATE practitioners SET city=%s, state=%s, country=%s, updated_at=now() "
                "WHERE id=%s",
                (city, state, cc, str(pid)))


# ── retire / restore a portal row ────────────────────────────────────────────────
# `practitioners` has no "this row is not a practitioner" switch, so a row created
# by mistake (the "Remedy Match" stub built from Glen's business name, sitting on
# the same email as his real level-12 account) kept counting as one forever: it
# competed in every email lookup, and whichever row won decided whether /book/<slug>
# had any session types and whether a referral paid 15% or 5%.
#
# Retiring clears portal_role. The row, its history and its finder listing stay
# exactly where they are; it simply stops being a portal account. That is also what
# makes it reversible: unretire_practitioner puts the role back.

PORTAL_ROLES = ("licensed", "coach", "reseller")

_RETIRE_COLS = ("id, name, email, portal_role, modules_completed, "
                "wallet_balance_cents, wholesale_unlocked_at")


class PractitionerNotFound(Exception):
    """No practitioners row with that id."""


class RetireBlocked(Exception):
    """The row has something attached, so retiring it would take a working account
    away from someone. Carries the human-readable list so the console can show the
    operator exactly what is in the way instead of a silent no-op."""

    def __init__(self, name, reasons):
        self.name = str(name or "this practitioner")
        self.reasons = list(reasons)
        super().__init__(
            f"Cannot retire {self.name}: {', '.join(self.reasons)}. "
            f"Clear what is attached first, or retire the other row for this email.")


def retire_blockers(row: dict, activity: Optional[dict] = None) -> List[str]:
    """Everything attached to this practitioner, named in plain words. Empty list
    means the row is a bare stub and is safe to retire. Pure."""
    act = {**_ACTIVITY_DEFAULTS, **(activity or {})}
    reasons: List[str] = []
    orders = int(act.get("orders") or 0) + int(act.get("disp_count") or 0)
    if orders:
        reasons.append(f"{orders} order(s) on record")
    if row.get("wholesale_unlocked_at") is not None:
        reasons.append("wholesale access is granted")
    level = int(row.get("modules_completed") or 0)
    if level:
        reasons.append(f"certification level {level}")
    balance = int(row.get("wallet_balance_cents") or 0)
    if balance:
        reasons.append(f"a wallet balance of ${balance / 100:,.2f}")
    return reasons


def _fetch_practitioner(cur, pid: str, cols: str = _RETIRE_COLS) -> dict:
    cur.execute(f"SELECT {cols} FROM practitioners WHERE id=%s LIMIT 1", (str(pid),))
    row = cur.fetchone()
    if not row:
        raise PractitionerNotFound(f"no practitioner row with id {pid}")
    return dict(row)


def retire_practitioner(pid: str, *, db_path: Optional[str] = None) -> dict:
    """Stop a row counting as a portal practitioner by clearing portal_role.

    NOT a delete: the record, its email, its finder listing and any history stay.
    Refuses (RetireBlocked) if orders, wholesale access, a certification level or a
    wallet balance are attached, because retiring one of those is taking a working
    account away from whoever is using it. Returns the cleared role so the console
    can offer the exact undo.
    """
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        row = _fetch_practitioner(cur, pid)
        activity = (aggregate_activity(db_path) or {}).get(str(pid)) if db_path else None
        reasons = retire_blockers(row, activity)
        if reasons:
            raise RetireBlocked(row.get("name") or row.get("email"), reasons)
        cur.execute("UPDATE practitioners SET portal_role=NULL, updated_at=now() "
                    "WHERE id=%s", (str(pid),))
    return {"id": str(pid), "name": row.get("name"), "email": row.get("email"),
            "retired_role": row.get("portal_role")}


def unretire_practitioner(pid: str, role: str) -> dict:
    """Put a retired row back on the roster. The exact undo for retire_practitioner.

    Refuses if another row already holds this email as a portal account, so the undo
    cannot recreate the duplicate the retirement removed.
    """
    role = (role or "").strip().lower()
    if role not in PORTAL_ROLES:
        raise ValueError(f"role must be one of {', '.join(PORTAL_ROLES)}")
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        row = _fetch_practitioner(cur, pid, "id, name, email, portal_role")
        holder = pp.find_row_for_email(cur, row.get("email"), "id, name", portal_only=True)
        if holder and str(holder["id"]) != str(pid):
            raise pp.DuplicatePortalEmail(row.get("email"))
        cur.execute("UPDATE practitioners SET portal_role=%s, updated_at=now() "
                    "WHERE id=%s", (role, str(pid)))
    return {"id": str(pid), "name": row.get("name"), "email": row.get("email"),
            "portal_role": role}


# ── duplicate-email audit (read-only) ────────────────────────────────────────────
# Deliberately NOT filtered to portal_role: list_practitioners (the console roster)
# only ever shows portal rows, which is precisely why the "Remedy Match" stub was
# invisible until it started winning email lookups. The scraped directory lives in
# the same table and is where half of every duplicate pair comes from.

# lat/lng and removal_requested are what decide PUBLIC visibility: the
# v_practitioners_public view the finder reads filters on
# `removal_requested = false AND lat IS NOT NULL`. Without them, choosing which
# of two duplicate rows to retire is a guess that can silently drop a
# practitioner out of the directory. The rest are completeness signals, so the
# richer row can be kept rather than whichever sorts first.
_DUP_COLS = ("id, name, email, portal_role, tier, modules_completed, "
             "wallet_balance_cents, wholesale_unlocked_at, application_status, "
             "city, state, created_at, "
             "lat, lng, removal_requested, source_org, source_url, "
             "last_scraped_at, phone, website, credentials, address1, postal, "
             "country, bio, photo_url, specialties, accepting_new_patients")


def duplicate_email_rows() -> List[dict]:
    """Every row whose email is shared with at least one other row, whole table."""
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute(
            f"SELECT {_DUP_COLS} FROM practitioners WHERE lower(trim(email)) IN ("
            "  SELECT lower(trim(email)) FROM practitioners"
            "  WHERE email IS NOT NULL AND trim(email) <> ''"
            "  GROUP BY lower(trim(email)) HAVING COUNT(*) > 1)"
            " ORDER BY lower(trim(email)), created_at NULLS LAST, id")
        return [dict(r) for r in (cur.fetchall() or [])]


def _iso(value):
    """A timestamp as a string, or None. psycopg hands back datetime objects and
    jsonify cannot serialise them; SQLite hands back strings already."""
    if value in (None, ""):
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def group_duplicates(rows: List[dict], activity: dict) -> dict:
    """Group duplicate rows by email with enough per-row detail to decide what to do.

    portal_count is the number that count as portal accounts; a group with 2 or more
    is a live conflict (an email lookup there is a coin toss). A group with 1 is the
    ordinary "portal account alongside its own scraped listing" and is fine. Pure.
    """
    groups: dict = {}
    for r in rows:
        key = str(r.get("email") or "").strip().lower()
        pid = str(r.get("id"))
        act = {**_ACTIVITY_DEFAULTS, **((activity or {}).get(pid) or {})}
        groups.setdefault(key, []).append({
            "id": pid,
            "name": r.get("name"),
            "email": r.get("email"),
            "portal_role": r.get("portal_role"),
            "tier": r.get("tier"),
            "level": int(r.get("modules_completed") or 0),
            "wallet_balance_cents": int(r.get("wallet_balance_cents") or 0),
            "wholesale_access": r.get("wholesale_unlocked_at") is not None,
            "application_status": r.get("application_status"),
            "city": r.get("city"),
            "state": r.get("state"),
            "orders": int(act.get("orders") or 0) + int(act.get("disp_count") or 0),
            "spent_cents": int(act.get("spent_cents") or 0),
            # PUBLIC visibility, computed the same way v_practitioners_public
            # filters. A row that is finder_listed is one a person can actually
            # find; retiring it removes them from the directory.
            "finder_listed": (not bool(r.get("removal_requested"))
                              and r.get("lat") is not None),
            "has_coords": r.get("lat") is not None,
            "removal_requested": bool(r.get("removal_requested")),
            "source_org": r.get("source_org"),
            "source_url": r.get("source_url"),
            "last_scraped_at": _iso(r.get("last_scraped_at")),
            "created_at": _iso(r.get("created_at")),
            # How much this row actually carries. Counted rather than returned in
            # full so the payload stays readable at ~900 rows; the point is only
            # to tell a fuller record from a thinner one.
            "completeness": sum(
                1 for _f in ("lat", "phone", "website", "credentials", "address1",
                             "postal", "country", "city", "state", "bio",
                             "photo_url", "specialties", "source_url")
                if (r.get(_f) not in (None, "", []))),
        })
    out = []
    for email, members in sorted(groups.items()):
        portal = [m for m in members if m["portal_role"]]
        listed = [m for m in members if m["finder_listed"]]
        out.append({"email": email, "count": len(members),
                    "portal_count": len(portal),
                    # How many of these are actually visible in the directory.
                    # 0 means retiring any of them changes nothing public; 2 or
                    # more means the public sees this practitioner twice.
                    "finder_listed_count": len(listed),
                    "rows": members})
    return {
        "emails": len(out),
        "rows": sum(g["count"] for g in out),
        "portal_conflicts": sum(1 for g in out if g["portal_count"] > 1),
        "finder_duplicates": sum(1 for g in out if g["finder_listed_count"] > 1),
        "groups": out,
    }


def audit_duplicate_emails(db_path: Optional[str] = None) -> dict:
    """Read-only duplicate report over the whole practitioners table, with each row's
    SQLite order activity merged in."""
    activity = aggregate_activity(db_path) if db_path else {}
    return group_duplicates(duplicate_email_rows(), activity)


# ── the database backstop ────────────────────────────────────────────────────────

PORTAL_EMAIL_INDEX = "ux_practitioners_portal_email"

# Scoped ON PURPOSE. A blanket unique constraint on practitioners.email would break
# the scraped directory, where several practitioners legitimately share one clinic
# address; that would be a worse bug than the one being fixed. This says only "at
# most one PORTAL account per email" and leaves directory rows alone.
_PORTAL_EMAIL_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {PORTAL_EMAIL_INDEX} "
    "ON practitioners (lower(email)) WHERE portal_role IS NOT NULL")

_PORTAL_EMAIL_VIOLATIONS = (
    "SELECT COUNT(*) AS n FROM (SELECT lower(email) FROM practitioners "
    "WHERE portal_role IS NOT NULL AND email IS NOT NULL "
    "GROUP BY lower(email) HAVING COUNT(*) > 1) d")


def portal_email_index_present() -> bool:
    """True if the partial unique index actually exists in the database.

    This is how a future operator tells an enforced deploy from an unenforced one:
    GET /api/console/practitioners/duplicates reports it as `index_present`. Never
    infer it from the deploy being green.
    """
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute("SELECT 1 AS present FROM pg_indexes WHERE indexname=%s",
                    (PORTAL_EMAIL_INDEX,))
        return bool(cur.fetchone())


def ensure_portal_email_unique_index() -> dict:
    """Create the partial unique index, but only over a clean table, and LOUDLY.

    Postgres refuses to build a unique index while a violation exists, and DDL
    failures elsewhere in this codebase are swallowed one statement at a time (see
    dashboard.practitioner_slugs._try) precisely so one failure cannot take the next
    statement down. That habit is wrong here: a swallowed failure would leave a green
    deploy enforcing nothing at all, which reads exactly like an enforced one. So the
    violation count is checked FIRST and reported rather than attempted, and a genuine
    failure prints and re-raises instead of being absorbed.
    """
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute(_PORTAL_EMAIL_VIOLATIONS)
        row = cur.fetchone() or {}
        blocked = int(dict(row).get("n") or 0)
        if blocked:
            print(f"[practitioner-admin] {PORTAL_EMAIL_INDEX} NOT created: "
                  f"{blocked} email(s) still carry more than one portal row. "
                  f"Nothing is enforcing one portal account per email. "
                  f"GET /api/console/practitioners/duplicates lists them; retire "
                  f"the spare rows, then run this again.", flush=True)
            return {"index": PORTAL_EMAIL_INDEX, "created": False, "present": False,
                    "blocked_by": blocked}
        try:
            cur.execute(_PORTAL_EMAIL_INDEX_DDL)
        except Exception as e:
            print(f"[practitioner-admin] {PORTAL_EMAIL_INDEX} FAILED to create: {e!r}. "
                  f"Nothing is enforcing one portal account per email; two concurrent "
                  f"registrations can still both land.", flush=True)
            raise
    present = portal_email_index_present()
    if not present:
        print(f"[practitioner-admin] {PORTAL_EMAIL_INDEX} reported no error but is "
              f"NOT present. Do not treat this deploy as enforced.", flush=True)
    return {"index": PORTAL_EMAIL_INDEX, "created": True, "present": present,
            "blocked_by": 0}


def build_rows(practitioners: List[dict], activity: dict) -> List[dict]:
    """Merge Supabase practitioner records with their SQLite activity, deriving the
    booleans + section the roster UI groups on. Activity defaults to zeros."""
    rows = []
    for p in practitioners:
        pid = str(p.get("id"))
        act = {**_ACTIVITY_DEFAULTS, **(activity.get(pid) or {})}
        role = p.get("portal_role")
        rows.append({
            "id": pid,
            "name": p.get("name"),
            "email": p.get("email"),
            "portal_role": role,
            "credentials": p.get("credentials"),
            "level": int(p.get("modules_completed") or 0),
            "certified": p.get("tier") == "panel_certified",
            "wallet_balance_cents": int(p.get("wallet_balance_cents") or 0),
            "wholesale_access": p.get("wholesale_unlocked_at") is not None,
            "application_status": p.get("application_status"),
            "finder_listed": bool(p.get("show_contact")),
            "city": p.get("city"),
            "state": p.get("state"),
            "country": p.get("country") or "US",
            "section": "coach" if role == "coach" else "practitioner",
            **act,
        })
    return rows
