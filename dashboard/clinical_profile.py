"""Build the console's mineable clinical profile from every client-entered store."""

def _table_values(rows, *keys):
    out = []
    for row in rows or []:
        if not isinstance(row, dict): continue
        text = " — ".join(str(row.get(k) or "").strip() for k in keys if row.get(k))
        if text: out.append(text)
    return out

def consolidate(people=None, intake_row=None, product_history=None, extended_history=None):
    profile = dict(people or {}); answers = ((intake_row or {}).get("answers") or {})
    conditions = (_table_values(answers.get("health_concerns"), "concern")
                  + _table_values(answers.get("diagnoses"), "diagnosis", "current")
                  + _table_values(answers.get("allergies"), "sensitivity", "reaction"))
    narrative = [str(answers.get(k)).strip() for k in (
        "obstacles", "sleep", "dental", "vaccinations", "physical_trauma",
        "psychoemotional_trauma", "toxins") if answers.get(k)]
    narrative += _table_values(answers.get("medications"), "medication", "reason")
    narrative += _table_values(answers.get("otc_drugs"), "medication", "reason")
    narrative += _table_values(answers.get("supplements"), "brand", "name", "reason")
    narrative += _table_values(answers.get("surgeries"), "procedure", "reason")
    narrative += _table_values(answers.get("family_history"), "relative", "condition", "age_onset")
    narrative += [str((product_history or {}).get(k + "_text") or "").strip() for k in ("prescriptions", "otc", "supplements") if (product_history or {}).get(k + "_text")]
    narrative += [str(v).strip() for k, v in (((extended_history or {}).get("answers") or {}).items()) if k.endswith("_text") and str(v or "").strip()]
    existing = profile.get("conditions") or []
    if not isinstance(existing, list): existing = [x.strip() for x in str(existing).split(",") if x.strip()]
    profile["conditions"] = existing + conditions
    old = str(profile.get("challenges") or "").strip()
    profile["challenges"] = "\n".join(([old] if old else []) + narrative)
    profile["intake_submitted"] = bool(intake_row and intake_row.get("status") == "submitted")
    return profile
