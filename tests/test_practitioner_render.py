import json
import re

import pytest
from dashboard import practitioner_render as pr


def _view(**over):
    """Minimal payload: name only. This is what EVERY practitioner renders
    today, since zero of the 23 are self-authored."""
    v = {"slug": "mary-boyd", "practitioner_name": "Mary Boyd",
         "practice_name": "", "bio": "", "photo_url": "", "logo_url": "",
         "services": [], "location": "", "accepting_clients": True,
         "featured_products": [], "catalog_url": "/begin/explore",
         "profit_disclosure": "Your practitioner earns a portion of what you"
                              " spend here. Your price is the same either way.",
         "tagline": "", "how_i_work": ""}
    v.update(over)
    return v


def test_name_only_page_is_a_complete_document():
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<h1>Mary Boyd</h1>" in html
    assert "<title>Mary Boyd</title>" in html


def test_noindex_is_present_on_every_page():
    """Section 5a never lifts noindex. 5b owns that, behind a content bar."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '<meta name="robots" content="noindex">' in html


def test_markup_in_a_practitioner_name_is_escaped():
    html = pr.render_page_html(
        _view(practitioner_name='Mary <script>alert(1)</script> Boyd'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_quotes_in_an_attribute_value_are_escaped():
    """A raw double quote in a meta content= attribute breaks out of it."""
    html = pr.render_page_html(
        _view(tagline='She said "hello" and left'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '"hello"' not in html
    assert "&quot;hello&quot;" in html


def test_title_pairs_name_with_practice_when_there_is_one():
    v = _view(practice_name="Fairbanks Wellness")
    assert pr.build_title(v) == "Mary Boyd — Fairbanks Wellness"


def test_title_is_just_the_name_when_there_is_no_practice():
    assert pr.build_title(_view()) == "Mary Boyd"


def test_description_prefers_the_tagline():
    v = _view(tagline="Helping nurses stop running on empty",
              bio="A much longer biography that should not win.")
    assert pr.build_description(v) == "Helping nurses stop running on empty"


def test_description_falls_back_to_the_bio():
    v = _view(bio="I work with nurses and shift workers.")
    assert pr.build_description(v) == "I work with nurses and shift workers."


def test_description_falls_back_to_a_neutral_line_when_empty():
    """Name-only is the common case today. An empty description tag is worse
    than a plain one: the preview card shows the raw URL instead."""
    assert pr.build_description(_view()) == "Mary Boyd on Remedy Match."


def test_description_is_truncated_on_a_word_boundary():
    v = _view(bio="word " * 100)
    d = pr.build_description(v)
    assert len(d) <= pr.MAX_DESCRIPTION
    assert d.endswith("…")
    assert not d.endswith(" …")


def test_bio_paragraphs_survive_as_separate_paragraphs():
    """how_i_work and bio are stored with blank lines preserved on purpose --
    flattening them was a data-destroying defect fixed in section 2b."""
    v = _view(bio="First para.\n\nSecond para.")
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<p>First para.</p>" in html
    assert "<p>Second para.</p>" in html


def test_absent_blocks_emit_nothing_but_present_blocks_do():
    """A bare 'not in html' check on the empty payload alone was green before
    _section/_photo existed and stays green if either is deleted -- it never
    proves the surface can render, only that it currently doesn't. Same
    shape as test_no_raw_angle_bracket_survives_in_the_jsonld_script_body:
    absence on the empty payload, then presence and a value round-trip on
    the populated one."""
    empty = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" not in empty
    assert "<h2>How I work</h2>" not in empty
    assert "<img" not in empty

    v = _view(bio="First-hand account of my practice.",
              how_i_work="Weekly check-ins by phone.",
              photo_url="https://cdn.example/mary.jpg")
    full = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" in full
    assert "First-hand account of my practice." in full
    assert "<h2>How I work</h2>" in full
    assert "Weekly check-ins by phone." in full
    assert '<img class="photo" src="https://cdn.example/mary.jpg"' in full


def test_present_blocks_are_labelled():
    v = _view(bio="About me.", how_i_work="My approach.", location="Fairbanks, AK",
              practice_name="Fairbanks Wellness", tagline="A tagline")
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" in html
    assert "<h2>How I work</h2>" in html
    assert "Fairbanks, AK" in html
    assert "Fairbanks Wellness" in html
    assert "A tagline" in html


def test_dark_mode_is_present():
    """The deleted JS shell carried a prefers-color-scheme: dark block. Its
    absence here would leave the practitioner page white while the rest of
    the portal follows the system theme."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "@media (prefers-color-scheme: dark)" in html
    assert "background:#121212" in html


def test_the_profit_disclosure_is_always_rendered():
    """It is a disclosure. It does not get to be conditional."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Your practitioner earns a portion of what you spend here." in html


def test_the_remaining_public_fields_render():
    """logo_url, services, accepting_clients and featured_products had no
    renderer in the JS shell. Covered here per-block; the whole-whitelist
    sweep lives in tests/test_public_surface_routes.py (Task 6) so there is
    exactly one place that knows the full field list."""
    v = _view(logo_url="https://cdn.example/logo.jpg",
              services=["sleep coaching"],
              featured_products=[{"name": "SentinelProduct", "price": "$10"}])
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "logo.jpg" in html
    assert "sleep coaching" in html
    assert "SentinelProduct" in html


def test_not_accepting_clients_says_so_rather_than_going_silent():
    """A False value is information. Rendering nothing would read as 'unknown'
    to a visitor deciding whether to reach out."""
    html = pr.render_page_html(_view(accepting_clients=False),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Not currently accepting new clients" in html


def test_accepting_clients_true_still_renders_its_line():
    html = pr.render_page_html(_view(accepting_clients=True),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Accepting new clients" in html
    assert "Not currently accepting new clients" not in html


def test_accepting_clients_none_renders_neither_claim():
    """CRITICAL fix: None means the practitioner never said, which is the
    default for every practitioner who has never authored a profile (see
    dashboard/public_surface.py::build_practitioner_storefront). Rendering
    either sentence for None would publish an availability claim nobody
    made."""
    html = pr.render_page_html(_view(accepting_clients=None),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Accepting new clients" not in html
    assert "Not currently accepting new clients" not in html
    assert '<p class="accepting">' not in html


def test_services_render_as_a_list():
    html = pr.render_page_html(_view(services=["sleep coaching", "nutrition"]),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "sleep coaching" in html and "nutrition" in html


CANON = "https://myhealingoasis.com/mary-boyd"


def test_open_graph_tags_are_present():
    v = _view(tagline="Helping nurses stop running on empty",
              practice_name="Fairbanks Wellness")
    html = pr.render_page_html(v, canonical_url=CANON)
    assert '<meta property="og:type" content="profile">' in html
    assert '<meta property="og:title" content="Mary Boyd — Fairbanks Wellness">' in html
    assert ('<meta property="og:description" content="Helping nurses stop '
            'running on empty">') in html
    assert f'<meta property="og:url" content="{CANON}">' in html
    assert '<meta property="og:site_name" content="Remedy Match">' in html


def test_og_image_is_present_only_when_there_is_a_photo():
    assert 'property="og:image"' not in pr.render_page_html(_view(), canonical_url=CANON)
    v = _view(photo_url="https://cdn.example/mary.jpg")
    html = pr.render_page_html(v, canonical_url=CANON)
    assert '<meta property="og:image" content="https://cdn.example/mary.jpg">' in html


def test_twitter_card_type_follows_the_photo():
    """summary_large_image with no image renders as an empty box."""
    assert ('<meta name="twitter:card" content="summary">'
            in pr.render_page_html(_view(), canonical_url=CANON))
    v = _view(photo_url="https://cdn.example/mary.jpg")
    assert ('<meta name="twitter:card" content="summary_large_image">'
            in pr.render_page_html(v, canonical_url=CANON))


def test_a_relative_photo_url_is_absolutized_for_the_share_tags():
    """sanitize_image_url deliberately permits a site-relative "/path" -- a
    legal input, not a bug. Facebook, iMessage and Slack all require an
    ABSOLUTE og:image; a relative one silently produces an imageless card,
    the same failure this whole feature exists to fix. The visible <img> in
    the body keeps the relative URL -- it resolves correctly in a browser --
    only the meta tags need the absolute form."""
    v = _view(photo_url="/uploads/mary.jpg")
    html = pr.render_page_html(v, canonical_url=CANON)
    assert ('<meta property="og:image" '
            'content="https://myhealingoasis.com/uploads/mary.jpg">') in html
    assert ('<meta name="twitter:image" '
            'content="https://myhealingoasis.com/uploads/mary.jpg">') in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    # the body <img> keeps the relative form -- it resolves fine in a browser
    assert '<img class="photo" src="/uploads/mary.jpg"' in html
    assert 'https://myhealingoasis.com/uploads/mary.jpg" alt' not in html


def test_a_relative_photo_url_with_no_canonical_base_omits_the_image_tags():
    """PORTAL_BASE_URL unset -> canonical_url is None -> there is no base to
    resolve a relative photo path against. Omit the image tags entirely
    rather than emit a broken relative og:image, matching the existing
    contract for canonical/og:url in this same situation."""
    v = _view(photo_url="/uploads/mary.jpg")
    html = pr.render_page_html(v, canonical_url=None)
    assert 'property="og:image"' not in html
    assert 'name="twitter:image"' not in html
    assert '<meta name="twitter:card" content="summary">' in html
    # the body <img> still renders -- only the meta tags are affected
    assert '<img class="photo" src="/uploads/mary.jpg"' in html


def test_canonical_link_uses_the_url_it_was_given():
    html = pr.render_page_html(_view(), canonical_url=CANON)
    assert f'<link rel="canonical" href="{CANON}">' in html


def test_og_and_meta_descriptions_agree():
    """Three descriptions from one builder. They must not drift."""
    v = _view(bio="I work with nurses and shift workers.")
    html = pr.render_page_html(v, canonical_url=CANON)
    d = pr.build_description(v)
    assert f'<meta name="description" content="{d}">' in html
    assert f'<meta property="og:description" content="{d}">' in html
    assert f'<meta name="twitter:description" content="{d}">' in html


def test_a_quote_in_the_canonical_url_cannot_break_the_attribute():
    """Scoped to the head's attribute tags, not the whole document: the
    JSON-LD block (added in Task 4) also carries this same URL, safely,
    as a JSON string inside a <script> element -- browsers only scan for a
    literal `</script` to end script content, so a raw quote or `>` inside
    it is inert. Checking the full html string would fail on that harmless
    occurrence instead of on an actual attribute break-out."""
    html = pr.render_page_html(_view(), canonical_url='https://x/"><script>')
    head, _, _ = html.partition('<script type="application/ld+json">')
    assert '"><script>' not in head


def _jsonld(html_str):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                  html_str, re.S)
    assert m, "no JSON-LD block"
    return json.loads(m.group(1))


def test_jsonld_is_person_plus_professional_service():
    data = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))
    assert [d["@type"] for d in data] == ["Person", "ProfessionalService"]


def test_jsonld_never_asserts_a_medical_practice():
    """Spec constraint: not MedicalBusiness, not Physician, no specialty.

    A bare 'banned string absent' check also passes if the JSON-LD block is
    removed from the page entirely, which would be a bigger regression than
    the one this test is meant to catch. Prove the block exists and carries
    the correct, allowed types first, then prove the banned ones are absent
    from it."""
    v = _view(bio="RN and health coach", practice_name="Fairbanks Wellness",
              services=["health coaching", "nutrition"])
    raw = pr.render_page_html(v, canonical_url=CANON)
    data = _jsonld(raw)
    assert [d["@type"] for d in data] == ["Person", "ProfessionalService"]
    assert data[1]["name"] == "Fairbanks Wellness"
    assert data[1]["serviceType"] == ["health coaching", "nutrition"]
    for banned in ("MedicalBusiness", "Physician", "medicalSpecialty",
                   "MedicalClinic", "Hospital"):
        assert banned not in raw


def test_jsonld_carries_name_url_and_description():
    v = _view(tagline="Helping nurses stop running on empty")
    person = _jsonld(pr.render_page_html(v, canonical_url=CANON))[0]
    assert person["name"] == "Mary Boyd"
    assert person["url"] == CANON
    assert person["description"] == "Helping nurses stop running on empty"


def test_jsonld_omits_absent_fields_rather_than_emitting_empty_ones():
    person = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))[0]
    assert "image" not in person
    assert "address" not in _jsonld(pr.render_page_html(_view(), canonical_url=CANON))[1]


def test_jsonld_includes_photo_and_location_when_present():
    v = _view(photo_url="https://cdn.example/mary.jpg", location="Fairbanks, AK",
              practice_name="Fairbanks Wellness", services=["health coaching"])
    data = _jsonld(pr.render_page_html(v, canonical_url=CANON))
    assert data[0]["image"] == "https://cdn.example/mary.jpg"
    assert data[1]["name"] == "Fairbanks Wellness"
    assert data[1]["address"] == "Fairbanks, AK"
    assert data[1]["serviceType"] == ["health coaching"]


def test_professional_service_falls_back_to_the_person_name():
    """A coach practising under their own name still gets a service entity."""
    data = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))
    assert data[1]["name"] == "Mary Boyd"


def test_jsonld_omits_url_when_canonical_url_is_falsy():
    """PORTAL_BASE_URL unset means the caller has no correct absolute URL to
    give. A wrong or relative `url` asserted to Google is worse than the
    field being absent -- same rule as the other optional fields above."""
    for missing in (None, ""):
        person, service = pr.build_jsonld(_view(), missing)
        assert "url" not in person
        assert "url" not in service
    person, service = pr.build_jsonld(_view(), CANON)
    assert person["url"] == CANON
    assert service["url"] == CANON


def test_canonical_and_og_url_are_omitted_when_canonical_url_is_falsy():
    """A relative canonical resolves in the BROWSER against the page's own
    host -- on the funnel host that silently becomes the exact funnel-host
    canonical the spec forbids. Omit both tags rather than emit either with
    no value or a bare path."""
    html = pr.render_page_html(_view(), canonical_url=None)
    assert 'rel="canonical"' not in html
    assert 'property="og:url"' not in html
    # everything else still renders
    assert "<h1>Mary Boyd</h1>" in html
    assert 'property="og:title"' in html
    assert '<meta name="robots" content="noindex">' in html


def test_a_closing_script_tag_in_the_data_cannot_break_out():
    v = _view(bio="</script><script>alert(1)</script>")
    raw = pr.render_page_html(v, canonical_url=CANON)
    assert "</script><script>alert(1)" not in raw
    _jsonld(raw)  # must still parse


def _script_body(html_str):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                  html_str, re.S)
    assert m, "no JSON-LD block"
    return m.group(1)


def test_no_raw_angle_bracket_survives_in_the_jsonld_script_body():
    """Closed-class guard, not a list of spellings.

    A regex that extracts "parseable JSON" from the document cannot catch a
    browser that has already been pushed into script data double escaped
    state: `<!--<script>` contains no `</` at all, so a guard that only
    escapes `</` never touches it, yet it still ends the <script> element
    early in a real browser (the block's own trailing </script> stops
    closing it once the tokenizer is in that state). Asserting that no raw
    `<` reaches the script body at all defeats that payload and every other
    spelling of the same class in one guard, rather than enumerating them.
    """
    v = _view(bio="</script><script>alert(1)</script>",
              tagline="<!--<script>",
              practice_name="<!--<script>",
              location="<!--<script>",
              services=["<!--<script>"])
    raw = pr.render_page_html(v, canonical_url=CANON)
    body = _script_body(raw)
    assert "<" not in body
    data = json.loads(body)
    assert data[0]["description"] == "<!--<script>"
    assert data[1]["address"] == "<!--<script>"
    assert data[1]["serviceType"] == ["<!--<script>"]


def test_a_non_string_field_value_renders_rather_than_raising():
    """Six call sites did `(view.get(k) or "").strip()` instead of
    `str(view.get(k) or "").strip()`: tagline, practice_name, photo_url,
    logo_url, location, bio. A non-string value for any of them -- a
    Postgres column drifting from its documented TEXT type, a stub payload
    upstream -- raised AttributeError on `.strip()` and turned one bad
    field into a 500 for the whole page. This exercises all six through the
    full render, including the JSON-LD block, which reads photo_url and
    location a second time after the string-coercion guard that protects
    the guard condition itself."""
    v = _view(tagline=42, practice_name=43, photo_url=44, logo_url=45,
              location=46, bio=47)
    html = pr.render_page_html(v, canonical_url=CANON)
    assert "42" in html  # tagline
    assert "43" in html  # practice_name
    assert 'src="44"' in html  # photo_url in the <img>
    assert 'src="45"' in html  # logo_url in the <img>
    assert "46" in html  # location
    assert "47" in html  # bio
    data = _jsonld(html)
    assert data[0]["image"] == "44"
    assert data[1]["address"] == "46"
