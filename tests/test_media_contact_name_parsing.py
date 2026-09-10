"""Name parsing for the media-contacts feeder.

The feeder split contact_name on the first space, so "Dr. Randy Schulman" wrote
first_name="Dr." and last_name="Randy Schulman" over a good record. Two shapes in
the live CSV parse wrong the same way: an honorific prefix, and a cell holding two
names or a name plus an outlet.
"""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sync_media_contacts",
    pathlib.Path(__file__).resolve().parents[1] / "sync-media-contacts.py",
)
sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync)


@pytest.mark.parametrize("name,title,first,last", [
    # The record that prompted this. Her hub row already holds "Dr." in title.
    ("Dr. Randy Schulman", "Dr.", "Randy", "Schulman"),
    ("Dr Marc Grossman", "Dr.", "Marc", "Grossman"),
    ("Prof. Ada Lovelace", "Prof.", "Ada", "Lovelace"),
    ("Ms. Gila Varis", "Ms.", "Gila", "Varis"),
    # No honorific, unchanged behaviour.
    ("Nathan Oxenfeld", "", "Nathan", "Oxenfeld"),
    ("Cher", "", "Cher", ""),
    # Two people in one cell. The first name is the one the row is addressed to.
    ("Nathan Oxenfeld & Esther Joy Van Der Werf", "", "Nathan", "Oxenfeld"),
    ("Luana Ribeira / Dauntless PR", "", "Luana", "Ribeira"),
    ("Ann Bauder and Rae Luscombe", "", "Ann", "Bauder"),
    # A multi-word family name stays whole.
    ("Esther Joy Van Der Werf", "", "Esther", "Joy Van Der Werf"),
    ("", "", "", ""),
])
def test_split_contact_name(name, title, first, last):
    assert sync.split_contact_name(name) == (title, first, last)


def test_row_keeps_the_full_cell_as_name():
    """`name` is the display string and must not lose the honorific."""
    row = {"email": "drrandyschulman@gmail.com", "contact_name": "Dr. Randy Schulman",
           "outlet": "Reclaim Your Vision / CSO", "phone": ""}
    p = sync.row_to_person(row)
    assert p["name"] == "Dr. Randy Schulman"
    assert p["first_name"] == "Randy"
    assert p["last_name"] == "Schulman"
    assert p["title"] == "Dr."


def test_row_without_an_honorific_sends_no_title():
    """An empty title must not be sent, or it would blank a stored one."""
    row = {"email": "nathan@integraleyesight.com", "contact_name": "Nathan Oxenfeld",
           "outlet": "The Naked Eye", "phone": ""}
    assert "title" not in sync.row_to_person(row)


# ── phone: fill only, never replace ──────────────────────────────────────────
# The CSV holds the number as it was typed. Randy Schulman's hub row holds
# "+12033942722" from a richer source, and the CSV holds "1-203-394-2722". The
# additive upsert overwrites any non-empty scalar, so the feeder used to trade a
# normalised number for a typed one on every run.

def _persons():
    return [
        {"email": "a@x.com", "phone": "1-203-394-2722"},
        {"email": "b@x.com", "phone": "323-422-6930"},
        {"email": "c@x.com", "phone": ""},
        {"email": "d@x.com", "phone": "0330 043 4102"},
    ]


def test_phone_is_dropped_when_one_is_already_stored():
    stored = {"a@x.com": "+12033942722"}
    out = {p["email"]: p for p in sync.drop_stored_phones(_persons(), stored)}
    assert "phone" not in out["a@x.com"]


def test_phone_is_kept_when_the_stored_one_is_blank():
    stored = {"b@x.com": ""}
    out = {p["email"]: p for p in sync.drop_stored_phones(_persons(), stored)}
    assert out["b@x.com"]["phone"] == "323-422-6930"


def test_phone_is_kept_for_a_person_not_yet_in_the_hub():
    out = {p["email"]: p for p in sync.drop_stored_phones(_persons(), {})}
    assert out["d@x.com"]["phone"] == "0330 043 4102"


def test_a_blank_csv_phone_is_never_sent():
    out = {p["email"]: p for p in sync.drop_stored_phones(_persons(), {})}
    assert "phone" not in out["c@x.com"]


def test_the_rest_of_the_payload_is_untouched():
    persons = [{"email": "a@x.com", "phone": "555", "name": "A", "tags": ["t"]}]
    out = sync.drop_stored_phones(persons, {"a@x.com": "+1555"})
    assert out[0] == {"email": "a@x.com", "name": "A", "tags": ["t"]}
