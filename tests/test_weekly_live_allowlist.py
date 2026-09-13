"""The weekly live invitation mails only the fingerprinted filter_audience.py list.

2026-09-13: the Sunday run blocked itself because the sender builds its own
audience and could not be limited to the 959 addresses filter_audience.py
returned. These tests drive run() itself, so deleting the filter at the call
site turns them red, not only the helper's own tests.
"""
import importlib.util
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import live_invitation_allowlist as allowlist

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")

KEY = "test-console-secret"


def _weekly():
    spec = importlib.util.spec_from_file_location(
        "weekly_live_invitation_allowlist_under_test",
        Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the argument itself ──────────────────────────────────────────────────────

def test_round_trip_deduplicates_and_normalises():
    argument, stats = allowlist.encode(
        ["a@example.com", " A@Example.com ", "b@example.com", "", "not-an-email"], KEY)
    assert stats == {"unique_addresses": 2, "invalid_lines": 1, "fingerprints": 2}
    assert argument.startswith("v1.2.")
    prints = allowlist.decode(argument)
    assert allowlist.allows(prints, "A@EXAMPLE.COM", KEY)
    assert allowlist.allows(prints, "b@example.com", KEY)
    assert not allowlist.allows(prints, "c@example.com", KEY)


def test_argument_carries_no_address():
    argument, _ = allowlist.encode(["private.person@example.com"], KEY)
    assert "private" not in argument and "example" not in argument


def test_missing_key_refuses(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="CONSOLE_SECRET is missing"):
        allowlist.encode(["a@example.com"])


def test_truncated_argument_refuses():
    argument, _ = allowlist.encode(
        [f"person{i}@example.com" for i in range(50)], KEY)
    with pytest.raises(RuntimeError, match="count does not match"):
        allowlist.decode(argument[:-12])


def test_declared_count_must_match():
    argument, _ = allowlist.encode(["a@example.com", "b@example.com"], KEY)
    _, _, blob = argument.split(".", 2)
    with pytest.raises(RuntimeError, match="count does not match"):
        allowlist.decode(f"v1.3.{blob}")


@pytest.mark.parametrize("bad", ["", "v1", "v1.x.abc", "v2.1.AAAAAAAAAAA="])
def test_malformed_argument_refuses(bad):
    with pytest.raises(RuntimeError):
        allowlist.decode(bad)


def test_empty_list_refuses():
    with pytest.raises(RuntimeError, match="empty"):
        allowlist.decode("v1.0.")


def test_a_list_built_with_another_key_matches_nobody():
    argument, _ = allowlist.encode(["a@example.com"], "some-other-key")
    assert not allowlist.allows(allowlist.decode(argument), "a@example.com", KEY)


def test_sender_normalises_exactly_as_the_list_does():
    weekly = _weekly()
    for raw in ["A@B.co", "  x@y.org ", "no-at-sign", "", None, "a b@c.d"]:
        assert weekly._email(raw) == allowlist.normalize_email(raw)


# ── run(), the caller ────────────────────────────────────────────────────────

AUDIENCE = {
    "listed@example.com": {"id": "c1", "email": "listed@example.com", "firstName": "Lee"},
    "unlisted@example.com": {"id": "c2", "email": "unlisted@example.com", "firstName": "Una"},
    "listed.two@example.com": {"id": "c3", "email": "listed.two@example.com"},
}
PAID_OUTSIDE_LIST = "paid.not.listed@example.com"


def _wire(monkeypatch, tmp_path, weekly):
    from dashboard import unsubscribe
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    monkeypatch.setenv("CONSOLE_SECRET", KEY)
    monkeypatch.setattr(weekly.appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(weekly, "_event_gate", lambda d: {"ok": True, "issues": []})
    monkeypatch.setattr(weekly, "_contacts_by_tag", lambda tag: (
        list(AUDIENCE.values()) if tag == "e4l account" else []))
    monkeypatch.setattr(weekly, "_authoritative_access_sets",
                        lambda: ({PAID_OUTSIDE_LIST}, set()))
    looked_up = []
    monkeypatch.setattr(weekly, "_find_contact",
                        lambda email: looked_up.append(email) or None)
    monkeypatch.setattr(weekly, "_create_contact",
                        lambda email, name="": looked_up.append(email) or None)
    monkeypatch.setattr(weekly.client_portal, "ensure_token", lambda cx, email, name: "tok")
    monkeypatch.setattr(weekly.email_suppression, "is_suppressed", lambda cx, email: False)
    monkeypatch.setattr(weekly.time, "sleep", lambda s: None)
    sent = []

    def fake_send(contact_id, subject, text, body_html, *, email_to="",
                  scheduled_timestamp=None):
        sent.append(email_to)
        return 201, f"msg-{len(sent)}", {}

    monkeypatch.setattr(weekly, "_send", fake_send)
    return sent, looked_up


def _args(**kw):
    base = dict(date="2026-09-16", dry_run=False, send=True, only_list=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_send_mails_only_listed_audience_members(monkeypatch, tmp_path, capsys):
    weekly = _weekly()
    sent, looked_up = _wire(monkeypatch, tmp_path, weekly)
    argument, _ = allowlist.encode(
        ["listed@example.com", "LISTED.TWO@example.com", "not.in.ghl@example.com"], KEY)

    assert weekly.run(_args(only_list=argument)) == 0

    assert sorted(sent) == ["listed.two@example.com", "listed@example.com"]
    # A paid member outside the list is neither looked up nor created in GHL,
    # so an unrelated missing contact cannot block the send.
    assert PAID_OUTSIDE_LIST not in looked_up
    out = capsys.readouterr().out
    assert '"allowlist_matched": 2' in out
    assert '"allowlist_not_in_audience": 1' in out
    assert '"outside_allowlist_skipped": 1' in out


def test_send_without_the_list_refuses_before_reading_any_contact(monkeypatch, tmp_path):
    weekly = _weekly()
    sent, _ = _wire(monkeypatch, tmp_path, weekly)
    gate_calls = []
    monkeypatch.setattr(weekly, "_event_gate",
                        lambda d: gate_calls.append(d) or {"ok": True, "issues": []})
    read = []
    monkeypatch.setattr(weekly, "_contacts_by_tag", lambda tag: read.append(tag) or [])

    with pytest.raises(RuntimeError, match="--send requires --only-list"):
        weekly.run(_args())

    assert (gate_calls, read, sent) == ([], [], [])


def test_send_with_a_list_matching_nobody_refuses(monkeypatch, tmp_path):
    weekly = _weekly()
    sent, _ = _wire(monkeypatch, tmp_path, weekly)
    argument, _ = allowlist.encode(["listed@example.com"], "wrong-key")

    with pytest.raises(RuntimeError, match="no audience member matches"):
        weekly.run(_args(only_list=argument))
    assert sent == []


def test_dry_run_reports_list_counts_and_sends_nothing(monkeypatch, tmp_path, capsys):
    weekly = _weekly()
    sent, _ = _wire(monkeypatch, tmp_path, weekly)
    argument, _ = allowlist.encode(["listed@example.com"], KEY)

    assert weekly.run(_args(send=False, dry_run=True, only_list=argument)) == 0

    out = capsys.readouterr().out
    assert '"status": "DRY_RUN"' in out
    assert '"allowlist_size": 1' in out and '"allowlist_matched": 1' in out
    assert sent == []


def test_family_plan_members_without_a_membership_row_reach_the_paid_set(monkeypatch, tmp_path):
    # 2026-09-13: 2 of 3 family-plan members had no memberships row, so the sender
    # never checked them and would have told paying members Group Coaching was an
    # upgrade. Seeded through the modules' own writers, not a hand-typed schema.
    import sys
    import types
    from contextlib import contextmanager
    from dashboard import db, family_plan, household

    weekly = _weekly()
    path = str(tmp_path / "chat_log.db")
    with db.connect(path) as cx:
        cx.execute("CREATE TABLE memberships (email TEXT)")
        cx.execute("INSERT INTO memberships VALUES ('Row.Member@example.com')")
        family_plan.init_family_plan_table(cx)
        household.init_household_tables(cx)
        family_plan.activate(cx, "holder@example.com", next_charge_at=None, source="comp")
        household.add_member(cx, "holder@example.com", "covered@example.com")
        family_plan.activate(cx, "lapsed@example.com", next_charge_at=None, source="comp")
        family_plan.set_status(cx, "lapsed@example.com", "canceled")
        cx.commit()
    monkeypatch.setattr(weekly.appmod, "LOG_DB", path)
    checked = []
    monkeypatch.setattr(weekly.appmod, "_is_paid_member",
                        lambda email: checked.append(email) or True)

    class _Cursor:
        def execute(self, *args):
            pass

        def fetchall(self):
            return []

    @contextmanager
    def fake_cursor():
        yield _Cursor()

    monkeypatch.setitem(sys.modules, "db_supabase",
                        types.SimpleNamespace(supabase_cursor=fake_cursor))

    paid, certification = weekly._authoritative_access_sets()

    assert {"row.member@example.com", "holder@example.com",
            "covered@example.com"} <= paid
    assert "lapsed@example.com" not in checked
    assert certification == set()


def test_cli_prints_the_argument_and_no_address(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONSOLE_SECRET", KEY)
    listing = tmp_path / "mailable.txt"
    listing.write_text("a@example.com\nA@example.com\nb@example.com\n")

    assert allowlist.main(["x", str(listing)]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip().startswith("v1.2.")
    assert "unique addresses 2" in captured.err
    assert "example.com" not in captured.out + captured.err
