"""Counting affiliates who actually did something.

307 signups, all 'approved' because that is the column default, 11 attribution records
between them, and many addresses of the shape bots generate. This counts the actions a
bot cannot fake: a profile, a photo, pricing, a social link, an earning.

Read only. It must never exclude anyone, only count. Narrowing who earns is a separate
and riskier decision: a genuine referrer who never finished their profile would silently
stop being paid, which is the opposite mistake from paying bots and much harder to spot.
"""
import sqlite3
import pytest
from dashboard import affiliate_activity as aa

DDL = """
CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT, slug TEXT, status TEXT);
CREATE TABLE practitioner_profile_drafts (practitioner_id TEXT, fields TEXT, status TEXT);
CREATE TABLE practitioner_pricing (practitioner_id TEXT, slug TEXT, price_cents INTEGER);
CREATE TABLE affiliate_social_links (slug TEXT, url TEXT);
CREATE TABLE affiliate_earnings (email TEXT, amount_cents INTEGER);
CREATE TABLE intake_responses (email TEXT PRIMARY KEY, form_version TEXT, status TEXT,
  answers_json TEXT, created_at TEXT, submitted_at TEXT);
CREATE TABLE query_log (id INTEGER PRIMARY KEY, ts TEXT, query TEXT, email TEXT);
CREATE TABLE e4l_accounts (email TEXT PRIMARY KEY, created_at TEXT);
"""


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.executescript(DDL)
    for i in range(1, 6):
        c.execute("INSERT INTO affiliate_signups VALUES (?,?,?,'approved')",
                  (i, f"a{i}@x.com", f"slug{i}"))
    c.commit()
    return c


def test_signups_alone_count_as_nothing(cx):
    """The whole point. A row in the signup table is what a bot produces."""
    s = aa.summary(cx)
    assert s["approved_signups"] == 5
    assert s["any_sign_of_setup"] == 0


def test_a_profile_counts(cx):
    cx.execute("INSERT INTO practitioner_profile_drafts VALUES ('7','{\"bio\":\"hi\"}','review')")
    cx.commit()
    s = aa.summary(cx)
    assert s["started_a_profile"] == 1
    # deliberately NOT in any_sign_of_setup: practitioner_id resolves through a
    # different database and cannot be joined to an affiliate here
    assert s["any_sign_of_setup"] == 0


def test_a_photo_is_counted_separately(cx):
    cx.execute("INSERT INTO practitioner_profile_drafts VALUES ('7','{\"photo_url\":\"x\"}','review')")
    cx.execute("INSERT INTO practitioner_profile_drafts VALUES ('8','{\"bio\":\"hi\"}','review')")
    cx.commit()
    s = aa.summary(cx)
    assert s["started_a_profile"] == 2
    assert s["profile_has_photo"] == 1, "photo is the strongest single signal; count it alone"


def test_pricing_and_social_links_count(cx):
    cx.execute("INSERT INTO practitioner_pricing VALUES ('9','slug9',6997)")
    cx.execute("INSERT INTO affiliate_social_links VALUES ('slug2','https://x')")
    cx.commit()
    s = aa.summary(cx)
    assert s["set_their_pricing"] == 1
    assert s["added_a_social_link"] == 1
    assert s["any_sign_of_setup"] == 1   # only the slug-keyed social link counts


def test_a_missing_table_is_reported_not_counted_as_zero(cx):
    cx.execute("DROP TABLE affiliate_social_links")
    cx.commit()
    assert aa.summary(cx)["added_a_social_link"] == "table absent", (
        "a missing table must say so; counting it as zero makes a broken read look "
        "identical to an empty one"
    )


def test_it_only_reads(cx):
    before = cx.execute("SELECT COUNT(*) FROM affiliate_signups").fetchone()[0]
    aa.summary(cx)
    assert cx.execute("SELECT COUNT(*) FROM affiliate_signups").fetchone()[0] == before
    src = (__import__("pathlib").Path(aa.__file__)).read_text()
    for w in ("INSERT", "UPDATE", "DELETE", "DROP"):
        assert w not in src.upper().replace("INSERTED", ""), f"{w} in a read-only counter"


def test_any_data_in_their_intake_counts(cx):
    """Glen's ruling: ANY data entered counts, not only a submitted form. Starting it is
    already more than a bot does."""
    cx.execute("INSERT INTO intake_responses VALUES "
               "('a1@x.com','v1','draft','{\"goal\":\"sleep\"}','2026-01-01',NULL)")
    cx.commit()
    s = aa.summary(cx)
    assert s["started_their_intake"] == 1
    assert s["submitted_their_intake"] == 0
    assert s["any_sign_of_setup"] == 1


def test_an_empty_intake_row_does_not_count(cx):
    """A row created by opening the page is not data entered."""
    for blank in ("", "{}"):
        cx.execute("DELETE FROM intake_responses")
        cx.execute("INSERT INTO intake_responses VALUES ('a1@x.com','v1','draft',?,'x',NULL)",
                   (blank,))
        cx.commit()
        assert aa.summary(cx)["started_their_intake"] == 0, f"{blank!r} counted as data"


def test_a_non_affiliates_intake_is_not_counted(cx):
    cx.execute("INSERT INTO intake_responses VALUES "
               "('stranger@x.com','v1','submitted','{\"a\":1}','x','y')")
    cx.commit()
    assert aa.summary(cx)["started_their_intake"] == 0


def test_chat_use_counts(cx):
    cx.execute("INSERT INTO query_log VALUES (1,'t','hello?','a1@x.com')")
    cx.execute("INSERT INTO query_log VALUES (2,'t','again','a1@x.com')")
    cx.commit()
    s = aa.summary(cx)
    assert s["has_used_the_chat"] == 1, "two messages from one person is one person"
    assert s["any_sign_of_setup"] == 1


def test_an_e4l_account_counts(cx):
    cx.execute("INSERT INTO e4l_accounts VALUES ('a3@x.com','2026-01-01')")
    cx.commit()
    assert aa.summary(cx)["has_an_e4l_account"] == 1


def test_chat_and_e4l_from_a_stranger_are_not_counted(cx):
    cx.execute("INSERT INTO query_log VALUES (1,'t','hi','stranger@x.com')")
    cx.execute("INSERT INTO e4l_accounts VALUES ('stranger@x.com','x')")
    cx.commit()
    s = aa.summary(cx)
    assert s["has_used_the_chat"] == 0
    assert s["has_an_e4l_account"] == 0


def test_one_person_with_several_signals_is_still_one(cx):
    """any_sign_of_setup counts PEOPLE, not signals. Someone who chatted, has an E4L
    account and started an intake must not appear three times."""
    cx.execute("INSERT INTO query_log VALUES (1,'t','hi','a1@x.com')")
    cx.execute("INSERT INTO e4l_accounts VALUES ('a1@x.com','x')")
    cx.execute("INSERT INTO intake_responses VALUES ('a1@x.com','v1','draft','{\"g\":1}','x',NULL)")
    cx.commit()
    assert aa.summary(cx)["any_sign_of_setup"] == 1


def test_a_person_with_two_kinds_of_signal_is_counted_once(cx):
    """The bug this file exists to prevent recurring. An earlier version kept
    practitioner-keyed and slug-keyed signals in separate sets and added the lengths, so
    one person with a profile AND a chat record counted twice, and the production total
    came back inflated. Everything now resolves to the affiliate slug."""
    cx.execute("INSERT INTO practitioner_profile_drafts VALUES ('1','{\"photo_url\":\"x\"}','review')")
    cx.execute("INSERT INTO query_log VALUES (1,'t','hi','a1@x.com')")
    cx.execute("INSERT INTO e4l_accounts VALUES ('a1@x.com','x')")
    cx.commit()
    assert aa.summary(cx)["any_sign_of_setup"] == 1, "one person counted more than once"


def test_two_different_people_are_counted_twice(cx):
    """The other direction: deduplicating must not collapse distinct people."""
    cx.execute("INSERT INTO query_log VALUES (1,'t','hi','a1@x.com')")
    cx.execute("INSERT INTO e4l_accounts VALUES ('a2@x.com','x')")
    cx.commit()
    assert aa.summary(cx)["any_sign_of_setup"] == 2
