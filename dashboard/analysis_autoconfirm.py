"""Gated auto-confirm of AI biofield drafts (Phase 1, B+C).

evaluate_quality = the "B" gate: a draft is publishable only if every layer is
complete and resolvable and no red-flag term appears. should_sample = the "C"
audit: a deterministic slice always goes to human review. maybe_auto_confirm ties
them together and logs every decision. Pure module: no Flask, no network."""
import hashlib
import json


def _dosing_present(layer):
    return any((layer.get(k) or "").strip() for k in ("dosage", "frequency", "timing", "dosing"))


def evaluate_quality(content, *, resolve_slug, red_flag_terms):
    """(ok, reasons). ok only when every check passes; reasons lists each failure."""
    reasons = []
    content = content or {}
    all_layers = content.get("layers") or []
    layers = [L for L in all_layers if (L.get("title") or "").strip()]
    if not layers:
        reasons.append("no titled layer")
    for i, L in enumerate(all_layers):
        title = (L.get("title") or "").strip()
        if title:
            continue
        rem = (L.get("remedy") or "").strip()
        if rem or _dosing_present(L):
            reasons.append(f"layer {i}: has content but no title")
    for i, L in enumerate(layers):
        rem = (L.get("remedy") or "").strip()
        if not rem:
            reasons.append(f"layer {i}: empty remedy")
        elif resolve_slug(rem) is None:
            reasons.append(f"layer {i}: remedy not in catalog ({rem!r})")
        if not _dosing_present(L):
            reasons.append(f"layer {i}: missing dosing")
    if red_flag_terms:
        blob = json.dumps(content, ensure_ascii=False).lower()
        for term in red_flag_terms:
            if term and term.lower() in blob:
                reasons.append(f"red_flag term: {term}")
    return (not reasons, reasons)


def animal_formulation_reasons(content, *, resolve_slug, is_formulation):
    """Why an animal's draft must not publish. Glen, 2026-09-15: animal reports recommend
    only the E4L report's infoceuticals, never our Functional Formulations. Checks each
    layer's remedy and any named alternatives; an unresolvable name is left to the gate."""
    reasons = []
    for i, L in enumerate((content or {}).get("layers") or []):
        names = [(L.get("remedy") or "").strip()]
        for alt in (L.get("alternatives") or []):
            if isinstance(alt, dict):
                names.append((alt.get("remedy") or alt.get("name") or "").strip())
            elif isinstance(alt, str):
                names.append(alt.strip())
        for name in names:
            slug = resolve_slug(name) if name else None
            if slug is not None and is_formulation(slug):
                reasons.append(f"layer {i}: animal report names a formulation ({name!r})")
    return reasons


def should_sample(email, scan_date, pct):
    """Deterministic audit sampling: hash(email|scan_date) mod 100 < pct."""
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        pct = 0
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    key = f"{(email or '').strip().lower()}|{(scan_date or '').strip()}"
    bucket = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 100
    return bucket < pct


def init_autoconfirm_log(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS analysis_autoconfirm_log ("
        "email TEXT, scan_date TEXT, decision TEXT, reasons TEXT, "
        "sampled INTEGER DEFAULT 0, created_at TEXT DEFAULT '', "
        "PRIMARY KEY (email, scan_date))")
    cx.commit()


def _log(cx, email, scan_date, decision, reasons, sampled, now):
    # ON CONFLICT, not INSERT OR REPLACE: pgcompat does not translate OR REPLACE, so on
    # Postgres every decision raised a syntax error and no row was ever written.
    cx.execute(
        "INSERT INTO analysis_autoconfirm_log "
        "(email, scan_date, decision, reasons, sampled, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (email, scan_date) DO UPDATE SET decision=excluded.decision, "
        "reasons=excluded.reasons, sampled=excluded.sampled, created_at=excluded.created_at",
        ((email or "").strip().lower(), (scan_date or "").strip(), decision,
         json.dumps(reasons or []), 1 if sampled else 0, now or ""))
    cx.commit()


def maybe_auto_confirm(cx, email, scan_date, content, *, enabled, sample_pct,
                       resolve_slug, red_flag_terms, confirm_fn, now):
    """Decide confirm-vs-hold for one ai_draft. Returns an outcome string; logs it."""
    if not enabled:
        return "disabled"
    ok, reasons = evaluate_quality(content, resolve_slug=resolve_slug,
                                   red_flag_terms=red_flag_terms)
    if not ok:
        _log(cx, email, scan_date, "held_quality", reasons, False, now)
        return "held_quality"
    if should_sample(email, scan_date, sample_pct):
        _log(cx, email, scan_date, "held_sample", [], True, now)
        return "held_sample"
    confirm_fn(cx, email, scan_date, content)
    _log(cx, email, scan_date, "confirmed", [], False, now)
    return "confirmed"
