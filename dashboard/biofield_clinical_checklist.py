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
        # A remedy on a layer explicitly headed/tailed by this condition is related.
        for layer in layers:
            remedy = (layer.get("remedy") or "").strip()
            if remedy and (_related(label, layer.get("head")) or
                           _related(label, layer.get("most_affected"))):
                covered_by = remedy
                break
        # Reuse the existing per-test remedy-to-stress coverage calculation.
        if not covered_by:
            for stress in balanced:
                if _related(label, stress.get("label") or stress.get("code")):
                    covered_by = (stress.get("balanced_by") or "").strip()
                    break
        # Finally use FileMaker's historical stress/remedy relationship table.
        if not covered_by and remedy_lookup:
            for remedy in remedy_lookup(label) or []:
                name = remedy.get("remedy") if isinstance(remedy, dict) else remedy
                match = current.get((name or "").strip().lower())
                if match:
                    covered_by = match
                    break
        rows.append({"label": label, "checked": bool(covered_by),
                     "covered_by": covered_by})
    return rows
