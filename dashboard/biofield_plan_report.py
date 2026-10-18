"""The Foods, Remedies and Healing Tools Plan: what Glen ticked in a client's
Clinical Summary, as lists and as paragraphs.

Glen, 2026-10-18: "It will provide the checked information in the Clinical Summary
in both list and paragraph form with explanations in the paragraphs. It is it's own
report, but when printing a Biofield Analysis report, it can be selected to print at
the same time as an addition to the same pdf file."

Headings (Glen, same day): Foods; Remedies, which holds Infoceuticals and PRL too;
Healing Tools. An item ticked under several conditions is listed once, with every
condition named (clinical's frame note 1). Only the overall rating digit shows.
Each item's paragraph is the explanation stored with it, printed only once Glen has
approved it; an item without one stays in the list and gets no paragraph.
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


def plan(cx, test_id):
    """{group key: [item]} with repeats merged. An item carries its name, dose, the
    overall digit, every condition it was ticked under, and its paragraph if
    approved. `doses` lists every dose seen, so Glen can see where they differ."""
    groups = {}
    for section, label, name, dose, overall, explanation, approved in gather(cx, test_id):
        bucket = groups.setdefault(section, {})
        item = bucket.setdefault(_norm(name), {
            "name": name, "dose": "", "doses": [], "overall": None, "conditions": [],
            "explanation": "", "approved": False})
        if label not in item["conditions"]:
            item["conditions"].append(label)
        if dose and dose not in item["doses"]:
            item["doses"].append(dose)
        item["dose"] = item["dose"] or dose or ""
        if overall is not None and (item["overall"] is None or overall > item["overall"]):
            item["overall"] = overall
        if approved and explanation and not item["approved"]:
            item["explanation"], item["approved"] = explanation, True
        elif explanation and not item["explanation"]:
            item["explanation"] = explanation
    return {key: sorted(bucket.values(),
                        key=lambda i: (-(i["overall"] or 0), i["name"].lower()))
            for key, bucket in groups.items()}


def _digit(overall):
    if overall is None:
        return ""
    return "X" if int(overall) >= 10 else str(int(overall))


def missing(plan_groups):
    """The ticked items with no approved explanation, for Glen's eyes only."""
    return [(key, item["name"]) for key, items in plan_groups.items() for item in items
            if not item["approved"]]


def render_plan_html(plan_groups, client_name="", date=""):
    """The plan as a printable page. Nothing unapproved is printed."""
    e = html.escape
    parts = []
    for heading, groups in HEADINGS:
        lists, paras = [], []
        for key, title in groups:
            items = plan_groups.get(key) or []
            if not items:
                continue
            rows = "".join(
                f"<li><span class=plan-name>{e(i['name'])}</span>"
                + (f" <span class=plan-dose>{e(i['dose'])}</span>" if i["dose"] else "")
                + (f" <span class=plan-rating title='Overall rating'>{_digit(i['overall'])}</span>"
                   if i["overall"] is not None else "")
                + f" <span class=plan-for>for {e(', '.join(i['conditions']))}</span></li>"
                for i in items)
            lists.append(f"<h3>{e(title)}</h3><ul class=plan-list>{rows}</ul>")
            written = "".join(f"<p><b>{e(i['name'])}.</b> {e(i['explanation'])}</p>"
                              for i in items if i["approved"])
            if written:
                paras.append(f"<h3>{e(title)}</h3>{written}")
        if not lists:
            continue
        parts.append(f"<section class=plan-heading><h2>{e(heading)}</h2>"
                     f"<div class=plan-lists>{''.join(lists)}</div>"
                     + (f"<div class=plan-paras><h2 class=plan-sub>{e(heading)}: why each "
                        f"is here</h2>{''.join(paras)}</div>" if paras else "")
                     + "</section>")
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
    ".plan h2.plan-sub{font-size:17px;border:0;margin-top:18px}"
    ".plan h3{font-size:15px;margin:14px 0 4px;color:#2f5d73}"
    ".plan-list{margin:0;padding-left:20px}.plan-list li{margin:2px 0}"
    ".plan-dose{color:#3d4b55}.plan-for{color:#6b7a84;font-size:13px}"
    ".plan-rating{display:inline-block;min-width:16px;padding:0 4px;border:1px solid #9fb3bf;"
    "border-radius:3px;font:12px/16px system-ui,sans-serif;text-align:center;color:#2f5d73}"
    ".plan-paras p{margin:6px 0}"
    "@media print{.plan{padding:0}.plan-heading{break-inside:auto}h2,h3{break-after:avoid}}"
)
