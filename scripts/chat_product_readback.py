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
        ],
        # Each question's answer must contain every phrase (case-insensitive).
        "required": {
            0: ["Angiostasis", "green tea"],
            1: ["1 capsule", "evening meal", "drowsiness", "breastfeeding",
                "blood thinner", "medical treatment"],
        },
        "forbidden": [r"tumou?r", r"cancer", r"wet\s*AMD", r"macular", r"AngiogenX"],
    },
    "clear-the-way": {
        "questions": [
            "What is Clear the Way?",
            "How much Clear the Way should I take, and are there any cautions?",
        ],
        "required": {
            0: ["Clear the Way", "serrapeptase", "blood thinner"],
            1: ["1 capsule", "empty stomach", "blood thinner"],
        },
        "forbidden": [r"dissolv", r"resorb", r"reduc\w*\s+(?:\w+\s+){0,3}scar", r"break\w*\s+down\s+scar",
                      r"tumou?r", r"cancer"],
    },
}


def _facts(question: str) -> str:
    # Absent before PR #1951, so the same script measures main as the baseline.
    return getattr(app, "named_product_facts_block", lambda q: "")(question)


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
    facts = _facts(question)
    system = app.get_system_prompt("self-healing")
    gated = bool(gate and app._is_gated_question(question))
    if gated:
        system += app._EDUCATE_ONLY_POLICY
        # chat() swaps in the gated variant (label information), Glen 2026-10-09
        facts = getattr(app, "named_product_facts_block", lambda q, gated=False: "")(question, gated=True)
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
            found = [p for p in case["forbidden"] if re.search(p, answer, re.I)]
            ok = not missing and not found
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0], int(args[1]) if len(args) > 1 else 1,
                  session="--session" in sys.argv, gate="--gate" in sys.argv,
                  poisoned="--poisoned" in sys.argv))
