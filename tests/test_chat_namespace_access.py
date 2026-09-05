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
