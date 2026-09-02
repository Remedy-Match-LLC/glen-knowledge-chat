"""Practitioner-reviewed clinical checklist suggestions from recent communications."""

import json
import re


def _key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def item_key(value):
    return _key(value)


def ensure_schema(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS biofield_clinical_proposals (
            test_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (test_id, item_key)
        )
    """)
    cx.execute("""
        CREATE TABLE IF NOT EXISTS biofield_clinical_order (
            test_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL,
            position INTEGER NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (test_id, item_key)
        )
    """)
    # The remedy ticks the practitioner makes on a checklist item.  Without this the
    # ticks live only in the DOM, and every action on the page ends in location.reload().
    cx.execute("""
        CREATE TABLE IF NOT EXISTS biofield_clinical_selection (
            test_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL,
            remedies TEXT,
            pattern TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (test_id, item_key)
        )
    """)


def decisions(cx, test_id):
    ensure_schema(cx)
    return {
        row[0]: {"label": row[1], "status": row[2], "evidence": row[3] or ""}
        for row in cx.execute(
            "SELECT item_key,label,status,evidence FROM biofield_clinical_proposals WHERE test_id=?",
            (str(test_id),),
        ).fetchall()
    }


def decide(cx, test_id, label, status, evidence=""):
    status = status if status in ("accepted", "dismissed") else "dismissed"
    label = str(label or "").strip()[:160]
    item_key = _key(label)
    if not item_key:
        return False
    ensure_schema(cx)
    cx.execute(
        """INSERT INTO biofield_clinical_proposals
           (test_id,item_key,label,status,evidence,updated_at)
           VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(test_id,item_key) DO UPDATE SET
             label=excluded.label,status=excluded.status,evidence=excluded.evidence,
             updated_at=CURRENT_TIMESTAMP""",
        (str(test_id), item_key, label, status, str(evidence or "")[:500]),
    )
    return True


def accepted_labels(cx, test_id):
    return [row["label"] for row in decisions(cx, test_id).values()
            if row["status"] == "accepted"]


def dismissed_labels(cx, test_id):
    return [row["label"] for row in decisions(cx, test_id).values()
            if row["status"] == "dismissed"]


def save_order(cx, test_id, labels):
    ensure_schema(cx)
    test_id = str(test_id)
    cleaned, seen = [], set()
    for label in labels or []:
        label = str(label or "").strip()[:160]
        key = _key(label)
        if key and key not in seen:
            seen.add(key)
            cleaned.append((key, label))
    cx.execute("DELETE FROM biofield_clinical_order WHERE test_id=?", (test_id,))
    cx.executemany(
        """INSERT INTO biofield_clinical_order
           (test_id,item_key,label,position,updated_at)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP)""",
        [(test_id, key, label, pos) for pos, (key, label) in enumerate(cleaned)],
    )
    return len(cleaned)


def save_selection(cx, test_id, label, remedies):
    """Remember which remedies are ticked for one checklist item on this test."""
    ensure_schema(cx)
    label = str(label or "").strip()[:160]
    key = _key(label)
    if not key:
        return 0
    cleaned, seen = [], set()
    for raw in remedies or []:
        name = str(raw or "").strip()[:160]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            cleaned.append(name)
    cx.execute(
        """INSERT INTO biofield_clinical_selection
           (test_id,item_key,label,remedies,updated_at)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(test_id,item_key) DO UPDATE SET
             label=excluded.label,remedies=excluded.remedies,updated_at=CURRENT_TIMESTAMP""",
        (str(test_id), key, label, json.dumps(cleaned)),
    )
    return len(cleaned)


def selections(cx, test_id):
    ensure_schema(cx)
    out = {}
    for key, raw in cx.execute(
        "SELECT item_key,remedies FROM biofield_clinical_selection "
        "WHERE test_id=? AND remedies IS NOT NULL",
        (str(test_id),),
    ).fetchall():
        try:
            names = json.loads(raw or "[]")
        except ValueError:
            names = []
        out[key] = [str(name) for name in names if str(name or "").strip()]
    return out


def save_pattern(cx, test_id, label, pattern):
    """The stress pattern typed for one item on this test (not yet the standing term)."""
    ensure_schema(cx)
    label = str(label or "").strip()[:160]
    key = _key(label)
    if not key:
        return False
    cx.execute(
        """INSERT INTO biofield_clinical_selection
           (test_id,item_key,label,pattern,updated_at)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(test_id,item_key) DO UPDATE SET
             label=excluded.label,pattern=excluded.pattern,updated_at=CURRENT_TIMESTAMP""",
        (str(test_id), key, label, str(pattern or "").strip()[:160]),
    )
    return True


def patterns(cx, test_id):
    ensure_schema(cx)
    return {row[0]: row[1] for row in cx.execute(
        "SELECT item_key,pattern FROM biofield_clinical_selection WHERE test_id=? AND pattern<>''",
        (str(test_id),),
    ).fetchall()}


def apply_selection(cx, test_id, items):
    """Overlay the practitioner's own ticks; untouched items keep deriving from the chain.

    An item with a saved row wins outright, including a deliberately emptied one --
    otherwise unticking the derived remedy would silently re-tick on the next reload.
    """
    saved = selections(cx, test_id)
    typed = patterns(cx, test_id)
    out = []
    for item in items or []:
        key = _key(item.get("label"))
        if key in typed:
            item = dict(item)
            item["stress_pattern"] = typed[key]
        if key not in saved:
            out.append(item)
            continue
        item = dict(item)
        chosen = saved[key]
        item["selection_saved"] = True
        item["selected_remedies"] = chosen
        known = {str(name).strip().lower() for name in item.get("common_remedies") or []}
        # A tick must stay visible even if the remedy fell off the common list.
        item["common_remedies"] = list(item.get("common_remedies") or []) + [
            name for name in chosen if name.strip().lower() not in known
        ]
        out.append(item)
    return out


def apply_order(cx, test_id, items):
    """Apply remembered positions; unseen/new profile items follow in natural order."""
    ensure_schema(cx)
    positions = {row[0]: row[1] for row in cx.execute(
        "SELECT item_key,position FROM biofield_clinical_order WHERE test_id=?",
        (str(test_id),),
    ).fetchall()}
    indexed = list(enumerate(items or []))
    indexed.sort(key=lambda pair: (
        0 if _key(pair[1].get("label")) in positions else 1,
        positions.get(_key(pair[1].get("label")), pair[0]),
        pair[0],
    ))
    return [item for _, item in indexed]


def _evidence_lines(context):
    context = context or {}
    lines = []
    for inquiry in context.get("recent_inquiries") or []:
        for field in ("main_challenge", "main_goal"):
            value = str(inquiry.get(field) or "").strip()
            if value:
                lines.append((value, inquiry.get("created_at") or "Recent inquiry"))
    for query in context.get("recent_queries") or []:
        value = str(query.get("question") or "").strip()
        if value:
            lines.append((value, query.get("ts") or "Recent query"))
    for feedback in context.get("recent_feedback") or []:
        value = str(feedback.get("summary") or "").strip()
        if value:
            lines.append((value, feedback.get("received_at") or "Recent message"))
    return lines


def proposals(context, extracted_labels, existing_labels=(), prior_decisions=None):
    """Return undecided clinical candidates with the closest available source text."""
    existing = {_key(x) for x in existing_labels}
    prior = prior_decisions or {}
    evidence = _evidence_lines(context)
    out, seen = [], set()
    for raw in extracted_labels or []:
        label = str(raw or "").strip()[:160]
        item_key = _key(label)
        if not item_key or item_key in seen or item_key in existing or item_key in prior:
            continue
        seen.add(item_key)
        words = [word for word in item_key.split() if len(word) > 3]
        match = next(((text, when) for text, when in evidence
                      if any(word in _key(text) for word in words)), None)
        text, when = match or ((evidence[0] if evidence else ("Recent communication", "")))
        out.append({"label": label, "evidence": text[:300], "when": str(when or "")[:40]})
    return out
