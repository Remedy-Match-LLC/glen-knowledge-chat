"""Which portal requests a staff member must confirm, and how to describe them.

Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md. Pure, so it is
testable without app.py. The hook in app.py decides WHO is staff; this decides WHAT.
"""
import re

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = tuple(re.compile(p) for p in (
    r"^/api/portal/[^/]+/folds$",      # PUT: the viewer's own fold record, never the client's
    r"^/chat/tts$",                    # read-aloud: makes audio, records nothing
    r"^/portal/logout$",               # signs this browser out; staff hold no client session
))
BACKGROUND = tuple(re.compile(p) for p in (
    r"^/api/portal/[^/]+/cards/[^/]+/open$",
    r"^/api/portal/[^/]+/open$",           # report read receipt
    r"^/api/invoice/[^/]+/open$",          # invoice read receipt
    r"^/api/portal/[^/]+/process-request$",
    r"^/api/intake/save-draft$",
    r"^/api/portal/[^/]+/recommendation/section$",
    r"^/api/portal/[^/]+/recommendation/click$",
    r"^/api/portal/[^/]+/eye-vision-report/state$",
    r"^/api/portal/[^/]+/scene-pref$",
    r"^/api/portal/time-zone/browser$",     # fills an empty zone; staff views save nothing
))
_DESCRIPTIONS = [      # first match wins, so the specific patterns come first
    (r"^/api/(onboarding|consult)/book$", "books a real appointment and emails {c} and Rae"),
    (r"appointment-proposals/\d+/confirm$", "confirms an appointment for {c} and books it"),
    (r"appointment-proposals", "sends an appointment request or reply to the team"),
    (r"/pay-consent$", "lets a caregiver pay {cp} orders"),
    (r"^/api/peer/optin$", "changes {cp} peer connection setting"),
    (r"/(block|report)$", "blocks or reports a member as {c}"),
    (r"/request-analysis$", "sends {cp} request for an analysis to the team"),
    (r"/request-review$", "sends {cp} request for a review to the team"),
    (r"/recommendation/", "changes {cp} recommendations"),
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
    if any(p.search(path) for p in EXEMPT):
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
