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


def gather(cx, test_id):
    """[(section, condition label, name, dose, overall, explanation, approved)] for
    every ticked item, before merging repeats."""
    from dashboard.biofield_clinical_proposals import selections
    cs.ensure_schema(cx)
    test_id = str(test_id)
    labels = _labels(cx, test_id)
    out, cache = [], {}

    def rows(item_key):
        if item_key not in cache:
            cache[item_key] = _item_rows(cx, item_key)
        return cache[item_key]

    # Functional Formulations and Infoceuticals: the row's ticked remedies.
    for item_key, names in selections(cx, test_id).items():
        for name in names:
            section = _remedy_section(name)
            row = rows(item_key).get((section, _norm(name))) \
                or rows(item_key).get((cs.FORMULATIONS, _norm(name))) or {}
            out.append((section, labels.get(item_key, item_key.capitalize()), name,
                        row.get("dose") or "", row.get("overall"),
                        row.get("explanation") or "", bool(row.get("approved"))))
    # Every other section: the ticks kept per item.
    for item_key, section, name_key, dose, overall in cx.execute(
            "SELECT item_key,section,name_key,dose,overall FROM "
            "biofield_clinical_section_pick WHERE test_id=? AND checked=1", (test_id,)):
        row = rows(item_key).get((section, name_key))
        if not row:
            continue   # the item left the catalog; nothing to name
        out.append((section, labels.get(item_key, item_key.capitalize()), row["name"],
                    dose or row["dose"], overall if overall is not None else row["overall"],
                    row["explanation"], row["approved"]))
    # PRL products ticked in a focus area.
    try:
        prl = cx.execute(
            "SELECT r.name,COALESCE(p.overall,r.overall),COALESCE(m.display_name,'') "
            "FROM biofield_prl_pick p JOIN biofield_prl_rating r ON "
            "r.focus_area_id=p.focus_area_id AND r.name_key=p.name_key "
            "LEFT JOIN biofield_prl_condition_map m ON m.focus_area_id=p.focus_area_id "
            "WHERE p.test_id=? AND p.checked=1", (test_id,)).fetchall()
    except Exception:  # PRL tables absent
        prl = []
    for name, overall, area in prl:
        out.append(("prl", area or "PRL", name, "", overall, "", False))
    return out


# What each section is called beside an item under Remedies, where sections mix.
KIND = {cs.FORMULATIONS: "Functional Formulation", cs.INFOCEUTICALS: "Infoceutical",
        "prl": "PRL", cs.NUTRIENTS: "Nutrient", cs.BOTANICALS: "Botanical",
        cs.FULLSCRIPT: "Fullscript supplement"}


def plan(cx, test_id, merge_map=None):
    """{group key: [line]}. Identical names ticked under several conditions are one
    line. Differently named items share a line only through `merge_map` ({name key:
    shared name}), which clinical writes and Glen approves (Glen, 2026-10-18). A line
    carries its highest overall score and, per condition, that condition's score, dose
    and approved explanation."""
    merge_map = merge_map or {}
    groups = {}
    for section, label, name, dose, overall, explanation, approved in gather(cx, test_id):
        shared = merge_map.get(_norm(name))
        bucket = groups.setdefault(section, {})
        line = bucket.setdefault(_norm(shared or name), {
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
    doses = {en["dose"] for en in line["entries"] if en["dose"]}
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


def render_plan_html(plan_groups, client_name="", date="", score_meanings=None):
    """The plan as a printable page. Nothing unapproved is printed. Under Remedies,
    sections mix within each score, so each line names its kind."""
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
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{PLAN_CSS}</style></head><body>"
            f"<main class=plan><h1>{title}</h1>{who}{''.join(parts)}</main></body></html>")


PLAN_CSS = (
    "body{margin:0;background:#fff;color:#1d2a33;font:15px/1.5 Georgia,serif}"
    ".plan{max-width:760px;margin:0 auto;padding:28px 18px}"
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
    "@media print{.plan{padding:0}.plan-heading{break-inside:auto}h2,h3{break-after:avoid}}"
)
