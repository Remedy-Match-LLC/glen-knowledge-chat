"""Every product-answering system prompt forbids inventing how a product works.

Glen, 2026-10-08. The live /chat (self-healing, brief) answered "What is AngioGenX
and how does it help the eyes?" with the right ingredients, dose and caution, then
added a mechanism ("strengthen vascular endothelial function ... neovascularization"),
a reason for the caution ("vascular remodeling ... hemostasis"), a dose rationale
("maximizes bioavailability") and labels ("clinically active", "adaptogen"). None was
in a retrieved source. An invented mechanism turns "supports" into a treatment claim.

Each assertion reads the prompt as the code assembles it (the builder or the module
constant the route passes as `system=`), never the source text, so a comment that
mentions the rule cannot satisfy it.
"""
import app
from dashboard import intake_chat
from dashboard import portal_concierge as pcz

# Literal phrases, written out here on purpose: if the shared constant were emptied
# or reworded away from its intent, the "constant is in the prompt" check would still
# pass, but these would not.
REQUIRED_PHRASES = (
    "only as the retrieved snippets or product data give them",
    "Do not explain how a product works, why its dose is set, or why a caution exists",
    "'clinically active' or 'adaptogen'",
    "even where a format asks for mechanism or rationale",
    "never what it treats or prevents",
)


def _assert_rule(prompt, where):
    # Exactly once: a duplicated insertion would pass a plain presence check.
    assert prompt.count(pcz.SOURCED_PRODUCT_CLAIMS.strip()) == 1, f"{where}: not exactly once"
    for phrase in REQUIRED_PHRASES:
        assert phrase in prompt, f"{where}: missing {phrase!r}"


def test_main_chat_prompt_carries_the_rule_at_every_level():
    # /chat (brief and full) and the emailed full report all call get_system_prompt.
    for level in ("self-healing", "health-care", "science", "no-such-level"):
        _assert_rule(app.get_system_prompt(level), f"get_system_prompt({level!r})")


def test_rule_sits_inside_the_rules_block_and_keeps_its_neighbours():
    prompt = app.get_system_prompt("self-healing")
    rules = prompt.index("\nRULES:\n")
    rule = prompt.index("PRODUCT CLAIMS COME ONLY FROM SOURCES")
    assert rule > rules
    # Inserting it must not have dropped the rules either side.
    assert prompt.index("- Do NOT fabricate.") < rule < prompt.index("- MICROPHONE SCOPE:")


def test_intake_guide_inherits_the_rule():
    # /api/intake/public/chat wraps the site prompt with intake_chat.build_system.
    _assert_rule(intake_chat.build_system(app.get_system_prompt("self-healing"), "Ana"),
                 "intake_chat.build_system")


def test_remedy_match_and_post_purchase_concierge_carry_the_rule():
    _assert_rule(app._REMEDY_MATCH_SYSTEM, "_REMEDY_MATCH_SYSTEM (/begin/match/chat)")
    _assert_rule(app._CONCIERGE_SYSTEM, "_CONCIERGE_SYSTEM (/begin/concierge/chat)")


def test_glens_approved_role_line_is_left_unchanged():
    # The rule sits beside ROLE_AND_CLAIMS, never inside it (Glen approved that wording).
    assert "PRODUCT CLAIMS" not in pcz.ROLE_AND_CLAIMS


def test_portal_concierge_carries_the_rule_with_and_without_data():
    ctx = pcz.build_context(
        {"layers": [{"n": 1, "title": "Eye terrain", "remedy": "AngioGenX"}]},
        [{"items": [{"name": "AngioGenX"}]}])
    _assert_rule(pcz.system_prompt(ctx), "portal_concierge.system_prompt(data)")
    _assert_rule(pcz.system_prompt(pcz.build_context({}, [])),
                 "portal_concierge.system_prompt(empty)")


# ── Review rounds 1-2: the per-turn and format instructions asked for mechanism ──

NOTE = pcz.SOURCED_PRODUCT_NOTE


def test_note_says_what_it_must():
    for phrase in ("For a named product", "mechanism, dose reasons, caution reasons and labels",
                   "only as a retrieved source states them"):
        assert phrase in NOTE


def test_brief_user_turn_qualifies_beat_2_and_keeps_the_rule():
    instr = app._brief_synth_instruction()
    beat2 = instr[instr.index("2. WHY IT WORKS"):instr.index("3. LIMITATION")]
    assert "For a named product, give its mechanism only as a retrieved source states it." in beat2
    keep = instr[instr.index("- Keep ALL existing rules"):instr.index("- NOT EVERY TURN")]
    assert NOTE in keep


def test_full_user_turn_carries_the_note_in_both_branches():
    for logged_in in (True, False):
        assert app._long_form_synth_instr(logged_in).count(NOTE) == 1, logged_in


def test_system_format_lines_that_ask_for_mechanism_are_qualified():
    p = app.get_system_prompt("self-healing")
    q = "(for a named product, only as a retrieved source states it)"
    assert f"the mechanism or evidence in compressed form {q}." in p
    assert f"Mechanism {q}, what to do differently" in p
    assert f"as the mechanism behind the formulations {q}." in p
    extended = p[p.index("Expand each bullet with mechanism"):]
    assert NOTE in extended.split("\n", 1)[0]


def test_scan_chat_carries_the_rule():
    _assert_rule(app._SCAN_CHAT_SYSTEM, "_SCAN_CHAT_SYSTEM (/member/scan-analysis/chat)")
    # The closing education line must still be last.
    assert app._SCAN_CHAT_SYSTEM.rstrip().endswith("prevent any disease.'")


# ── Product-page generators ──

def test_how_it_works_generator_is_sourced_only():
    from dashboard import product_content as pc
    h = pc._HOW_SYSTEM
    assert "mechanism, what the key ingredients do and why they were chosen" not in h
    assert "only where the page copy states it; never add one" in h
    assert "never what it treats or prevents" in h
    assert "write less rather than fill the gap" in h


def _sales_prompts():
    from dashboard import sales_copy as sc
    formula = {"name": "AngioGenX", "description": "Supports healthy circulation.",
               "ingredients": [{"name": "Bilberry", "dose": "100 mg"}]}
    infoceutical = {"name": "Rejuvenation Infoceutical", "description": "Energetic support."}
    for product in (formula, infoceutical):
        for section in sc.NARRATIVE_SECTIONS:
            system, user = sc.build_section_prompt(section, product)
            yield section, system + "\n" + user


def test_sales_copy_prompts_never_ask_for_an_unsourced_mechanism():
    for section, prompt in _sales_prompts():
        low = prompt.lower()
        assert "how it works" not in low, section
        assert "mechanisms of the listed ingredients" not in low, section
        assert ("give a mechanism, a design rationale, or a reason for a dose or caution "
                "only where the details given below state it; never invent one") in low, section
