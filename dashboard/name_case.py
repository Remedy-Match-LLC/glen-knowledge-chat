"""Capitalise a person's name when it was typed all lowercase or all capitals.

Glen, 2026-09-13. 4,140 of 10,334 people carried an all-lowercase name, most as
the person typed it into a form. Rules he approved:

  1. Only a name that is entirely lowercase or entirely capitals changes. Mixed
     case is a choice somebody made ("DeAnna", "bell hooks" typed deliberately
     is still all lowercase, and that trade-off was accepted).
  2. de, van, der, dos and their kin stay lowercase inside a name.
  3. Credentials from a fixed list go to capitals, PhD as PhD. After the first
     comma anything not on the list stays as typed.
  4. A value holding an email address is left alone.
  5. In an all-capitals value, a 2 or 3 letter word with no vowel stays in
     capitals: JC, KBH, RDN. Titles such as DR are not initials. A lowercase
     "jc" carries no sign of initials and becomes "Jc".

Pure. Every writer of people.name, first_name and last_name calls it, because a
one-off cleanup is undone by the next feeder run: the additive upsert replaces
any stored scalar with a non-blank incoming one.
"""
import re
import unicodedata

PARTICLES = {"de", "da", "del", "della", "di", "van", "von", "der", "den",
             "du", "dos", "das"}

# After a comma, any of these. Before one, only the unambiguous subset, because
# "Ma", "Do" and "Pa" are also surnames.
CREDENTIALS = {"od", "md", "nd", "dc", "do", "dds", "dmd", "lac", "lmt", "np",
               "fnp", "dnp", "aprn", "rn", "bsn", "msn", "mph", "ms", "ma", "mba",
               "pa", "cnm", "lcsw", "lpc", "cch", "faao", "cfmp", "ifmcp", "dacm",
               "cns", "llc", "pllc", "ii", "iii", "iv"}
BARE_CREDENTIALS = CREDENTIALS - {"do", "ms", "ma", "pa", "ii", "iii", "iv"}
SPECIAL = {"phd": "PhD"}

TITLES = {"dr", "mr", "ms", "mrs", "jr", "sr", "st", "mt", "ft", "ltd"}

_WORD_START = re.compile(r"(^|[-'’.(\"])([a-z])")


def _core(token):
    """The token with punctuation and inner dots removed, for list lookups."""
    return re.sub(r"[^a-z]", "", token.lower())


def _is_initials(token):
    """A 2 or 3 letter word with no vowel, accented vowels included, not a title."""
    base = unicodedata.normalize("NFD", token)
    letters = "".join(c for c in base if c.isalpha())
    if not 2 <= len(letters) <= 3 or letters.lower() in TITLES:
        return False
    return not re.search(r"[aeiouy]", letters.lower())


def _credential(token):
    core = _core(token)
    if core in SPECIAL:
        return token.lower().replace("phd", "PhD").replace("ph.d", "Ph.D")
    return token.upper()


def _title(token):
    low = token.lower()
    out = _WORD_START.sub(lambda m: m.group(1) + m.group(2).upper(), low)
    # McGinty, and McMasters after a hyphen. Mac is left alone: Macy, Mack.
    return re.sub(r"(^|[-'(])Mc([a-z])", lambda m: m.group(1) + "Mc" + m.group(2).upper(),
                  out)


def normalize_name(value, leading_particle=False):
    """The name with rules 1 to 4 applied. Anything else comes back unchanged.

    leading_particle: True for a last_name field, so "van der vegt" matches the
    full name "Maria van der Vegt" rather than becoming "Van der Vegt".
    """
    if not isinstance(value, str):
        return value
    if not value.strip() or "@" in value or not re.search(r"[A-Za-z]", value):
        return value
    if value not in (value.lower(), value.upper()):
        return value

    all_capitals = value == value.upper()
    parts = re.split(r"(\s+)", value)
    out, word_index, after_comma = [], 0, False
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        core = _core(part)
        if after_comma and (core in CREDENTIALS or core in SPECIAL):
            out.append(_credential(part))
        elif after_comma:
            out.append(part)
        elif all_capitals and _is_initials(part):
            out.append(part)
        elif word_index > 0 and (core in BARE_CREDENTIALS or core in SPECIAL):
            out.append(_credential(part))
        elif core in PARTICLES and (word_index > 0 or leading_particle):
            out.append(part.lower())
        else:
            out.append(_title(part))
        word_index += 1
        if part.endswith(","):
            after_comma = True
    return "".join(out)


def normalize_person_names(fields):
    """Apply normalize_name to the name keys of a people dict, in place. Returns it."""
    for key in ("name", "first_name"):
        if key in fields:
            fields[key] = normalize_name(fields[key])
    if "last_name" in fields:
        fields["last_name"] = normalize_name(fields["last_name"], leading_particle=True)
    return fields
