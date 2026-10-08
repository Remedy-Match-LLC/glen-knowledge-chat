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
