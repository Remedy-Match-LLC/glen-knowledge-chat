"""A contact with no first name is greeted "Aloha," and never by a name from the address.

Glen, 2026-11-24, after his own copy of the 25 Nov invitation opened "Aloha
Drglenswartwout": GoHighLevel stores the address's local part as the first name of a
contact that has none, and the sender printed it.
"""
import importlib.util
import os
from datetime import date
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")

SPEC = importlib.util.spec_from_file_location(
    "weekly_live_invitation_greeting",
    Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
weekly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(weekly)

DAY = date(2026, 11, 25)


def first_line(first, email):
    text, body = weekly._copy(first, "https://myhealingoasis.com/portal/t", False, DAY, email)
    return text.split("\n", 1)[0], body


def test_glens_own_copy_opens_with_aloha_alone():
    line, body = first_line("Drglenswartwout", "drglenswartwout@gmail.com")
    assert line == "Aloha,"
    assert "Drglenswartwout" not in body


def test_a_name_from_a_plus_tag_is_dropped_too():
    assert first_line("Rmtest", "drglenswartwout+rmtest1007@gmail.com")[0] == "Aloha,"
    assert first_line("Rmtest1007b", "drglenswartwout+rmtest1007b@gmail.com")[0] == "Aloha,"


def test_a_dotted_address_matches_without_its_punctuation():
    assert first_line("Jsmith", "j.smith@example.com")[0] == "Aloha,"
    assert first_line("J.Smith", "j.smith@example.com")[0] == "Aloha,"


def test_a_real_first_name_is_kept_even_when_the_address_contains_it():
    assert first_line("John", "john.smith@example.com")[0] == "Aloha John,"
    assert first_line("Rae", "suerae1111@gmail.com")[0] == "Aloha Rae,"
    assert first_line("Glen", "this.elf@gmail.com")[0] == "Aloha Glen,"


def test_no_first_name_and_no_email_both_open_with_aloha():
    assert first_line("", "someone@example.com")[0] == "Aloha,"
    assert first_line("Sam", "")[0] == "Aloha Sam,"


def test_the_correction_email_uses_the_same_rule():
    text, _ = weekly._correction_copy("Drglenswartwout", "https://h/portal/t#calendar", False,
                                      DAY, "drglenswartwout@gmail.com")
    assert text.split("\n", 1)[0].startswith("Aloha,")
    text, _ = weekly._correction_copy("Glen", "https://h/portal/t#calendar", False,
                                      DAY, "drglenswartwout@gmail.com")
    assert text.split("\n", 1)[0].startswith("Aloha Glen,")
