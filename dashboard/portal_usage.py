"""How many client portals exist, and how many have actually been used.

Created and used are different questions, and only the second says whether the portal is
working as a product. A portal is created FOR a client by the practice; opening it is
something the client chooses to do.

Three distinct signals, deliberately not merged:
  created   a row in client_portals: the portal exists
  opened    portal_opens: the link was followed at least once
  signed in portal_auth_events / portal_credentials: they set a password and returned

READ ONLY.
"""
from dashboard import db


def _exists(cx, name):
    """Ask the backend which catalogue to read. On Postgres a failed statement aborts
    the transaction, so probing by try/except poisons the connection."""
    if db.backend_of(cx) == "postgres":
        return cx.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (name,)).fetchone() is not None
    return cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def _count(cx, table, sql):
    if not _exists(cx, table):
        return "table absent"
    try:
        return cx.execute(sql).fetchone()[0]
    except Exception as e:
        return f"error: {str(e).splitlines()[0][:60]}"


def summary(cx):
    out = {}
    out["portals_created"] = _count(
        cx, "client_portals", "SELECT COUNT(*) FROM client_portals")
    out["distinct_clients_with_a_portal"] = _count(
        cx, "client_portals",
        "SELECT COUNT(DISTINCT LOWER(email)) FROM client_portals "
        "WHERE COALESCE(email,'') <> ''")

    # Opens are recorded per kind ("report", "invoice", ...). Reported per kind rather
    # than summed: a client opening two reports is one person, not two.
    if _exists(cx, "portal_opens"):
        try:
            out["opens_by_kind"] = {
                str(k): n for k, n in cx.execute(
                    "SELECT kind, COUNT(*) FROM portal_opens GROUP BY kind").fetchall()}
            out["distinct_things_opened"] = cx.execute(
                "SELECT COUNT(*) FROM portal_opens").fetchone()[0]
            out["total_open_events"] = cx.execute(
                "SELECT COALESCE(SUM(open_count),0) FROM portal_opens").fetchone()[0]
        except Exception as e:
            out["opens_by_kind"] = f"error: {str(e).splitlines()[0][:60]}"
    else:
        out["opens_by_kind"] = "table absent"

    # Signing in is a stronger signal than opening a link: it means they set a password
    # and came back.
    out["set_a_password"] = _count(
        cx, "portal_credentials",
        "SELECT COUNT(*) FROM portal_credentials "
        "WHERE COALESCE(password_hash,'') <> ''")
    out["distinct_people_with_auth_events"] = _count(
        cx, "portal_auth_events",
        "SELECT COUNT(DISTINCT person_id) FROM portal_auth_events")
    if _exists(cx, "portal_auth_events"):
        try:
            out["auth_events_by_type"] = {
                str(k): n for k, n in cx.execute(
                    "SELECT event, COUNT(*) FROM portal_auth_events "
                    "GROUP BY event").fetchall()}
        except Exception as e:
            out["auth_events_by_type"] = f"error: {str(e).splitlines()[0][:60]}"

    out["_note"] = ("created, opened and signed-in are separate questions. Opens are "
                    "counted per thing opened, not per person, so they must not be "
                    "compared directly with portals_created.")
    return out
