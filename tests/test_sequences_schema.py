"""Sequence definitions: schema and upsert.

Slice 2 of docs/superpowers/specs/2026-08-30-sequence-engine-design.md. This
stores what a sequence IS. Nothing here sends, enrolls, or schedules; the runner
is slice 3.

`delay_days` is measured from enrollment, cumulatively, not from the previous
step. That is the whole reason a push can be re-run safely: editing step 3's
delay cannot silently shift steps 4 and 5 for someone already mid-flight.
"""
import pytest

from dashboard import db, sequences


@pytest.fixture()
def cx():
    c = db.connect(":memory:")
    sequences.init_tables(c)
    return c


STEPS = [
    {"step_no": 1, "subject": "A quiet check-in", "body_md": "Aloha,", "delay_days": 0},
    {"step_no": 2, "subject": "The quiet multiplier", "body_md": "Body two", "delay_days": 4},
    {"step_no": 3, "subject": "Do your remedies match?", "body_md": "Body three", "delay_days": 10},
]


def test_init_is_idempotent(cx):
    sequences.init_tables(cx)
    sequences.init_tables(cx)
    assert sequences.get(cx, "nurture") is None


def test_upsert_creates_a_sequence_and_its_steps(cx):
    sequences.upsert(cx, slug="nurture", name="Nurture Follow-Up",
                     trigger_kind="on_contact_created", active=False, steps=STEPS)
    seq = sequences.get(cx, "nurture")
    assert seq["name"] == "Nurture Follow-Up"
    assert seq["trigger_kind"] == "on_contact_created"
    assert seq["active"] is False
    assert [s["step_no"] for s in seq["steps"]] == [1, 2, 3]
    assert seq["steps"][2]["delay_days"] == 10


def test_upsert_is_idempotent_and_does_not_duplicate_steps(cx):
    for _ in range(3):
        sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                         active=False, steps=STEPS)
    assert len(sequences.get(cx, "nurture")["steps"]) == 3


def test_editing_copy_updates_in_place(cx):
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     active=False, steps=STEPS)
    edited = [dict(s) for s in STEPS]
    edited[1]["subject"] = "Rewritten subject"
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     active=False, steps=edited)
    assert sequences.get(cx, "nurture")["steps"][1]["subject"] == "Rewritten subject"


def test_removing_a_step_removes_it_from_the_stored_sequence(cx):
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     active=False, steps=STEPS)
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     active=False, steps=STEPS[:2])
    assert [s["step_no"] for s in sequences.get(cx, "nurture")["steps"]] == [1, 2]


def test_a_sequence_defaults_to_inactive(cx):
    sequences.upsert(cx, slug="new", name="New", trigger_kind="manual", steps=STEPS)
    assert sequences.get(cx, "new")["active"] is False


def test_steps_must_start_at_one_and_be_contiguous(cx):
    # A gap means a step file was deleted or misnamed. Refuse rather than store a
    # sequence whose day-4 email silently never exists.
    with pytest.raises(ValueError, match="contiguous"):
        sequences.upsert(cx, slug="bad", name="Bad", trigger_kind="manual",
                         steps=[{"step_no": 1, "subject": "a", "body_md": "x", "delay_days": 0},
                                {"step_no": 3, "subject": "c", "body_md": "z", "delay_days": 8}])


def test_delays_must_not_go_backwards(cx):
    # Offsets are from enrollment, so a later step with a smaller delay would
    # arrive before the one before it.
    with pytest.raises(ValueError, match="delay"):
        sequences.upsert(cx, slug="bad", name="Bad", trigger_kind="manual",
                         steps=[{"step_no": 1, "subject": "a", "body_md": "x", "delay_days": 5},
                                {"step_no": 2, "subject": "b", "body_md": "y", "delay_days": 2}])


def test_a_sequence_with_no_steps_is_refused(cx):
    with pytest.raises(ValueError, match="at least one step"):
        sequences.upsert(cx, slug="empty", name="Empty", trigger_kind="manual", steps=[])


def test_list_all_returns_slugs_and_step_counts(cx):
    sequences.upsert(cx, slug="a", name="A", trigger_kind="manual", steps=STEPS)
    sequences.upsert(cx, slug="b", name="B", trigger_kind="manual", steps=STEPS[:1])
    got = {r["slug"]: r["step_count"] for r in sequences.list_all(cx)}
    assert got == {"a": 3, "b": 1}
