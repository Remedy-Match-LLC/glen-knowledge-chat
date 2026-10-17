"""Symptoms, Labs and Conditions inside the Mind group of Stresses by layer.

Glen, 2026-10-16 (via the ai-training-fc tab): "Let's categorize within Mind: Symptoms,
Labs, Conditions." Asked where a stress that is none of the three goes, he answered
"Symptoms". So an unsorted stress, and one not yet sorted, shows under Symptoms.

A label's kind is the same for every client, so it is decided once by the model and
kept in `stress_kind`, keyed by the normalised label. A stress from the Clinical
Summary is a condition by construction and never needs the model.
"""

import json

from dashboard.biofield_stress import _norm

KINDS = ("symptom", "lab", "condition")
# Display order and names, Glen's order.
SUBGROUPS = (("symptom", "Symptoms"), ("lab", "Labs"), ("condition", "Conditions"))
# The sources that are asked about. 'clinical' rows are conditions already.
ASKED_SOURCES = ("tag", "comm", "paste")
BATCH = 80


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS stress_kind (
        label_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('symptom','lab','condition')),
        decided_at TEXT DEFAULT CURRENT_TIMESTAMP)""")


def kind_of(source, label, known):
    """The subgroup for one Mind stress. `known` is {label key: kind}."""
    if source == "clinical":
        return "condition"
    return known.get(_norm(label)) or "symptom"


def known_kinds(cx, labels):
    ensure_schema(cx)
    keys = sorted({_norm(l) for l in labels if _norm(l)})
    out = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        marks = ",".join("?" for _ in part)
        out.update(dict(cx.execute(
            f"SELECT label_key, kind FROM stress_kind WHERE label_key IN ({marks})", part)))
    return out


def unsorted_labels(cx, items):
    """Labels of the asked sources that have no kind yet, one per key."""
    asked = [i.get("label") or "" for i in items if i.get("source") in ASKED_SOURCES]
    known = known_kinds(cx, asked)
    out, seen = [], set()
    for label in asked:
        key = _norm(label)
        if key and key not in known and key not in seen:
            seen.add(key)
            out.append(label)
    return out


def build_prompt(labels):
    system = (
        "You sort a client's health stress labels for a naturopathic practitioner. "
        "For each numbered label answer one kind: 'symptom' (something felt or noticed, "
        "such as fatigue or cloudy vision), 'lab' (a test result or measurement, such as "
        "high ferritin or low blood pressure), or 'condition' (a named disease, diagnosis "
        "or disorder, such as cataract or rheumatoid arthritis). A family history counts "
        "as a condition. If a label is none of these, answer 'symptom'. "
        'Reply with JSON only: {"kinds": {"1": "symptom", "2": "lab"}}')
    user = "\n".join(f"{i}. {l}" for i, l in enumerate(labels, 1))
    return system, user


def parse_reply(raw, labels):
    """{label key: kind} for every label shown, from a well-formed reply.

    A label the reply skips, or answers with something that is not one of the three,
    is a symptom: Glen's ruling for "none of these". It is stored, so it is not asked
    about on every page load (review round 1). A reply that is not the shape asked
    for stores nothing."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}
    kinds = data.get("kinds") if isinstance(data, dict) else None
    if not isinstance(kinds, dict):
        return {}
    answered = {}
    for num, kind in kinds.items():
        try:
            n = int(str(num).strip())
        except ValueError:
            continue
        kind = str(kind or "").strip().lower()
        if 1 <= n <= len(labels) and kind in KINDS:
            answered[n] = kind
    out = {}
    for n, label in enumerate(labels, 1):
        key = _norm(label)
        if key:
            out[key] = answered.get(n, "symptom")
    return out


def classify(cx, labels, complete):
    """Ask the model about these labels, in batches, and keep what it answered.
    Returns how many were stored. A label it did not answer stays unsorted and shows
    under Symptoms."""
    ensure_schema(cx)
    stored = 0
    for i in range(0, len(labels), BATCH):
        part = labels[i:i + BATCH]
        system, user = build_prompt(part)
        found = parse_reply(complete(system, user), part)
        for key, kind in found.items():
            cx.execute("INSERT INTO stress_kind(label_key, kind) VALUES (?,?) "
                       "ON CONFLICT(label_key) DO NOTHING", (key, kind))
            stored += 1
        # Kept batch by batch: no write is held open during the next model call, and
        # a later failure does not lose what earlier batches sorted (review round 2).
        cx.commit()
    return stored
