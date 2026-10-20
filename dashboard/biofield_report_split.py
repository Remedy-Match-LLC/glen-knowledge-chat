"""The five reports and their section buttons (Glen, 2026-10-19).

Glen: "The remedies that balance the layers of the Biofield will be the ones that go
into the invoice and the primary narrative report." Then: "let's make the secondary
report functionally divided into multiple stand-alone reports for clarity: Diet;
Healing Tools; Ingredients; Other Remedy Sources". That last was renamed Remedy
Sources the same day.

Each report has one on/off button per section, and any section can be switched on in
any report. The settings are saved per client record (here, per test); a test with
nothing saved uses DEFAULTS. Glen: "keep the invoice matching the primary report", so
a section switched off in the Biofield Analysis comes off the invoice. The
stand-alone reports never add invoice lines. The one exception, Glen verbatim: "keep
them off the invoice unless Pure Powders is selected for the main report, since they
are supplied by us".

Pure Powders and Amazon have no data yet. Their keys are the section keys the
condition files will use, so a tick saved under either is picked up with no change
here. Until then their buttons say "no data yet".

Brief: 00 System/pillar-inboxes/platform/2026-10-19-report-split-and-section-buttons.md
"""
import html
import json
import os
import re

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from dashboard.biofield_clinical_checklist import _norm

PURE_POWDERS = "pure_powders"
AMAZON = "amazon"
PRL = "prl"

# Every section, in button order (the order of the approved sample).
SECTIONS = (
    (cs.FORMULATIONS, "Functional Formulations"),
    (cs.INFOCEUTICALS, "Infoceuticals"),
    (PURE_POWDERS, "Pure Powders"),
    ("foods_emphasize", "Foods to emphasize"),
    ("foods_avoid", "Foods to avoid"),
    (cs.HEALING_TOOLS, "Healing Tools"),
    (cs.NUTRIENTS, "Nutrients"),
    (cs.BOTANICALS, "Botanicals"),
    (cs.ESSENTIAL_OILS, "Essential Oils"),
    (PRL, "PRL"),
    (cs.FULLSCRIPT, "Fullscript"),
    (AMAZON, "Amazon"),
)
SECTION_KEYS = tuple(k for k, _ in SECTIONS)
SECTION_TITLES = dict(SECTIONS)
# Sections with no data in the condition files yet (brief, "Not in the data yet").
NO_DATA_YET = (PURE_POWDERS, AMAZON)

PRIMARY = "biofield"
REPORTS = (
    (PRIMARY, "Biofield Analysis"),
    ("diet", "Diet"),
    ("healing_tools", "Healing Tools"),
    ("ingredients", "Ingredients"),
    ("remedy_sources", "Remedy Sources"),
)
REPORT_KEYS = tuple(k for k, _ in REPORTS)
REPORT_TITLES = dict(REPORTS)
STAND_ALONE = REPORT_KEYS[1:]

DEFAULTS = {
    PRIMARY: frozenset({cs.FORMULATIONS, cs.INFOCEUTICALS}),
    "diet": frozenset({"foods_emphasize", "foods_avoid"}),
    "healing_tools": frozenset({cs.HEALING_TOOLS}),
    "ingredients": frozenset({cs.NUTRIENTS, cs.BOTANICALS, cs.ESSENTIAL_OILS}),
    "remedy_sources": frozenset({PRL, cs.FULLSCRIPT, PURE_POWDERS, AMAZON,
                                 cs.FORMULATIONS, cs.INFOCEUTICALS}),
}

# The sections the primary report takes from the Biofield layers. Everything else
# switched on in it is added after the layer narrative.
LAYER_SECTIONS = (cs.FORMULATIONS, cs.INFOCEUTICALS)

# The note beside an item that is also on a Biofield layer. Wording from the sample
# Glen approved on 2026-10-19.
IN_PROGRAM_NOTE = "Already in your recommended program."
# From 00 System/claude-memory/feedback_amazon_associate_disclosure.md.
# Glen approved this exact text, 2026-10-19 (clinical tab). It matches begin-buy,
# begin-learn and begin-match.
AMAZON_DISCLOSURE = ("Some recommendations include affiliate links. "
                     "As an Amazon Associate, Healing Oasis earns from qualifying purchases.")

_FOODS = ("foods_emphasize", "foods_avoid")
_REMEDY_KEYS = (cs.FORMULATIONS, cs.INFOCEUTICALS, PURE_POWDERS, PRL, cs.NUTRIENTS,
                cs.BOTANICALS, cs.ESSENTIAL_OILS, cs.FULLSCRIPT, AMAZON)


# ---- Saved settings ---------------------------------------------------------

def test_key(test_id, authored=None):
    """The one key a test's switches are saved under (review, 2026-10-19).

    An authored test is always "a<number>": "/author/48" and "/test/a48" are the same
    test, and "a048" is too. A FileMaker test is "fmp:<id>", so FileMaker test 48 can
    never read or write authored test a48's switches. `authored` is True on the
    /author routes, which always mean an authored test; None reads the prefix."""
    s = str(test_id).strip()
    digits = s[1:] if s.startswith("a") else s
    if authored or (authored is None and s.startswith("a") and digits.isdigit()):
        if not digits.isdigit():
            raise ValueError(f"not an authored test id: {test_id!r}")
        return f"a{int(digits)}"
    return f"fmp:{s}"


def is_authored(test_id):
    return test_key(test_id).startswith("a")


def ensure_schema(cx):
    """One row per switch Glen has pressed. A switch never pressed is its default."""
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_report_section (
        test_id TEXT NOT NULL, report TEXT NOT NULL, section TEXT NOT NULL,
        is_on INTEGER NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, report, section))""")


def settings(cx, test_id, authored=None):
    """{report key: frozenset of section keys switched on} for this test."""
    ensure_schema(cx)
    key = test_key(test_id, authored)
    out = {report: set(DEFAULTS[report]) for report in REPORT_KEYS}
    for report, section, is_on in cx.execute(
            "SELECT report,section,is_on FROM biofield_report_section WHERE test_id=?",
            (key,)):
        if report not in out or section not in SECTION_KEYS:
            continue
        (out[report].add if is_on else out[report].discard)(section)
    return {report: frozenset(on) for report, on in out.items()}


def set_section(cx, test_id, report, section, on, authored=None):
    """Save one switch. Raises ValueError for a report or section that does not exist."""
    key = test_key(test_id, authored)
    if report not in REPORT_KEYS:
        raise ValueError(f"unknown report {report!r}")
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown section {section!r}")
    ensure_schema(cx)
    cx.execute("INSERT INTO biofield_report_section (test_id,report,section,is_on) "
               "VALUES (?,?,?,?) ON CONFLICT(test_id,report,section) DO UPDATE SET "
               "is_on=excluded.is_on, updated_at=CURRENT_TIMESTAMP",
               (key, report, section, 1 if on else 0))
    cx.commit()


# ---- The invoice ------------------------------------------------------------

def layer_section(name):
    """Which primary-report section a Biofield layer remedy belongs to: Infoceuticals
    for an infoceutical, Functional Formulations for everything else we supply. The
    same split the Plan and the Clinical Summary use."""
    return pr._remedy_section(name)


def layer_remedy_on(name, primary_on):
    """True when a layer remedy belongs in the Biofield Analysis: its section is on.

    The one test the invoice, the printed report and the client's portal all use, so
    the three can never disagree (Glen, 2026-10-19: a section switched off "is
    presented as the complete report for that individual at that time")."""
    return layer_section(name or "") in primary_on


def all_layers_on(primary_on):
    """True when no layer section is switched off: nothing is filtered at all."""
    return all(k in primary_on for k in LAYER_SECTIONS)


def _head_group(row):
    """The layer a chain row belongs to, as group_layers groups it: rows sharing a
    non-empty head are one layer, and an empty-head row is a layer of its own."""
    head = (row.get("head") or "").strip().lower()
    return ("head", head) if head else ("row", id(row))


_BLANK_REMEDY = {"remedy": "", "dosage": "", "frequency": "", "timing": ""}


def primary_rows(rows, primary_on, remedy_of=lambda r: r.get("remedy"),
                 group_of=_head_group, blank=lambda r: dict(r, **_BLANK_REMEDY)):
    """Chain rows with every switched-off layer remedy taken out, in their order.

    A layer whose remedies are all switched off keeps its first row with the remedy
    blanked, so its number, head and findings still print with no remedy. A row that
    never had a remedy is kept as it is. With every layer section on, `rows` itself
    is returned, untouched."""
    rows = list(rows or [])
    if all_layers_on(primary_on):
        return rows

    def _on(r):
        name = (remedy_of(r) or "").strip()
        return not name or layer_remedy_on(name, primary_on)
    kept_groups = {group_of(r) for r in rows if _on(r)}
    out, placed = [], set()
    for r in rows:
        g = group_of(r)
        if _on(r):
            out.append(r)
        elif g not in kept_groups and g not in placed:
            out.append(blank(r))
        placed.add(g)
    return out


def primary_report(report, primary_on):
    """The Biofield Analysis report dict with switched-off layer remedies taken out
    of its layers and its Remedy Schedule. The narrative is Glen's prose and is not
    in here. With every layer section on, the same dict is returned, untouched."""
    if all_layers_on(primary_on):
        return report
    from dashboard.biofield_schedule import build_schedule
    report = dict(report)
    original = report.get("layers") or []
    layers = primary_rows(original, primary_on)
    report["layers"] = layers
    # The schedule is rebuilt from the rows still printed, as the report builders
    # build it. A FileMaker report schedules a row with no remedy too; an authored
    # one does not. A row blanked here is never scheduled.
    old = (report.get("schedule") or {}).get("entries") or []
    keep_unnamed = any(not (e.get("name") or "").strip() for e in old)
    own = {id(r) for r in original}
    report["schedule"] = build_schedule([
        {"name": l.get("remedy"), "dosage": l.get("dosage"),
         "frequency": l.get("frequency"), "timing": l.get("timing"),
         "schedule_slot": l.get("schedule_slot"),
         "source_rids": [l["rid"]] if l.get("rid") is not None else []}
        for l in layers
        if id(l) in own and ((l.get("remedy") or "").strip() or keep_unnamed)])
    return report


def invoice_remedies(remedies, primary_on, pure_powder_names=()):
    """The layer remedies the invoice may bill, as [{name, qty}], in order.

    A layer remedy stays only when its section is on in the primary report. Pure
    Powders ticked for this client are added, one bottle each, only when Pure Powders
    is on in the primary report. Nothing from a stand-alone report is ever added.
    With the default settings this returns `remedies` unchanged."""
    kept = [r for r in remedies or [] if layer_remedy_on(r.get("name"), primary_on)]
    if PURE_POWDERS in primary_on:
        have = {_norm(r.get("name") or "") for r in kept}
        for name in pure_powder_names or ():
            if name and _norm(name) not in have:
                have.add(_norm(name))
                kept.append({"name": name, "qty": 1})
    return kept


def left_off(remedies, primary_on):
    """Layer remedies the invoice leaves out because their section is off."""
    return [r.get("name") for r in remedies or []
            if not layer_remedy_on(r.get("name"), primary_on)]


def left_off_warning(left_off_names, fee_only=False):
    """What Glen reads beside a raised invoice when the Biofield Analysis's buttons
    took layer remedies off it (review, 2026-10-19). Plain text; empty when nothing
    was left off."""
    names = [n for n in left_off_names or () if n]
    if not names:
        return ""
    text = ("Left off this invoice because their section is switched off in the "
            "Biofield Analysis: " + ", ".join(names) + ".")
    if fee_only:
        text = ("This invoice holds the Biofield Analysis fee only. Every layer remedy "
                "is switched off in the Biofield Analysis. ") + text
    return text


def pure_powder_names(groups):
    return [line["name"] for line in (groups or {}).get(PURE_POWDERS) or []]


# ---- Remedy Sources -----------------------------------------------------------

_IN_TR = re.compile(r"\s+in terrain restore$")


def _program_key(name):
    """'Digestzymes in Terrain Restore' is Digestzymes taken in the program."""
    return _IN_TR.sub("", _norm(name))


def in_program(name, chain):
    """True when `name` is one of the remedies on the client's Biofield layers. An
    infoceutical matches by its code; anything else by its name, with an "in
    Terrain Restore" ending ignored on either side."""
    code = cs.infoceutical_code(name)
    if code and cs.is_infoceutical(name):
        return code in {cs.infoceutical_code(r) for r in chain or () if cs.is_infoceutical(r)}
    return _program_key(name) in {_program_key(r) for r in chain or ()}


def remedy_source_formulations(groups, chain):
    """Every Functional Formulation ticked in the Clinical Summary, each marked when
    it is also on a Biofield layer (Glen, 2026-10-19)."""
    out = []
    for line in (groups or {}).get(cs.FORMULATIONS) or []:
        line = dict(line)
        line["note"] = IN_PROGRAM_NOTE if in_program(line["name"], chain) else ""
        out.append(line)
    return out


def remedy_source_infoceuticals(groups, marks, chain, names=None, recommended_order=()):
    """Every infoceutical among the scan's 5 recommended, then every one purple on the
    scan, each once (Glen, 2026-10-19). A tick in the Clinical Summary is not needed;
    when there is one, its conditions and approved explanations print under the line.

    `marks` is e4l_scan_marks.infoceutical_marks. `recommended_order` lists the
    recommended codes by the scan's own rank; `names` is {code: remedy-list name}."""
    marks = marks or {}
    recommended = {c.upper() for c in marks.get("recommended") or ()}
    purple = {c.upper() for c in marks.get("purple") or ()
              if cs.infoceutical_code(c) == c.upper()}
    ordered = [c.upper() for c in recommended_order or () if c.upper() in recommended]
    ordered += sorted(recommended - set(ordered))
    ordered += sorted(purple - set(ordered))
    ticked = {cs.infoceutical_code(line["name"]): line
              for line in (groups or {}).get(cs.INFOCEUTICALS) or []
              if cs.infoceutical_code(line["name"])}
    out = []
    for code in ordered:
        if code not in ticked and not (names or {}).get(code):
            continue    # no readable name: left out, and named to Glen (unnamed_codes)
        line = dict(ticked.get(code) or {"name": names[code],
                                         "kind": "", "overall": None, "entries": []})
        why = [w for w, ok in (("one of the scan's 5 recommended", code in recommended),
                               ("purple on the scan", code in purple)) if ok]
        line["source"] = " and ".join(why).capitalize()
        line["note"] = IN_PROGRAM_NOTE if code in {
            cs.infoceutical_code(r) for r in chain or () if cs.is_infoceutical(r)} else ""
        out.append(line)
    return out


def unnamed_codes(groups, marks, names=None):
    """The scan's recommended and purple infoceutical codes with no readable name, so
    Remedy Sources leaves them out rather than print a bare code. Glen sees them."""
    marks = marks or {}
    codes = {c.upper() for c in marks.get("recommended") or ()}
    codes |= {c.upper() for c in marks.get("purple") or ()
              if cs.infoceutical_code(c) == c.upper()}
    ticked = {cs.infoceutical_code(line["name"])
              for line in (groups or {}).get(cs.INFOCEUTICALS) or []}
    return sorted(c for c in codes if c not in ticked and not (names or {}).get(c))


def _flat_list(title, lines, header="", frame=None):
    """`frame` is the stored frame, for the links in the header and explanations."""
    if not lines:
        return ""
    by_line, everywhere = pr._frame_links(frame)
    head = pr.sentence_html(header, by_line.get("infoceutical_header"), everywhere) \
        if header else ""
    return (f"<section class=plan-heading><h2>{html.escape(title)}</h2>"
            + (f"<p class=plan-frame>{head}</p>" if header else "")
            + "<ul class=plan-list>"
            + "".join(pr._line_html(line, False, everywhere) for line in lines)
            + "</ul></section>")


# ---- Rendering ----------------------------------------------------------------

def _join(titles):
    titles = list(titles)
    return titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]


def _headings(on, skip=(), title="", groups=None, name_remedies=False):
    """The report's headings from its switched-on sections, in print order: the
    foods, then the remedy sections mixed by score, then Healing Tools.

    A block heading the same as the report's title is not printed twice. With
    `name_remedies` (Remedy Sources) the mixed block is headed by the sections that
    hold lines, so PRL and Fullscript never sit unlabelled."""
    keys = [k for k in SECTION_KEYS if k in on and k not in skip]
    out = []
    foods = [(k, SECTION_TITLES[k]) for k in _FOODS if k in keys]
    remedies = [(k, _plan_title(k)) for k in _REMEDY_KEYS if k in keys]
    tools = [(cs.HEALING_TOOLS, SECTION_TITLES[cs.HEALING_TOOLS])] \
        if cs.HEALING_TOOLS in keys else []
    blocks = sum(bool(b) for b in (foods, remedies, tools))
    if foods:
        out.append(("Diet", tuple(foods), False, title != "Diet"))
    if remedies:
        if name_remedies:
            have = [t for k, t in remedies if (groups or {}).get(k)]
            name, show = _join(have or [t for _, t in remedies]), True
        else:
            # Alone in a report, the Remedies heading only repeats the report's title.
            name = "Remedies" if len(remedies) > 1 else remedies[0][1]
            show = blocks > 1 or name != "Remedies"
        out.append((name, tuple(remedies), True, show and name != title))
    if tools:
        out.append(("Healing Tools", tuple(tools), False, title != "Healing Tools"))
    return tuple(out)


def _plan_title(key):
    return dict(pr.HEADINGS[1][1]).get(key) or SECTION_TITLES[key]


def amazon_shown(groups, on):
    """The disclosure prints only when an Amazon section with lines is printed."""
    return AMAZON in on and bool((groups or {}).get(AMAZON))


def _amazon_line(groups, on):
    return (f"<p class=plan-amazon>{html.escape(AMAZON_DISCLOSURE)}</p>"
            if amazon_shown(groups, on) else "")


# The sections whose products carry nutrients, minerals or binders. The plan-wide
# dose totals and thyroid spacing lines concern these and nothing else, so they
# print only in a report that prints a line from one of them (review, 2026-10-19).
SUPPLEMENT_KEYS = (cs.FORMULATIONS, PURE_POWDERS, PRL, cs.NUTRIENTS, cs.BOTANICALS,
                   cs.FULLSCRIPT, AMAZON)


def printed_groups(ctx, report, on):
    """{section: lines} that this report actually prints. Every report-level line
    and caution is worked out from these alone, never from a switched-off section."""
    groups = ctx["groups"] or {}
    out = {k: v for k, v in groups.items() if k in on and v}
    if report == "remedy_sources" and cs.INFOCEUTICALS in on:
        out.pop(cs.INFOCEUTICALS, None)
        if ctx.get("remedy_source_infoceuticals"):
            out[cs.INFOCEUTICALS] = ctx["remedy_source_infoceuticals"]
    return out


def top_keys(printed):
    """The plan-wide lines this report may print, from what it prints."""
    keys = ["vertigo_safety"]          # render_plan_section adds it only with a Vertigo tick
    if any(printed.get(k) for k in SUPPLEMENT_KEYS):
        keys += ["dose_totals", "thyroid_spacing"]
    return tuple(keys)


_LEADS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "report_leads.json")


def approved_leads():
    """({report: [lead lines]}, {report: {phrase: url}}) from data/report_leads.json:
    the leads Glen approved on 2026-10-19 ("approved with edits"), printed as written."""
    try:
        with open(_LEADS_FILE, encoding="utf-8") as fh:
            doc = json.load(fh) or {}
        return dict(doc.get("leads") or {}), dict(doc.get("links") or {})
    except Exception:
        return {}, {}


def stored_leads(cx):
    """{report: [lead lines]}, the same for every client: the approved file, unless
    biofield_plan_wording holds rows of kind 'report_lead' (key '<report>:<n>') for
    that report."""
    pr.ensure_schema(cx)
    leads, _links = approved_leads()
    rows = {}
    for key, text in cx.execute(
            "SELECT key,text FROM biofield_plan_wording WHERE kind='report_lead'"):
        report, _, n = str(key).partition(":")
        rows.setdefault(report, []).append((int(n) if n.isdigit() else 0, text))
    # By paragraph number, so a tenth paragraph never sorts second as text would.
    leads.update({r: [t for _n, t in sorted(v, key=lambda x: x[0])]
                  for r, v in rows.items()})
    return leads


def primary_intro(cx, personal=()):
    """The Biofield Analysis's own introduction: its lead, then the personal passage
    (phase 2, empty for now). Glen, 2026-10-19: the same two parts as the others."""
    return report_intro({"report_leads": stored_leads(cx),
                         "report_personal": {PRIMARY: list(personal or ())}}, PRIMARY)


def report_intro(ctx, report):
    """This report's own introduction, for the book format Glen plans (2026-10-19).
    Two parts, in order: the standard lead, the same for every client, then a
    personal passage relating the guidance to this client's challenges and goals.
    Phase 1 fills only the lead (stored as biofield_plan_wording kind 'report_lead',
    keyed by report); the personal passage (ctx["report_personal"]) is phase 2 and
    prints nothing while empty. Both print before the shared intro, in every file."""
    def _lines(src):
        return tuple(str(t) for t in ((ctx.get(src) or {}).get(report) or ())
                     if str(t).strip())
    return _lines("report_leads") + _lines("report_personal")


def render_report(ctx, report, on, intro=True):
    """One stand-alone report as a plan block, sections limited to `on`. `intro`
    False leaves out the intro lines, for every report after the first in a Grouped
    file."""
    groups = ctx["groups"]
    printed = printed_groups(ctx, report, on)
    skip, extra = (), ""
    if report == "remedy_sources":
        # Here the two remedy sections are lists of their own (Glen, 2026-10-19).
        skip = LAYER_SECTIONS
        if cs.FORMULATIONS in on:
            extra += _flat_list("Functional Formulations",
                                remedy_source_formulations(groups, ctx.get("program_chain", ctx["chain"])),
                                frame=ctx["frame"])
        if cs.INFOCEUTICALS in on:
            extra += _flat_list("Infoceuticals", ctx["remedy_source_infoceuticals"],
                                (ctx["frame"] or {}).get("infoceutical_header") or "",
                                frame=ctx["frame"])
    extra += _amazon_line(groups, on)
    return pr.render_plan_section(
        printed, ctx["client_name"], ctx["date"], ctx["meanings"],
        report_intro(ctx, report) + tuple(ctx["intro"] if intro else ()),
        pr.cautions_for(printed, ctx["cautions"]), ctx["frame"],
        headings=_headings(on, skip, REPORT_TITLES[report], printed,
                           name_remedies=report == "remedy_sources"),
        title=REPORT_TITLES[report], extra=extra, top_keys=top_keys(printed),
        intro_links=approved_leads()[1].get(report))


def primary_added(ctx, on):
    """The sections switched on in the Biofield Analysis beyond its layer remedies,
    as a block to follow the layer narrative. Empty when there are none to print,
    so the default report is exactly what it was."""
    groups = ctx["groups"]
    extra_on = {k for k in on if k not in LAYER_SECTIONS}
    if not any(groups.get(k) for k in extra_on):
        return ""
    printed = printed_groups(ctx, PRIMARY, extra_on)
    return pr.render_plan_section(
        printed, "", "", ctx["meanings"], (), pr.cautions_for(printed, ctx["cautions"]),
        ctx["frame"], headings=_headings(extra_on), title="",
        extra=_amazon_line(groups, extra_on), top_keys=top_keys(printed))


def counts(ctx, report):
    """{section key: count, or None for "no data yet"} for the report's buttons."""
    groups = ctx["groups"]
    out = {}
    for key in SECTION_KEYS:
        if report == PRIMARY and key in LAYER_SECTIONS:
            n = sum(1 for r in ctx["chain"] if layer_section(r) == key)
        elif report == "remedy_sources" and key == cs.FORMULATIONS:
            n = len(groups.get(cs.FORMULATIONS) or [])
        elif report == "remedy_sources" and key == cs.INFOCEUTICALS:
            n = len(ctx["remedy_source_infoceuticals"])
        else:
            n = len(groups.get(key) or [])
        out[key] = None if (n == 0 and key in NO_DATA_YET) else n
    return out


def button_row(test_id, report, on, section_counts):
    e = html.escape
    out = []
    for key, title in SECTIONS:
        n = section_counts.get(key)
        state = "on" if key in on else "off"
        out.append(f"<button type=button class='rs-b {state}' data-tid='{e(str(test_id))}' "
                   f"data-report='{e(report)}' data-section='{e(key)}' "
                   f"aria-pressed={'true' if key in on else 'false'}>{e(title)} "
                   f"<small>{'no data yet' if n is None else n}</small></button>")
    return "<div class=rs-btns>" + "".join(out) + "</div>"


# ---- Printing -----------------------------------------------------------------

def _safe(name):
    """A client name fit for a file name: no path separators, quotes, control
    characters or leading dots, and at most 80 characters."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', " ", name or "")
    text = " ".join(text.split()).lstrip(". ")[:80].rstrip(". ")
    return text or "Client"


def print_jobs(selected, mode, client_name):
    """[(file name, [report keys])] for Save PDF and Print (Glen, 2026-10-19).
    Singly: one file per ticked report. Grouped: one file of every ticked report, in
    report order. The primary report prints on its own button, never here."""
    picked = [r for r in STAND_ALONE if r in set(selected or ())]
    if not picked:
        return []
    who = _safe(client_name)
    if mode == "grouped":
        return [(f"{who} - Reports.pdf", picked)]
    return [(f"{who} - {REPORT_TITLES[r]}.pdf", [r]) for r in picked]


def grouped_html(ctx, reports, settings_):
    """One file of the given reports. Each report starts a new printed page; the
    sections inside one flow on, and no heading is left alone at a page foot (Glen,
    2026-10-19: "each report")."""
    body = "".join(
        f"<div class=rs-report style='{'break-before:page' if i else ''}'>"
        f"{render_report(ctx, r, settings_[r], intro=not i)}</div>"
        for i, r in enumerate(reports))
    title = html.escape(", ".join(REPORT_TITLES[r] for r in reports))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>body{{margin:0;background:#fff}}"
            "@media print{h1,h2,h3,h4{break-after:avoid;page-break-after:avoid}"
            ".plan-heading,.plan-list li{break-inside:avoid-page}}</style>"
            "</head><body>" + body + "</body></html>")


def load(cx, test_id, client_name="", date="", kloud_program="", marks=None,
         recommended_order=(), names=None, chain=None):
    """Everything the reports print from, read once.

    `chain` is the remedies on this test's Biofield layers. Left None, an authored
    test reads its own chain and a FileMaker test has none: chain_remedies strips
    the "a", so FileMaker test 48 would read authored test a48, another client
    (review, 2026-10-19). The app passes a FileMaker test's own layers."""
    merge_map, meanings, intro = pr.stored_wording(cx)
    frame, cautions = pr.stored_frame(cx)
    groups = pr.plan(cx, test_id, merge_map)
    for line in pr.kloud_lines(groups):
        line["program"] = kloud_program
    if chain is None:
        chain = cs.chain_remedies(cx, test_id) if is_authored(test_id) else []
    # "Already in your recommended program" names only what the Biofield Analysis
    # keeps: a remedy in a switched-off section is not in the program (2026-10-19).
    try:
        primary_on = settings(cx, test_id)[PRIMARY]
    except Exception:
        primary_on = set(DEFAULTS[PRIMARY])
    program_chain = [r for r in chain if layer_remedy_on(r, primary_on)]
    return {"report_leads": stored_leads(cx), "report_personal": {},
            "program_chain": program_chain,
            "groups": groups, "meanings": meanings, "intro": intro, "frame": frame,
            "cautions": cautions, "chain": chain, "client_name": client_name,
            "date": date, "marks": marks,
            "remedy_source_infoceuticals": remedy_source_infoceuticals(
                groups, marks, program_chain, names, recommended_order),
            "unnamed_codes": unnamed_codes(groups, marks, names)}


def recommended_order(e4l_db, scan_id):
    """The scan's recommended infoceutical codes by the scan's own rank. Read only."""
    import sqlite3
    if not e4l_db or not scan_id or not os.path.exists(e4l_db):
        return []
    key = int(scan_id) if str(scan_id).isdigit() else scan_id
    try:
        ecx = sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)  # raw-sqlite-ok: e4l.db, read only
        try:
            return [r[0].upper() for r in ecx.execute(
                "SELECT item_code FROM e4l_scan_results WHERE scan_id=? AND "
                "section_context='Infoceuticals' AND item_code IS NOT NULL "
                "ORDER BY priority_rank", (key,))]
        finally:
            ecx.close()
    except sqlite3.Error:
        return []


PAGE_CSS = (
    ":root{--bg:#f6f5f2;--fg:#222;--on:#2d6a4f;--off:#bbb}"
    "body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;"
    "margin:0;padding:16px}"
    ".rs-wrap{max-width:900px;margin:0 auto}"
    ".rs-rep{margin:0 0 40px}.rs-rt{border-bottom:2px solid #444;padding-bottom:4px}"
    ".rs-btns{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 14px}"
    ".rs-b{border-radius:14px;padding:3px 10px;font:13px system-ui,sans-serif;"
    "border:1px solid var(--off);background:#fff;color:#777;cursor:pointer}"
    ".rs-b.on{background:var(--on);color:#fff;border-color:var(--on)}.rs-b small{opacity:.75}"
    ".rs-paper{background:#fff;border:1px solid #ddd;padding:8px}.rs-note{color:#555}"
    ".rs-pbar{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;"
    "margin:0 0 28px}.rs-pbar h2{margin:0 0 8px;font-size:17px}"
    ".rs-prow{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:6px 0}"
    ".rs-sw button,.rs-act{font:14px system-ui,sans-serif;padding:4px 12px;"
    "border:1px solid #888;background:#fff;cursor:pointer}"
    ".rs-sw button.on{background:#2f5d73;color:#fff;border-color:#2f5d73}"
    ".rs-sw button:first-child{border-radius:6px 0 0 6px}"
    ".rs-sw button:last-child{border-radius:0 6px 6px 0;border-left:0}"
    ".rs-act{border-radius:6px}.rs-out{color:#555;font-size:13px;margin:4px 0}"
    ".plan-glen{border:1px dashed #b9a6d8;padding:8px 12px;margin:0 0 14px;"
    "font:13px system-ui,sans-serif;background:#fff}"
)

PAGE_JS = r"""<script>
(function(){
function post(b){var on=!b.classList.contains('on');
 fetch('/test/'+b.dataset.tid+'/report-sections',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({report:b.dataset.report,section:b.dataset.section,on:on})})
 .then(function(r){return r.json()}).then(function(j){
  if(j.ok){location.reload()}else{alert(j.error||'Not saved')}})
 .catch(function(e){alert('Not saved: '+e)})}
document.querySelectorAll('.rs-b').forEach(function(b){b.onclick=function(){post(b)}});
var bar=document.getElementById('rs-pbar'); if(!bar)return;
var tid=bar.dataset.tid, who=bar.dataset.who, mode=bar.dataset.mode||'singly';
// The ticks and the switch live in the address, so a section button's reload keeps them.
function keep(){try{var q=new URLSearchParams();q.set('mode',mode);var t=ticked();
 if(t.length){t.forEach(function(k){q.append('p',k)})}else{q.append('p','')}
 history.replaceState(null,'',location.pathname+'?'+q.toString())}catch(e){}}
function ticked(){return [].slice.call(bar.querySelectorAll('.rs-pt'))
 .filter(function(c){return c.checked}).map(function(c){return c.value})}
function jobs(){var t=ticked(); if(!t.length)return [];
 if(mode==='grouped')return [{name:who+' - Reports.pdf',r:t}];
 return t.map(function(k){return {name:who+' - '+bar.querySelector('.rs-pt[value="'+k+'"]').dataset.title+'.pdf',r:[k]}})}
function url(j,dl){return '/test/'+tid+'/reports.pdf?mode='+mode+j.r.map(function(k){return '&r='+k}).join('')+(dl?'&download=1':'')}
function show(){keep();var o=document.getElementById('rs-pout'),js=jobs();
 o.innerHTML=js.length?js.map(function(j){return '<li>'+j.name.replace(/</g,'&lt;')+'</li>'}).join(''):'<li>Nothing: tick a report.</li>'}
bar.querySelectorAll('.rs-sw button').forEach(function(b){b.onclick=function(){mode=b.dataset.m;
 bar.querySelectorAll('.rs-sw button').forEach(function(x){x.classList.toggle('on',x===b)});show()}});
bar.querySelectorAll('.rs-pt').forEach(function(c){c.onchange=show});
// Each file is fetched first, so a refusal (a scan that could not be read) is shown here.
function save(j){return fetch(url(j,true)).then(function(r){
 if(!r.ok)return r.text().then(function(t){throw new Error(t)});return r.blob()})
 .then(function(b){var a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download=j.name;document.body.appendChild(a);a.click();
  setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},2000)})}
document.getElementById('rs-save').onclick=function(){var m=document.getElementById('rs-miss'),errs=[];
 m.textContent='';jobs().reduce(function(p,j){return p.then(function(){return save(j)
  .catch(function(e){errs.push(j.name+': '+e.message)})})},Promise.resolve())
 .then(function(){if(errs.length)m.textContent='Not saved. '+errs.join(' ')})};
document.getElementById('rs-print').onclick=function(){var miss=[];
 jobs().forEach(function(j){if(!window.open(url(j,false),'_blank'))miss.push(j)});
 var m=document.getElementById('rs-miss');
 m.innerHTML=miss.length?'The browser blocked '+miss.length+' tab(s). Open them here: '+
  miss.map(function(j){return '<a target=_blank href="'+url(j,false)+'">'+j.name.replace(/</g,'&lt;')+'</a>'}).join(' · '):''};
show()})();
</script>"""


def print_choice(args):
    """(ticked reports, mode) from the page's address. No `p` means every report
    ticked, as on a first visit; `p=` alone means none."""
    mode = args.get("mode") if args.get("mode") in ("singly", "grouped") else "singly"
    if "p" not in args:
        return list(STAND_ALONE), mode
    picked = set(args.getlist("p"))
    return [r for r in STAND_ALONE if r in picked], mode


def print_bar(test_id, client_name, picked=STAND_ALONE, mode="singly"):
    e = html.escape
    picked = set(picked)
    ticks = "".join(
        f"<label><input type=checkbox class=rs-pt value='{r}' data-title='{e(REPORT_TITLES[r])}'"
        f"{' checked' if r in picked else ''}> {e(REPORT_TITLES[r])}</label>"
        for r in STAND_ALONE)
    grouped = mode == "grouped"
    return (f"<section class=rs-pbar id=rs-pbar data-tid='{e(str(test_id))}' "
            f"data-mode='{'grouped' if grouped else 'singly'}' "
            f"data-who='{e(_safe(client_name))}'><h2>Save and print the stand-alone reports</h2>"
            f"<div class=rs-prow>{ticks}</div>"
            "<div class=rs-prow><span class=rs-sw>"
            f"<button type=button data-m=singly{'' if grouped else ' class=on'}>Singly</button>"
            f"<button type=button data-m=grouped{' class=on' if grouped else ''}>Grouped</button>"
            "</span>"
            "<button type=button class=rs-act id=rs-save>Save PDF</button>"
            "<button type=button class=rs-act id=rs-print>Print</button></div>"
            "<p class=rs-out>Save PDF and Print both make:</p><ul class=rs-out id=rs-pout></ul>"
            "<p class=rs-out id=rs-miss></p></section>")


def render_page(ctx, test_id, settings_, glen_note="", picked=STAND_ALONE, mode="singly"):
    """Glen's working page: the print bar, then each report under its buttons."""
    e = html.escape
    tid = e(str(test_id))
    prim_on = settings_[PRIMARY]
    added = primary_added(ctx, prim_on)
    parts = [
        f"<section class=rs-rep><h2 class=rs-rt>Biofield Analysis</h2>"
        + button_row(test_id, PRIMARY, prim_on, counts(ctx, PRIMARY))
        + "<p class=rs-note>Its layer-by-layer narrative, with the remedies on the Biofield "
        "layers. Those remedies make the invoice: a section switched off here comes off "
        "the invoice too. "
        f"<a href='/test/{tid}/report' target=_blank>Open</a> · "
        f"<a href='/test/{tid}/report.pdf' target=_blank>Print (PDF)</a></p>"
        + (f"<p class=rs-note>Added after the narrative:</p><div class=rs-paper>{added}</div>"
           if added else "")
        + "</section>"]
    for report in STAND_ALONE:
        on = settings_[report]
        parts.append(f"<section class=rs-rep><h2 class=rs-rt>{e(REPORT_TITLES[report])}</h2>"
                     + button_row(test_id, report, on, counts(ctx, report))
                     + f"<div class=rs-paper>{render_report(ctx, report, on)}</div></section>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Reports</title><style>{PAGE_CSS}</style></head><body><div class=rs-wrap>"
            f"<p><a href='/test/{tid}'>&larr; Report</a> · <a href='/author/{tid}'>Edit</a></p>"
            f"<h1>{e(ctx['client_name'] or 'Client')}: the reports</h1>"
            + glen_note + print_bar(test_id, ctx["client_name"], picked, mode) + "".join(parts)
            + "</div>" + PAGE_JS + "</body></html>")
