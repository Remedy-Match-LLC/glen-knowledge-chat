"""Name capitalisation rules Glen approved on 2026-09-13.

Every input below is a real value from the people hub preview that day.
"""
import pytest

from dashboard.name_case import normalize_name, normalize_person_names


@pytest.mark.parametrize("typed,expected", [
    ("aaron soto", "Aaron Soto"),
    ("ABHINAV MAHARWAL", "Abhinav Maharwal"),
    ("alice mcginty", "Alice McGinty"),
    ("barbara durant - mcmasters", "Barbara Durant - McMasters"),
    ("celeste o'brien", "Celeste O'Brien"),
    ("angela damon-jackson", "Angela Damon-Jackson"),
    ("darien 'sky' martin", "Darien 'Sky' Martin"),
    ("ada", "Ada"),
])
def test_an_all_lower_or_all_upper_name_is_capitalised(typed, expected):
    assert normalize_name(typed) == expected


@pytest.mark.parametrize("typed", [
    "DeAnna Smith", "Adrian Den Boer", "Alissa McDivitt", "Macy Gray",
])
def test_a_mixed_case_name_is_left_as_typed(typed):
    assert normalize_name(typed) == typed


@pytest.mark.parametrize("typed,expected", [
    ("maria van der vegt", "Maria van der Vegt"),
    ("alice de pina", "Alice de Pina"),
    ("isaura brito dos santos", "Isaura Brito dos Santos"),
])
def test_a_particle_inside_a_name_stays_lowercase(typed, expected):
    assert normalize_name(typed) == expected


def test_a_particle_leading_a_last_name_field_matches_the_full_name():
    assert normalize_name("van der vegt", leading_particle=True) == "van der Vegt"
    assert normalize_name("van horn") == "Van Horn"


@pytest.mark.parametrize("typed,expected", [
    ("aaron werner, o.d.", "Aaron Werner, O.D."),
    ("adero c e allison, phd", "Adero C E Allison, PhD"),
    ("angel scanzera, od, mph", "Angel Scanzera, OD, MPH"),
    ("avani dave, od, faao", "Avani Dave, OD, FAAO"),
    ("barry rose md", "Barry Rose MD"),
    ("adam cantor, ms, lac", "Adam Cantor, MS, LAC"),
    # Not on the list, after the comma: left exactly as typed.
    ("amanda hudson, ma, ed.s, lac (bmha)", "Amanda Hudson, MA, ed.s, LAC (bmha)"),
])
def test_credentials_follow_the_fixed_list(typed, expected):
    assert normalize_name(typed) == expected


def test_a_surname_that_is_also_a_credential_is_not_capitalised_without_a_comma():
    assert normalize_name("yo-yo ma") == "Yo-Yo Ma"


@pytest.mark.parametrize("typed", ["jane@clinic.com", "", "   ", "12345", None])
def test_an_email_blank_or_non_name_is_left_alone(typed):
    assert normalize_name(typed) == typed


def test_the_separator_between_words_is_kept():
    assert normalize_name("cheryl van allsburg\x0bwishowski") == \
        "Cheryl van Allsburg\x0bWishowski"


def test_person_dict_normalises_only_name_keys():
    p = normalize_person_names({"name": "hannah van horn", "first_name": "hannah",
                                "last_name": "van horn", "city": "hilo"})
    assert p == {"name": "Hannah van Horn", "first_name": "Hannah",
                 "last_name": "van Horn", "city": "hilo"}


# ── initials typed in capitals stay in capitals (Glen, 2026-09-13) ────────────

@pytest.mark.parametrize("typed,expected", [
    ("JC DAVIS", "JC Davis"),
    ("KBH FARMS, LLC", "KBH Farms, LLC"),
    ("REBECCA ROMOHR RDN LD", "Rebecca Romohr RDN LD"),
    ("BJ", "BJ"),
    ("LOBO DR HUANG", "Lobo Dr Huang"),       # a title is not initials
])
def test_a_short_all_capitals_word_with_no_vowel_stays_in_capitals(typed, expected):
    assert normalize_name(typed) == expected


@pytest.mark.parametrize("typed,expected", [
    ("jc davis", "Jc Davis"),                  # lowercase carries no sign of initials
    ("ANN KAY", "Ann Kay"),                    # a vowel means a word, not initials
    ("GRÁ O", "Grá O"),                        # an accented vowel counts as a vowel
])
def test_only_vowel_less_capitals_count_as_initials(typed, expected):
    assert normalize_name(typed) == expected
