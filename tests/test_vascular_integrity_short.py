"""/vascular-integrity sends visitors to the Vascular Integrity product page (Glen, 2026-10-07)."""
import os

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import app


def test_the_short_address_lands_on_the_live_product_page():
    c = app.app.test_client()
    r = c.get("/vascular-integrity")
    assert r.status_code == 302
    target = r.headers["Location"]
    assert target.endswith("/begin/product/vitamin-p-polyphenols")
    slug = target.rsplit("/", 1)[1]
    p = app._get_product(slug)
    assert p and p["name"].startswith("Vascular Integrity")
    assert c.get("/begin/product/vitamin-p-polyphenols").status_code == 200


def test_tracking_tags_carry_over():
    r = app.app.test_client().get("/vascular-integrity?utm_source=email&utm_campaign=x")
    assert r.headers["Location"].endswith("/begin/product/vitamin-p-polyphenols?utm_source=email&utm_campaign=x")
