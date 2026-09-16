"""Build the console's mineable clinical profile from every intake store."""
import json

def _table_values(rows, *keys):
    out = []
    for row in rows or []:
        if not isinstance(row, dict): continue
        text = " — ".join(str(row.get(k) or "").strip() for k in keys if row.get(k))
        if text: out.append(text)
    return out

def _answer_parts(answers):
    answers = answers or {}
    conditions = (_table_values(answers.get("health_concerns"), "concern")
                  + _table_values(answers.get("diagnoses"), "diagnosis", "current")
                  + _table_values(answers.get("allergies"), "sensitivity", "reaction"))
    narrative = [str(answers.get(k)).strip() for k in (
        "other_symptoms", "obstacles", "sleep", "dental", "vaccinations",
        "physical_trauma", "psychoemotional_trauma", "toxins") if answers.get(k)]
    narrative += _table_values(answers.get("medications"), "medication", "reason")
    narrative += _table_values(answers.get("otc_drugs"), "medication", "reason")
    narrative += _table_values(answers.get("supplements"), "brand", "name", "reason")
    narrative += _table_values(answers.get("surgeries"), "procedure", "reason")
    narrative += _table_values(answers.get("family_history"), "relative", "condition", "age_onset")
    return conditions, narrative


def _as_list(value):
    """A discrete people column. canonical_tags writes these with json.dumps, so a
    JSON list arrives here as a STRING. Splitting that on commas shredded it into
    fragments ('["Adrenal Fatigue', 'Current', '2023"') which then showed up as rows
    in the authoring Clinical summary. Parse first, comma-split only as a fallback."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x or "").strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x or "").strip()]
    return [x.strip() for x in text.split(",") if x.strip()]


def _intake_priorities(answers):
    """The intake's Top Health Goals table, in the order the client typed it.

    The form asks for concerns 'in order of importance', so form order IS the
    client's ranking. The 1-10 rating is carried for display but is never a sort
    key: of the three clients who have filled it in, one used it as importance
    (three 10s) and one as a rank (eyes=1, the reason she came). Sorting on it
    would bury her main concern."""
    out = []
    for row in (answers or {}).get("health_concerns") or []:
        if not isinstance(row, dict):
            continue
        concern = str(row.get("concern") or "").strip()
        if not concern:
            continue
        def _num(key):
            raw = str(row.get(key) or "").strip()
            try:
                return int(float(raw))
            except Exception:
                return None
        out.append({"concern": concern, "rating": _num("rating"),
                    "years_since_onset": _num("years_since_onset")})
    return out


def _dedupe(values):
    out, seen = [], set()
    for value in values:
        text = str(value or "").strip()
        key = " ".join(text.lower().split())
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def consolidate(people=None, intake_row=None, product_history=None, extended_history=None,
                historical_snapshots=None):
    """Merge current data first, then dated immutable intake history."""
    profile = dict(people or {}); answers = ((intake_row or {}).get("answers") or {})
    conditions, narrative = _answer_parts(answers)
    narrative += [str((product_history or {}).get(k + "_text") or "").strip() for k in ("prescriptions", "otc", "supplements") if (product_history or {}).get(k + "_text")]
    narrative += [str(v).strip() for k, v in (((extended_history or {}).get("answers") or {}).items()) if k.endswith("_text") and str(v or "").strip()]

    historical_goals = []
    historical_sources = []
    for snapshot in historical_snapshots or []:
        historical_answers = snapshot.get("answers") or {}
        old_conditions, old_narrative = _answer_parts(historical_answers)
        conditions += old_conditions
        raw = historical_answers.get("legacy_application_fields") or {}
        historical_goals += [raw.get("Wellness Goals"), raw.get("Healing_Support")]
        old_narrative += [raw.get(k) for k in ("Problems", "Chronicity") if raw.get(k)]
        date = str(snapshot.get("form_date") or "date unknown").strip()
        source = str(snapshot.get("form_name") or "Historical intake").strip()
        if old_narrative:
            narrative.append(f"[Historical intake — {date}] " + " | ".join(_dedupe(old_narrative)))
        historical_sources.append({
            "id": snapshot.get("id"), "date": date, "source": source,
            "review_status": snapshot.get("review_status") or "",
        })

    profile["conditions"] = _dedupe(_as_list(profile.get("conditions")) + conditions)
    profile["intake_priorities"] = _intake_priorities(answers)
    old = str(profile.get("challenges") or "").strip()
    profile["challenges"] = "\n".join(_dedupe(([old] if old else []) + narrative))
    old_goals = str(profile.get("goals") or "").strip()
    profile["goals"] = "\n".join(_dedupe(([old_goals] if old_goals else []) + historical_goals))
    profile["intake_submitted"] = bool(intake_row and intake_row.get("status") == "submitted")
    profile["historical_intake_count"] = len(historical_sources)
    profile["historical_intake_sources"] = historical_sources
    return profile
