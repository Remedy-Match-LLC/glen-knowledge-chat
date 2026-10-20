"""The Foods, Remedies and Healing Tools Plan: what Glen ticked in a client's
Clinical Summary, as one list per heading, each line
followed by its approved explanation.

Glen, 2026-10-18: "It will provide the checked information in the Clinical Summary
in both list and paragraph form with explanations in the paragraphs. It is it's own
report, but when printing a Biofield Analysis report, it can be selected to print at
the same time as an addition to the same pdf file."

Headings (Glen, same day): Foods; Remedies, which holds Infoceuticals and PRL too;
Healing Tools. Within each, items group under "Score N" headings by their highest
overall score, highest first; only the overall digit shows. An item ticked under
several conditions is one line, naming each condition with its score and, beneath,
its explanation. The explanation prints only once Glen has approved it; an item
without one prints its line alone. Differently named items share a line only
through a merge map clinical writes and Glen approves.
"""
import html
import json
import os

from dashboard import biofield_clinical_sections as cs
from dashboard.biofield_clinical_checklist import _norm

# (heading, [(group key, group title)]) in print order.
HEADINGS = (
    ("Foods", (("foods_emphasize", "Foods to emphasize"),
               ("foods_avoid", "Foods to avoid"))),
    ("Remedies", ((cs.FORMULATIONS, "Functional Formulations"),
                  (cs.INFOCEUTICALS, "Infoceuticals"),
                  ("prl", "PRL"),
                  (cs.NUTRIENTS, "Nutrients"),
                  (cs.BOTANICALS, "Botanicals"),
                  (cs.ESSENTIAL_OILS, "Essential Oils"),
                  (cs.FULLSCRIPT, "Fullscript supplements"))),
    ("Healing Tools", ((cs.HEALING_TOOLS, "Healing Tools"),)),
)


def _labels(cx, test_id):
    """Condition keys to the names Glen gave the rows."""
    try:
        row = cx.execute("SELECT labels FROM biofield_clinical_rows WHERE test_id=?",
                         (str(test_id),)).fetchone()
        labels = json.loads(row[0]) if row else []
    except Exception:  # table absent on a fresh database
        labels = []
    return {_norm(label): label for label in labels}


def _item_rows(cx, item_key):
    rows = cx.execute(
        "SELECT section,name_key,name,dose,overall,explanation,explanation_approved "
        "FROM biofield_clinical_section_item WHERE item_key=?", (item_key,)).fetchall()
    return {(r[0], r[1]): {"name": r[2], "dose": r[3], "overall": r[4],
                           "explanation": r[5] or "", "approved": bool(r[6])}
            for r in rows}


def _remedy_section(name):
    key = _norm(name)
    if cs.is_infoceutical(name) and not cs._NOT_INFOCEUTICAL.match(str(name).strip()):
        return cs.INFOCEUTICALS
    return cs.FORMULATIONS


def gather(cx, test_id, lost=None):
    """[(section, condition label, name, dose, overall, explanation, approved,
    condition key)] for
    every ticked item, before merging repeats. A tick that cannot print is added to
    `lost` as (section, condition, name, why), for Glen's note."""
    from dashboard.biofield_clinical_checklist import aliases, display_labels
    from dashboard.biofield_clinical_proposals import dismissed_labels, selections
    cs.ensure_schema(cx)
    test_id = str(test_id)
    labels = _labels(cx, test_id)
    shown = display_labels(cx)
    lost = [] if lost is None else lost
    out, cache, pick_cache = [], {}, {}
    # A dismissed row is off the Summary. A folded row's own ticks are not shown on
    # it either, so neither prints; Glen's note names them.
    dismissed = {_norm(label) for label in dismissed_labels(cx, test_id)}
    folded = aliases(cx)
    # Shared-list remedies ticked with no item in the condition's file.
    extra = {(r[0], r[1], r[2]): r[3] for r in cx.execute(
        "SELECT item_key,section,name_key,explanation FROM biofield_clinical_extra_explanation")}

    def rows(item_key):
        if item_key not in cache:
            cache[item_key] = _item_rows(cx, item_key)
        return cache[item_key]

    def picks(item_key):
        if item_key not in pick_cache:
            pick_cache[item_key] = cs._picks(cx, test_id, item_key)
        return pick_cache[item_key]

    def label_for(item_key):
        return shown.get(item_key) or labels.get(item_key) or item_key.capitalize()

    def off_summary(section, item_key, name):
        if item_key in dismissed:
            lost.append((section, label_for(item_key), name, "its row was dismissed"))
            return True
        if item_key in folded:
            lost.append((section, label_for(item_key), name,
                         f"its row was folded into {folded[item_key]}"))
            return True
        return False

    # Functional Formulations and Infoceuticals: the row's ticked remedies.
    selections(cx, test_id)   # creates the table on a fresh database
    for label, raw in cx.execute(
            "SELECT label,remedies FROM biofield_clinical_selection "
            "WHERE test_id=? AND remedies IS NOT NULL", (test_id,)).fetchall():
        # The selection key drops "+"; every other store spells it "plus", so key
        # from the row's own label.
        item_key = _norm(label)
        try:
            names = [str(n) for n in json.loads(raw or "[]") if str(n or "").strip()]
        except ValueError:
            names = []
        for name in names:
            key = _norm(name)
            # Catalogued under Infoceuticals counts, as on the Summary.
            section = cs.INFOCEUTICALS if (cs.INFOCEUTICALS, key) in rows(item_key) \
                else _remedy_section(name)
            if off_summary(section, item_key, name):
                continue
            row = rows(item_key).get((section, key)) \
                or rows(item_key).get((cs.FORMULATIONS, key)) or {}
            explanation, approved = row.get("explanation") or "", bool(row.get("approved"))
            # An extra explains only a remedy with no catalog row.
            if not row:
                text = extra.get((item_key, section, key)) \
                    or extra.get((item_key, cs.FORMULATIONS, key))
                if text:
                    explanation, approved = text, True
            # The client's own score and dose win, as on the Summary.
            pick = picks(item_key).get((section, key)) \
                or picks(item_key).get((cs.FORMULATIONS, key)) or {}
            overall = pick.get("overall") if pick.get("overall") is not None \
                else row.get("overall")
            # A dose the client cleared stays clear, as on the Summary.
            dose = pick.get("dose") if pick.get("dose") is not None else row.get("dose")
            out.append((section, label_for(item_key), name, dose or "", overall,
                        explanation, approved, item_key))
    # Every other section: the ticks kept per item.
    for item_key, section, name_key, dose, overall in cx.execute(
            "SELECT item_key,section,name_key,dose,overall FROM "
            "biofield_clinical_section_pick WHERE test_id=? AND checked=1", (test_id,)):
        row = rows(item_key).get((section, name_key))
        name = row["name"] if row else name_key
        if off_summary(section, item_key, name):
            continue
        if not row:
            lost.append((section, label_for(item_key), name_key,
                         "the item left the catalog"))
            continue
        out.append((section, label_for(item_key), row["name"],
                    dose if dose is not None else row["dose"],
                    overall if overall is not None else row["overall"],
                    row["explanation"], row["approved"], item_key))
    # PRL products ticked in a focus area.
    try:
        prl = cx.execute(
            "SELECT p.name_key,r.name,COALESCE(p.overall,r.overall),"
            "COALESCE(m.display_name,'') "
            "FROM biofield_prl_pick p LEFT JOIN biofield_prl_rating r ON "
            "r.focus_area_id=p.focus_area_id AND r.name_key=p.name_key "
            "LEFT JOIN biofield_prl_condition_map m ON m.focus_area_id=p.focus_area_id "
            "WHERE p.test_id=? AND p.checked=1", (test_id,)).fetchall()
    except Exception:  # PRL tables absent
        prl = []
    for name_key, name, overall, area in prl:
        if name is None:
            lost.append(("prl", area or "PRL", name_key, "it has no rating row"))
            continue
        out.append(("prl", area or "PRL", name, "", overall, "", False, _norm(area or "")))
    return out


def lost_ticks(cx, test_id):
    """(section, condition, name, why) for each tick the Plan cannot print."""
    lost = []
    gather(cx, test_id, lost)
    return lost


# What each section is called beside an item under Remedies, where sections mix.
KIND = {cs.FORMULATIONS: "Functional Formulation", cs.INFOCEUTICALS: "Infoceutical",
        "prl": "PRL", cs.NUTRIENTS: "Nutrient", cs.BOTANICALS: "Botanical",
        cs.ESSENTIAL_OILS: "Essential oil", cs.FULLSCRIPT: "Fullscript supplement",
        # No data yet (2026-10-19); the condition files will use these keys.
        "pure_powders": "Pure Powders", "amazon": "Amazon"}


# A merge group in clinical's map covers one of these scopes; products never merge.
_SCOPE = {"foods_emphasize": "foods_emphasize", "foods_avoid": "foods_avoid",
          cs.NUTRIENTS: "nutrients_botanicals", cs.BOTANICALS: "nutrients_botanicals"}


def ensure_schema(cx):
    """Clinical's merge map and score-heading lines, approved entries only."""
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_plan_merge (
        scope TEXT NOT NULL, name_key TEXT NOT NULL, display_name TEXT NOT NULL,
        PRIMARY KEY (scope, name_key))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_plan_wording (
        kind TEXT NOT NULL, key TEXT NOT NULL, text TEXT NOT NULL,
        PRIMARY KEY (kind, key))""")
    # Clinical's ingredient caution table, and the plan items each caution covers.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_plan_caution (
        pos INTEGER PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_plan_caution_item (
        pos INTEGER NOT NULL, section TEXT NOT NULL, name_key TEXT NOT NULL,
        PRIMARY KEY (pos, section, name_key))""")


# Report-level lines in frame-lines.json, each printed once (Glen, 2026-10-18).
FRAME_KEYS = ("vertigo_safety", "dose_totals", "thyroid_spacing", "infoceutical_header",
              "ingredient_caution_intro", "essential_oils_header")


def load_content(cx, folder):
    """Replace the stored map and lines with the approved entries in clinical's
    merge-map.json and score-lines.json. Unapproved entries are counted, not kept."""
    ensure_schema(cx)
    report = {"merges_applied": 0, "merges_waiting": 0, "names_merged": 0,
              "score_lines_applied": 0, "score_lines_waiting": 0, "intro": "waiting"}
    with open(os.path.join(folder, "merge-map.json"), encoding="utf-8") as f:
        merges = json.load(f).get("merges") or []
    with open(os.path.join(folder, "score-lines.json"), encoding="utf-8") as f:
        lines = json.load(f)
    frame, cautions, frame_report = _parse_frame(os.path.join(folder, "frame-lines.json"))
    cx.execute("DELETE FROM biofield_plan_merge")
    cx.execute("DELETE FROM biofield_plan_wording")
    cx.execute("DELETE FROM biofield_plan_caution")
    cx.execute("DELETE FROM biofield_plan_caution_item")
    for group in merges:
        if group.get("approved") is not True:
            report["merges_waiting"] += 1
            continue
        report["merges_applied"] += 1
        for member in group.get("members") or []:
            cx.execute("INSERT OR REPLACE INTO biofield_plan_merge VALUES (?,?,?)",
                       (group["group"], _norm(member), group["display_name"]))
            report["names_merged"] += 1
    for digit, entry in (lines.get("lines") or {}).items():
        if entry.get("approved") is True and entry.get("text"):
            cx.execute("INSERT INTO biofield_plan_wording VALUES ('score',?,?)",
                       ("X" if digit == "10" else digit, entry["text"]))
            report["score_lines_applied"] += 1
        else:
            report["score_lines_waiting"] += 1
    report.update(frame_report)
    report["caution_items_not_in_catalog"] = _write_frame(cx, frame, cautions)
    intro = lines.get("intro") or {}
    if intro.get("approved") is True:
        for n, text in enumerate(intro.get("lines") or []):
            cx.execute("INSERT INTO biofield_plan_wording VALUES ('intro',?,?)", (str(n), text))
        report["intro"] = "applied"
    return report


def _parse_frame(path):
    """(frame rows, caution rows, report) from clinical's frame-lines.json, approved
    only. Everything is read and checked before anything is written, so a bad file
    fails the load with the stored set untouched. A caution prints only for the items
    clinical lists under it, so one with no items never prints and is counted."""
    report = {"frame_lines_applied": 0, "frame_lines_waiting": 0, "frame_lines_unknown": [],
              "cautions_applied": 0, "cautions_waiting": 0, "cautions_without_items": [],
              "caution_items": 0}
    frame, cautions = [], []
    if not os.path.exists(path):
        return frame, cautions, report
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, entry in (data.get("lines") or {}).items():
        if key not in FRAME_KEYS:
            report["frame_lines_unknown"].append(key)
        elif entry.get("approved") is True and entry.get("text"):
            frame.append((key, str(entry["text"])))
            report["frame_lines_applied"] += 1
        else:
            report["frame_lines_waiting"] += 1
    for name, entry in (data.get("ingredient_cautions") or {}).items():
        if entry.get("approved") is not True or not entry.get("text"):
            report["cautions_waiting"] += 1
            continue
        items = []
        for item in entry.get("items") or []:
            section, key = str(item.get("section") or ""), _norm(item.get("name") or "")
            if section and key and (section, key) not in [i[:2] for i in items]:
                items.append((section, key, item.get("name")))
        if not items:
            report["cautions_without_items"].append(name)
        cautions.append((name, str(entry["text"]), items))
        report["cautions_applied"] += 1
        report["caution_items"] += len(items)
    return frame, cautions, report


def _write_frame(cx, frame, cautions):
    """Store the parsed rows; returns the listed items no condition file holds."""
    cs.ensure_schema(cx)
    known = {(_class(sec), key) for sec, key in cx.execute(
        "SELECT DISTINCT section,name_key FROM biofield_clinical_section_item")}
    known |= {(_class(sec), key) for sec, key in cx.execute(
        "SELECT section,name_key FROM biofield_clinical_extra_explanation")}
    try:
        known |= {("prl", key) for (key,) in cx.execute(
            "SELECT DISTINCT name_key FROM biofield_prl_rating")}
    except Exception:  # PRL tables absent
        pass
    unknown = []
    for key, text in frame:
        cx.execute("INSERT INTO biofield_plan_wording VALUES ('frame',?,?)", (key, text))
    for pos, (name, text, items) in enumerate(cautions):
        cx.execute("INSERT INTO biofield_plan_caution VALUES (?,?,?)", (pos, name, text))
        for section, key, raw in items:
            cx.execute("INSERT INTO biofield_plan_caution_item VALUES (?,?,?)",
                       (pos, section, key))
            if (_class(section), key) not in known:
                unknown.append(f"{name}: {section}: {raw}")
    return unknown


def stored_frame(cx):
    """({frame key: text}, [(caution name, text, {(section, name key)})]), approved only."""
    ensure_schema(cx)
    frame = dict(cx.execute("SELECT key,text FROM biofield_plan_wording WHERE kind='frame'"))
    # One statement, so a caution and its items always come from the same load.
    cautions = {}
    for pos, name, text, section, key in cx.execute(
            "SELECT c.pos,c.name,c.text,i.section,i.name_key FROM biofield_plan_caution c "
            "LEFT JOIN biofield_plan_caution_item i ON i.pos=c.pos ORDER BY c.pos"):
        items = cautions.setdefault(pos, (name, text, set()))[2]
        if section is not None:
            items.add((_class(section), key))
    return frame, list(cautions.values())


def cautions_for(plan_groups, cautions):
    """[(caution name, text, [plan line names])] for each caution with a ticked item,
    in clinical's order. A line is named by what prints, once per caution."""
    out = []
    for name, text, items in cautions:
        hits = []
        for lines in plan_groups.values():
            for line in lines:
                if any((_class(en["section"]), _norm(en["name"])) in items
                       for en in line["entries"]) \
                        and line["name"] not in hits:
                    hits.append(line["name"])
        if hits:
            out.append((name, text, hits))
    return out


def _has_condition(plan_groups, key):
    """By the condition's key, so a row Glen renames still counts."""
    return any(en.get("condition_key") == key for lines in plan_groups.values()
               for line in lines for en in line["entries"])


def _class(section):
    """A remedy ticked through a row's selection can land under Formulations or
    Infoceuticals by its name, so a caution matches either."""
    return cs.FORMULATIONS if section == cs.INFOCEUTICALS else section


def stored_wording(cx):
    """(merge map, score meanings, intro lines) as stored, approved only."""
    ensure_schema(cx)
    merge_map = {(r[0], r[1]): r[2] for r in cx.execute(
        "SELECT scope,name_key,display_name FROM biofield_plan_merge")}
    meanings = {r[0]: r[1] for r in cx.execute(
        "SELECT key,text FROM biofield_plan_wording WHERE kind='score'")}
    intro = [r[0] for r in cx.execute(
        "SELECT text FROM biofield_plan_wording WHERE kind='intro' ORDER BY CAST(key AS INT)")]
    return merge_map, meanings, intro


def plan(cx, test_id, merge_map=None):
    """{group key: [line]}. Identical names ticked under several conditions are one
    line. Differently named items share a line only through `merge_map` ({(scope,
    name key): shared name}, the stored approved map by default), which clinical writes and Glen approves (Glen, 2026-10-18). A line
    carries its highest overall score and, per condition, that condition's score, dose
    and approved explanation."""
    if merge_map is None:
        merge_map = stored_wording(cx)[0]
    groups = {}
    for section, label, name, dose, overall, explanation, approved, item_key \
            in gather(cx, test_id):
        own_section = section
        shared = merge_map.get((_SCOPE.get(section, ""), _norm(name)))
        # A merged nutrient and botanical share the Nutrients line.
        if shared and _SCOPE.get(section) == "nutrients_botanicals":
            section = cs.NUTRIENTS
        bucket = groups.setdefault(section, {})
        # A merged group never shares a line with an item that merely has its name.
        line = bucket.setdefault(("merged", _norm(shared)) if shared else ("name", _norm(name)), {
            "name": shared or name, "kind": KIND.get(section, ""), "overall": None,
            "entries": []})
        if overall is not None and (line["overall"] is None or overall > line["overall"]):
            line["overall"] = overall
        line["entries"].append({"condition": label, "name": name, "overall": overall,
                                "section": own_section, "condition_key": item_key,
                                "dose": dose or "",
                                "explanation": explanation if approved else ""})
    for bucket in groups.values():
        for line in bucket.values():
            line["entries"].sort(key=lambda en: (-(en["overall"] or 0), en["condition"]))
    return {key: sorted(bucket.values(), key=lambda i: (-(i["overall"] or 0), i["name"].lower()))
            for key, bucket in groups.items()}


def _digit(overall):
    if overall is None:
        return ""
    return "X" if int(overall) >= 10 else str(int(overall))


def missing(plan_groups):
    """(section, item, condition) ticked with no approved explanation, for Glen only."""
    return [(key, line["name"], en["condition"]) for key, lines in plan_groups.items()
            for line in lines for en in line["entries"] if not en["explanation"]]


def _line_html(line, show_kind):
    e = html.escape
    # One dose heads the line only when every condition gives that same dose.
    doses = {en["dose"] for en in line["entries"]}
    one_dose = doses.pop() if len(doses) == 1 else ""
    head = (f"<span class=plan-name>{e(line['name'])}</span>"
            + (f" <span class=plan-kind>{e(line['kind'])}</span>" if show_kind and line["kind"] else "")
            + (f" <span class=plan-program>{e(line['program'])}</span>" if line.get("program") else "")
            + (f" <span class=plan-dose>{e(one_dose)}</span>" if one_dose else "")
            + (f" <span class=plan-note>{e(line['note'])}</span>" if line.get("note") else "")
            + (f"<p class=plan-source>{e(line['source'])}</p>" if line.get("source") else ""))
    subs = []
    for en in line["entries"]:
        bits = [e(en["condition"])]
        if en["overall"] is not None:
            bits.append(f"score {_digit(en['overall'])}")
        if en["name"] != line["name"]:
            bits.append(e(en["name"]))
        if en["dose"] and not one_dose:
            bits.append(e(en["dose"]))
        subs.append(f"<li><span class=plan-for>{' · '.join(bits)}</span>"
                    + (f"<p class=plan-why>{e(en['explanation'])}</p>" if en["explanation"] else "")
                    + "</li>")
    return f"<li>{head}" + (f"<ul class=plan-conds>{''.join(subs)}</ul>" if subs else "") + "</li>"


def _by_score(lines, show_kind, meanings):
    """Lines grouped under "Score N" headings, highest first (Glen, 2026-10-18). A
    heading's one-line meaning prints only once Glen has approved it."""
    out, current = [], None
    for line in lines:
        digit = _digit(line["overall"]) or "unrated"
        if digit != current:
            if current is not None:
                out.append("</ul>")
            title = f"Score {digit}" if digit != "unrated" else "Not yet scored"
            meaning = (meanings or {}).get(digit, "")
            out.append(f"<h4 class=plan-score>{e_(title)}</h4>"
                       + (f"<p class=plan-score-means>{e_(meaning)}</p>" if meaning else "")
                       + "<ul class=plan-list>")
            current = digit
        out.append(_line_html(line, show_kind))
    if current is not None:
        out.append("</ul>")
    return "".join(out)


def e_(text):
    return html.escape(str(text))


PLAN_TITLE = "Foods, Remedies and Healing Tools Plan"
# The report-level lines printed once at the top, in this order.
TOP_KEYS = ("vertigo_safety", "dose_totals", "thyroid_spacing")


def render_plan_section(plan_groups, client_name="", date="", score_meanings=None,
                        intro=(), cautions=(), frame=None, headings=None, title=PLAN_TITLE,
                        extra="", top_keys=None, intro_links=None):
    """The plan as one block with its own scoped styles, so it can follow the
    Biofield Analysis in the same PDF or stand alone. Nothing unapproved is printed.
    Under Remedies, sections mix within each score, so each line names its kind.
    `cautions` is [(name, text, [line names])] from cautions_for; `frame` holds the
    approved report-level lines by key.

    `headings` replaces HEADINGS for one report (Glen, 2026-10-19: the Plan splits
    into stand-alone reports). Each is (heading, groups) or (heading, groups, mixed,
    show heading); by default only "Remedies" mixes. `title` heads the block, and
    none prints when it is empty. `extra` is html placed after the headings.
    `top_keys` limits the report-level lines to those a stand-alone report concerns;
    None prints all three, as the single Plan always has."""
    e = html.escape
    frame = frame or {}
    parts = []
    for entry in (HEADINGS if headings is None else headings):
        heading, groups = entry[0], entry[1]
        mixed = entry[2] if len(entry) > 2 else heading == "Remedies"
        show_heading = entry[3] if len(entry) > 3 else True
        keys = {key for key, _ in groups}
        blocks = []
        if mixed:
            lines = sorted((line for key, _ in groups for line in plan_groups.get(key) or []),
                           key=lambda i: (-(i["overall"] or 0), i["name"].lower()))
            if lines:
                blocks.append(_by_score(lines, True, score_meanings))
        else:
            # Foods to emphasize and Foods to avoid stay apart (Glen, 2026-10-18).
            for key, group_title in groups:
                lines = plan_groups.get(key) or []
                if lines:
                    sub = f"<h3>{e(group_title)}</h3>" if len(groups) > 1 else ""
                    blocks.append(sub + _by_score(lines, False, score_meanings))
        # The infoceutical header heads Remedies, where infoceuticals mix by score.
        if mixed and cs.INFOCEUTICALS in keys and plan_groups.get(cs.INFOCEUTICALS) \
                and frame.get("infoceutical_header"):
            blocks.insert(0, f"<p class=plan-frame>{e(frame['infoceutical_header'])}</p>")
        # The oils' safety line heads Remedies too, once, only with an oil ticked.
        if mixed and cs.ESSENTIAL_OILS in keys and plan_groups.get(cs.ESSENTIAL_OILS) \
                and frame.get("essential_oils_header"):
            at = 1 if blocks and blocks[0].startswith("<p class=plan-frame>") else 0
            blocks.insert(at, f"<p class=plan-frame>{e(frame['essential_oils_header'])}</p>")
        if blocks:
            parts.append("<section class=plan-heading>"
                         + (f"<h2>{e(heading)}</h2>" if show_heading else "")
                         + f"{''.join(blocks)}</section>")
    who = f"<p class=plan-who>{e(client_name)}{' · ' + e(date) if date else ''}</p>" \
        if client_name and title else ""
    # Report-level lines print once at the top; the vertigo line only with a Vertigo tick.
    allowed = TOP_KEYS if top_keys is None else top_keys
    top = [frame[k] for k in TOP_KEYS
           if k in allowed and frame.get(k)
           and (k != "vertigo_safety" or _has_condition(plan_groups, "vertigo"))]
    # Each caution once, naming the client's items that hold its ingredient.
    caution = ""
    if cautions:
        caution = ("<div class=plan-cautions>"
                   + (f"<p class=plan-caution-intro>{e(frame['ingredient_caution_intro'])}</p>"
                      if frame.get("ingredient_caution_intro") else "")
                   + "".join(f"<div class=plan-caution><p><span class=plan-caution-name>"
                             f"{e(name)}</span> {e(text)}</p><p class=plan-caution-items>"
                             f"{' · '.join(e(n) for n in hits)}</p></div>"
                             for name, text, hits in cautions) + "</div>")
    return (f"<style>{PLAN_CSS}</style><main class=plan>"
            + (f"<h1>{e(title)}</h1>" if title else "") + who
            + "".join(f"<p class=plan-intro>{_linked(line, intro_links)}</p>"
                      for line in intro or ())
            + "".join(f"<p class=plan-frame>{e(line)}</p>" for line in top)
            + caution + "".join(parts) + extra + "</main>")


def _linked(line, links):
    """One intro line, escaped, with each approved phrase in `links` ({phrase: url})
    made a link. Only an http(s) address is linked."""
    out = e_(line)
    for phrase, url in (links or {}).items():
        if phrase and str(url).startswith(("https://", "http://")):
            out = out.replace(e_(phrase), f'<a href="{e_(url)}">{e_(phrase)}</a>')
    return out


def plan_page(body):
    """A page of its own around a plan section."""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Foods, Remedies and Healing Tools Plan</title>"
            "<style>body{margin:0;background:#fff}</style></head><body>"
            + body + "</body></html>")


def render_plan_html(plan_groups, client_name="", date="", score_meanings=None, intro=(),
                     cautions=(), frame=None):
    """The plan as a page of its own."""
    return plan_page(render_plan_section(plan_groups, client_name, date, score_meanings,
                                         intro, cautions, frame))


def append_to_report(report_html, plan_section):
    """The Biofield Analysis with the plan after it, on a new page of the same PDF."""
    block = f"<div class=plan-appended style='break-before:page'>{plan_section}</div>"
    at = report_html.rfind("</body>")
    return report_html[:at] + block + report_html[at:] if at >= 0 else report_html + block


def kloud_lines(plan_groups):
    """The plan's Kloud mat lines."""
    from dashboard.kloud_program import is_kloud
    return [line for lines in plan_groups.values() for line in lines if is_kloud(line["name"])]


def build(cx, test_id, client_name="", date="", kloud_program=""):
    """(plan section html, entries lacking an approved explanation) from what is
    stored: the ticks, the approved merge map, score lines, intro and cautions. A
    Kloud line names `kloud_program`, which the scan's Phase sets (Glen, 2026-10-18)."""
    merge_map, meanings, intro = stored_wording(cx)
    frame, cautions = stored_frame(cx)
    groups = plan(cx, test_id, merge_map)
    for line in kloud_lines(groups):
        line["program"] = kloud_program
    return (render_plan_section(groups, client_name, date, meanings, intro,
                                cautions_for(groups, cautions), frame),
            missing(groups))


PLAN_CSS = (
    ".plan{max-width:760px;margin:0 auto;padding:28px 18px;background:#fff;color:#1d2a33;"
    "font:15px/1.5 Georgia,serif}"
    ".plan-cautions{border:1px solid #d9c7a3;background:#fbf6ec;border-radius:6px;"
    "padding:8px 12px;margin:12px 0}.plan-cautions p{margin:4px 0}"
    ".plan-caution{margin:8px 0}.plan-caution-name{font-weight:600}"
    ".plan-caution-items{color:#555;font-size:.92em}.plan-frame{margin:8px 0}"
    ".plan-program{font-weight:600}"
    ".plan-note{font:12px system-ui,sans-serif;color:#2d6a4f;background:#e8f3ec;"
    "border-radius:4px;padding:1px 6px;margin-left:4px}"
    ".plan-source{margin:2px 0 0;color:#56656f;font-size:13px}"
    ".plan-amazon{margin:18px 0 0;color:#56656f;font-size:13px}"
    ".plan h1{font-size:26px;margin:0 0 4px}.plan-who{color:#56656f;margin:0 0 18px}"
    ".plan h2{font-size:20px;border-bottom:1px solid #c9d3d9;padding-bottom:4px;margin:26px 0 8px}"
    ".plan h3{font-size:15px;margin:14px 0 4px;color:#2f5d73}"
    ".plan-list{margin:0;padding-left:20px}.plan-list li{margin:2px 0}"
    ".plan-dose{color:#3d4b55}.plan-for{color:#56656f;font-size:13px}"
    ".plan-kind{font:11px system-ui,sans-serif;color:#56656f;border:1px solid #c9d3d9;"
    "border-radius:3px;padding:0 4px}"
    ".plan-score{font:600 14px system-ui,sans-serif;color:#2f5d73;margin:14px 0 2px}"
    ".plan-score-means{margin:0 0 4px;color:#56656f;font-size:13px}"
    ".plan-conds{list-style:none;padding-left:12px;margin:2px 0 0}"
    ".plan-list li{margin:0 0 8px}.plan-why{margin:2px 0 0;color:#2b3a44}"
    ".plan-glen{border:1px dashed #b9a6d8;padding:8px 12px;margin:0 0 14px;font:13px system-ui,"
    "sans-serif}"
    "@media print{.plan{padding:0}.plan h2,.plan h3,.plan h4{break-after:avoid}"
    ".plan-glen{display:none}}"
)
