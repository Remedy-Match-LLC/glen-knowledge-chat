# tests/test_portal_reorder_ui.py
"""Task 6: portal reorder module UI (static/client-portal.html). Pure markup/
JS-source assertions — no app import, no network — mirroring the pattern in
test_portal_finder_markup.py. Guards against three concrete regressions:
  1. the rendered JS referencing a payload field Task 5's builder doesn't emit
     (see app.py::_portal_reorder_module and tests/test_portal_reorder_module.py
     for the ground-truth shape),
  2. the Reorder button losing its one-shot double-fire latch,
  3. locked-row copy drifting into backward/loss framing ("you overpaid").
"""
import pathlib
import re

HTML = pathlib.Path("static/client-portal.html").read_text()

# Fields Task 5's _portal_reorder_module() actually emits (app.py) — the
# single source of truth this test pins the UI against.
REORDER_FIELDS = {"slug", "name", "qty", "regular_cents", "your_cents",
                   "is_member_price", "in_repertoire", "channel", "is_reorder",
                   "source_label", "refill_eligible"}
LOCKED_FIELDS = {"slug", "name", "regular_cents", "tier"}
UPSELL_FIELDS = {"reorders_30d", "spend_30d_cents", "member_would_pay_cents",
                  "savings_cents", "net_after_fee_cents", "already_member"}


def _render_fn_source():
    m = re.search(r"function render\(d, v\)\{[\s\S]*?\n\}\n", HTML)
    assert m, "render(d, v) function not found in client-portal.html"
    return m.group(0)


def test_reorder_module_present_and_guarded():
    src = _render_fn_source()
    assert "Array.isArray(d.reorder) && d.reorder.length" in src
    assert "Array.isArray(d.locked_rows) && d.locked_rows.length" in src
    assert "d.membership_upsell &&" in src


def test_reorder_row_only_references_real_payload_fields():
    src = _render_fn_source()
    block = src[src.index('Array.isArray(d.reorder) && d.reorder.length'):
                src.index('Array.isArray(d.locked_rows)')]
    used = set(re.findall(r"\bit\.(\w+)", block))
    assert used <= REORDER_FIELDS, f"unknown reorder field(s) referenced: {used - REORDER_FIELDS}"
    # sanity: it should actually be exercising the fields that matter for pricing
    assert {"regular_cents", "your_cents", "is_member_price"} <= used


def test_reorder_row_labels_provenance_and_reserves_reorder_word():
    """Glen 2026-07-11 relabel (labels only): every purchase carries a provenance
    label by channel ('Ordered on your portal' vs the storefront label), and the
    word 'Reorder' is reserved for a true reorder (is_reorder) — a first-time
    purchase gets a neutral CTA instead."""
    src = _render_fn_source()
    block = src[src.index('Array.isArray(d.reorder) && d.reorder.length'):
                src.index('Array.isArray(d.locked_rows)')]
    # provenance line renders the server-computed, website-referencing label
    assert "it.source_label" in block
    assert "it.is_reorder" in block
    # 'Reorder' still offered for true reorders
    assert "Reorder" in block


def _locked_rows_blocks():
    """Every place the locked-rows list is actually built.

    Anchoring on the two gate expressions is what silently unpinned this test:
    the membership merge put `Array.isArray(d.locked_rows)` and
    `d.membership_upsell &&` on adjacent lines, so the slice between them was
    empty and `used <= LOCKED_FIELDS` passed on an empty set for a whole branch.
    Anchor on the loop that reads the rows instead, and return every occurrence,
    because the shell path and the shell-off path each build their own copy and
    a guard that only covers one of them is half a guard.
    """
    src = _render_fn_source()
    blocks = []
    for m in re.finditer(r"d\.locked_rows\.forEach\(it=>\{", src):
        end = src.index("});", m.end())
        blocks.append(src[m.start():end])
    return blocks


def test_locked_rows_only_references_real_fields_and_is_forward_framed():
    blocks = _locked_rows_blocks()
    assert len(blocks) == 2, (
        f"expected 2 locked-rows builders (merged under the shell, and the "
        f"shell-off original), found {len(blocks)}")
    for i, block in enumerate(blocks):
        # The slice must actually contain the row markup. An empty or truncated
        # slice makes every assertion below pass vacuously.
        assert len(block) > 300, f"locked-rows block {i} is only {len(block)} chars"
        assert "lockedrow" in block, f"locked-rows block {i} has no .lockedrow markup"
        used = set(re.findall(r"\bit\.(\w+)", block))
        assert used, f"locked-rows block {i} references no payload field at all"
        assert used <= LOCKED_FIELDS, f"unknown locked_rows field(s) referenced: {used - LOCKED_FIELDS}"
        assert "tier" in used
        low = block.lower()
        assert "overpa" not in low  # never "you overpaid" or similar backward framing
        assert "unlock" in low


def test_membership_upsell_only_references_real_fields_and_hides_for_members():
    src = _render_fn_source()
    # Same re-anchoring as the locked-rows test above, and for the same reason:
    # a fixed 1800-character window from a gate expression stops pinning anything
    # the moment the code it was aimed at moves. Anchor on the `mu` binding, and
    # take every builder, since the shell and shell-off paths each have one.
    blocks = []
    for m in re.finditer(r"const mu = d\.membership_upsell;", src):
        end = src.index("</div>`", m.end())
        blocks.append(src[m.start():end])
    assert len(blocks) == 2, (
        f"expected 2 membership-pitch builders (merged under the shell, and the "
        f"shell-off original), found {len(blocks)}")
    for i, block in enumerate(blocks):
        assert len(block) > 800, f"membership-pitch block {i} is only {len(block)} chars"
        used = set(re.findall(r"\bmu\.(\w+)", block))
        assert used <= UPSELL_FIELDS, f"unknown membership_upsell field(s) referenced: {used - UPSELL_FIELDS}"
        assert {"savings_cents", "reorders_30d", "net_after_fee_cents"} <= used
    block = blocks[0]
    idx = src.index('d.membership_upsell &&')
    # gated: hidden entirely when already_member is true
    guard_line = src[idx: idx + 120]
    assert "already_member" in guard_line
    # Glen 2026-07-12: never claim the member is "ahead" off a single order —
    # net_after_fee_cents is the UNCOVERED part of the fee (a shortfall) when
    # savings are below it, not a gain. Guard the inverted-sign copy from
    # returning; "covers your membership" is only shown when savings reach it.
    low = block.lower()
    assert "ahead even after" not in low


def test_reorder_button_has_one_shot_latch():
    m = re.search(r"async function reorderItem\(btn\)\{[\s\S]*?\n\}\n", HTML)
    assert m, "reorderItem(btn) not found"
    fn = m.group(0)
    # Latch must fire synchronously, before the first await, so a double-click
    # or a slow network can't double-fire the checkout call.
    guard_idx = fn.index("if(btn.disabled) return;")
    disable_idx = fn.index("btn.disabled = true;")
    first_await_idx = fn.index("await ")
    assert guard_idx < disable_idx < first_await_idx


def test_reorder_adds_to_shared_basket_instead_of_opening_side_checkout():
    m = re.search(r"async function reorderItem\(btn\)\{[\s\S]*?\n\}\n", HTML)
    fn = m.group(0)
    assert "await addItemToBasket(slug, qty, format)" in fn
    assert "/checkout" not in fn
    assert "stripe_url" not in fn


def test_portal_exposes_and_saves_global_cello_default():
    assert 'id="celloRefillDefault"' in HTML
    assert 'Send all eligible capsules in cellophane packs by default' in HTML
    assert 'This includes first-time purchases and reorders.' in HTML
    assert 'Each pack comes with a product label for your own bottle.' in HTML
    assert 'cello_refill_default:wanted' in HTML
    assert '/packaging-preference' in HTML
    assert 'data-refill-eligible' in HTML


def test_first_purchase_offers_explicit_bottle_or_cello_choice():
    assert 'id="orderAddFormat"' in HTML
    assert 'Packaging for this purchase' in HTML
    assert '<option value="bottle">Bottle</option>' in HTML
    assert '<option value="refill">Cellophane pack + label</option>' in HTML
    assert 'opt.dataset.refillEligible = p.refill_eligible ? "1" : "0"' in HTML
    assert 'addItemToBasket(slug, 1, format)' in HTML
