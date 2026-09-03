"""What a client may do to their own invoice from the emailed link.

Two rules that disagreed with the console until now:

  * A line reduced to zero STAYS. Glen built zero-qty lines as a client-visible
    reorder reference ("she wants to order more of these next month"), and the
    console honours that, but the client page filtered `qty > 0` on the way out
    and the route dropped `qty <= 0` on the way in. A client zeroing a line
    destroyed the very reference the feature exists to keep.
  * Removing a line is now its own deliberate act, because zeroing no longer
    does it.
"""
import pytest

from dashboard import client_invoice_lines as cil

KNOWN = {"ocuflow-bedtime", "iop-syntropy", "biofield-analysis", "perfect-skin"}
known = KNOWN.__contains__


def test_a_zeroed_line_is_kept_as_a_reorder_reference():
    out = cil.rebuild([{"slug": "ocuflow-bedtime", "qty": 0}], {}, known=known)
    assert [(l["slug"], l["qty"]) for l in out] == [("ocuflow-bedtime", 0)]


def test_a_line_the_client_removed_is_gone():
    # Deletion is omission from the posted list; nothing else may remove a line.
    existing = {"ocuflow-bedtime": {"slug": "ocuflow-bedtime", "qty": 2},
                "iop-syntropy": {"slug": "iop-syntropy", "qty": 1}}
    out = cil.rebuild([{"slug": "iop-syntropy", "qty": 1}], existing, known=known)
    assert [l["slug"] for l in out] == ["iop-syntropy"]


def test_quantities_are_clamped_not_trusted():
    out = cil.rebuild([{"slug": "iop-syntropy", "qty": 500},
                       {"slug": "perfect-skin", "qty": -3}], {}, known=known)
    assert [l["qty"] for l in out] == [99, 0]


def test_a_missing_qty_still_means_one():
    # Only an EXPLICIT zero means zero; `int(x or 1)` is the trap this avoids.
    out = cil.rebuild([{"slug": "perfect-skin"}, {"slug": "iop-syntropy", "qty": ""}],
                      {}, known=known)
    assert [l["qty"] for l in out] == [1, 1]


def test_unknown_products_are_refused():
    out = cil.rebuild([{"slug": "not-a-product", "qty": 1}, {"slug": "", "qty": 1},
                       {"slug": "perfect-skin", "qty": 1}], {}, known=known)
    assert [l["slug"] for l in out] == ["perfect-skin"]


def test_only_an_owner_override_carries_a_price_forward():
    """A client may never set a price, and a non-override line must re-price.

    Sending unit_cents for an ordinary line would freeze it at whatever it last
    showed, which is exactly how #165 went out at $809.13.
    """
    existing = {"biofield-analysis": {"slug": "biofield-analysis", "unit_cents": 0,
                                      "override": True},
                "iop-syntropy": {"slug": "iop-syntropy", "unit_cents": 6997}}
    out = cil.rebuild([{"slug": "biofield-analysis", "qty": 1, "unit_cents": 1},
                       {"slug": "iop-syntropy", "qty": 1, "unit_cents": 1}],
                      existing, known=known)
    by = {l["slug"]: l for l in out}
    assert by["biofield-analysis"]["unit_cents"] == 0      # owner's override, kept
    assert "unit_cents" not in by["iop-syntropy"]          # re-priced by the server


def test_notes_and_provenance_come_from_the_stored_invoice_only():
    existing = {"iop-syntropy": {"slug": "iop-syntropy", "note": "2 daily",
                                 "source": "recommended"}}
    out = cil.rebuild([{"slug": "iop-syntropy", "qty": 1, "note": "free bottle",
                        "source": "self"}], existing, known=known)
    assert out[0]["note"] == "2 daily" and out[0]["source"] == "recommended"


def test_packaging_is_the_clients_to_choose_but_not_to_invent():
    existing = {"iop-syntropy": {"slug": "iop-syntropy", "format": "bottle"}}
    out = cil.rebuild([{"slug": "iop-syntropy", "qty": 1, "format": "refill"},
                       {"slug": "perfect-skin", "qty": 1, "format": "gold-plated"}],
                      existing, known=known)
    assert out[0]["format"] == "refill"
    assert out[1]["format"] == "bottle"


def test_junk_rows_never_raise():
    assert cil.rebuild([None, "x", 7, {}, {"slug": "perfect-skin", "qty": "two"}],
                       {}, known=known) == [{"slug": "perfect-skin", "qty": 1,
                                             "format": "bottle"}]


def test_nothing_posted_yields_nothing():
    assert cil.rebuild([], {}, known=known) == []
    assert cil.rebuild(None, {}, known=known) == []
