"""The remaining links now point at the public store: static pages, the email
footer, the chat's browse destination and the affiliate offer."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_browse_the_store_links_point_at_the_shop():
    for page in ("reorder.html", "subscription.html"):
        html = (ROOT / "static" / page).read_text()
        assert 'href="/shop"' in html
        assert 'href="https://remedymatch.com"' not in html


def test_email_footer_names_the_shop():  # [D4]
    from dashboard import tracking
    assert "https://myhealingoasis.com/shop" in tracking.SIGNATURE_TEXT
    assert "remedymatch.com/" not in tracking.SIGNATURE_TEXT
    assert "https://myhealingoasis.com/shop" in tracking.SIGNATURE_HTML
    assert "remedymatch.com/" not in tracking.SIGNATURE_HTML


def test_chat_browsing_entry_is_the_shop():  # [D5]
    from dashboard import legacy_store_links
    assert legacy_store_links.BROWSE_ENTRY_PATH == "/shop"


def test_shop_affiliate_offer_migrates_the_old_url_and_stays_idempotent(tmp_path, monkeypatch):
    import app as app_module

    log_db = tmp_path / "chat_log.db"
    monkeypatch.setattr(app_module, "LOG_DB", log_db)
    app_module._init_referral_tables()  # builds the schema against a temp db

    old_url = "https://remedymatch.com?utm_source={slug}&utm_medium=affiliate&utm_campaign=store"
    old_desc = ("Dr. Glen's full line of remedies and formulations at RemedyMatch.com. "
                "Share with anyone ready to start their protocol.")
    with sqlite3.connect(log_db) as cx:
        cx.execute("UPDATE affiliate_offers SET url_template=?, description=? "
                   "WHERE name='Shop for Remedies'", (old_url, old_desc))

    new_url = ("https://myhealingoasis.com/shop?utm_source={slug}"
              "&utm_medium=affiliate&utm_campaign=store")

    app_module._init_referral_tables()
    with sqlite3.connect(log_db) as cx:
        row = cx.execute("SELECT url_template, description FROM affiliate_offers "
                         "WHERE name='Shop for Remedies'").fetchone()
    assert row[0] == new_url
    assert "at myhealingoasis.com" in row[1]
    assert "at RemedyMatch.com" not in row[1]

    # Running it again against the already-migrated row must not change it further.
    app_module._init_referral_tables()
    with sqlite3.connect(log_db) as cx:
        row_again = cx.execute("SELECT url_template, description FROM affiliate_offers "
                               "WHERE name='Shop for Remedies'").fetchone()
    assert row_again == row
