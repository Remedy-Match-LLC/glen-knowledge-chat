"""People hub Phase 2 — classifier + opted-in GHL mirror.

Pure classifier rule tests + queue-mirror tests. LOG_DB/CONSOLE_SECRET
monkeypatch pattern, like test_people_feeders.py.
"""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest


def _app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    # Importing app builds OpenAI + Pinecone clients, so without these the whole
    # file SKIPPED in the secretless CI. Twenty green skips look identical to
    # twenty passes and guard nothing. The clients construct fine with dummy keys
    # and no test here ever calls them.
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable in this env: {e}")


# ── _classify_person (pure) ───────────────────────────────────────────────────

def test_orderer_is_client_opted_in():
    add = _app()._classify_person({"tags": [], "order_count": 2, "pb_id": ""})
    assert "type:client" in add and "consent:opted-in" in add


def test_pb_member_is_client_opted_in():
    add = _app()._classify_person({"tags": [], "order_count": 0, "pb_id": "pb123"})
    assert add == {"type:client", "consent:opted-in"}


def test_bare_lead_is_prospect_cold():
    add = _app()._classify_person({"tags": [], "order_count": 0, "pb_id": ""})
    assert add == {"type:prospect", "consent:cold-no-consent"}


def test_tos_agreed_lead_is_opted_in():
    add = _app()._classify_person({"tags": [], "order_count": 0, "pb_id": ""}, tos_agreed=True)
    assert add == {"type:prospect", "consent:opted-in"}


def test_optin_tag_makes_opted_in():
    add = _app()._classify_person({"tags": ["newsletter-opt-in"], "order_count": 0, "pb_id": ""})
    assert "consent:opted-in" in add


def test_suppression_overrides_optin():
    # bounced/unsubscribed must never be opted-in, even with an opt-in tag
    add = _app()._classify_person(
        {"tags": ["newsletter-opt-in", "email bounced"], "order_count": 0, "pb_id": ""})
    assert "consent:cold-no-consent" in add and "consent:opted-in" not in add


def test_existing_consent_not_downgraded_or_duplicated():
    # already has a consent tag -> classifier adds no consent tag
    add = _app()._classify_person(
        {"tags": ["type:practitioner-cold", "consent:cold-no-consent"], "order_count": 0, "pb_id": ""})
    assert not any(t.startswith("consent:") for t in add)
    assert "type:prospect" not in add  # already has a type


def test_client_tag_is_client_opted_in():
    a = _app()
    for tag in ("client", "nes client", "VIP client"):
        add = a._classify_person({"tags": [tag], "order_count": 0, "pb_id": ""})
        assert "type:client" in add and "consent:opted-in" in add, tag


def test_client_substring_false_positive_excluded():
    # 'client-intake-form' is not a client relationship
    add = _app()._classify_person({"tags": ["client-intake-form"], "order_count": 0, "pb_id": ""})
    assert "type:client" not in add


def test_typed_contact_with_order_also_gets_client():
    add = _app()._classify_person(
        {"tags": ["type:pr-media", "consent:cold-no-consent"], "order_count": 1, "pb_id": ""})
    assert "type:client" in add


# ── mirror (queue) ────────────────────────────────────────────────────────────

@pytest.fixture
def app_db(monkeypatch, tmp_path):
    app = _app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    app._init_people_table()
    with sqlite3.connect(db) as cx:
        app._bos_ghl_queue.init_ghl_queue_table(cx)
        cx.commit()
    return app, db


def _seed(app, people):
    c = app.app.test_client()
    c.post("/api/people?merge_tags=1", json=people, headers={"X-Console-Key": "testkey"})


def _queue(db):
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return [dict(r) for r in cx.execute(
            "SELECT op,email,payload_json FROM ghl_write_queue WHERE status='pending'").fetchall()]


def test_only_opted_in_get_enqueued(app_db):
    app, db = app_db
    _seed(app, [
        {"email": "client@x.com", "tags": ["type:client", "consent:opted-in"]},
        {"email": "cold@x.com", "tags": ["type:prospect", "consent:cold-no-consent"]},
    ])
    s = app.sync_people_to_ghl()
    assert s["enqueued"] == 1
    q = _queue(db)
    assert len(q) == 1 and q[0]["op"] == "tag_add" and q[0]["email"] == "client@x.com"
    assert json.loads(q[0]["payload_json"])["tags"] == ["type:client"]


def test_mirror_is_idempotent(app_db):
    app, db = app_db
    _seed(app, [{"email": "c@x.com", "tags": ["type:client", "consent:opted-in"]}])
    app.sync_people_to_ghl()
    app.sync_people_to_ghl()
    assert len(_queue(db)) == 1  # no duplicate pending op


def test_opted_in_without_type_is_skipped(app_db):
    app, db = app_db
    _seed(app, [{"email": "notype@x.com", "tags": ["consent:opted-in"]}])
    s = app.sync_people_to_ghl()
    assert s["enqueued"] == 0 and s["skipped_no_type"] == 1
    assert _queue(db) == []


# ── a cold row must be upgradable when evidence turns up later ────────────────
#
# `consent:cold-no-consent` is the ELSE branch of this classifier. It fires once,
# because the guard was "has any consent tag at all", and then nothing ever looked
# again. Measured on the live hub 2026-09-11: 7,992 rows carry it, and 363 of them
# hold an E4L account, a Practice Better membership, a client tag or a scan. 125 of
# those are in the weekly invitation audience.
#
# One of the classifier's three opt-in tests also cannot pass. `has_commerce` reads
# order_count, which is 0 for every row including 200 already marked opted-in, and
# _is_client_tag carries a comment saying so.
#
# So a cold row is now revisitable. Only upward, and only to opted-in.

def test_a_cold_row_is_upgraded_when_a_client_tag_appears():
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent", "nes client"], "order_count": 0, "pb_id": ""})
    assert "consent:opted-in" in add


def test_a_cold_row_is_upgraded_when_a_practice_better_id_appears():
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent"], "order_count": 0, "pb_id": "pb123"})
    assert "consent:opted-in" in add


def test_a_cold_row_is_upgraded_when_they_agree_to_terms():
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent"], "order_count": 0, "pb_id": ""}, tos_agreed=True)
    assert "consent:opted-in" in add


def test_a_cold_row_with_still_no_evidence_is_left_exactly_as_it_was():
    """6,905 scraped practitioner rows are correctly cold. Adding the tag again
    would be churn, and re-adding it is not the same as leaving it alone."""
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent"], "order_count": 0, "pb_id": ""})
    assert "consent:opted-in" not in add
    assert "consent:cold-no-consent" not in add, "re-stamped a tag that is already there"


def test_a_bounced_cold_row_is_never_upgraded():
    """An email-negative signal outranks the evidence. This is the one that would
    put mail back in front of someone who should not get it."""
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent", "nes client", "email bounced"],
         "order_count": 0, "pb_id": ""})
    assert "consent:opted-in" not in add


def test_an_unsubscribed_row_is_never_upgraded():
    """consent:unsubscribed means the row is no longer ONLY cold, so the upgrade
    branch must not open. _collapse_consent is the second line of defence, not the
    first."""
    add = _app()._classify_person(
        {"tags": ["consent:cold-no-consent", "consent:unsubscribed", "nes client"],
         "order_count": 0, "pb_id": ""})
    assert "consent:opted-in" not in add


def test_an_already_opted_in_row_is_still_left_alone():
    """The control. The widened guard must not start rewriting settled rows."""
    add = _app()._classify_person(
        {"tags": ["consent:opted-in", "nes client"], "order_count": 3, "pb_id": ""})
    assert not any(t.startswith("consent:") for t in add)
