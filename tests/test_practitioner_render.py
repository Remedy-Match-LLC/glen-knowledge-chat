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


def test_absent_blocks_emit_nothing():
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" not in html
    assert "<h2>How I work</h2>" not in html
    assert "<img" not in html


def test_present_blocks_are_labelled():
    v = _view(bio="About me.", how_i_work="My approach.", location="Fairbanks, AK",
              practice_name="Fairbanks Wellness", tagline="A tagline")
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" in html
    assert "<h2>How I work</h2>" in html
    assert "Fairbanks, AK" in html
    assert "Fairbanks Wellness" in html
    assert "A tagline" in html


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
    html = pr.render_page_html(_view(), canonical_url='https://x/"><script>')
    assert '"><script>' not in html
