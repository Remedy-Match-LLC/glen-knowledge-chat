"""The Import Reveal preview Glen confirms (Glen, 2026-10-18).

The reveal synthesis is a model call, so two runs give different layers. The layers
Glen saw in the preview are stored here, and the import writes exactly those. One
preview per test; it expires after an hour and is removed once imported.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from dashboard import biofield_reveal_cover as _cover

TTL = timedelta(hours=1)


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_reveal_preview (
        test_id TEXT PRIMARY KEY, token TEXT NOT NULL, res TEXT NOT NULL,
        preview TEXT NOT NULL, created_at TEXT NOT NULL)""")


def split(cx, test_id, rep, layers, scan_id, e4l_db):
    """The cover plan for `layers` against the chain as it stands now."""
    from dashboard.biofield_authoring import exact_canonical_name
    from dashboard.biofield_clinical_proposals import selections
    from dashboard.e4l_scan_marks import infoceutical_marks
    marks = None
    try:
        marks = infoceutical_marks(cx, e4l_db, scan_id)
    except Exception:  # noqa: BLE001  unread: the infoceutical rule skips nothing
        marks = None
    recommended = (marks or {}).get("recommended")
    ticked = [n for names in selections(cx, test_id).values() for n in names or []]
    try:
        vocabulary = [r[0] for r in cx.execute(
            "SELECT DISTINCT item_key FROM biofield_clinical_catalog")]
    except Exception:  # noqa: BLE001  no catalog yet: the client's own names only
        vocabulary = []
    return _cover.plan(layers, rep.get("layers") or [], recommended, ticked,
                       resolve=lambda name: exact_canonical_name(cx, name),
                       vocabulary=vocabulary)


def describe(split_):
    """What the page shows: every layer, added or skipped with its rule."""
    row = lambda L: {"n": L.get("n"), "title": L.get("title") or "",
                     "remedy": L.get("remedy_name") or ""}
    return {"add": [row(L) for L in split_["add"]],
            "skip": [dict(row(s["layer"]), rule=s["rule"], why=s["why"])
                     for s in split_["skip"]],
            "recommended_unread": split_["recommended_unread"]}


def save(cx, test_id, res, split_):
    ensure_schema(cx)
    token = secrets.token_urlsafe(16)
    cx.execute("DELETE FROM biofield_reveal_preview WHERE test_id=?", (str(test_id),))
    cx.execute("INSERT INTO biofield_reveal_preview (test_id,token,res,preview,created_at) "
               "VALUES (?,?,?,?,?)", (str(test_id), token, json.dumps(res),
                                      json.dumps(describe(split_)),
                                      datetime.now(timezone.utc).isoformat()))
    cx.commit()
    return token


def load(cx, test_id, token, now=None):
    """{"res", "preview"} for this test's stored preview, or None when the token does
    not match or it has expired."""
    ensure_schema(cx)
    row = cx.execute("SELECT token,res,preview,created_at FROM biofield_reveal_preview "
                     "WHERE test_id=?", (str(test_id),)).fetchone()
    if not row or not token or not secrets.compare_digest(row[0], str(token)):
        return None
    made = datetime.fromisoformat(row[3])
    if (now or datetime.now(timezone.utc)) - made > TTL:
        return None
    return {"res": json.loads(row[1]), "preview": json.loads(row[2])}


def forget(cx, test_id):
    ensure_schema(cx)
    cx.execute("DELETE FROM biofield_reveal_preview WHERE test_id=?", (str(test_id),))
