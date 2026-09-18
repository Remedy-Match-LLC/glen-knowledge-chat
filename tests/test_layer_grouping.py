"""Glen, 2026-09-16: "Balance All ... adding layers and remedies to balance both the
clinical and the scan factors integrated into one sequence with minimal remedies.
Integrate energetic and clinical patterns where possible together into layers."

The grouping keys on FUNCTION first and location second, because function is what
integrates across body systems and location only groups what is already in one place.
Verified on Connie Chmielewski's 2026-09-09 scan, where it puts Mucous Membranes -
Small Intestine (integumentary) with Gall Bladder (digestive) on shared digestion —
a pairing no location axis can produce.
"""
from dashboard.layer_grouping import group_findings


def F(code, name, system=None, functions=()):
    return {"code": code, "name": name, "system": system, "functions": tuple(functions)}


def test_function_beats_location_across_systems():
    got = group_findings([
        F("EI3", "Mucous Membranes - Small Intestine", "integumentary", ("digestion", "barrier")),
        F("ER17", "Gall Bladder", "digestive-hepatobiliary", ("detoxification", "digestion")),
    ])
    assert len(got) == 1
    assert got[0]["why"].startswith("digestion")
    assert {m["code"] for m in got[0]["members"]} == {"EI3", "ER17"}


def test_same_function_and_system_groups_first():
    got = group_findings([
        F("ED4", "Nerve", "nervous", ("signalling",)),
        F("ER68", "Acoustic Nerve", "nervous", ("signalling", "sensing")),
    ])
    assert got[0]["why"] == "signalling in nervous"


def test_a_finding_lands_in_exactly_one_layer():
    """It may serve several functions, but a causal chain places it once."""
    got = group_findings([
        F("ER17", "Gall Bladder", "digestive-hepatobiliary", ("detoxification", "digestion")),
        F("EI6", "Kidney", "urinary", ("detoxification", "fluid-balance")),
        F("ED8", "Stomach", "digestive-hepatobiliary", ("digestion",)),
    ])
    placed = [m["code"] for L in got for m in L["members"]]
    assert sorted(placed) == ["ED8", "EI6", "ER17"]
    assert len(placed) == len(set(placed))


def test_a_findng_with_no_tissue_stands_alone():
    got = group_findings([F("MR1", "Super Cell Driver"), F("BFA", "Big Field Aligner")])
    assert [L["why"] for L in got] == ["on its own", "on its own"]


def test_a_clinical_stress_groups_with_a_scan_finding():
    """The integration Glen asked for: a clinical pattern and an energetic finding in
    one layer when they serve the same function."""
    got = group_findings([
        F("EI6", "Kidney", "urinary", ("detoxification",)),
        F("", "Liver clearance", "digestive-hepatobiliary", ("detoxification",)),
    ])
    assert len(got) == 1 and len(got[0]["members"]) == 2


def test_order_is_stable():
    findings = [F("ER17", "Gall Bladder", "digestive-hepatobiliary", ("digestion",)),
                F("ED8", "Stomach", "digestive-hepatobiliary", ("digestion",))]
    assert group_findings(findings) == group_findings(findings)


def test_nothing_in_nothing_out():
    assert group_findings([]) == []


def test_the_button_and_handlers_are_on_the_page():
    from dashboard.biofield_fee import build_fee_state
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "", clinical_checklist=[],
                           fee_state=build_fee_state("s@f.co", lambda _e: {"available": True}))
    assert "Balance All" in h
    assert "function balanceAll" in h
    assert "function balanceAllApply" in h
    assert "/author/a7/balance-all" in h


def test_the_button_proposes_rather_than_applies():
    """The apply call is a SEPARATE handler behind a confirm. Creating the causal
    chain is the intake's core artifact and must not happen on one click."""
    from dashboard.biofield_report_html import _AUTHOR_JS
    propose = _AUTHOR_JS[_AUTHOR_JS.index("async function balanceAll("):]
    propose = propose[:propose.index("async function balanceAllApply(")]
    assert "apply:true" not in propose.replace(" ", "")


# ── The route itself, not just the grouping ──────────────────────────────────────
# Glen, 2026-09-18: "Balance All -> propose layers hangs with the message grouping...".
# It was not hanging. It was returning 500 on every call since it shipped, and the page
# has no handler for a 500 so the status text never moves. The cause:
#
#   ImportError: cannot import name 'phases_for' from 'dashboard.terrain_phase'
#
# phases_for lives in dashboard/finding_phase.py, which was on the unmerged #1713. The
# nine tests in this file all exercise group_findings directly, so not one of them ever
# called the route, and a dormant path shipped unproven. This test calls it.

def test_the_balance_all_route_answers(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import create_test, init_auth_tables
    from dashboard.biofield_stress import add_stress, init_stress_tables

    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Sharon", "s@example.com", "2026-09-18")
        init_stress_tables(cx)
        add_stress(cx, tid, "Kidney Driver", source="scan", balance="required")
    client = create_app(db, fetch_profile=lambda email: {},
                        fetch_recent_comms=lambda email: {}).test_client()

    r = client.post(f"/author/{tid}/balance-all", json={})
    assert r.status_code == 200, r.data[:300]
    j = r.get_json()
    assert j["ok"] is True and j["proposed"] is True
