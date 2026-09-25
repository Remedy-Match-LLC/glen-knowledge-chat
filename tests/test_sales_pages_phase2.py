import sqlite3
from dashboard import sales_pages as sp
from dashboard import sales_copy as sc

def _cx():
    return sqlite3.connect(":memory:")

def test_upsert_then_get_section_roundtrip():
    cx = _cx()
    assert sp.get_section(cx, "longevity", "intro") is None
    sp.upsert_section(cx, "longevity", "intro", "Hello world.", model="m1")
    assert sp.get_section(cx, "longevity", "intro") == "Hello world."

def test_upsert_accretes_sections_in_one_row():
    cx = _cx()
    sp.upsert_section(cx, "energy", "intro", "A.")
    sp.upsert_section(cx, "energy", "description", "B.")
    page = sp.get_page(cx, "energy")
    assert page["content"] == {"intro": "A.", "description": "B."}
    assert page["state"] == "draft"

def test_get_page_missing_returns_none():
    assert sp.get_page(_cx(), "nope") is None

def test_prompt_includes_compliance_and_no_disease_claim():
    system, user = sc.build_section_prompt("intro", {"name": "Longevity", "ingredients": []})
    assert "treat" in system.lower() and "cure" in system.lower() and "prevent" in system.lower()
    assert "supports" in system.lower() or "structure/function" in system.lower()

def test_prompt_grounds_in_product_name_and_ingredients():
    prod = {"name": "Longevity", "ingredients": [{"name": "Resveratrol", "dose": "200 mg"}, "Quercetin"]}
    system, user = sc.build_section_prompt("research", prod)
    assert "Longevity" in user
    assert "Resveratrol" in user and "200 mg" in user and "Quercetin" in user

def test_prompt_grounds_device_in_description_without_inventing_ingredients():
    # A device/tool has no ingredients: the prompt must carry the authored description and
    # forbid inventing a formula or asking for a missing ingredient stack (that produced
    # hallucinated nutritional-formula copy + LLM refusals on the nightlight pages).
    prod = {"name": "Therapeutic Nightlight",
            "description": "A 660 nm soft-laser red nightlight that supports melatonin.",
            "ingredients": []}
    system, user = sc.build_section_prompt("description", prod)
    assert "devices" in system.lower() and "never invent" in system.lower()
    assert "660 nm soft-laser red nightlight" in user
    assert "no ingredient list" in user.lower()

def test_prompt_frames_infoceutical_energetically_not_nutritionally():
    # Infoceuticals (url under .../infoceuticals/) are energetic-frequency remedies, not
    # nutritional supplements. The prompt must forbid nutritional framing and asking for a
    # missing ingredient stack (that produced an LLM refusal on the Rejuvenation page).
    prod = {"name": "Rejuvenation",
            "url": "https://remedymatch.com/remedies/modalities/biocommunication/infoceuticals/220-rejuv",
            "ingredients": []}
    system, user = sc.build_section_prompt("intro", prod)
    assert "energetic frequency" in system.lower()
    assert "not a nutritional supplement" in system.lower()
    assert "nutritional support" in system.lower()  # named only to forbid it
    assert "never ask for missing information" in system.lower()
    assert "Rejuvenation" in user
    assert "no ingredient list" in user.lower()


def test_prompt_still_nutritional_for_a_supplement():
    # A real supplement (no infoceutical url) keeps the nutritional-formula framing.
    prod = {"name": "Iron Syntropy", "url": "https://remedymatch.com/remedies/syntropy/319-iron-syntropy",
            "ingredients": [{"name": "Iron bisglycinate", "dose": "18 mg"}]}
    system, user = sc.build_section_prompt("research", prod)
    assert "nutritional formulas" in system.lower()
    assert "Iron bisglycinate" in user and "18 mg" in user


def test_narrative_sections_are_exactly_three():
    assert sc.NARRATIVE_SECTIONS == ("intro", "description", "research")


# ---------------------------------------------------------------------------
# Task 3: SALES_PAGES_AI_COPY flag + page-data ai markers
# ---------------------------------------------------------------------------

import importlib
import os
import pytest


def _reload_app(monkeypatch, tmp_path, ai="true"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", ai)
    import app as appmod
    importlib.reload(appmod)
    return appmod


def test_page_data_marks_narrative_pending_when_no_draft(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    data = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    nar = {s["id"]: s for s in data["sections"] if s["id"] in ("intro", "description", "research")}
    assert all(s.get("ai") == "pending" for s in nar.values())


def test_page_data_serves_cached_draft(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.upsert_section(cx, slug, "intro", "Cached intro copy.")
    data = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    intro = next(s for s in data["sections"] if s["id"] == "intro")
    assert intro["ai"] == "cached" and intro["body"] == "Cached intro copy."


def test_page_data_no_ai_field_when_flag_off(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path, ai="false")
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    data = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    assert all("ai" not in s for s in data["sections"])


# ---------------------------------------------------------------------------
# Task 4: /begin/product-page-gen SSE endpoint
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, toks): self._toks = toks
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def text_stream(self):
        for t in self._toks: yield t

class _FakeMessages:
    def __init__(self, toks, boom=False): self._toks=toks; self.boom=boom; self.calls=0
    def stream(self, **kw):
        self.calls += 1
        if self.boom: raise RuntimeError("claude down")
        return _FakeStream(self._toks)

class _FakeCl:
    def __init__(self, toks, boom=False): self.messages=_FakeMessages(toks, boom)

def _frames(resp):
    return resp.get_data(as_text=True)

def test_gen_streams_and_persists(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"ingredients": [{"name": "Resveratrol", "dose": "200 mg"}]})
    monkeypatch.setattr(appmod, "_cl", _FakeCl(["Live ", "intro ", "copy."]))
    body = _frames(appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro"))
    assert "Live " in body and '"done": true' in body
    import sqlite3
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sp.get_section(cx, slug, "intro") == "Live intro copy."

def test_gen_returns_cached_without_calling_claude(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    import sqlite3
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.upsert_section(cx, slug, "intro", "Cached copy.")
    fake = _FakeCl([], boom=True)
    monkeypatch.setattr(appmod, "_cl", fake)
    body = _frames(appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro"))
    assert "Cached copy." in body and fake.messages.calls == 0

def test_gen_404_when_flag_off(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path, ai="false")
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    assert appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro").status_code == 404

def test_gen_error_frame_on_claude_failure(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"ingredients": []})
    monkeypatch.setattr(appmod, "_cl", _FakeCl([], boom=True))
    body = _frames(appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro"))
    assert '"error": true' in body


# ---------------------------------------------------------------------------
# Task 6: integration sanity + flag default
# ---------------------------------------------------------------------------

def test_full_phase2_file_and_flag_default(monkeypatch, tmp_path):
    # flag unset → disabled
    monkeypatch.delenv("SALES_PAGES_AI_COPY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    import importlib, app as appmod; importlib.reload(appmod)
    assert appmod._SALES_AI_COPY_ENABLED is False
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    assert appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro").status_code == 404


# ---------------------------------------------------------------------------
# Task 4 (Finding 1): em-dash strip applied to streamed + persisted text
# ---------------------------------------------------------------------------

def test_gen_strips_em_dash_from_streamed_and_persisted(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"ingredients": []})
    # Model emits a token with an em dash
    monkeypatch.setattr(appmod, "_cl", _FakeCl(["Calm — focused."]))
    body = _frames(appmod.app.test_client().get(f"/begin/product-page-gen/{slug}/intro"))
    # Streamed SSE body must not contain an em dash
    assert "—" not in body, "Em dash found in streamed SSE body"
    # Persisted text must not contain an em dash
    import sqlite3
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        persisted = sp.get_section(cx, slug, "intro")
    assert persisted is not None, "Nothing was persisted"
    assert "—" not in persisted, "Em dash found in persisted section text"


# ---------------------------------------------------------------------------
# copy_pinned: sections Glen approved word for word are never replaced by an AI draft
# ---------------------------------------------------------------------------

def _pinned_product(appmod, slug, pinned):
    p = dict(appmod._PRODUCTS["products"][slug])
    p.update({"intro": "Approved intro.", "description": "Approved description.",
              "copy_pinned": pinned})
    return p


def _page(appmod, monkeypatch, slug, product, drafts):
    from dashboard import sales_pages as sp
    monkeypatch.setitem(appmod._PRODUCTS["products"], slug, product)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec, body in drafts.items():
            sp.upsert_section(cx, slug, sec, body)
    data = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    return {s["id"]: s for s in data["sections"]}


def test_a_pinned_section_keeps_the_approved_text_over_an_ai_draft(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    secs = _page(appmod, monkeypatch, slug, _pinned_product(appmod, slug, ["intro", "description"]),
                 {"intro": "AI intro.", "description": "AI description."})
    assert secs["intro"]["body"] == "Approved intro."
    assert secs["description"]["body"] == "Approved description."
    assert "ai" not in secs["intro"] and "ai" not in secs["description"]


def test_an_unpinned_section_still_takes_the_ai_draft(monkeypatch, tmp_path):
    """The control. Pinning one section must not freeze the others."""
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    secs = _page(appmod, monkeypatch, slug, _pinned_product(appmod, slug, ["intro"]),
                 {"intro": "AI intro.", "description": "AI description."})
    assert secs["intro"]["body"] == "Approved intro."
    assert secs["description"]["body"] == "AI description." and secs["description"]["ai"] == "cached"


def test_without_copy_pinned_a_description_is_still_replaced(monkeypatch, tmp_path):
    """Opt-in: most products carry scraped descriptions the AI draft should keep beating."""
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    secs = _page(appmod, monkeypatch, slug, _pinned_product(appmod, slug, []),
                 {"description": "AI description."})
    assert secs["description"]["body"] == "AI description."


def test_reverse_aging_program_pins_glens_approved_copy():
    import json
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "products.json")
    with open(path) as f:
        p = json.load(f)["products"]["reverse-aging-program"]
    # All three narrative sections: no unreviewed AI text, so no "pending review" banner.
    assert set(p["copy_pinned"]) == {"intro", "description", "research"}
    assert p["description"].startswith(p["intro"])
    assert "$419.82" in p["description"] and p["regular_cents"] == 41982


def test_the_real_page_carries_no_ai_marker_so_no_banner(monkeypatch, tmp_path):
    """The front end shows "pending his personal review" if ANY section has `ai`."""
    appmod = _reload_app(monkeypatch, tmp_path)
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.upsert_section(cx, "reverse-aging-program", "intro", "AI intro.")
    data = appmod.app.test_client().get("/begin/product-page-data/reverse-aging-program").get_json()
    assert not any("ai" in s for s in data["sections"])
    intro = next(s for s in data["sections"] if s["id"] == "intro")
    assert intro["body"].startswith("The Reverse Aging Program is six remedies")


def test_a_malformed_copy_pinned_does_not_break_the_page(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    for bad in (True, 3, "intro"):
        p = dict(appmod._PRODUCTS["products"][slug]); p["copy_pinned"] = bad
        monkeypatch.setitem(appmod._PRODUCTS["products"], slug, p)
        assert appmod.app.test_client().get(f"/begin/product-page-data/{slug}").status_code == 200
