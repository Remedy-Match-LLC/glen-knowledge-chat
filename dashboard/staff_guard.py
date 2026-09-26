"""Which portal requests a staff member must confirm, and how to describe them.

Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md. Pure, so it is
testable without app.py. The hook in app.py decides WHO is staff; this decides WHAT.
"""
import re

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = (re.compile(r"^/api/portal/[^/]+/folds$"),)            # PUT: the viewer's own folds
BACKGROUND = tuple(re.compile(p) for p in (
    r"^/api/portal/[^/]+/cards/[^/]+/open$",
    r"^/api/portal/[^/]+/process-request$",
    r"^/api/intake/save-draft$",
    r"^/api/portal/[^/]+/recommendation-section$",
    r"^/api/portal/[^/]+/eye-vision-state$",
    r"^/api/portal/[^/]+/scene-pref$",
))
_DESCRIPTIONS = [
    (r"^/api/(onboarding|consult)/book$", "books a real appointment and emails {c} and Rae"),
    (r"appointment-proposals", "sends an appointment request or reply to the team"),
    (r"^/calendar/register$", "registers {c} for a live session with Zoom"),
    (r"/cancel$", "cancels {cp} subscription or plan"),
    (r"(checkout|/subscribe|caregiver-pay|coach-subscribe)", "starts a payment as {c}"),
    (r"/chat$", "sends a chat message as {c}"),
    (r"^/api/intake/submit$", "submits {cp} intake form"),
    (r"(add-to-invoice|order-add|/cart|set-qty|set-format)", "changes {cp} cart or invoice"),
    (r"(consent|agree-tos|sharing|notify-pref|scan-prefs|cc-pref)", "changes {cp} consent or preferences"),
    (r"(photo|documents|five-voice)", "uploads a file to {cp} record"),
    (r"(coach-thread|/api/peer|coach-request)", "sends a message or request as {c} to another member"),
]


def classify(method, path):
    method = (method or "").upper()
    if method not in _WRITE:
        return "pass"
    if method == "PUT" and any(p.search(path) for p in EXEMPT):
        return "exempt"
    if method == "POST" and any(p.search(path) for p in BACKGROUND):
        return "background"
    return "guard"


def describe(path, client):
    c = (client or "").strip() or "the client"
    cp = (c + "'s") if client else "the client's"
    for pattern, text in _DESCRIPTIONS:
        if re.search(pattern, path or ""):
            return "This " + text.format(c=c, cp=cp) + "."
    return f"This changes {cp} account."
