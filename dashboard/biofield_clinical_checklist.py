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


def init_related_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_item_remedies(
        test_id INTEGER NOT NULL, item_key TEXT NOT NULL, item_label TEXT NOT NULL,
        remedy TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(test_id,item_key,remedy))""")


def add_related_remedy(cx, test_id, label, remedy):
    from dashboard.biofield_authoring import _num
    label, remedy = str(label or "").strip()[:160], str(remedy or "").strip()[:160]
    if not label or not remedy:
        raise ValueError("Condition and remedy are required")
    init_related_table(cx)
    cx.execute(
        "INSERT OR IGNORE INTO biofield_clinical_item_remedies"
        "(test_id,item_key,item_label,remedy) VALUES(?,?,?,?)",
        (_num(test_id), _norm(label), label, remedy))
    cx.commit()
    return remedy


def related_remedies(cx, test_id, label):
    from dashboard.biofield_authoring import _num
    init_related_table(cx)
    return [r[0] for r in cx.execute(
        "SELECT remedy FROM biofield_clinical_item_remedies "
        "WHERE test_id=? AND item_key=? ORDER BY created_at,remedy",
        (_num(test_id), _norm(label))).fetchall()]


def init_catalog_table(cx):
    """Global practitioner-owned condition/remedy memory across all intakes."""
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_catalog(
        item_key TEXT NOT NULL, item_label TEXT NOT NULL,
        remedy_key TEXT NOT NULL, remedy TEXT NOT NULL,
        hidden INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(item_key,remedy_key))""")


def remember_remedy(cx, label, remedy):
    label, remedy = str(label or "").strip()[:160], str(remedy or "").strip()[:160]
    if not label or not remedy:
        raise ValueError("Condition and remedy are required")
    init_catalog_table(cx)
    cx.execute("""INSERT INTO biofield_clinical_catalog
        (item_key,item_label,remedy_key,remedy,hidden,updated_at)
        VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)
        ON CONFLICT(item_key,remedy_key) DO UPDATE SET
          item_label=excluded.item_label,remedy=excluded.remedy,hidden=0,
          updated_at=CURRENT_TIMESTAMP""",
        (_norm(label), label, _norm(remedy), remedy))
    cx.commit()
    return remedy


def forget_remedy(cx, label, remedy):
    label, remedy = str(label or "").strip()[:160], str(remedy or "").strip()[:160]
    if not label or not remedy:
        raise ValueError("Condition and remedy are required")
    init_catalog_table(cx)
    cx.execute("""INSERT INTO biofield_clinical_catalog
        (item_key,item_label,remedy_key,remedy,hidden,updated_at)
        VALUES(?,?,?,?,1,CURRENT_TIMESTAMP)
        ON CONFLICT(item_key,remedy_key) DO UPDATE SET hidden=1,updated_at=CURRENT_TIMESTAMP""",
        (_norm(label), label, _norm(remedy), remedy))
    cx.commit()
    return remedy


def remembered_remedies(cx, label):
    init_catalog_table(cx)
    return [row[0] for row in cx.execute(
        "SELECT remedy FROM biofield_clinical_catalog "
        "WHERE item_key=? AND hidden=0 ORDER BY remedy", (_norm(label),)).fetchall()]


def forgotten_remedies(cx, label):
    init_catalog_table(cx)
    return {row[0] for row in cx.execute(
        "SELECT remedy_key FROM biofield_clinical_catalog "
        "WHERE item_key=? AND hidden=1", (_norm(label),)).fetchall()}


_PROGRAM_ALIASES = {
    "amd": ("dry-amd", "wet-amd"),
    "amd age related macular degeneration": ("dry-amd", "wet-amd"),
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
    """Approved condition-program remedies for broad labels such as Glaucoma."""
    key = _norm(label)
    if not key:
        return []
    programs = _condition_programs()
    alias_keys = set(_PROGRAM_ALIASES.get(key, ()))
    names, seen = [], set()
    for program_key, program in programs.items():
        program_label = _norm(program.get("label") or program_key)
        if program_key not in alias_keys and key not in program_label and program_label not in key:
            continue
        for item in program.get("items") or []:
            name = (item.get("name") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower()); names.append(name)
    return names


def catalog_items(cx, q="", limit=200):
    """Curated symptoms/conditions with remedies; never product names/body locations."""
    init_catalog_table(cx)
    query, found = _norm(q), {}
    for program_key, program in _condition_programs().items():
        remedies = {(item.get("name") or "").strip() for item in program.get("items") or []}
        remedies.discard("")
        if not remedies:
            continue
        labels = [program.get("label") or program_key] + list(program.get("symptoms") or [])
        for label in labels:
            if label and (not query or query in _norm(label)):
                found.setdefault(_norm(label), {"label": label, "remedy_count": len(remedies)})
    display_aliases = {
        "amd": "AMD (Age-Related Macular Degeneration)",
        "dry macular degeneration": "Dry Macular Degeneration",
        "wet macular degeneration": "Wet Macular Degeneration",
    }
    for alias, display in display_aliases.items():
        if query and query not in _norm(display):
            continue
        remedies = program_remedies(alias)
        if remedies:
            found[_norm(display)] = {"label": display, "remedy_count": len(remedies)}
    like = f"%{str(q or '').strip()}%"
    for label, count in cx.execute(
        "SELECT item_label,COUNT(*) FROM biofield_clinical_catalog "
        "WHERE hidden=0 AND item_label LIKE ? GROUP BY item_key ORDER BY item_label LIMIT ?",
        (like, int(limit))).fetchall():
        row = found.setdefault(_norm(label), {"label": label, "remedy_count": 0})
        row["remedy_count"] += int(count or 0)
    return sorted(found.values(), key=lambda row: row["label"].lower())[:int(limit)]


def build(profile, layers, stress_data=None, remedy_lookup=None):
    """Return checklist rows, deriving completion from current program remedies."""
    layers = layers or []
    current = {(row.get("remedy") or "").strip().lower():
               (row.get("remedy") or "").strip() for row in layers
               if (row.get("remedy") or "").strip()}
    balanced = [s for s in (stress_data or {}).get("balanced", []) if s.get("balanced_by")]
    rows = []
    for label in profile_labels(profile):
        covered_remedies = []
        balanced_layer = None
        # A remedy on a layer explicitly headed/tailed by this condition is related.
        for layer in layers:
            remedy = (layer.get("remedy") or "").strip()
            if remedy and (_related(label, layer.get("head")) or
                           _related(label, layer.get("most_affected"))):
                if remedy.lower() not in {x.lower() for x in covered_remedies}:
                    covered_remedies.append(remedy)
                if balanced_layer is None:
                    balanced_layer = layer.get("stored_layer", layer.get("layer"))
        # Reuse the existing per-test remedy-to-stress coverage calculation.
        if not covered_remedies:
            for stress in balanced:
                if _related(label, stress.get("label") or stress.get("code")):
                    remedy = (stress.get("balanced_by") or "").strip()
                    if remedy:
                        covered_remedies.append(remedy)
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
                if match and match.lower() not in {x.lower() for x in covered_remedies}:
                    covered_remedies.append(match)
                if match and balanced_layer is None:
                    for layer in layers:
                        if (layer.get("remedy") or "").strip().lower() == match.lower():
                            balanced_layer = layer.get("stored_layer", layer.get("layer"))
                            break
        covered_by = ", ".join(covered_remedies)
        rows.append({"label": label, "checked": bool(covered_remedies),
                     "covered_by": covered_by, "covered_remedies": covered_remedies,
                     "layer": balanced_layer,
                     "common_remedies": common_remedies[:8]})
    return rows


def _parts(value):
    return [part.strip() for part in re.split(r"[,;\n]+", value or "") if part.strip()]


def _with_item(value, label):
    parts = _parts(value)
    if not any(_related(label, part) for part in parts):
        parts.append(label.strip())
    return ", ".join(parts)


def balance_item(cx, test_id, label, layer, remedies, resolve_name=lambda cx, name: name,
                 dosing=lambda cx, name: {}):
    """Place a clinical item on a layer and add all checked remedies to that layer."""
    from dashboard.biofield_authoring import _num, add_chain_row, init_auth_tables

    label = str(label or "").strip()[:160]
    layer = int(layer)
    if not label or layer < 1:
        raise ValueError("Item and a positive layer number are required")
    init_auth_tables(cx)
    rows = cx.execute(
        "SELECT id,head,most_affected,remedy FROM biofield_auth_chain "
        "WHERE test_id=? AND layer=? ORDER BY id", (_num(test_id), layer),
    ).fetchall()
    head_exists = any((row[1] or "").strip() for row in rows)
    if rows:
        anchor = rows[0]
        cx.execute(
            "UPDATE biofield_auth_chain SET head=?,most_affected=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ((anchor[1] or "").strip() or label,
             _with_item(anchor[2] or "", label), anchor[0]),
        )
    else:
        add_chain_row(cx, test_id, layer, label, label, "", confirmed=1, origin="live")
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
        add_chain_row(cx, test_id, layer, "" if head_exists or rows else label, "", name,
                      details.get("dosage", ""), details.get("frequency", ""),
                      details.get("timing", ""), confirmed=1, origin="live")
        existing.add(name.lower())
        added.append(name)
        remember_remedy(cx, label, name)
    cx.commit()
    return {"layer": layer, "added_remedies": added}
