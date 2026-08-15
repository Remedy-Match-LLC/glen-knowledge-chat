"""Build the console's mineable clinical profile from every client-entered store."""


def _table_values(rows, *keys):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = " — ".join(str(row.get(k) or "").strip() for k in keys if row.get(k))
        if text:
            out.append(text)
    return out


def consolidate(people=None, intake_row=None, product_history=None, extended_history=None):
    """Return the shape consumed by biofield_profile.mine_profile_stresses."""
    profile = dict(people or {})
    answers = ((intake_row or {}).get("answers") or {})
    conditions = []
    conditions += _table_values(answers.get("health_concerns"), "concern")
    conditions += _table_values(answers.get("diagnoses"), "diagnosis", "current")
    conditions += _table_values(answers.get("allergies"), "sensitivity", "reaction")

    narrative = []
    for key in ("obstacles", "sleep", "dental", "vaccinations"):
        value = str(answers.get(key) or "").strip()
        if value:
            narrative.append(value)
    narrative += _table_values(answers.get("medications"), "medication", "reason")
    narrative += _table_values(answers.get("supplements"), "brand", "name", "reason")
    narrative += _table_values(answers.get("surgeries"), "procedure", "reason")

    for kind in ("prescriptions", "otc", "supplements"):
        value = str((product_history or {}).get(kind + "_text") or "").strip()
        if value:
            narrative.append(value)
    for key, value in (((extended_history or {}).get("answers") or {}).items()):
        if key.endswith("_text") and str(value or "").strip():
            narrative.append(str(value).strip())

    existing = profile.get("conditions") or []
    if not isinstance(existing, list):
        existing = [x.strip() for x in str(existing).split(",") if x.strip()]
    profile["conditions"] = existing + conditions
    existing_challenges = str(profile.get("challenges") or "").strip()
    profile["challenges"] = "\n".join(([existing_challenges] if existing_challenges else []) + narrative)
    profile["intake_submitted"] = bool(intake_row and intake_row.get("status") == "submitted")
    return profile
