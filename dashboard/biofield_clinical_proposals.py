"""Practitioner-reviewed clinical checklist suggestions from recent communications."""

import re


def _key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


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
