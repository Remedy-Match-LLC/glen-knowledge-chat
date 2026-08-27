"""Clean, client-facing presentation of a Biofield report — no editing chrome.
Shared renderer for the print/PDF (and later portal) skins. Pure function:
takes the report dict + narrative text, returns a complete print-styled HTML doc.
Section order is schedule-forward (the printed sheet ships with the bottles)."""

import os as _os
import re as _re

WORDMARK = "Accelerated Self Healing™"
FOOTER = "In wellness, Dr. Glen & Rae · illtowell.com"


def _e(s):
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


_CSS = """
  :root { --ink:#1a1a1a; --muted:#6b6b6b; --gold:#9a7a1f; --hair:#e6e1d5; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 Georgia, 'Times New Roman', serif; color: var(--ink);
         max-width: 760px; margin: 0 auto; padding: 28px; }
  .masthead { display: flex; align-items: center; gap: 14px; text-align: left;
              border-bottom: 2px solid var(--gold); padding-bottom: 12px; margin-bottom: 18px; }
  .masthead .logo { width: 54px; height: auto; flex: 0 0 auto; }
  .masthead .mh-text { flex: 1 1 auto; }
  .masthead .wordmark-row { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }
  .masthead .wordmark { font-weight: 700; letter-spacing: .04em; font-size: 20px; }
  .masthead .mh-name { font-weight: 700; font-size: 20px; text-align: right; margin-left: auto; white-space: nowrap; }
  .masthead .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
  h2 { color: var(--gold); font-size: 16px; border-bottom: 1px solid var(--hair); padding-bottom: 3px; margin: 22px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 4px 7px; border-bottom: 1px solid var(--hair); vertical-align: top; }
  th { background: #faf7ef; }
  .slot { white-space: nowrap; font-weight: 600; }
  .food { color: var(--muted); }
  .terrain { margin: 0 0 16px; padding: 9px 13px; background: #faf7ef; border: 1px solid var(--hair);
             border-left: 3px solid var(--gold); border-radius: 4px; font-size: 14px; font-weight: 600; }
  .terrain-k { display: block; font-size: 10px; font-weight: 700; text-transform: uppercase;
               letter-spacing: .08em; color: var(--gold); margin-bottom: 2px; }
  .terrain-loc { font-weight: 400; color: var(--muted); }
  .terrain-tag { color: var(--gold); font-weight: 600; }
  .narrative p { margin: 0 0 9px; }
  .footer { margin-top: 26px; border-top: 1px solid var(--hair); padding-top: 8px;
            text-align: center; color: var(--muted); font-size: 12px; }
  @media print {
    body { padding: 0; max-width: none; }
    h2 { page-break-after: avoid; }
    tr, .narrative p { page-break-inside: avoid; }
  }
"""


def _logo_data_uri():
    """Header logo (resized static/logo-mark.png) as a data URI, embedded so it
    survives the standalone-HTML -> Playwright PDF render (no server/base URL)."""
    import base64
    import os
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "static", "logo-mark.png")
        with open(p, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return ""


def _masthead(report):
    c = report.get("client") or {}
    name = _e(c.get("name"))
    date = _e(report.get("date"))
    logo = _logo_data_uri()
    img = f'<img class="logo" src="{logo}" alt="">' if logo else ""
    name_el = f'<div class="mh-name">{name}</div>' if name else ""
    sub = "Biofield Analysis" + (f" · {date}" if date else "")
    return (f'<div class="masthead">{img}'
            f'<div class="mh-text">'
            f'<div class="wordmark-row"><div class="wordmark">{_e(WORDMARK)}</div>{name_el}</div>'
            f'<div class="sub">{sub}</div></div></div>')


def _terrain(report):
    """The scan's terrain reading in the requested print-header format."""
    from dashboard.terrain_phase import phase_num
    phase = phase_num(report.get("phase"))
    if not phase:
        return ""
    loc = (report.get("location") or "").strip()
    return f'<div class="terrain">Phase {phase}: {_e(loc)}</div>'


def _sched_name(name, report):
    """Display name for a schedule entry. The one combined 'Terrain Restore' liquid
    bottle carries the scan's phase name (all its essences share the single scan phase)."""
    from dashboard.terrain_phase import phase_name
    label = _e(name)
    if (name or "").strip().lower() == "terrain restore":
        pn = phase_name(report.get("phase"))
        if pn:
            label += f' <span class="terrain-tag">· {_e(pn)}</span>'
    return label


def _schedule(report):
    sched = report.get("schedule") or {}
    entries = sched.get("entries") or []
    placed = [e for e in entries if not e.get("as_directed")]
    rows = ""
    for slot in sched.get("slots") or []:
        here = [e for e in placed if slot in (e.get("slots") or [])]
        if not here:
            continue
        cells = "; ".join(
            f"{_sched_name(e.get('name'), report)} <span class=food>({_e(e.get('per_slot') or e.get('dosage'))}"
            + (f", {_e(e.get('food'))}" if e.get("food") else "") + ")</span>"
            for e in here)
        rows += f"<tr><td class=slot>{_e(slot)}</td><td>{cells}</td></tr>"
    asdir = [e for e in entries if e.get("as_directed")]
    if asdir:
        cells = "; ".join(
            f"{_sched_name(e.get('name'), report)} <span class=food>({_e(e.get('timing') or 'as directed')})</span>"
            for e in asdir)
        rows += f"<tr><td class=slot>As directed</td><td>{cells}</td></tr>"
    return ("<h2>Remedy Schedule</h2>"
            "<table><tr><th>When</th><th>Take</th></tr>" + rows + "</table>")


def _narrative(narrative):
    paras = "".join(f"<p>{_e(p.strip())}</p>" for p in (narrative or "").split("\n") if p.strip())
    return f'<h2>Narrative</h2><div class="narrative">{paras}</div>'


def _essence_benefit(description):
    text = _re.sub(r"\s+", " ", description or "").strip()
    text = _re.split(r"\b(?:10 drops?|take \d+ drops?|dosage:)\b", text,
                     maxsplit=1, flags=_re.I)[0].strip(" •-;,")
    if not text:
        return "Included to provide the supportive energetic benefits Dr. Glen tested for you."
    if len(text) > 420:
        cut = text.rfind(".", 0, 421)
        text = text[:cut + 1] if cut >= 120 else text[:417].rstrip() + "..."
    return text


def _essence_name(value):
    return _re.sub(r"\s+in\s+Terrain Restore\s*$", "", value or "", flags=_re.I).strip()


def _essence_product(value, products):
    wanted = _essence_name(value).casefold()
    if not wanted or "essence" not in wanted:
        return "", {}
    for slug, product in (products.get("products") or {}).items():
        if _essence_name((product or {}).get("name")).casefold() == wanted:
            return slug, product or {}
    return "", {}


def _stress_essence_for(remedy_item, layers, products):
    remedy_slug = remedy_item.get("slug") or _essence_product(
        remedy_item.get("name"), products)[0]
    for row in layers or []:
        if _essence_product(row.get("remedy"), products)[0] != remedy_slug:
            continue
        stress_name = (row.get("head") or row.get("head_chain") or "").strip()
        _stress_slug, product = _essence_product(stress_name, products)
        if product:
            return {"name": product.get("name") or stress_name,
                    "description": product.get("description") or ""}
    return None


def _life_stress(report):
    """Render only hand-tested Terrain Restore essences, never an AI fallback."""
    if _os.environ.get("LIFE_STRESS_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        return ""
    try:
        curation = (report or {}).get("life_stress_curation")
        if not curation or not curation.get("slugs"):
            return ""
        from dashboard import life_stress_curation as _lsc
        from dashboard.life_stress import _load_json, _PRODUCTS_PATH
        products = _load_json(_PRODUCTS_PATH)
        ls = _lsc.apply_data(curation, {}, products)
        if not ls or not ls.get("curated") or not ls.get("items"):
            return ""
        rows = []
        for item in ls["items"]:
            stress = _stress_essence_for(item, (report or {}).get("layers"), products)
            indication = ""
            if stress:
                indication = (f'<div><span class="food">Stress indication &mdash; '
                              f'{_e(_essence_name(stress.get("name")))}</span>: '
                              f'{_e(_essence_benefit(stress.get("description")))}</div>')
            rows.append(
                f'<li>{indication}<div><strong>Balancing essence &mdash; '
                f'{_e(_essence_name(item.get("name")))}</strong>: '
                f'{_e(_essence_benefit(item.get("description")))}</div></li>')
        return ("<h2>Supportive Life Stress Essences</h2>"
                '<p class="food">These essences were tested and selected for you '
                "by hand for inclusion in your Terrain Restore. Their supportive "
                "benefits are described below.</p><ul>" + "".join(rows) + "</ul>")
    except Exception:
        return ""


def _chain(report):
    from dashboard.biofield_report_html import render_chain_table
    # Grouped by layer (Head/Tail span their remedies) to match the editor + viewer.
    return "<h2>Causal Chain</h2>" + render_chain_table(report.get("layers") or [])


def render_present(report, narrative=""):
    body = (_masthead(report) + _terrain(report) + _schedule(report) + _narrative(narrative)
            + _life_stress(report) + _chain(report) + f'<div class="footer">{_e(FOOTER)}</div>')
    return (f"<!doctype html><html><head><meta charset=utf-8>"
            f"<title>Biofield Analysis</title><style>{_CSS}</style></head>"
            f"<body>{body}</body></html>")
