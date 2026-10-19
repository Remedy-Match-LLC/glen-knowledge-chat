"""The Import Reveal preview Glen confirms (Glen, 2026-10-18).

The reveal synthesis is a model call, so two runs give different layers. The layers
Glen saw in the preview are stored here, and the import writes exactly those. One
preview per test; it expires after an hour and is removed once imported.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from dashboard import biofield_reveal_cover as _cover

TTL = timedelta(hours=1)


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_reveal_preview (
        test_id TEXT PRIMARY KEY, token TEXT NOT NULL, res TEXT NOT NULL,
        preview TEXT NOT NULL, email TEXT NOT NULL, chain TEXT NOT NULL,
        created_at TEXT NOT NULL)""")


def chain_digest(rows):
    """A digest of the chain's rows, so any change after the preview refuses the import."""
    return hashlib.sha256(json.dumps(rows or [], sort_keys=True, default=str)
                          .encode()).hexdigest()


SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "condition_programs_seed.json")


def program_conditions():
    """The condition program labels, as more vocabulary for the heading rule."""
    try:
        with open(SEED) as f:
            programs = json.load(f).get("condition_programs") or {}
        return [str(p.get("label") or "") for p in programs.values() if p.get("label")]
    except (OSError, ValueError, AttributeError):
        return []


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
    catalog_unread = False
    ticked = [n for names in selections(cx, test_id).values() for n in names or []]
    try:
        vocabulary = [r[0] for r in cx.execute(
            "SELECT DISTINCT item_key FROM biofield_clinical_catalog")]
    except Exception:  # noqa: BLE001  said on the preview, never silent
        vocabulary, catalog_unread = [], True
    # More names can only stop a shorter match: a wider vocabulary never adds a skip.
    vocabulary += program_conditions()
    layers = [dict(L, _key=i) for i, L in enumerate(layers or [])]
    out = _cover.plan(layers, rep.get("layers") or [], recommended, ticked,
                      resolve=lambda name: exact_canonical_name(cx, name),
                      vocabulary=vocabulary)
    out["catalog_unread"] = catalog_unread
    return out


def describe(split_):
    """What the page shows: every layer, added or skipped with its rule."""
    # key is the layer's position in the stored result: a reveal layer's n can be
    # missing or repeated, and "add anyway" must name exactly one layer.
    row = lambda L: {"n": L.get("n"), "key": L.get("_key"), "title": L.get("title") or "",
                     "remedy": L.get("remedy_name") or ""}
    return {"add": [row(L) for L in split_["add"]],
            "skip": [dict(row(s["layer"]), rule=s["rule"], why=s["why"])
                     for s in split_["skip"]],
            "recommended_unread": split_["recommended_unread"],
            "catalog_unread": bool(split_.get("catalog_unread"))}


def save(cx, test_id, res, split_, email, chain):
    """`email` is the client the reveal was made for, `chain` the chain_digest now."""
    ensure_schema(cx)
    token = secrets.token_urlsafe(16)
    cx.execute("DELETE FROM biofield_reveal_preview WHERE test_id=?", (str(test_id),))
    cx.execute("INSERT INTO biofield_reveal_preview "
               "(test_id,token,res,preview,email,chain,created_at) VALUES (?,?,?,?,?,?,?)",
               (str(test_id), token, json.dumps(res), json.dumps(describe(split_)),
                str(email or "").strip().lower(), chain,
                datetime.now(timezone.utc).isoformat()))
    cx.commit()
    return token


def load(cx, test_id, token, now=None):
    """{"res", "preview", "email", "chain"} for this test's stored preview, or None
    when the token does not match or it has expired."""
    ensure_schema(cx)
    row = cx.execute("SELECT token,res,preview,email,chain,created_at "
                     "FROM biofield_reveal_preview WHERE test_id=?", (str(test_id),)).fetchone()
    if not row or not token or not secrets.compare_digest(row[0], str(token)):
        return None
    made = datetime.fromisoformat(row[5])
    if (now or datetime.now(timezone.utc)) - made > TTL:
        return None
    return {"res": json.loads(row[1]), "preview": json.loads(row[2]), "email": row[3],
            "chain": row[4]}


def consume(cx, test_id, token):
    """Delete this preview by its token. True for exactly one caller: a second
    Import click finds it gone and imports nothing. Not committed here, so it
    commits with the import."""
    ensure_schema(cx)
    return cx.execute("DELETE FROM biofield_reveal_preview WHERE test_id=? AND token=?",
                      (str(test_id), str(token))).rowcount == 1
