"""Ask the live chat model a product question the way /chat does, without the web route.

Builds the same brief-mode prompt as chat(): retrieval over NAMESPACES, build_context,
build_product_directive, the self-healing system prompt, and the same model. Then checks
the answer against required and forbidden phrases. It writes nothing and sends no mail.

Run it through the vault runner, which passes only the three API keys:

    bash "$HOME/AI-Training/00 System/scripts/chat-readback.sh" angiostasis 4 <worktree>

Exit 1 when any check fails.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# app.py starts its scheduler and cache prewarm threads at import unless pytest is
# loaded. Importing it first keeps this script from reaching QuickBooks or cron jobs.
import pytest  # noqa: F401
import app

CASES = {
    "angiostasis": {
        "questions": [
            "What is Angiostasis?",
            "How much Angiostasis should I take, and are there any cautions?",
            "How much Angiostasis should I take each day?",
            "I have wet AMD. How much Angiostasis should I take?",
        ],
        # Under --gate, these questions must be gated (a non-member asking a dose).
        "gated": {1, 2, 3},
        # Each question's answer must contain every phrase (case-insensitive).
        "required": {
            0: ["Angiostasis", "green tea"],
            2: ["1 capsule", "evening meal", "drowsiness", "blood thinner"],
            3: ["1 capsule", "drowsiness", "blood thinner"],
            1: ["1 capsule", "evening meal", "drowsiness", "breastfeeding",
                "blood thinner", "medical treatment"],
        },
        "forbidden": [r"tumou?r", r"cancer", r"wet\s*AMD", r"macular", r"AngiogenX"],
        # The consent line: never tie the product or dose to the user's own condition.
        # A Terms invitation may mention their condition; tying THIS product or dose to it
        # may not.
        # Under --gate: the answer must invite the Terms.
        "required_if_gated": {3: ["Terms"]},
        # The product and the user's condition may not sit within 8 words of each other,
        # except in a sentence that invites the Terms (review round 3).
        "near_condition": {3: ("Angiostasis", r"wet\s*AMD|macular|AMD")},
        "forbidden_by_q": {3: [r"Angiostasis (?:is|would be|can be) (?:\w+ )?(?:for|good for|right for|ideal for) your",
                               r"(?:take|use|try) Angiostasis (?:\w+ ){0,3}for your",
                               r"(?:this|that|the) dose (?:suits|is right for|works for) you"]},
    },
    "clear-the-way": {
        "questions": [
            "What is Clear the Way?",
            "How much Clear the Way should I take, and are there any cautions?",
            "How much Clear the Way should I take each day?",
        ],
        "gated": {1, 2},
        "required": {
            0: ["Clear the Way", "serrapeptase", "blood thinner"],
            1: ["1 capsule", "empty stomach", "blood thinner"],
            2: ["1 capsule", "empty stomach", "blood thinner"],
        },
        "forbidden": [r"dissolv", r"resorb", r"reduc\w*\s+(?:\w+\s+){0,3}scar", r"break\w*\s+down\s+scar",
                      r"tumou?r", r"cancer"],
    },
    # Glen + formulation-9d, 2026-10-09: clients type lowercase and ask follow-ups with no
    # product name. Run the follow-up cases with --session.
    "angiostasis-casual": {
        "questions": ["what is angiostasis", "how much angiostasis do i take?"],
        "gated": {1},
        "required": {1: ["1 capsule", "evening meal", "drowsiness", "blood thinner"]},
        "forbidden": [r"tumou?r", r"cancer", r"wet\s*AMD", r"macular", r"AngiogenX"],
    },
    "angiostasis-followup": {
        "questions": ["What is Angiostasis?", "How much should I take?",
                      "is it safe while breastfeeding?"],
        "required": {1: ["1 capsule", "evening meal", "drowsiness"], 2: ["breastfeeding", "avoid"]},
        "forbidden": [r"tumou?r", r"cancer", r"wet\s*AMD", r"macular", r"AngiogenX"],
    },
    "clear-the-way-casual": {
        "questions": ["what is clear the way", "how much clear the way should i take?"],
        "gated": {1},
        "required": {0: ["serrapeptase", "blood thinner"],
                     1: ["1 capsule", "empty stomach", "blood thinner"]},
        "forbidden": [r"dissolv", r"resorb", r"reduc\w*\s+(?:\w+\s+){0,3}scar",
                      r"break\w*\s+down\s+scar", r"tumou?r", r"cancer",
                      r"menses", r"pregnan", r"fracture"],
    },
    "clear-the-way-followup": {
        "questions": ["What is Clear the Way?", "How much should I take?",
                      "can I take it with warfarin?"],
        "required": {1: ["1 capsule", "empty stomach"], 2: ["blood thinner", "physician"]},
        "forbidden": [r"dissolv", r"resorb", r"break\w*\s+down\s+scar", r"tumou?r",
                      r"cancer", r"menses", r"pregnan", r"fracture"],
    },
}


def _facts(question: str, prior=None, gated=False) -> str:
    """The facts block as chat() builds it. Older builds lack some arguments, so the same
    script still measures main as a baseline."""
    fn = getattr(app, "named_product_facts_block", None)
    if fn is None:
        return ""
    for kwargs in ({"gated": gated, "prior_user_turns": prior}, {"gated": gated}, {}):
        try:
            return fn(question, **kwargs)
        except TypeError:
            continue
    return ""


# A conversation that already holds the pre-fix wrong answer (knowledge-5a, 2026-10-09:
# the live chat repeated "You're asking about AngiogenX" after the fix deployed).
POISONED = {
    "angiostasis": [
        {"role": "user", "content": "How much Angiostasis should I take?"},
        {"role": "assistant", "content": "I notice your question uses Angiostasis, but the "
         "product is AngiogenX. Take 1 capsule daily with food. It supports wet AMD and "
         "healthy capillaries, and pairs with Clear the Way."},
    ],
    "clear-the-way": [
        {"role": "user", "content": "What is Clear the Way?"},
        {"role": "assistant", "content": "Clear the Way is a serrapeptase tissue editor that "
         "helps dissolve accumulated scar tissue, including the capsule around tumors."},
    ],
}


def ask(question: str, history=None, gate=False) -> tuple:
    """(answer, gated). history: earlier [{"role", "content"}] turns, as chat() sends
    them. gate=True runs the non-member consent classifier as chat() does."""
    vec = app.embed(question)
    context_str, _ = app.build_context(app.query_all_namespaces(vec))
    directive = app.build_product_directive(snippets_text=context_str, query_text=question)
    product_block = f"{directive}\n\n" if directive else ""
    prior = [h["content"] for h in (history or []) if h.get("role") == "user"]
    facts = _facts(question, prior)
    system = app.get_system_prompt("self-healing")
    gated = bool(gate and app._is_gated_question(question))
    if gated:
        system += app._EDUCATE_ONLY_POLICY
        # chat() swaps in the gated variant (label information), Glen 2026-10-09
        facts = _facts(question, prior, gated=True)
    content = (f"USER QUESTION: {question}\n\n"
               f"RETRIEVED SNIPPETS:\n{context_str}\n\n"
               f"{product_block}"
               f"{app._brief_synth_instruction()}"
               f"{facts}")
    msg = app._cl.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1024, system=system,
        messages=list(history or [])[-6:] + [{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text"), gated


def main(case_name: str, runs: int = 1, session: bool = False, gate: bool = False,
         poisoned: bool = False) -> int:
    """session=True asks the questions in one conversation, as a client would."""
    case = CASES[case_name]
    print(f"case={case_name} runs={runs} session={session} gate={gate} poisoned={poisoned}")
    failures = 0
    for n in range(runs):
        history = list(POISONED.get(case_name, [])) if poisoned else []
        for i, q in enumerate(case["questions"]):
            answer, gated = ask(q, history if (session or poisoned) else None, gate)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": answer}]
            missing = [p for p in case["required"].get(i, []) if p.lower() not in answer.lower()]
            # A term the question itself uses (the user's own condition) is not a defect.
            found = [p for p in case["forbidden"]
                     if re.search(p, answer, re.I) and not re.search(p, q, re.I)]
            found += [p for p in case.get("forbidden_by_q", {}).get(i, [])
                      if re.search(p, answer, re.I)]
            if gated:
                missing += [p for p in case.get("required_if_gated", {}).get(i, [])
                            if p.lower() not in answer.lower()]
            near = case.get("near_condition", {}).get(i)
            if near:
                for sent in re.split(r"(?<=[.!?])\s+", answer):
                    if "terms" in sent.lower():
                        continue
                    words = re.findall(r"[\w'-]+", sent)
                    pi = [k for k, w in enumerate(words) if w.lower() == near[0].lower()]
                    ci = [k for k, w in enumerate(words) if re.fullmatch(near[1], w, re.I)
                          or (w.lower() == "wet" and k + 1 < len(words) and words[k + 1] == "AMD")]
                    if any(abs(a - b) <= 8 for a in pi for b in ci):
                        found.append(f"product near condition: {sent.strip()[:120]}")
                        break
            ungated = gate and i in case.get("gated", set()) and not gated
            if ungated:
                print("expected a gated turn; the classifier said OPEN")
            ok = not missing and not found and not ungated
            failures += not ok
            print(f"=== run {n + 1} | {q} | {'PASS' if ok else 'FAIL'}{' | GATED' if gated else ''}")
            if missing:
                print(f"missing: {missing}")
            if found:
                print(f"forbidden: {found}")
            print(answer.strip(), "\n")
    print(f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    # Doppler runs the runner's command under zsh, which passes "--gate --session" as one
    # argument; split every argument on whitespace.
    argv = [x for a in sys.argv[1:] for x in a.split()]
    sys.argv[1:] = argv
    args = [a for a in argv if not a.startswith("--")]
    sys.exit(main(args[0], int(args[1]) if len(args) > 1 else 1,
                  session="--session" in sys.argv, gate="--gate" in sys.argv,
                  poisoned="--poisoned" in sys.argv))
