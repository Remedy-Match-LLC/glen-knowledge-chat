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
            "How much Angiostasis should I take each day?",
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
            "How much Clear the Way should I take each day?",
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


def ask(question: str) -> str:
    vec = app.embed(question)
    context_str, _ = app.build_context(app.query_all_namespaces(vec))
    directive = app.build_product_directive(snippets_text=context_str, query_text=question)
    product_block = f"{directive}\n\n" if directive else ""
    content = (f"USER QUESTION: {question}\n\n"
               f"RETRIEVED SNIPPETS:\n{context_str}\n\n"
               f"{product_block}"
               f"{app._brief_synth_instruction()}"
               f"{_facts(question)}")
    msg = app._cl.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1024,
        system=app.get_system_prompt("self-healing"),
        messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def main(case_name: str, runs: int = 1) -> int:
    case = CASES[case_name]
    failures = 0
    for n in range(runs):
        for i, q in enumerate(case["questions"]):
            answer = ask(q)
            missing = [p for p in case["required"].get(i, []) if p.lower() not in answer.lower()]
            found = [p for p in case["forbidden"] if re.search(p, answer, re.I)]
            ok = not missing and not found
            failures += not ok
            print(f"=== run {n + 1} | {q} | {'PASS' if ok else 'FAIL'}")
            if missing:
                print(f"missing: {missing}")
            if found:
                print(f"forbidden: {found}")
            print(answer.strip(), "\n")
    print(f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1))
