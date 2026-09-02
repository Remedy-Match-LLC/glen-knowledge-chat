"""Build the console's mineable clinical profile from every intake store."""

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

    existing = profile.get("conditions") or []
    if not isinstance(existing, list): existing = [x.strip() for x in str(existing).split(",") if x.strip()]
    profile["conditions"] = _dedupe(existing + conditions)
    old = str(profile.get("challenges") or "").strip()
    profile["challenges"] = "\n".join(_dedupe(([old] if old else []) + narrative))
    old_goals = str(profile.get("goals") or "").strip()
    profile["goals"] = "\n".join(_dedupe(([old_goals] if old_goals else []) + historical_goals))
    profile["intake_submitted"] = bool(intake_row and intake_row.get("status") == "submitted")
    profile["historical_intake_count"] = len(historical_sources)
    profile["historical_intake_sources"] = historical_sources
    return profile
