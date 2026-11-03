"""Wellness Ensurance is no longer offered, and the chat must never offer it.

Glen, 2026-11-02, platform tab: "yes". The knowledge tab deleted 152 chunks, yet chat 2672
still described "Level 1 ... through Level 5" and said to "reach out" for prices. Its
sources were private notes the KB never edits. Some passages carry the name with no
link, so the rule names the program (knowledge, 2 Nov).
"""
import importlib

import pytest


@pytest.fixture()
def appmod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app
    importlib.reload(app)
    return app


def test_every_chat_route_carries_it(appmod):
    """test_cds_standing_rule_routes sends a request through each chat route and asserts
    its system prompt holds the whole _STANDING_USE_RULE once. This block is part of that
    constant, so it reaches every route those tests cover."""
    r = appmod._STANDING_USE_RULE
    assert r.count("RETIRED PROGRAM.") == 1
    assert ("Wellness Ensurance (also written WellnessEnsurance, Wellness-Ensurance or "
            "Wellness Insurance, or wellnessensurance.com)") in r
    assert "is no longer offered" in r


@pytest.mark.parametrize("phrase", ["Never offer", "price", "link it", "levels",
                                    "sign up", "website", "video", "reach out"])
def test_it_covers_what_the_old_snippets_say(appmod, phrase):
    assert phrase in appmod._STANDING_USE_RULE.split("RETIRED PROGRAM.")[1]


def test_the_chat_prompt_names_it_beside_the_other_retired_products(appmod):
    base = appmod.get_system_prompt("self-healing")
    assert '"Wellness Ensurance" is NO LONGER OFFERED (2026-11-02)' in base
    assert base.count("RETIRED PROGRAM.") == 1


@pytest.mark.parametrize("name", ["Wellness Ensurance", "Wellness Ensurance Level 3",
                                  "WellnessEnsurance", "wellnessensurance.com",
                                  "Wellness-Ensurance", "Wellness  Ensurance",
                                  "Wellness Insurance Level 3"])
def test_the_matcher_never_auto_recommends_it(appmod, name):
    assert appmod._ff_auto_excluded(name)


def test_an_ordinary_product_still_passes_the_matcher(appmod):
    assert not appmod._ff_auto_excluded("Brain Cleanse")
