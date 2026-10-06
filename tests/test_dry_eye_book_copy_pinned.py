"""The Dry Eye Relief book page carries Glen's approved copy, pinned.

Glen approved it in the marketing tab on 2026-09-30:
marketing/04 Copy/dry-eye-relief-book-page-2026-09-30.md, section "Description".
Until then the page showed only the name and $19.97, and 14 of week 2's emails link to it.
Pinned as the Reverse Aging Program is (#1809), so no AI draft replaces it.
"""
import importlib
import json

import pytest

SLUG = "book-dry-eye-relief"
APPROVED = (
    "Dry Eye Relief: Natural Medicine for Accelerated Self-Healing is volume 5 of Dr. Glen "
    "Swartwout's Natural Vision & Eye Care series. Your cornea has more nerve endings in each "
    "square millimeter than anywhere else in your body, and it is the most exposed moist surface "
    "you have. That is why dry eye symptoms are so hard to ignore. Your tears depend on water, "
    "oil and protein working together, and that balance responds to your environment, outside "
    "and inside. This book is about the inside: supporting the whole body so the front of your "
    "eye has the support it needs. It covers dozens of natural remedies associated with the "
    "causes underneath dry eyes. Which of them fit differs from person to person, and it can "
    "change from month to month for the same person. The book is a resource in that process. It "
    "is a reference book, not a substitute for an eye exam. A sudden change in your vision or "
    "severe eye pain needs care right away, and eyes that stay red or irritated are a reason to "
    "see your eye doctor. 103 pages, 9 by 6 inch paperback. The ebook is sold separately.")


@pytest.fixture(scope="module")
def entry():
    return json.load(open("data/products.json"))["products"][SLUG]


def test_the_book_carries_the_approved_copy_word_for_word(entry):
    assert entry["description"] == APPROVED
    assert entry["intro"] == APPROVED
    assert "—" not in entry["description"]


def test_the_copy_is_pinned(entry):
    assert {"intro", "description", "research"} <= set(entry["copy_pinned"])


def test_price_and_type_unchanged(entry):
    assert entry["price_cents"] == 2000  # Glen, 2026-10-06: sells at $20, SRP $30
    assert entry["bottle_type"] == "book"


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    # With AI copy on (as in prod), an unpinned section is marked "ai" and replaced.
    monkeypatch.setattr(appmod, "_SALES_AI_COPY_ENABLED", True)
    return appmod


def test_the_page_serves_the_pinned_copy(a):
    data = a.app.test_client().get("/begin/product-page-data/" + SLUG).get_json()
    secs = {s["id"]: s for s in data["sections"]}
    for sid in ("intro", "description"):
        assert secs[sid]["body"] == APPROVED
        assert "ai" not in secs[sid], sid
    if "research" in secs:
        assert "ai" not in secs["research"]
