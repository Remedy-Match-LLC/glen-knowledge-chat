"""Increment 2: verbal-notes + narrative for the local Biofield Analysis viewer.

Stores Glen's per-test verbal notes and the generated narrative locally, builds the
Glen-voice prompt (following the biofield-causal-chain-narrative skill rules), and
generates the narrative via an injected LLM callable `complete(system, user) -> str`
so the logic is testable without a live API call.
"""
import datetime
import re
import sqlite3

from dashboard.narrative_grounding import (
    check_narrative, clean_scan_description, fix_scan_names, scan_name_problems)


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def fmt_saved_hst(iso):
    """Render a stored UTC timestamp (``2026-07-10T22:14:03Z``) as an HST label
    for the notes boxes, e.g. ``Jul 10, 2026 · 12:14 PM HST``. Hawaii observes no
    DST, so HST is a fixed UTC-10. Returns "" for an empty/unparseable value so
    the caller can show nothing rather than a broken date."""
    if not iso:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso.rstrip("Z")) - datetime.timedelta(hours=10)
    except ValueError:
        return ""
    hour12 = dt.hour % 12 or 12
    return (f"{dt.strftime('%b')} {dt.day}, {dt.year} · "
            f"{hour12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} HST")


def init_notes_tables(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_notes "
               "(test_id TEXT PRIMARY KEY, notes TEXT, updated_at TEXT)")
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_narratives "
               "(test_id TEXT PRIMARY KEY, narrative TEXT, updated_at TEXT)")
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_video_scripts "
               "(test_id TEXT PRIMARY KEY, script TEXT, updated_at TEXT)")
    cx.commit()


def _get(cx, table, col, test_id):
    init_notes_tables(cx)
    row = cx.execute(f"SELECT {col} FROM {table} WHERE test_id=?", (str(test_id),)).fetchone()
    return (row[0] if row and row[0] else "")


def get_notes_updated(cx, test_id):
    """The raw UTC timestamp of the last notes save, or "" if never saved."""
    return _get(cx, "biofield_notes", "updated_at", test_id)


def _save(cx, table, col, test_id, val):
    init_notes_tables(cx)
    ts = _now()
    cx.execute(
        f"INSERT INTO {table} (test_id, {col}, updated_at) VALUES (?,?,?) "
        f"ON CONFLICT(test_id) DO UPDATE SET {col}=excluded.{col}, updated_at=excluded.updated_at",
        (str(test_id), val or "", ts))
    cx.commit()
    return ts


def get_notes(cx, test_id):
    return _get(cx, "biofield_notes", "notes", test_id)


def save_notes(cx, test_id, notes):
    return _save(cx, "biofield_notes", "notes", test_id, notes)


def get_narrative(cx, test_id):
    return _get(cx, "biofield_narratives", "narrative", test_id)


def save_narrative(cx, test_id, narrative):
    return _save(cx, "biofield_narratives", "narrative", test_id, narrative)


def get_video_script(cx, test_id):
    return _get(cx, "biofield_video_scripts", "script", test_id)


def save_video_script(cx, test_id, script):
    return _save(cx, "biofield_video_scripts", "script", test_id, script)


_SYSTEM = (
    "You write in Dr. Glen Swartwout's warm, calm clinical voice, as a letter to a "
    "patient about their Biofield Analysis (a Causal Chain Report). RULES:\n"
    "- Open with 'Aloha <first name>,' then, when a TERRAIN READING is present, make the "
    "first paragraph a plain-language description of that terrain phase and its location. "
    "Do not omit, rename, or infer a different phase or location. The authoritative clinical "
    "phase names are: Phase 1 = Energize; Phase 2 = Rejuvenate; Phase 3 = Regenerate; "
    "Phase 4 = Cleanse; Phase 5 = Balance. In particular, NEVER call Phase 2 'Regenerate'—"
    "Regenerate belongs only to Phase 3. Then use 2-3 warm sentences framing the causal chain: "
    "the most recent layer sits on top, deeper and older roots beneath, and supporting them "
    "in order lets the chain unwind and the body self-correct.\n"
    "- One short plain-English paragraph per NUMBERED layer, top-down (Layer 1 = most "
    "recent/surface first, down to the deepest root). A numbered layer may contain multiple "
    "remedies. Keep all remedies carrying the same causal-layer identifier together in that one paragraph; "
    "never describe them as separate layers. Name every remedy and its dosing for that layer.\n"
    "- NAME EACH LAYER BY ITS HEAD: open each layer's paragraph with the layer's Head, in the "
    "practitioner's words, as what that layer is about. The 'most affected' list is supporting "
    "detail beneath the Head; never let it replace the Head as the layer's subject.\n"
    "- DRAW THE RELATIONSHIPS: explain how each layer connects to the others -- how a surface "
    "layer sits on or is driven by a deeper root -- so the chain reads as one connected story, "
    "not a list.\n"
    "- OBSERVATION LANGUAGE ONLY: the body 'identified' / 'showed coherence with' / the remedy "
    "was 'detected as best suited'. NEVER 'probably', 'should', 'most likely', or any hedge.\n"
    "- Fold the clinician's verbal notes in naturally where they fit; do not quote them as a list.\n"
    "- LIFE STRESS / ESSENCE LAYERS: inspect BOTH the Head and Tail of every causal layer for an "
    "essence, even when the Head is not labeled Life Stress or Psychoemotional Stress. Do not list "
    "AI-matched or 'supportive' essences. First describe the indications of the LIFE STRESS "
    "ASSOCIATED ESSENCE found at the Head or Tail, then "
    "describe the healing qualities of the THERAPEUTIC ESSENCE actually recommended as the remedy. "
    "Keep those roles distinct; the associated essence identifies the pattern, while the therapeutic "
    "essence is the treatment. REQUIRED OUTPUT: for every layer block containing 'LIFE STRESS "
    "ASSOCIATED ESSENCE / PATTERN', the layer paragraph MUST name that associated essence and "
    "state at least two of its supplied indications, then name the therapeutic essence and describe "
    "its supplied healing qualities. Never omit either half. Use only the catalog descriptions "
    "supplied in the layer block.\n"
    "- TAIL BEYOND THE HEAD: when a layer block carries 'TAIL BEYOND THE HEAD', add 2 to 4 "
    "plain sentences to that layer's paragraph, after naming the Head. Describe the structure and "
    "function of those tail areas and how they relate to the Head, so the reader sees why they "
    "sit on one layer. A tail item may carry a remedy-style name such as 'Jejunum Rejuvenator' "
    "or 'Liver Driver': describe the body area or function it names, not a product. Cover "
    "every tail area listed, grouping related ones. Connect them to the client only where a CLIENT-STATED CONCERNS item "
    "plainly relates; never invent a symptom or condition. Name one or two key pathways the "
    "layer's remedies support, taken ONLY from that remedy's 'pathways source' (its listed "
    "ingredients); when it says none was supplied, name no pathway for it. Name a remedy's "
    "ingredients only in a sentence that names that one remedy; never credit two remedies "
    "with an ingredient list together. Frame it as balancing these patterns supports the "
    "body's own function. Never say a remedy treats, heals, cures, fixes or prevents anything, "
    "and never state or imply a diagnosis. Never name another layer's remedy in this paragraph.\n"
    "- NAME ONLY THE CHAIN'S REMEDIES: never name a product, formula or supplement that is not "
    "listed as a remedy in the CAUSAL CHAIN.\n"
    "- PLAIN TEXT ONLY: no markdown, no asterisks, no bold, no headings. Still begin each "
    "layer paragraph with its number, as '1.', '2.' and so on.\n"
    "- NAME EVERY INFOCEUTICAL: whenever an infoceutical appears, give its full name with its "
    "code (for example 'ED5 Circulation Driver'), never the code alone.\n"
    "- NEVER use the words 'prescribe', 'prescribed', 'prescribes' or 'prescribing' for any "
    "remedy. Say 'recommended'.\n"
    "- Plain English; translate any technical codes. No jargon, no emojis, no AI-pleasantry "
    "filler ('I hope you're well'). Open with substance.\n"
    "- GROUNDED VOICE: write the way a calm clinician speaks to a patient -- concrete, warm, "
    "direct, plain. NO literary or poetic metaphors and NO ornamental flourish: do not call the "
    "analysis 'fascinating', do not use figures like 'a painting', 'weaving a story', 'tapestry', "
    "'cunning', 'a narrative of health', or 'journey'. Prefer short, plain sentences over flowery "
    "ones. Describe what was found and what to do, not how poetic it is.\n"
    "- Close with this practical guidance, using these sentences verbatim: 'If you tend to be "
    "highly sensitive or reactive, you can introduce each layer or each remedy one at a time and "
    "adjust the dosage to your tolerance. Begin gently with new remedies, visualize the desired "
    "healing effects, and observe how your body responds. Be sure to record or write any meaningful "
    "observations or questions in your portal chat interface.' Use this closing directly, without "
    "a different lead-in.\n"
    "- Sign off exactly: 'In wellness,' then 'Dr. Glen & Rae'.\n"
    "This is a DRAFT for Dr. Glen's review."
)


_SCAN_GUIDANCE = (
    "\n- If a RECENT BIOENERGETIC WELLNESS SCAN block is present, you may reference what the scan "
    "showed as corroborating context for the causal chain. Always call it the 'Bioenergetic "
    "Wellness Scan'; never write 'voice scan'. Never name a product from the scan block; "
    "only the CAUSAL CHAIN's remedies are recommended. Use observation language; "
    "do not invent scan findings beyond those listed, and do not treat a scan marked "
    "stale as current.")

_PROFILE_GUIDANCE = (
    "\n- If a CLIENT-STATED CONCERNS block is present, acknowledge the client's own "
    "stated symptoms, challenges, and goals in plain, validating language and connect "
    "them to the causal chain where honest to do so. Do not invent concerns beyond those listed.")

_PROFILE_FIELDS = ("conditions", "challenges", "goals", "tags", "terrain_concerns", "body_systems")


def _profile_content(profile):
    return bool(profile) and any(str((profile or {}).get(f) or "").strip() for f in _PROFILE_FIELDS)


def _profile_block(profile):
    if not _profile_content(profile):
        return ""
    lines = ["CLIENT-STATED CONCERNS (acknowledge in the client's own terms):"]
    for f in _PROFILE_FIELDS:
        v = profile.get(f)
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x).strip() for x in v if str(x).strip())
        v = str(v or "").strip()
        if v:
            lines.append(f"- {f.replace('_', ' ')}: {v}")
    return "\n".join(lines)


def _narrative_findings(scan):
    """The scan findings fed to the patient narrative = INFOCEUTICALS only. ER/MR
    'stresses' are information Glen doesn't balance, so they stay off the patient
    message. Falls back to splitting `findings` by group for un-split callers."""
    if not (scan and scan.get("found")):
        return []
    fs = scan.get("infoceuticals")
    if fs is None:
        fs = [f for f in (scan.get("findings") or []) if f.get("group") != "stress"]
    return fs or []


def _scan_block(scan):
    """Optional context block from the client's most recent E4L voice scan. Empty
    string unless the scan has infoceutical findings (back-compatible)."""
    findings = _narrative_findings(scan)
    if not findings:
        return ""
    days = scan.get("days_ago")
    age = f"{days} day{'s' if days != 1 else ''} ago" if days is not None else "date unknown"
    fresh = "fresh" if scan.get("fresh") else "STALE — older than the 2-week window"
    lines = [f"RECENT BIOENERGETIC WELLNESS SCAN (E4L; {age}, {fresh}; "
             f"scan {scan.get('scan_date') or ''}):"]
    for f in findings:
        rank = f.get("rank")
        # Product suggestions and source tags are the practitioner's notes. Handed to the
        # writer, "Consider: Liver Support" put Liver Support in Donna Banks's letter.
        desc = clean_scan_description(f.get("description"))
        lines.append(f"- {('#' + str(rank) + ' ') if rank is not None else ''}"
                     f"{f.get('code') or ''} {f.get('name') or ''}"
                     f"{(' — ' + desc) if desc else ''}".rstrip())
    return "\n".join(lines)


# An animal's narrative is addressed to the OWNER and names the animal. Glen,
# 2026-09-18: "Address the animal report to the owner (e.g. Aloha Sharon, Hershey's
# Biofield Analysis...)", and, since no store records an animal's sex, "name only is
# perfect". The human prompt above is not touched; these swap two of its rules.
_HUMAN_OPEN = "- Open with 'Aloha <first name>,' then, when a TERRAIN READING is present, make the "
_HUMAN_CLOSE = (
    "'If you tend to be "
    "highly sensitive or reactive, you can introduce each layer or each remedy one at a time and "
    "adjust the dosage to your tolerance. Begin gently with new remedies, visualize the desired "
    "healing effects, and observe how your body responds. Be sure to record or write any meaningful "
    "observations or questions in your portal chat interface.'"
)


def _animal_system(base, animal):
    pet = (animal.get("name") or "").strip() or "your animal"
    owner = (animal.get("owner") or "").strip()
    greeting = f"Aloha {owner}," if owner else "Aloha,"
    opening = (f"- Open with '{greeting}' on its own line. Begin the first paragraph with "
               f"'{pet}'s Biofield Analysis', then, when a TERRAIN READING is present, make that "
               "first paragraph a plain-language description of that terrain phase and its location. ")
    closing = (f"'If {pet} tends to be highly sensitive or reactive, you can introduce each layer "
               f"or each remedy one at a time and adjust the dosage to {pet}'s tolerance. Begin "
               f"gently with new remedies, visualize the desired healing effects, and observe how "
               f"{pet} responds. Be sure to record or write any meaningful observations or "
               f"questions in your portal chat interface.'")
    assert _HUMAN_OPEN in base and _HUMAN_CLOSE in base, "narrative rules moved; update the animal swap"
    system = base.replace(_HUMAN_OPEN, opening).replace(_HUMAN_CLOSE, closing)
    species = (animal.get("species") or "animal").strip().lower()
    return system + (
        f"\nANIMAL CLIENT: this analysis is of {pet}, a {species}. The reader is "
        f"{owner or 'the owner'}, who cares for {pet}. Write to the owner as 'you'; the body, "
        f"the layers and the remedies are {pet}'s. Never call {pet} 'you'. Never refer to {pet} "
        "with a pronoun: not 'he', 'she', 'him', 'his', 'her', 'hers' or 'it'. Nothing records "
        f"the animal's sex, so repeat the name '{pet}' instead.\n"
    )


def _enforce_animal_greeting(text, animal):
    """Correct an LLM that greets the animal instead of the owner."""
    if not animal:
        return text
    pet = (animal.get("name") or "").strip()
    owner = (animal.get("owner") or "").strip()
    if not pet:
        return text
    greeting = f"Aloha {owner}," if owner else "Aloha,"
    return re.sub(rf"^\s*Aloha\s+{re.escape(pet)}\b[^,\n]*,", greeting, text or "", count=1)


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _user_block(report, notes, scan=None, profile=None, animal=None, with_tail=True):
    c = report.get("client") or {}
    if animal:
        pet = (animal.get("name") or "").strip()
        species = (animal.get("species") or "animal").strip().lower()
        owner = (animal.get("owner") or "").strip() or "(unknown; greet with 'Aloha,')"
        who = [f"ANIMAL (the subject of this analysis): {pet}, a {species}",
               f"OWNER (the reader; address the letter to them): {owner}"]
    else:
        who = [f"PATIENT: {c.get('name') or ''}"]
    lines = who + [f"DATE: {report.get('date') or ''}",
                   ""]
    from dashboard.terrain_phase import phase_narrative_description
    terrain = phase_narrative_description(report.get("phase"), report.get("location"))
    if terrain:
        lines += ["TERRAIN READING (use as the first paragraph after the greeting):",
                  terrain, ""]
    lines += ["CAUSAL CHAIN (top-down, most recent layer first to deepest root):"]
    # Number the layers exactly as the editor's cards and the report's Causal Chain
    # table do, with the same function. Grouping by stored layer number split Michael
    # Hill's three spleen remedies, stored as layers 4, 5 and 6 under one head, into
    # three layers and threw every later number off the report's (Glen, 2026-09-18).
    from dashboard.biofield_report_html import group_layers
    grouped = [(g["layer"], g["rows"]) for g in group_layers(report.get("layers") or [])]
    # The portal splits this letter into layer cards by finding each layer's remedy
    # name (segment_narrative). An ingredient containing another layer's remedy name,
    # named in this layer's paragraph, would start that layer's card here.
    from dashboard.biofield_portal_publish import _cue_candidates
    # Remedy cues only: a head is a cue of last resort, and a short one ("Acid") would
    # strip "Ascorbic Acid" from every other layer.
    cues_by_layer = {ln: {c.lower() for r in rows if (r.get("remedy") or "").strip()
                          for c in _cue_candidates({"remedy": r.get("remedy")})}
                     for ln, rows in grouped}
    other_cues = {ln: set().union(*[v for k, v in cues_by_layer.items() if k != ln])
                  for ln in cues_by_layer}
    for display_ln, layer_rows in grouped:
        first = layer_rows[0]
        head = (first.get("head") or "").strip()
        affected = (first.get("most_affected") or "").strip()
        # A layer keeps a remedy-less anchor row when its last remedy is removed. It is
        # not a remedy: counting it told the writer Hershey Connour's layer 4 had two
        # remedies, and it wrote "the specific remedy and dose were not detailed".
        remedy_rows = [l for l in layer_rows if (l.get("remedy") or "").strip()]
        lines.append(
            f"- Layer {display_ln} (ONE layer; {len(remedy_rows)} remed{'y' if len(remedy_rows) == 1 else 'ies'}): "
            f"{head} (most affected: {affected})")
        head_is_essence = _is_essence(head)
        tail_is_essence = _is_essence(affected)
        is_life_stress = ("life stress" in head.lower() or
                          "psychoemotional" in head.lower() or
                          head_is_essence or tail_is_essence)
        # Glen, 2026-09-24: a tail naming more than the head gets its own description.
        # A whole-tail essence is left to the life stress rule below.
        # Every row's tail counts, not only the first's; items compare without case
        # or punctuation, so "Liver." is the head "Liver" and "kidney" repeats "Kidney".
        seen = {_norm(head)}
        if tail_is_essence:
            seen.add(_norm(affected))
        beyond = []
        for row in layer_rows:
            for t in str(row.get("most_affected") or "").split(","):
                t = t.strip()
                if t and _norm(t) and _norm(t) not in seen:
                    seen.add(_norm(t))
                    beyond.append(t)
        if beyond and with_tail:
            lines.append(f"  - TAIL BEYOND THE HEAD: {'; '.join(beyond)}")
            lines.append("    (write 2 to 4 sentences on these tail areas in this layer's "
                         "paragraph, per the TAIL BEYOND THE HEAD rule)")
        if is_life_stress:
            associated = affected if tail_is_essence else head
            if not associated:
                associated = affected or head
            associated_desc = _catalog_description(associated)
            lines.append(
                f"  - LIFE STRESS ASSOCIATED ESSENCE / PATTERN: {associated}"
                f"; indications: {associated_desc or '(catalog description unavailable)'}")
        for l in remedy_rows:
            remedy = l.get("remedy") or ""
            role = "THERAPEUTIC ESSENCE" if is_life_stress else "remedy"
            qualities = _catalog_description(remedy) if is_life_stress else ""
            pathways = (_pathways_source(remedy, other_cues[display_ln])
                        if beyond and with_tail else "")
            # The report prints "(as directed)" for a remedy with no dosing; say the same
            # here, or the writer reports the dose as missing.
            dose = " ".join(x for x in (l.get("dosage") or "", l.get("frequency") or "",
                                        l.get("timing") or "") if x.strip()) or "as directed"
            lines.append(
                f"  - {role}: {remedy}"
                f"{('; healing qualities: ' + qualities) if qualities else ''}"
                f"{('; pathways source: ' + pathways) if pathways else ''}; "
                f"dose: {dose}")
    sb = _scan_block(scan)
    if sb:
        lines += ["", sb]
    pb = _profile_block(profile)
    if pb:
        lines += ["", pb]
    lines += ["", "CLINICIAN VERBAL NOTES (weave in naturally):", (notes or "(none)")]
    return "\n".join(lines)


def _catalog_description(name):
    """Best-effort catalog description for a named associated/remedy essence."""
    return (_catalog_product(name).get("description") or "").strip()


def _catalog_product(name):
    """Best-effort catalog product resolution, including local-only essences."""
    if not (name or "").strip():
        return {}
    try:
        import re
        from dashboard.biofield_portal_publish import load_catalog
        from dashboard.practitioner_portal import name_to_slug
        catalog = load_catalog()
        # "+" is kept as "plus": OcuHeal+ must not be written from OcuHeal's page.
        key = lambda t: re.sub(r"[^a-z0-9]", "", str(t or "").lower().replace("+", "plus"))
        wanted = key(name)
        exact_slug = next((s for s, p in catalog.items()
                           if key((p or {}).get("name")) == wanted), None)
        try:
            slug = exact_slug or name_to_slug(name, catalog)
        except Exception:
            slug = exact_slug
        if not slug:
            # Practitioner-store resolution intentionally excludes some local-only
            # Terrain Restore essences. Narrative context still needs their catalog
            # descriptions, so fall back to an exact punctuation-insensitive name.
            slug = next((s for s, p in catalog.items()
                         if key((p or {}).get("name")) == wanted), None)
        product = catalog.get(slug) if slug else None
        return product or {}
    except Exception:
        return {}


def _pathways_source(name, exclude=()):
    """What the writer may draw a remedy's pathways from: its ingredient names only.
    Catalog descriptions carry disease claims ("healing the underlying causes of
    Glaucoma"), prices and competitor comparisons, so they are never passed here.
    An ingredient containing another layer's remedy name is left out: named in this layer's
    paragraph, it would split the portal cards at the wrong place. Nothing found says
    so, rather than leaving the writer free to invent a pathway."""
    skip = {str(x).strip().lower() for x in exclude} - {""}
    names = [str(i.get("name") or "").strip()
             for i in (_catalog_product(name).get("ingredients") or []) if isinstance(i, dict)]
    # "(unnamed FMP ingredient 5461)" is a placeholder for a missing FileMaker name.
    names = [n for n in names if n and "unnamed fmp ingredient" not in n.lower()
             and not any(c in n.lower() for c in skip)][:8]
    return ("ingredients: " + ", ".join(names)) if names else "(none supplied; name no pathway)"


def _is_essence(name):
    """Whether a Head/Tail value resolves to an essence-class catalog remedy."""
    product = _catalog_product(name)
    catalog_name = ((product or {}).get("name") or "").lower()
    return any(term in catalog_name for term in (
        "essence", "gem elixir", "flower enhancer", "flower remedy"))


def _system_with_scan(base, scan):
    """Append scan guidance only when the narrative actually carries scan findings, so
    the no-scan prompt stays byte-identical to before (back-compat)."""
    return base + (_SCAN_GUIDANCE if _narrative_findings(scan) else "")


def build_narrative_prompt(report, notes, scan=None, profile=None, animal=None):
    system = _system_with_scan(_SYSTEM, scan)
    if animal:
        system = _animal_system(system, animal)
    if _profile_content(profile):
        system += _PROFILE_GUIDANCE
    return {"system": system, "user": _user_block(report, notes, scan, profile, animal)}


def generate_narrative(report, notes, complete, scan=None, profile=None, animal=None,
                       problems_out=None):
    """complete(system, user) -> narrative text. scan = E4L context; profile = People-hub
    context; animal = {"name", "species", "owner"} when the client is an animal, else None.

    The draft is checked for products off the chain and nutrients credited to the wrong
    remedy. A failing draft is written once more, told exactly what was wrong (Glen,
    2026-09-25). Whatever is still wrong is appended to problems_out for the editor."""
    p = build_narrative_prompt(report, notes, scan, profile, animal)
    return _checked(p, complete, report, lambda t: _finish(t, report, animal), problems_out)


def _checked(p, complete, report, finish, problems_out):
    """Write, check, and on a failing draft write once more with its errors named.
    Keeps whichever draft has fewer problems; leftovers go to problems_out."""
    text = finish(complete(p["system"], p["user"]))
    problems = _safe_problems(text, report)
    if problems:
        retry = (p["user"] + "\n\nYOUR PREVIOUS DRAFT HAD THESE ERRORS. Write the whole "
                 "text again, following every rule, without them:\n"
                 + "\n".join("- " + x for x in problems))
        try:
            second = finish(complete(p["system"], retry))
        except Exception as e:
            # The first draft was paid for; a failed retry must not throw it away.
            print(f"[narrative] retry failed, keeping first draft: {e!r}", flush=True)
            second = None
        if second is not None:
            second_problems = _safe_problems(second, report)
            if len(second_problems) <= len(problems):
                text, problems = second, second_problems
    if problems_out is not None:
        problems_out.extend(problems)
    return text


def _finish(text, report, animal):
    text = _enforce_animal_greeting(text, animal)
    text = _enforce_no_prescribe(text)
    text = fix_scan_names(text)
    return _enforce_phase_name(text, report.get("phase"))


def _ingredient_lines(name):
    return [str(i.get("name") or "").strip()
            for i in (_catalog_product(name).get("ingredients") or []) if isinstance(i, dict)]


def _safe_problems(text, report):
    """A fault in the check must never cost the paid-for draft."""
    try:
        return narrative_problems(text, report)
    except Exception as e:
        print(f"[narrative-check] {e!r}", flush=True)
        return []


def narrative_problems(text, report):
    """check_narrative against this report's chain and the catalog. Only the chain's own
    rows (remedies, Heads, Tails) permit a product name. Notes, scan findings and the
    profile do not: "Consider Liver Support" in any of them is still not on the chain
    (blind review, 2026-09-25). The same inputs on generate, save and page load."""
    rows = report.get("layers") or []
    # A row may hold two products, "Focus, Neuromagnesium"; each is checked on its own.
    chain = list(dict.fromkeys(
        part.strip() for r in rows
        for part in re.split(r"\s*,\s*|\s+\+\s+", str(r.get("remedy") or ""))
        if part.strip()))
    heads = " ".join(f"{r.get('head') or ''} {r.get('most_affected') or ''}" for r in rows)
    try:
        from dashboard.biofield_portal_publish import load_catalog
        catalog = load_catalog() or {}
    except Exception:
        catalog = {}
    names = [str((p or {}).get("name") or "") for p in catalog.values()]
    # Other catalog spellings of a chain remedy's own product ("OcuHeal" and "OcuHeal Eye
    # Drops" when both are one entry) count as on the chain.
    same = []
    for c in chain:
        prod = _catalog_product(c)
        if prod:
            same += [str(p.get("name") or "") for p in catalog.values()
                     if p is prod or (p or {}).get("name") == prod.get("name")]
    return scan_name_problems(text) + check_narrative(
        text, chain=chain + [n for n in same if n and n not in chain], ingredients={c: _ingredient_lines(c) for c in chain},
        catalog_names=names,
        # The service itself is the one name allowed beyond the chain's own rows.
        # Terrain Restore is the essence base, named inside essence products.
        allowed_text=heads + "\nBiofield Analysis\nTerrain Restore", heads_text=heads)


_PRESCRIBE = {"prescribe": "recommend", "prescribes": "recommends",
              "prescribed": "recommended", "prescribing": "recommending"}


def _enforce_no_prescribe(text):
    """Glen, 2026-09-18: "Never use the term prescribe." Rewrites the verb only;
    'prescription' is left alone, since it names a client's own medication."""
    def sub(m):
        w = m.group(0)
        r = _PRESCRIBE[w.lower()]
        return r.capitalize() if w[0].isupper() else r
    return re.sub(r"\bprescrib(?:e|es|ed|ing)\b", sub, text or "", flags=re.IGNORECASE)


def _enforce_phase_name(text, phase):
    """Correct an LLM phase-name slip while preserving the rest of its draft."""
    from dashboard.terrain_phase import PHASE_CLINICAL_NAMES, phase_num
    n = phase_num(phase)
    if not n:
        return text
    expected = PHASE_CLINICAL_NAMES[n]
    other = "|".join(re.escape(v) for k, v in PHASE_CLINICAL_NAMES.items() if k != n)
    # Covers “Phase 2, Regenerate”, “Phase 2: Regenerate”, and “Phase 2 Regenerate”.
    return re.sub(
        rf"(\bPhase\s*{n}\s*(?:[,:\u2014-]\s*)?)(?:{other})\b",
        rf"\1{expected}",
        text or "",
        flags=re.IGNORECASE,
    )


_VIDEO_SYSTEM = (
    "You are Dr. Glen Swartwout speaking ALOUD to a patient -- recording a short voice "
    "walkthrough of their Biofield Analysis. Output ONLY the words to be spoken: no stage "
    "directions, no headings, no markdown, no remedy bullet list. RULES:\n"
    "- SHORT: about 150 words, roughly 60-90 seconds spoken. Give an overview plus the 2-3 most "
    "important layers and their key remedy -- NOT every layer or every dose.\n"
    "- Open 'Aloha <first name>,' and speak warmly in the first person ('I', 'we'), "
    "conversational and plain, the way you'd talk to them across the table.\n"
    "- Frame the causal chain simply: the most recent layer sits on top, deeper roots beneath, "
    "and supporting them in order lets the body unwind and self-correct.\n"
    "- OBSERVATION LANGUAGE: the body 'identified' / 'showed' / 'pointed to'. NEVER 'probably', "
    "'should', 'most likely'.\n"
    "- Fold in the clinician's verbal notes naturally if they fit.\n"
    "- GROUNDED VOICE: plain, warm, direct. No literary or poetic metaphors, no AI filler.\n"
    "- Name where to begin and reassure them: start gently, watch, adjust, and you'll guide them. "
    "Close warmly. This is a DRAFT for Dr. Glen's review."
)


def build_video_script_prompt(report, notes, scan=None):
    return {"system": _system_with_scan(_VIDEO_SYSTEM, scan),
            "user": _user_block(report, notes, scan, with_tail=False)}


def generate_video_script(report, notes, complete, scan=None, problems_out=None):
    """complete(system, user) -> short spoken walkthrough script. `scan` optional. The
    client hears it, so it gets the letter's check and one retry (blind review)."""
    p = build_video_script_prompt(report, notes, scan)
    return _checked(p, complete, report, fix_scan_names, problems_out)
