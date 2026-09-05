"""The 2026-09-04 split: product list free, audio and written report paid.

Glen's ruling. Matching remedies to an E4L scan is automated and no longer costs his
individual case review, so the recommended PRODUCT LIST is shown to a client who had a
free scan. The report AUDIO and the written PDF stay behind payment.

Two things this file protects:
  1. Shipping the split changes nothing until someone deliberately flips the new flag.
  2. The payment tests for the deliverables gate stay IDENTICAL to the old combined
     gate, so a client who could see everything before because they genuinely paid
     still can. Only the scope changes, never the definition of "paid".
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
SRC = APP.read_text()


def _fn(name):
    m = re.search(rf"^def {name}\(.*?\n(.*?)(?=^def )", SRC, re.S | re.M)
    assert m, f"{name} is gone from app.py"
    return m.group(1)


def test_the_new_gate_ships_off_so_deploying_it_changes_nothing():
    body = _fn("_portal_deliverables_gate_enabled")
    assert 'os.environ.get("PORTAL_DELIVERABLES_GATE_ENABLED", "")' in body, (
        "the flag must default to empty, i.e. off. A default that reads as ON would blur "
        "paying clients the moment this deploys."
    )


def test_off_means_everyone_keeps_what_they_have_today():
    body = _fn("_portal_deliverables_unlocked")
    assert re.search(r"if not _portal_deliverables_gate_enabled\(\):\s*\n\s*return True", body), (
        "with the flag off the gate must return True for everyone, matching today's behaviour"
    )


def test_paid_definition_is_identical_to_the_product_list_gate():
    """The scope changed; what counts as paid must not. If these drift, a client who can
    see the product list but genuinely paid could lose their own report."""
    deliver = _fn("_portal_deliverables_unlocked")
    product = _fn("_portal_biofield_unlocked")
    for check in ("_has_paid_biofield", "_active_membership_for_email",
                  "comped_intake", "_family_plan_enabled"):
        assert check in deliver, f"{check} missing from the deliverables gate"
        assert check in product, f"{check} missing from the product-list gate"


def test_the_gate_fails_closed_on_error():
    body = _fn("_portal_deliverables_unlocked")
    assert re.search(r"except Exception:\s*\n\s*return False", body), (
        "an error must blur, never release the paid deliverable"
    )


def test_audio_and_pdf_are_on_the_deliverables_gate_not_the_product_gate():
    assert '"audio": (bf_content.get("audio") or {}) if bf_deliver else {}' in SRC
    assert '"report_pdf": (bf_content.get("report_pdf") or {}) if bf_deliver else {}' in SRC


def test_the_dosing_schedule_is_on_the_paid_side():
    """Glen moved this 2026-09-04: which remedies to take is free, WHEN to take them is
    part of the paid deliverable."""
    assert '"schedule": (bf_content.get("schedule") or {}) if bf_deliver else {}' in SRC


def test_the_product_list_stays_on_its_own_gate():
    """The point of the split. If the blur starts keying on the deliverables gate, a free
    scan client stops seeing the recommendations, which is the opposite of the ruling."""
    assert '"biofield_status": bf_status, "blurred": not bf_show,' in SRC


def test_the_page_can_tell_the_client_why_something_is_missing():
    assert '"deliverables_locked": bool(bf_show and not bf_deliver),' in SRC
