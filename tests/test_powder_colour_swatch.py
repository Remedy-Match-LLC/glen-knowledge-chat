"""A powder product page shows its latest batch's reference colour (Glen, 2026-11-16).

Glen: "Also put the color on each product page for reference. It can be an average color
of the image rather than the original image." data/powder-colours.json holds one list of
batches per slug. The latest batch_date wins. A bad entry is skipped. No entry, no swatch.
"""
import json
import os
import re

import pytest

import app
from dashboard import powder_colours as PC

PAGE = open(os.path.join(os.path.dirname(__file__), "..", "static",
                         "begin-product.html"), encoding="utf-8").read()


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.fixture()
def colours(monkeypatch, tmp_path):
    """Point the loader at a temporary file the test writes."""
    path = tmp_path / "powder-colours.json"

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(PC, "_PATH", str(path))
    return write


def test_the_live_file_carries_the_first_two_batches():
    assert PC.colour_for("reverse-age") == {
        "hex": "#a37850", "batch_date": "2026-11-16",
        "label": "Reference colour, batch of 16 November 2026"}
    assert PC.colour_for("microbiome")["hex"] == "#8f8475"
    products = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                           "products.json")))["products"]
    live = json.load(open(PC._PATH))
    assert [s for s in live if not s.startswith("_") and s not in products] == []


def test_reverse_age_page_data_carries_the_swatch(client):
    d = client.get("/begin/product-page-data/reverse-age").get_json()
    assert d["powder_colour"] == {
        "hex": "#a37850", "batch_date": "2026-11-16",
        "label": "Reference colour, batch of 16 November 2026"}


def test_latest_batch_wins_whatever_the_order(colours, client):
    colours({"reverse-age": [
        {"hex": "#111111", "batch_date": "2026-11-16"},
        {"hex": "#222222", "batch_date": "2027-02-03"},
        {"hex": "#333333", "batch_date": "2026-12-01"}]})
    d = client.get("/begin/product-page-data/reverse-age").get_json()
    assert d["powder_colour"]["hex"] == "#222222"
    assert d["powder_colour"]["label"] == "Reference colour, batch of 3 February 2027"


@pytest.mark.parametrize("bad", [
    {"hex": "a37850", "batch_date": "2027-01-01"},
    {"hex": "#a3785", "batch_date": "2027-01-01"},
    {"hex": "#a37850ff", "batch_date": "2027-01-01"},
    {"hex": "#zz7850", "batch_date": "2027-01-01"},
    {"hex": "red", "batch_date": "2027-01-01"},
    {"hex": "#a37850\n", "batch_date": "2027-01-01"},
    {"hex": "#a37850", "batch_date": "2027-13-01"},
    {"hex": "#a37850", "batch_date": "1 Jan 2027"},
    {"hex": "#a37850"},
    "#a37850",
])
def test_a_bad_entry_is_skipped_not_raised(colours, bad):
    colours({"reverse-age": [{"hex": "#8f8475", "batch_date": "2026-11-16"}, bad]})
    assert PC.colour_for("reverse-age")["hex"] == "#8f8475"


def test_only_bad_entries_means_no_swatch(colours, client):
    colours({"reverse-age": [{"hex": "#nothex", "batch_date": "2026-11-16"}]})
    d = client.get("/begin/product-page-data/reverse-age").get_json()
    assert "powder_colour" not in d


def test_a_broken_file_means_no_swatch(monkeypatch, tmp_path, client):
    path = tmp_path / "powder-colours.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(PC, "_PATH", str(path))
    d = client.get("/begin/product-page-data/reverse-age").get_json()
    assert "powder_colour" not in d and d["slug"] == "reverse-age"


def test_a_product_with_no_entry_has_no_key(client):
    d = client.get("/begin/product-page-data/scar-silk").get_json()
    assert "powder_colour" not in d
    assert PC.colour_for("scar-silk") is None
    assert PC.colour_for("_readme") is None


def test_only_the_three_keys_leave_the_server(colours, client):
    colours({"reverse-age": [{"hex": "#A37850", "batch_date": "2026-11-16",
                              "photo": "/private/batch.jpg", "note": "internal"}]})
    d = client.get("/begin/product-page-data/reverse-age").get_json()
    assert d["powder_colour"] == {"hex": "#a37850", "batch_date": "2026-11-16",
                                  "label": "Reference colour, batch of 16 November 2026"}


def test_the_label_is_sentence_case_with_no_dash():
    from datetime import date
    for d in (date(2026, 11, 16), date(2027, 1, 1), date(2027, 5, 31)):
        label = PC.label_for(d)
        assert label.startswith("Reference colour, batch of ")
        words = label.split()
        assert words[0] == "Reference"
        # Only the first word and the month name are capitalised.
        caps = [w for w in words if w[0].isupper()]
        assert caps == ["Reference", PC._MONTHS[d.month - 1]], label
        assert label != label.upper()
        assert "—" not in label and "–" not in label


def test_the_page_renders_the_swatch_only_from_page_data():
    # The swatch element starts hidden, so a product with no colour looks as before.
    assert re.search(r'class="p-colour hidden" id="sp-colour"', PAGE)
    assert "data.powder_colour" in PAGE
    # The page re-checks the hex before it touches style, and writes the label as text.
    assert "/^#[0-9a-fA-F]{6}$/.test(pcol.hex" in PAGE
    assert "getElementById('sp-colour-label').textContent = pcol.label" in PAGE
    # The border is a theme token, so a dark swatch shows on the dark surface.
    rule = re.search(r"\.p-colour-chip\{[^}]*\}", PAGE).group(0)
    assert "border:1px solid var(--muted)" in rule
    assert "REFERENCE COLOUR" not in PAGE
    assert "uppercase" not in re.search(r"\.p-colour\{[^}]*\}", PAGE).group(0)
