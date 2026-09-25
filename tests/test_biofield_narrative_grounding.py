"""The writer retries a draft that names off-chain products or misplaces nutrients, and
reports what is still wrong. Uses the real catalog (data/products.json): B17 Max,
B17 Syntropy and Liver Support are real products there."""
from dashboard.biofield_narrative import build_narrative_prompt, generate_narrative


def _report():
    return {"test_id": "a1", "client": {"name": "Jane Doe", "email": "jane@x.com"},
            "date": "2026-09-24",
            "layers": [
                {"layer": 1, "head": "Growth", "most_affected": "Growth, Source Driver",
                 "remedy": "B17 Syntropy", "dosage": "1 capsule", "frequency": "daily"},
                {"layer": 1, "head": "Growth", "most_affected": "Growth, Source Driver",
                 "remedy": "B17 Max", "dosage": "1 capsule", "frequency": "daily"}],
            "schedule": {"slots": [], "entries": []}}


def _scan():
    return {"status": "fresh", "found": True, "days_ago": 3, "fresh": True,
            "scan_date": "2026-09-21",
            "findings": [{"rank": 4, "code": "ET11", "name": "Liver 2 Terrain",
                          "description": "Liver\nConsider: Liver Support, Free & Easy\n\n"
                                         "Primarily the liver and large intestine.\n\n"
                                         "[SOURCED: FMP vl_bio_main_stress, Glen-authored]"}]}


BAD = ("Aloha Jane,\n\n1. B17 Syntropy and B17 Max support pathways including vitamins A, "
       "B6 and D3. Liver Support aids liver health. Your E4L voice scan agrees.")
GOOD = ("Aloha Jane,\n\n1. B17 Syntropy supplies vitamins A, B6 and D3. B17 Max supports "
        "the growth layer. Your E4L voice scan agrees.")


def test_scan_block_carries_no_product_suggestions_or_tags():
    usr = build_narrative_prompt(_report(), "", scan=_scan())["user"]
    assert "Liver 2 Terrain" in usr and "Primarily the liver" in usr
    assert "Liver Support" not in usr and "Consider" not in usr and "SOURCED" not in usr
    assert "BIOENERGETIC WELLNESS SCAN" in usr


def test_bad_draft_is_retried_with_its_errors_and_the_fix_is_kept():
    calls = []

    def fake(system, user):
        calls.append(user)
        return BAD if len(calls) == 1 else GOOD

    problems = []
    out = generate_narrative(_report(), "", fake, scan=_scan(), problems_out=problems)
    assert len(calls) == 2
    assert "YOUR PREVIOUS DRAFT HAD THESE ERRORS" in calls[1]
    assert "Liver Support" in calls[1] and "B17 Max" in calls[1]
    assert problems == []
    assert "Liver Support" not in out
    assert "Bioenergetic Wellness Scan" in out and "voice scan" not in out.lower()


def test_a_second_failure_is_reported_not_hidden():
    problems = []
    out = generate_narrative(_report(), "", lambda s, u: BAD, scan=_scan(),
                             problems_out=problems)
    assert any("Liver Support" in p for p in problems), problems
    assert any("B17 Max" in p and "Vitamin A" in p for p in problems), problems
    assert "Liver Support" in out          # the text is not silently edited; Glen sees it


def test_a_clean_draft_is_written_once():
    calls = []

    def fake(system, user):
        calls.append(user)
        return GOOD

    problems = []
    generate_narrative(_report(), "", fake, scan=_scan(), problems_out=problems)
    assert len(calls) == 1 and problems == []


def test_problems_out_is_optional():
    assert generate_narrative(_report(), "", lambda s, u: GOOD).startswith("Aloha Jane")


# ── the editor shows what is still wrong ────────────────────────────────────

def test_author_page_renders_warnings_escaped_and_hides_an_empty_box():
    from dashboard.biofield_report_html import _narrative_warning_box
    shown = _narrative_warning_box(["Names <Liver Support>, which is not on this client's chain."])
    assert "Check before sending" in shown and "&lt;Liver Support&gt;" in shown
    assert " hidden" not in shown.split(">")[0]
    empty = _narrative_warning_box([])
    assert "id=narrWarn" in empty and " hidden" in empty.split(">")[0]


def test_editor_scripts_read_the_warnings():
    import dashboard.biofield_report_html as h
    src = open(h.__file__).read()
    assert "showNarrWarn(j.warnings)" in src and "showNarrWarn(j&&j.warnings)" in src
    assert "r.warnings.join" in src


def test_a_failed_retry_keeps_the_first_draft_and_its_warnings():
    calls = []

    def fake(system, user):
        calls.append(1)
        if len(calls) == 1:
            return BAD
        raise RuntimeError("429 rate limit")

    problems = []
    out = generate_narrative(_report(), "", fake, scan=_scan(), problems_out=problems)
    assert out.startswith("Aloha Jane") and "Liver Support" in out
    assert any("Liver Support" in p for p in problems)


def test_generate_and_save_judge_with_the_same_inputs():
    """The check never counts the People-hub profile as permission to name a product,
    so generate and a later save or page load give the same verdict."""
    from dashboard.biofield_narrative import narrative_problems
    rep = _report()
    text = "Aloha Jane,\n\n1. B17 Max supports growth. You already take Liver Support."
    profile = {"conditions": "Client already takes Liver Support"}
    problems = []
    generate_narrative(rep, "", lambda s, u: text, profile=profile, problems_out=problems)
    assert problems == narrative_problems(text, rep)
    assert any("Liver Support" in p for p in problems)


def test_a_product_in_the_session_notes_is_still_off_chain():
    from dashboard.biofield_narrative import narrative_problems
    probs = narrative_problems("1. You may later add Liver Support.", _report())
    assert any("Liver Support" in p for p in probs), probs


def test_the_service_name_is_not_an_off_chain_product():
    from dashboard.biofield_narrative import narrative_problems
    assert narrative_problems("Aloha Jane,\n\nYour Biofield Analysis showed a clear chain.",
                              _report()) == []
