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


def test_a_name_from_a_plus_tag_with_a_digit_is_dropped():
    assert first_line("Rmtest1007b", "drglenswartwout+rmtest1007b@gmail.com")[0] == "Aloha,"
    assert first_line("Jsmith1985", "jsmith1985@example.com")[0] == "Aloha,"


def test_a_short_plain_word_from_the_address_is_kept_as_it_may_be_a_real_name():
    # Glen's 10-letter rule, 2026-11-24: "Mary" for mary@ reads correctly either way.
    assert first_line("Mary", "mary@gmail.com")[0] == "Aloha Mary,"
    assert first_line("Rmtest", "drglenswartwout+rmtest1007@gmail.com")[0] == "Aloha Rmtest,"
    assert first_line("Christine", "christine@example.com")[0] == "Aloha Christine,"


def test_the_ten_letter_boundary():
    assert first_line("Abcdefghij", "abcdefghij@x.com")[0] == "Aloha Abcdefghij,"
    assert first_line("Abcdefghijk", "abcdefghijk@x.com")[0] == "Aloha,"


def test_a_dotted_address_matches_without_its_punctuation():
    assert first_line("Johnsmithson", "john.smithson@example.com")[0] == "Aloha,"
    assert first_line("John.Smithson", "john.smithson@example.com")[0] == "Aloha,"


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


def test_a_name_in_another_alphabet_copied_from_the_address_is_dropped():
    assert first_line("Grünewaldhofer", "grünewaldhofer@example.org")[0] == "Aloha,"
    assert first_line("李", "李@example.org")[0] == "Aloha 李,"
    assert first_line("José", "jose.garcia@example.org")[0] == "Aloha José,"


def test_letters_are_counted_as_written_not_after_case_folding():
    assert first_line("Weißbacher", "weißbacher@example.de")[0] == "Aloha Weißbacher,"
