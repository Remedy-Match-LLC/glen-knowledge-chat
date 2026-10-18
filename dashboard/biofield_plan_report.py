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
    """[(section, condition label, name, dose, overall, explanation, approved)] for
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
                        explanation, approved))
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
                    row["explanation"], row["approved"]))
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
        out.append(("prl", area or "PRL", name, "", overall, "", False))
    return out


def lost_ticks(cx, test_id):
    """(section, condition, name, why) for each tick the Plan cannot print."""
    lost = []
    gather(cx, test_id, lost)
    return lost


# What each section is called beside an item under Remedies, where sections mix.
KIND = {cs.FORMULATIONS: "Functional Formulation", cs.INFOCEUTICALS: "Infoceutical",
        "prl": "PRL", cs.NUTRIENTS: "Nutrient", cs.BOTANICALS: "Botanical",
        cs.FULLSCRIPT: "Fullscript supplement"}


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


def load_content(cx, folder):
    """Replace the stored map and lines with the approved entries in clinical's
    merge-map.json and score-lines.json. Unapproved entries are counted, not kept."""
    import os
    ensure_schema(cx)
    report = {"merges_applied": 0, "merges_waiting": 0, "names_merged": 0,
              "score_lines_applied": 0, "score_lines_waiting": 0, "intro": "waiting"}
    with open(os.path.join(folder, "merge-map.json"), encoding="utf-8") as f:
        merges = json.load(f).get("merges") or []
    with open(os.path.join(folder, "score-lines.json"), encoding="utf-8") as f:
        lines = json.load(f)
    cx.execute("DELETE FROM biofield_plan_merge")
    cx.execute("DELETE FROM biofield_plan_wording")
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
    # Caution lines, if clinical has written the file: approved ones only.
    report["cautions_applied"] = report["cautions_waiting"] = 0
    path = os.path.join(folder, "cautions.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for n, entry in enumerate(json.load(f).get("lines") or []):
                if entry.get("approved") is True and entry.get("text"):
                    cx.execute("INSERT INTO biofield_plan_wording VALUES ('caution',?,?)",
                               (f"{n:03d}", entry["text"]))
                    report["cautions_applied"] += 1
                else:
                    report["cautions_waiting"] += 1
    intro = lines.get("intro") or {}
    if intro.get("approved") is True:
        for n, text in enumerate(intro.get("lines") or []):
            cx.execute("INSERT INTO biofield_plan_wording VALUES ('intro',?,?)", (str(n), text))
        report["intro"] = "applied"
    return report


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
    for section, label, name, dose, overall, explanation, approved in gather(cx, test_id):
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
            + (f" <span class=plan-dose>{e(one_dose)}</span>" if one_dose else ""))
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
    return f"<li>{head}<ul class=plan-conds>{''.join(subs)}</ul></li>"


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


def render_plan_section(plan_groups, client_name="", date="", score_meanings=None,
                        intro=(), cautions=()):
    """The plan as one block with its own scoped styles, so it can follow the
    Biofield Analysis in the same PDF or stand alone. Nothing unapproved is printed.
    Under Remedies, sections mix within each score, so each line names its kind."""
    e = html.escape
    parts = []
    for heading, groups in HEADINGS:
        blocks = []
        if heading == "Remedies":
            lines = sorted((line for key, _ in groups for line in plan_groups.get(key) or []),
                           key=lambda i: (-(i["overall"] or 0), i["name"].lower()))
            if lines:
                blocks.append(_by_score(lines, True, score_meanings))
        else:
            # Foods to emphasize and Foods to avoid stay apart (Glen, 2026-10-18).
            for key, title in groups:
                lines = plan_groups.get(key) or []
                if lines:
                    sub = f"<h3>{e(title)}</h3>" if len(groups) > 1 else ""
                    blocks.append(sub + _by_score(lines, False, score_meanings))
        if blocks:
            parts.append(f"<section class=plan-heading><h2>{e(heading)}</h2>"
                         f"{''.join(blocks)}</section>")
    title = "Foods, Remedies and Healing Tools Plan"
    who = f"<p class=plan-who>{e(client_name)}{' · ' + e(date) if date else ''}</p>" \
        if client_name else ""
    # Caution lines are client wording: only the ones Glen approved are stored.
    caution = ("<div class=plan-cautions>"
               + "".join(f"<p>{e(line)}</p>" for line in cautions) + "</div>"
               if cautions else "")
    return (f"<style>{PLAN_CSS}</style><main class=plan><h1>{title}</h1>{who}"
            + "".join(f"<p class=plan-intro>{e(line)}</p>" for line in intro or ())
            + caution + "".join(parts) + "</main>")


def plan_page(body):
    """A page of its own around a plan section."""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Foods, Remedies and Healing Tools Plan</title>"
            "<style>body{margin:0;background:#fff}</style></head><body>"
            + body + "</body></html>")


def render_plan_html(plan_groups, client_name="", date="", score_meanings=None, intro=(),
                     cautions=()):
    """The plan as a page of its own."""
    return plan_page(render_plan_section(plan_groups, client_name, date, score_meanings,
                                         intro, cautions))


def append_to_report(report_html, plan_section):
    """The Biofield Analysis with the plan after it, on a new page of the same PDF."""
    block = f"<div class=plan-appended style='break-before:page'>{plan_section}</div>"
    at = report_html.rfind("</body>")
    return report_html[:at] + block + report_html[at:] if at >= 0 else report_html + block


def build(cx, test_id, client_name="", date=""):
    """(plan section html, entries lacking an approved explanation) from what is
    stored: the ticks, the approved merge map, score lines, intro and cautions."""
    merge_map, meanings, intro = stored_wording(cx)
    cautions = [r[0] for r in cx.execute(
        "SELECT text FROM biofield_plan_wording WHERE kind='caution' ORDER BY key")]
    groups = plan(cx, test_id, merge_map)
    return (render_plan_section(groups, client_name, date, meanings, intro, cautions),
            missing(groups))


PLAN_CSS = (
    ".plan{max-width:760px;margin:0 auto;padding:28px 18px;background:#fff;color:#1d2a33;"
    "font:15px/1.5 Georgia,serif}"
    ".plan-cautions{border:1px solid #d9c7a3;background:#fbf6ec;border-radius:6px;"
    "padding:8px 12px;margin:12px 0}.plan-cautions p{margin:4px 0}"
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
