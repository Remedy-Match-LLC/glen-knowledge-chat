"""The client-facing AI prompts describe Glen as he describes himself, and limit claims.

He is retired from licensed practice. Three prompts called him a "naturopathic physician"
(found by R and D, 2026-10-02) and none limited health claims. Glen, 2026-10-02: "I tend to
say Naturopathic Optometrist, or Doctor of Optometry and Natural Medicine", and for the
role line, "consults" rather than "analyses biofield scans".

Checked on the assembled runtime prompts (round 2 of the review): a source grep would miss
split literals and text appended from elsewhere.
"""
import importlib
import re

import pytest

import dashboard.portal_concierge as pc

ROLE = ("Describe what a remedy supports, never a diagnosis or a cure. If asked about "
        "Dr. Glen's licence or practice, say he is retired from licensed practice and now "
        "formulates remedies, consults, and teaches.")


@pytest.fixture(scope="module")
def prompts(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
    import app as appmod
    importlib.reload(appmod)
    yield {"remedy_match": appmod._REMEDY_MATCH_SYSTEM,
           "post_purchase": appmod._CONCIERGE_SYSTEM,
           "portal": pc.system_prompt({"owned": ["X"], "findings": [{"name": "Y"}]})}
    mp.undo()


def _flat(text):
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("name", ["remedy_match", "post_purchase", "portal"])
def test_names_him_a_naturopathic_optometrist(prompts, name):
    p = _flat(prompts[name]).lower()
    assert "naturopathic optometrist" in p
    assert "naturopathic physician" not in p


@pytest.mark.parametrize("name", ["remedy_match", "post_purchase", "portal"])
def test_carries_the_role_and_claims_rule_verbatim(prompts, name):
    assert ROLE in _flat(prompts[name])


def test_the_rule_is_glens_wording():
    assert _flat(pc.ROLE_AND_CLAIMS).strip("- \n") == ROLE
