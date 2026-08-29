"""Brief, structured symptom/condition checklist for Biofield Intake."""
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


def build(profile, layers, stress_data=None, remedy_lookup=None):
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
        rows.append({"label": label, "checked": bool(covered_by),
                     "covered_by": covered_by, "layer": balanced_layer,
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
    cx.commit()
    return {"layer": layer, "added_remedies": added}
