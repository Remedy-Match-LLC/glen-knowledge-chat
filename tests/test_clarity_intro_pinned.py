"""Clarity's product page carries Glen's approved wording, pinned (Glen "approved", 2026-10-23).

What this does: marketing/04 Copy/clarity-store-page-2026-10-23/clarity-store-page.md, ten
one-sentence paragraphs, with the lens sentence from round 3 of clarity-app-sections.md.
Overview and The research: clarity-app-sections.md, approved in the marketing tab after three
review rounds. The ingredient panel: the August 2026 label, approved the same day. They
replace unreviewed eye-only AI drafts that said "One to three capsules" and named Trikatu,
which is not in the formula. The formula is one capsule daily, for brain and eyes.

The approved text is written out here, because CI has no vault to read it from.
"""
import importlib
import json
import os
import sqlite3
import subprocess

import pytest

SLUG = "clarity"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def entry():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"][SLUG]


PARAS = [
    "Clarity supports both your eyes and your brain.",
    "Your retina develops from the same tissue as your brain.",
    "So your retina and your brain share many of the same needs.",
    "Rosmarinic acid, concentrated from rosemary, is the heart of Clarity.",
    "In a laboratory study, it supported the clarity of human lens protein.",
    "That study inspired this formula.",
    "Pterostilbene is a natural polyphenol found in blueberries, and a close relative of resveratrol.",
    "In studies of aging animals, it supported memory and healthy brain function.",
    "In cell and animal studies, it also protected delicate eye tissues.",
    "A small amount of piperine, from black pepper, is included.",
]

OVERVIEW = [
    "Clarity is one capsule a day, for your eyes and your brain.",
    "Each capsule holds three ingredients.",
    "Rosemary extract, standardized to 50% rosmarinic acid (400 mg), is the heart of the formula.",
    "Pterostilbene 98% (75 mg), a polyphenol found in blueberries, joins it for the brain.",
    "Piperine 95% (5 mg), from black pepper, completes it.",
    "The capsule is certified organic pullulan, with zero fillers inside.",
    "Take 1 capsule daily with food, or as guided.",
]

T_AGING = ("Cellular and behavioral effects of stilbene resveratrol analogues: implications "
           "for reducing the deleterious effects of aging")
T_CORNEA = ("Blueberry Component Pterostilbene Protects Corneal Epithelial Cells from "
            "Inflammation via Anti-oxidative Pathway")
T_RETINA = ("Analysis of Lipid Peroxidation by UPLC-MS/MS and Retinoprotective Effects of the "
            "Natural Polyphenol Pterostilbene")
QUOTE = ("\"Results indicated that pterostilbene was effective in reversing cognitive "
         "behavioral deficits ...\"")
RESEARCH = [
    "One laboratory study inspired Clarity.",
    "In it, rosmarinic acid supported the clarity of human lens protein in the laboratory.",
    "Source: Scientific Reports, 2018",
    "Pterostilbene was studied in aging rats.",
    QUOTE,
    f"Source: {T_AGING} (Journal of Agricultural and Food Chemistry, 2008)",
    "In cell and animal studies, pterostilbene also protected delicate eye tissues.",
    f"Sources: {T_CORNEA} (Scientific Reports, 2016), human corneal cells; {T_RETINA} "
    "(Antioxidants, 2021), rabbit retina.",
    "These findings come from laboratory and animal research.",
]
LINKS = [
    {"label": "Scientific Reports, 2018", "url": "https://pubmed.ncbi.nlm.nih.gov/29921877/"},
    {"label": T_AGING, "url": "https://pubmed.ncbi.nlm.nih.gov/18954071/"},
    {"label": T_CORNEA, "url": "https://pubmed.ncbi.nlm.nih.gov/26762881/"},
    {"label": T_RETINA, "url": "https://pubmed.ncbi.nlm.nih.gov/33498744/"},
]
INGREDIENTS = [
    {"name": "Rosemary extract (Salvia rosmarinus), standardized to 50% rosmarinic acid",
     "dose": "400 mg"},
    {"name": "Pterostilbene 98%", "dose": "75 mg"},
    {"name": "Piperine 95%, from Piper nigrum (black pepper)", "dose": "5 mg"},
]
OTHER = "Other ingredients: certified organic pullulan vegicap, Healing Energy, Love, and Prayer."
DIRECTIONS = "Take 1 capsule daily with food, or as guided."


def test_the_intro_is_the_ten_approved_paragraphs(entry):
    assert entry["intro"].split("\n\n") == PARAS
    assert "One to three" not in entry["intro"]
    assert "absorb" not in entry["intro"]


def test_the_overview_is_the_approved_paragraphs(entry):
    assert entry["description"] == "\n\n".join(OVERVIEW)


def test_the_research_is_the_approved_paragraphs_with_their_links(entry):
    assert entry["how_it_works"] == "\n\n".join(RESEARCH)
    assert entry["research_links"] == LINKS
    assert QUOTE in entry["how_it_works"]          # the ellipsis marks the cut, verbatim
    assert "Source: Scientific Reports, 2018\n" in entry["how_it_works"]
    assert "research_learn_link" not in entry


def test_the_learn_page_markdown_is_the_research_text_with_real_links(entry):
    md = entry["learn_markdown"]
    for lk in LINKS:
        assert f"[{lk['label']}]({lk['url']})" in md
    plain = md
    for lk in LINKS:
        plain = plain.replace(f"[{lk['label']}]({lk['url']})", lk["label"])
    assert plain == "\n\n".join(RESEARCH)


def test_the_panel_is_the_approved_label(entry):
    assert entry["ingredients"] == INGREDIENTS
    assert entry["panel_note"] == OTHER
    assert "Trikatu" not in json.dumps(entry)
    assert "One to three" not in json.dumps(entry)


def test_the_directions_are_the_label_line(entry):
    assert entry["directions"] == DIRECTIONS


def test_the_four_approved_sections_are_pinned(entry):
    assert sorted(entry["copy_pinned"]) == ["description", "ingredients", "intro", "research"]
    assert entry["content_since"] == "2026-10-23"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD One to three capsules daily, Trikatu",
                                   "ingredients": [{"name": "Trikatu", "dose": "5 mg"}],
                                   "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW One to three capsules daily")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def _cache_drafts(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, f"AI DRAFT MARKER {sec} One to three capsules daily")


def _page(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    return d, {s["id"]: s for s in d["sections"]}


def test_the_page_shows_every_approved_section_over_cached_ai_drafts(appmod, entry):
    _cache_drafts(appmod)
    d, secs = _page(appmod)
    assert secs["intro"]["body"] == "\n\n".join(PARAS)
    assert secs["description"]["body"] == "\n\n".join(OVERVIEW)
    assert secs["description"]["keep_breaks"] is True
    assert secs["research"]["body"] == {"how_it_works": "\n\n".join(RESEARCH),
                                        "learn_url": "/begin/learn/clarity", "links": LINKS}
    for sec in ("intro", "description", "research"):
        assert "ai" not in secs[sec], sec
    ing = secs["ingredients"]["body"]
    assert [{"name": i["name"], "dose": i["dose"]} for i in ing["ingredients"]] == INGREDIENTS
    assert ing["note"] == OTHER
    assert ing["directions"] == DIRECTIONS
    whole = json.dumps(d)
    assert "AI DRAFT MARKER" not in whole
    assert "One to three" not in whole
    assert "Trikatu" not in whole


def test_the_learn_page_data_carries_the_approved_research(appmod, entry):
    d = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == entry["learn_markdown"]
    assert "In it, rosmarinic acid supported the clarity of human lens protein" in d["markdown"]
    assert "[Scientific Reports, 2018](https://pubmed.ncbi.nlm.nih.gov/29921877/)" in d["markdown"]
    assert "One to three" not in json.dumps(d)
    assert "Trikatu" not in json.dumps(d)


def test_content_since_drops_old_drafts_and_keeps_the_pinned_text(appmod):
    from dashboard import sales_pages as sp
    _cache_drafts(appmod)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE sales_pages SET generated_at='2026-10-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    _d, secs = _page(appmod)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sp.get_section(cx, SLUG, "intro") is None
    assert secs["intro"]["body"] == "\n\n".join(PARAS)
    assert secs["description"]["body"] == "\n\n".join(OVERVIEW)
    assert secs["research"]["body"]["how_it_works"] == "\n\n".join(RESEARCH)


def test_an_unpinned_research_gets_no_links(appmod):
    p = {"research_links": LINKS, "how_it_works": "Scientific Reports, 2018"}
    body = appmod._research_body(p, set(), "Scientific Reports, 2018", "x")
    assert body == {"how_it_works": "Scientific Reports, 2018", "learn_url": "/begin/learn/x"}


def test_only_https_links_whose_label_is_in_the_text_are_served(appmod):
    p = {"research_links": [{"label": "A", "url": "javascript:alert(1)"},
                            {"label": "B", "url": "http://x.org/"},
                            {"label": "missing", "url": "https://x.org/"},
                            {"label": "C", "url": "https://x.org/c"}]}
    body = appmod._research_body(p, {"research"}, "A B C", "x")
    assert body["links"] == [{"label": "C", "url": "https://x.org/c"}]


def _render_research(body):
    html = open(os.path.join(ROOT, "static", "begin-product.html"), encoding="utf-8").read()
    start = html.index("    function renderResearchBody(body){")
    end = html.index("    function renderPick(wrap, pick){")
    js = """
    function el(tag){ return {tag: tag, kids: [], className: '', textContent: '', href: '',
      setAttribute: function(){}, appendChild: function(c){ this.kids.push(c); }}; }
    var document = { createElement: el,
      createTextNode: function(t){ return {tag: '#text', textContent: t}; },
      createDocumentFragment: function(){ return el('#frag'); } };
    var slug = 'clarity';
    %s
    function flat(n){ return n.kids ? n.kids.map(function(k){
      return k.tag === 'a' ? {a: k.textContent, href: k.href, cls: k.className}
           : (k.kids && k.kids.length ? flat(k) : k.textContent); }) : n.textContent; }
    var w = renderResearchBody(%s);
    console.log(JSON.stringify(flat(w.kids[0])));
    """ % (html[start:end], json.dumps(body))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_links_each_source_title_in_place():
    got = _render_research({"how_it_works": "\n\n".join(RESEARCH),
                            "learn_url": "/begin/learn/clarity", "links": LINKS})
    assert got[1] == {"a": "The research ↗", "href": "/begin/learn/clarity", "cls": "learn-more"}
    para = got[0]
    anchors = [k for k in para if isinstance(k, dict)]
    assert anchors == [{"a": lk["label"], "href": lk["url"], "cls": "sp-how-source"}
                       for lk in LINKS]
    text = "".join(k["a"] if isinstance(k, dict) else k for k in para)
    assert text == "\n\n".join(RESEARCH)


def test_research_with_no_links_stays_one_text():
    got = _render_research({"how_it_works": "Text.", "learn_url": "/begin/learn/x"})
    assert got == [["Text."], {"a": "The research ↗", "href": "/begin/learn/x", "cls": "learn-more"}]


def test_a_non_https_link_is_left_as_text():
    got = _render_research({"how_it_works": "See A here.",
                            "links": [{"label": "A", "url": "javascript:alert(1)"}]})
    assert got[0] == ["See A here."]


@pytest.mark.parametrize("q", ["Tell me about Clarity", "How do I take Clarity?",
                               "What is Clarity?", "Clarity dose?"])
def test_the_bot_gets_the_approved_overview_and_panel(appmod, q):
    block = appmod._named_product_facts(q)
    assert "### Clarity" in block
    assert "\n\n".join(OVERVIEW) in block            # carries the directions sentence
    assert DIRECTIONS in block
    assert ("Ingredients from the label:\n"
            "- Rosemary extract (Salvia rosmarinus), standardized to 50% rosmarinic acid: 400 mg\n"
            "- Pterostilbene 98%: 75 mg\n"
            "- Piperine 95%, from Piper nigrum (black pepper): 5 mg") in block
    assert "Role:" not in block                      # roles are not pinned
    assert "One to three" not in block
    assert "Trikatu" not in block


@pytest.mark.parametrize("q", [
    "Clarity of mind is what I lack...",
    "Hi. Clarity of mind is what I lack",
    "tell me about clarity",
    "How much Crystalline Clarity should I take?",
    "Is Spiritual Clarity Flower Essence in Terrain Restore safe?",
    "How do I use Clarity Himalayan Flower Enhancer in Terrain Restore?",
    "What is Inspiration & Clarity Heliconia Flower Essence in Terrain Restore?",
])
def test_the_bot_does_not_bring_clarity_for_other_wording(appmod, q):
    assert "### Clarity" not in appmod._named_product_facts(q)


def test_any_longer_catalog_name_beats_a_pinned_shorter_one(appmod):
    products = {"zorbex": {"name": "Zorbex", "copy_pinned": ["intro"]},
                "zorbex-max": {"name": "Zorbex Max Drops"},
                "other": {"name": "Other", "copy_pinned": ["intro"]}}
    assert appmod._named_product_spans("How much Zorbex Max Drops?", products) == {}
    q = "how much zorbex max drops? and Other"
    assert appmod._named_product_spans(q, products) == {"other": (q.index("Other"), len(q))}
    assert "zorbex" in appmod._named_product_spans("How much Zorbex Max?", products)
