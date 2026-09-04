"""Brief, structured symptom/condition checklist for Biofield Intake."""
import json
import os
import re

from dashboard.biofield_profile import clean_health_tag, is_health_tag, _items


def _norm(value):
    value = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return " ".join(value.split())


def _related(left, right):
    """Conservative label match: exact, or a meaningful phrase contains the other."""
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and (a in b or b in a)


def ensure_catalog_schema(cx):
    """Practitioner-owned additions to the historical condition/remedy catalog."""
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_catalog (
        item_key TEXT NOT NULL, label TEXT NOT NULL,
        remedy_key TEXT NOT NULL, remedy TEXT NOT NULL,
        hidden INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (item_key, remedy_key)
    )""")
    cols = {row[1] for row in
            cx.execute("PRAGMA table_info(biofield_clinical_catalog)").fetchall()}
    # Local databases built before the rename carry `item_label`.  Every reader and
    # writer below speaks `label`, so without this the table silently becomes
    # unwritable -- "Add remedy" and "Add to layer" both raise on it.
    if "item_label" in cols and "label" not in cols:
        cx.execute("ALTER TABLE biofield_clinical_catalog RENAME COLUMN item_label TO label")
    try:
        cx.execute("ALTER TABLE biofield_clinical_catalog ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass


def ensure_stress_schema(cx):
    """The stress pattern a condition becomes when it enters a causal chain."""
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_stress (
        item_key TEXT PRIMARY KEY, label TEXT NOT NULL, pattern TEXT NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")


def remember_stress_pattern(cx, label, pattern, replace=False):
    """Record the term for future tests.  A term already remembered is only ever
    overwritten on an explicit request, so entering a one-off pattern for a single
    client never rewrites the practitioner's standing vocabulary."""
    ensure_stress_schema(cx)
    label = str(label or "").strip()[:160]
    pattern = str(pattern or "").strip()[:160]
    item_key = _norm(label)
    if not item_key or not pattern:
        return False
    if not replace and stress_pattern(cx, label):
        return False
    cx.execute("""INSERT INTO biofield_clinical_stress
        (item_key,label,pattern,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(item_key) DO UPDATE SET
          label=excluded.label,pattern=excluded.pattern,updated_at=CURRENT_TIMESTAMP""",
               (item_key, label, pattern))
    cx.commit()
    return True


def _suggested_patterns():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "clinical_stress_patterns_seed.json")
    try:
        raw = json.load(open(path, encoding="utf-8")).get("patterns", {})
    except (OSError, ValueError):
        return {}
    return {_norm(label): term for label, term in raw.items() if _norm(label) and term}


def suggested_pattern(label):
    """A drafted functional term for a catalogued condition -- the function to
    restore, not the pathology.  Only ever a suggestion: what the practitioner has
    recorded for a condition wins, and a free-text condition matches nothing here."""
    return _suggested_patterns().get(_norm(label), "")


def stress_pattern(cx, label):
    ensure_stress_schema(cx)
    row = cx.execute("SELECT pattern FROM biofield_clinical_stress WHERE item_key=?",
                     (_norm(label),)).fetchone()
    return (row[0] if row else "") or ""


def remember_remedies(cx, label, remedies):
    label = str(label or "").strip()[:160]
    item_key = _norm(label)
    if not item_key:
        return 0
    ensure_catalog_schema(cx)
    count = 0
    for raw in remedies or []:
        remedy = str(raw or "").strip()[:160]
        remedy_key = _norm(remedy)
        if not remedy_key:
            continue
        cx.execute("""INSERT INTO biofield_clinical_catalog
            (item_key,label,remedy_key,remedy,hidden,updated_at)
            VALUES (?,?,?,?,0,CURRENT_TIMESTAMP)
            ON CONFLICT(item_key,remedy_key) DO UPDATE SET
              label=excluded.label,remedy=excluded.remedy,hidden=0,updated_at=CURRENT_TIMESTAMP""",
                   (item_key, label, remedy_key, remedy))
        count += 1
    cx.commit()
    return count


def attach_remedy(cx, test_id, label, remedy):
    """Put `remedy` on `label`'s ticked list for this test, adding the condition
    to the client's list if it is not already there.

    One operation because it touches three stores: the per-test checklist, the
    shared catalog, and the per-test selection. save_selection REPLACES the
    ticked list, so "add one" is a read-modify-write; doing that from the browser
    in three calls drops a tick whenever two land close together.

    Returns what changed, so the caller can say so rather than guess.
    """
    from dashboard import biofield_clinical_proposals as _cp

    label = str(label or "").strip()[:160]
    remedy = str(remedy or "").strip()[:160]
    if not label or not remedy:
        raise ValueError("A condition and a remedy are both required")

    _cp.ensure_schema(cx)
    ensure_catalog_schema(cx)

    # Is this condition already on the client's list for this test?
    # Table is biofield_clinical_proposalS. Reading the singular silently
    # matched nothing, so every attach re-added a condition that was already
    # there -- caught by test_a_condition_already_on_the_list_is_not_re_added.
    try:
        row = cx.execute(
            "SELECT status FROM biofield_clinical_proposals "
            "WHERE test_id=? AND item_key=?",
            (str(test_id), _cp._key(label))).fetchone()
    except Exception:
        row = None
    already = bool(row) and str(row[0] or "") == "accepted"
    if not already:
        # An explicit click outranks an earlier dismissal, or it would silently
        # do nothing on a condition the practitioner once hid.
        _cp.decide(cx, test_id, label, "accepted",
                   "Added from the previously-dispensed panel")

    remember_remedies(cx, label, [remedy])

    ticked = list(_cp.selections(cx, test_id).get(_cp._key(label)) or [])
    already_ticked = any(t.strip().lower() == remedy.lower() for t in ticked)
    if not already_ticked:
        ticked.append(remedy)
        _cp.save_selection(cx, test_id, label, ticked)
    cx.commit()
    return {"label": label, "remedy": remedy, "added_condition": not already,
            "already_ticked": already_ticked, "ticked": ticked}


def conditions_for_remedy(cx, remedy, limit=8):
    """The conditions this remedy is used for: the catalog read backwards.

    Honours `hidden`, so a pairing the practitioner rejected through
    forget_remedy cannot reappear as a suggestion through the reverse door.

    Ordered and deduplicated because these render as clickable chips, and a
    repeat would add the same condition to the client's list twice. Capped so a
    remedy used everywhere cannot flood a single row. Never raises: it feeds a
    reference panel, and a fresh database must not break the page.
    """
    key = _norm(remedy)
    if not key:
        return []
    ensure_catalog_schema(cx)
    try:
        rows = cx.execute(
            "SELECT DISTINCT label FROM biofield_clinical_catalog "
            "WHERE remedy_key=? AND COALESCE(hidden,0)=0 ORDER BY label LIMIT ?",
            (key, int(limit))).fetchall()
    except Exception:
        return []
    seen, out = set(), []
    for row in rows:
        label = str(row[0] or "").strip()
        if label and label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out


def forget_remedy(cx, label, remedy):
    ensure_catalog_schema(cx)
    label = str(label or "").strip()[:160]
    remedy = str(remedy or "").strip()[:160]
    cur = cx.execute("""INSERT INTO biofield_clinical_catalog
        (item_key,label,remedy_key,remedy,hidden,updated_at)
        VALUES (?,?,?,?,1,CURRENT_TIMESTAMP)
        ON CONFLICT(item_key,remedy_key) DO UPDATE SET hidden=1,updated_at=CURRENT_TIMESTAMP""",
                     (_norm(label), label, _norm(remedy), remedy))
    cx.commit()
    return bool(cur.rowcount)


def custom_remedies(cx, label):
    ensure_catalog_schema(cx)
    return [row[0] for row in cx.execute(
        "SELECT remedy FROM biofield_clinical_catalog WHERE item_key=? AND hidden=0 ORDER BY remedy",
        (_norm(label),)).fetchall()]


def forgotten_remedies(cx, label):
    ensure_catalog_schema(cx)
    return {row[0] for row in cx.execute(
        "SELECT remedy_key FROM biofield_clinical_catalog WHERE item_key=? AND hidden=1",
        (_norm(label),)).fetchall()}


_PROGRAM_ALIASES = {
    "amd": ("dry-amd", "wet-amd"),
    "age related macular degeneration": ("dry-amd", "wet-amd"),
    "dry macular degeneration": ("dry-amd",),
    "dry age related macular degeneration": ("dry-amd",),
    "wet macular degeneration": ("wet-amd",),
    "wet age related macular degeneration": ("wet-amd",),
}


def _condition_programs():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "condition_programs_seed.json")
    try:
        return json.load(open(path, encoding="utf-8")).get("condition_programs", {})
    except (OSError, ValueError):
        return {}


def program_remedies(label):
    key = _norm(label)
    names, seen = [], set()
    alias_keys = set(_PROGRAM_ALIASES.get(key, ()))
    for program_key, program in _condition_programs().items():
        program_label = _norm(program.get("label") or program_key)
        if program_key not in alias_keys and key not in program_label and program_label not in key:
            continue
        for item in program.get("items") or []:
            name = (item.get("name") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower()); names.append(name)
    return names


def catalog_items(cx, q="", limit=100):
    """Curated symptoms/conditions with remedies; never products/body locations."""
    ensure_catalog_schema(cx)
    query, found = _norm(q), {}
    for program_key, program in _condition_programs().items():
        remedies = {(item.get("name") or "").strip() for item in program.get("items") or []}
        remedies.discard("")
        if not remedies:
            continue
        for label in [program.get("label") or program_key] + list(program.get("symptoms") or []):
            if label and (not query or query in _norm(label)):
                found.setdefault(_norm(label), {"label": label, "remedy_count": len(remedies)})
    displays = {"amd": "AMD (Age-Related Macular Degeneration)",
                "dry macular degeneration": "Dry Macular Degeneration",
                "wet macular degeneration": "Wet Macular Degeneration"}
    for alias, display in displays.items():
        if not query or query in _norm(display):
            remedies = program_remedies(alias)
            if remedies:
                found[_norm(display)] = {"label": display, "remedy_count": len(remedies)}
    like = f"%{str(q or '').strip()}%"
    for label, count in cx.execute(
        "SELECT label,COUNT(*) FROM biofield_clinical_catalog WHERE hidden=0 AND label LIKE ? "
        "GROUP BY item_key ORDER BY label LIMIT ?", (like, int(limit))).fetchall():
        key = _norm(label)
        if key in found:
            found[key]["remedy_count"] += int(count or 0)
        else:
            found[key] = {"label": label, "remedy_count": int(count or 0)}
    return sorted(found.values(), key=lambda row: row["label"].lower())[:int(limit)]


def profile_labels(profile):
    """Symptoms/conditions only; deliberately excludes communications and family history."""
    profile = profile or {}
    labels = list(_items(profile.get("conditions")))
    labels += [clean_health_tag(tag) for tag in _items(profile.get("tags"))
               if is_health_tag(tag)]
    out, seen = [], set()
    for label in labels:
        label = (label or "").strip()
        key = _norm(label)
        if label and key and key not in seen:
            seen.add(key)
            out.append(label)
    return out


def build(profile, layers, stress_data=None, remedy_lookup=None, stress_lookup=None):
    """Return checklist rows, deriving completion from current program remedies."""
    layers = layers or []
    current = {(row.get("remedy") or "").strip().lower():
               (row.get("remedy") or "").strip() for row in layers
               if (row.get("remedy") or "").strip()}
    balanced = [s for s in (stress_data or {}).get("balanced", []) if s.get("balanced_by")]
    rows = []
    for label in profile_labels(profile):
        covered_by = ""
        balanced_layer = None
        # A remedy on a layer explicitly headed/tailed by this condition is related.
        for layer in layers:
            remedy = (layer.get("remedy") or "").strip()
            if remedy and (_related(label, layer.get("head")) or
                           _related(label, layer.get("most_affected"))):
                covered_by = remedy
                balanced_layer = layer.get("stored_layer", layer.get("layer"))
                break
        # Reuse the existing per-test remedy-to-stress coverage calculation.
        if not covered_by:
            for stress in balanced:
                if _related(label, stress.get("label") or stress.get("code")):
                    covered_by = (stress.get("balanced_by") or "").strip()
                    balanced_layer = stress.get("layer") or stress.get("balanced_layer")
                    break
        # Finally use FileMaker's historical stress/remedy relationship table.
        common_remedies = []
        if remedy_lookup:
            for remedy in remedy_lookup(label) or []:
                name = remedy.get("remedy") if isinstance(remedy, dict) else remedy
                name = (name or "").strip()
                if name and name.lower() not in {x.lower() for x in common_remedies}:
                    common_remedies.append(name)
                match = current.get((name or "").strip().lower())
                if not covered_by and match:
                    covered_by = match
                    for layer in layers:
                        if (layer.get("remedy") or "").strip().lower() == match.lower():
                            balanced_layer = layer.get("stored_layer", layer.get("layer"))
                            break
        remembered = (stress_lookup(label) or "").strip() if stress_lookup else ""
        suggested = "" if remembered else suggested_pattern(label)
        rows.append({"label": label, "checked": bool(covered_by),
                     "covered_by": covered_by, "layer": balanced_layer,
                     "common_remedies": common_remedies[:8],
                     "stress_pattern": remembered or suggested,
                     "remembered_pattern": remembered,
                     "pattern_is_suggested": bool(suggested)})
    return rows


def _parts(value):
    return [part.strip() for part in re.split(r"[,;\n]+", value or "") if part.strip()]


def _with_item(value, label):
    parts = _parts(value)
    if not any(_related(label, part) for part in parts):
        parts.append(label.strip())
    return ", ".join(parts)


def balance_item(cx, test_id, label, layer, remedies, resolve_name=lambda cx, name: name,
                 dosing=lambda cx, name: {}, pattern=""):
    """Place a clinical item on a layer and add all checked remedies to that layer.

    The chain speaks in stress patterns, not in the client's own words for a
    condition, so `pattern` (when given) is what lands in Head and Tail.
    """
    from dashboard.biofield_authoring import _num, add_chain_row, init_auth_tables

    label = str(label or "").strip()[:160]
    layer = int(layer)
    if not label or layer < 1:
        raise ValueError("Item and a positive layer number are required")
    term = str(pattern or "").strip()[:160] or label
    init_auth_tables(cx)
    rows = cx.execute(
        "SELECT id,head,most_affected,remedy FROM biofield_auth_chain "
        "WHERE test_id=? AND layer=? ORDER BY id", (_num(test_id), layer),
    ).fetchall()
    if rows:
        anchor = rows[0]
        head_text = (anchor[1] or "").strip() or term
        cx.execute(
            "UPDATE biofield_auth_chain SET head=?,most_affected=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (head_text, _with_item(anchor[2] or "", term), anchor[0]),
        )
    else:
        head_text = term
        add_chain_row(cx, test_id, layer, term, term, "", confirmed=1, origin="live")
        rows = cx.execute(
            "SELECT id,head,most_affected,remedy FROM biofield_auth_chain "
            "WHERE test_id=? AND layer=? ORDER BY id", (_num(test_id), layer),
        ).fetchall()
    existing = {(row[3] or "").strip().lower() for row in rows if (row[3] or "").strip()}
    added = []
    for raw in remedies or []:
        name = (resolve_name(cx, str(raw or "").strip()) or "").strip()
        if not name or name.lower() in existing:
            continue
        details = dosing(cx, name) or {}
        # Layer cards group by head text, so a remedy row with a blank head would
        # split off into a card of its own instead of joining the layer it was added to.
        add_chain_row(cx, test_id, layer, head_text, "", name,
                      details.get("dosage", ""), details.get("frequency", ""),
                      details.get("timing", ""), confirmed=1, origin="live")
        existing.add(name.lower())
        added.append(name)
    remember_remedies(cx, label, remedies)
    # A term entered for a condition that has none yet becomes the default next time.
    remember_stress_pattern(cx, label, pattern)
    cx.commit()
    return {"layer": layer, "added_remedies": added}
