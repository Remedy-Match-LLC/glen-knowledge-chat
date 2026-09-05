"""The chat's namespace list: what it must reach, and what it must never reach.

Written 2026-09-04 alongside connecting five namespaces. An audit that day found 34,849
vectors (43% of the index) that no code path in the app could reach. Five were connected on
Glen's ruling; four were deliberately left out because they hold named client records.

The exclusions are the point of this file. A future change that quietly adds one of them
should fail here rather than reach a client.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"


def _namespaces():
    """Read NAMESPACES out of app.py without importing it (import needs credentials)."""
    src = APP.read_text()
    m = re.search(r"^NAMESPACES\s*=\s*\[(.*?)\]", src, re.S | re.M)
    assert m, "NAMESPACES list not found in app.py"
    body = re.sub(r"#.*", "", m.group(1))          # strip comments
    return re.findall(r'"([^"]*)"', body)


PHI_NAMESPACES = {
    "healing-oasis-records",   # named clients, dates, clinical findings
    "e4l-scans",               # client names, ids, scan dates
    "zyto-sessions",           # carries a client field
    "personal-notes",          # internal notes naming staff
}


def test_client_record_namespaces_are_never_in_the_chat_list():
    """The one that matters. These hold named people."""
    leaked = PHI_NAMESPACES & set(_namespaces())
    assert not leaked, (
        f"{sorted(leaked)} hold named client records and must not be searchable by the chat. "
        "If this is intentional, it needs a privacy decision, not a code change."
    )


def test_glens_published_material_is_connected():
    ns = set(_namespaces())
    for expected in ("websites", "youtube-transcripts", "training-transcripts",
                     "rumble-transcripts"):
        assert expected in ns, f"{expected} is Glen's own published material and should be searchable"


def test_third_party_references_are_connected_and_attributed():
    """clinical-references is other authors' books. Connecting it without the authorship
    guard would let the model quote Stargrove or Trivieri as Glen's own position."""
    assert "clinical-references" in set(_namespaces())
    src = APP.read_text()
    guard = re.search(
        r'namespace_purpose"\)\s*==\s*"clinical-references"(.{0,600})', src, re.S)
    assert guard, "the clinical-references authorship guard is gone from build_context"
    assert "NOT Dr. Swartwout" in guard.group(1), (
        "the authorship note no longer says the material is not Glen's own position"
    )


def test_no_duplicate_namespaces():
    ns = _namespaces()
    assert len(ns) == len(set(ns)), f"duplicate namespace in NAMESPACES: {ns}"


# ── Glen's documented connections to the reference works ──────────────────────
# Glen's ruling 2026-09-04: when the chat quotes one of the third-party books, it should
# say what his connection to it is. The risk being guarded here is the opposite of the
# usual one: not that a connection is missed, but that one is INVENTED. A book with no
# documented tie must get plain attribution and nothing more.

def _connect():
    import importlib.util, sys, types
    # _glen_connection_to is a pure function on metadata; extract it rather than importing
    # app.py, which needs credentials at import time.
    src = APP.read_text()
    start = src.index("_GLEN_BOOK_CONNECTIONS = [")
    end = src.index("def build_context(matches):")
    ns = {}
    exec(compile(src[start:end], "app_excerpt", "exec"), ns)
    return ns["_glen_connection_to"]


def test_contributed_book_states_he_wrote_a_chapter():
    note = _connect()({"author": "Larry Trivieri Jr. + Burton Goldberg (eds.)",
                       "book": "Alternative Medicine: The Definitive Guide"})
    assert "contributing author" in note
    assert "Chapter 77" in note


def test_ibis_states_the_research_team_connection():
    note = _connect()({"author": "Mitchell Bebel Stargrove + Health Resources Unlimited",
                       "book": None})
    assert "IBIS research team" in note


def test_a_book_that_cites_glen_says_so():
    note = _connect()({"author": "Alex Stark", "book": None,
                       "publisher": "Alex Stark — Creating Outstanding Environments"})
    assert "cites" in note.lower()


def test_an_unconnected_book_gets_NO_invented_connection():
    """The important one. Silence is the correct answer for an author Glen has no
    documented tie to. Inventing a relationship would be worse than giving none."""
    for meta in ({"author": "Some Other Author", "book": "A Book He Has No Link To"},
                 {"author": "", "book": ""},
                 {}):
        assert _connect()(meta) == "", f"invented a connection for {meta}"
