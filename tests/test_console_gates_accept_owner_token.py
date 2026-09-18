"""Every console gate that accepts the master secret must also accept an OWNER token.

Glen, 2026-09-18: "Rae still encounters the same problem in the console: it wants her to
enter console key when she tries to navigate between tabs."

WHY #1716 DID NOT FIX IT. That change taught dashboard.require_console_key to resolve a
cookie-borne owner token, which covered the 153 routes behind that decorator. It did not
touch the gates that carry their OWN inline copy of the same rule. Five did:

    /api/ask                    POST, the console's ask box
    /api/guide                  POST, the console's guide
    /console/biofield-intake    a PAGE route
    /console/clinical-tags      a PAGE route
    _sales_console_ok()         gates the sales-page console endpoints

Two of those are page routes, which is exactly the reported symptom: navigating to that
tab returns 401 and the console falls back to asking for the key. Rae holds an owner token
by design, because the verify route mints one that can be revoked on its own and never
escalates to the master secret.

THE REAL FIX IS THE SCAN BELOW, not the five. The rule was written out by hand in six
places and I repaired one. A seventh copy will be written eventually, and the scanning
test is what catches it. Doing this by hand a third time is the failure to avoid.

WHAT MUST NOT CHANGE: a VA token (Shaira, role 'va') is still refused everywhere.
_owner_token_ok is the gate precisely because it is narrower than "is this a valid token".
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SRC = APP.read_text()
TREE = ast.parse(SRC)

# Both of these resolve an IDENTITY rather than answering yes/no, and both already admit
# Rae by a route of their own. Widening them would grant authorship, which is a different
# decision from opening a gate, so they are excluded deliberately rather than silently.
#   _appointment_staff_actor  looks Rae up by NAME in access_tokens
#   _bos_actor                defers to _bos_rbac.resolve_actor, which maps Rae to owner
EXEMPT = {"_appointment_staff_actor", "_bos_actor"}

FIXED = ("api_ask", "api_guide", "console_biofield_intake",
         "console_clinical_tags", "_sales_console_ok")


LINES = SRC.splitlines()


def _functions():
    """name -> source, sliced by line range.

    NOT ast.get_source_segment: it re-splits the whole 57k-line file per call, which made
    this file take minutes instead of a second. Computed once and cached.
    """
    global _FN_CACHE
    if _FN_CACHE is None:
        out = {}
        for node in ast.walk(TREE):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", None) or node.lineno
                out[node.name] = "\n".join(LINES[node.lineno - 1:end])
        _FN_CACHE = out
    return _FN_CACHE.items()


_FN_CACHE = None


def _gates():
    """Functions that read the presented key AND compare it to a configured secret."""
    for name, body in _functions():
        if name in ("_present_console_key", "_owner_token_ok"):
            continue
        if "_present_console_key()" not in body:
            continue
        if "CONSOLE_SECRET" not in body:
            continue
        yield name, body


@pytest.mark.parametrize("name", FIXED)
def test_each_reported_gate_now_admits_an_owner_token(name):
    body = dict(_functions())[name]
    assert "_owner_token_ok" in body, (
        f"{name} compares against the master secret only, so Rae gets a 401 there"
    )


LAUNCHERS = ("console_biofield_intake", "console_clinical_tags")


def _fn_node(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


@pytest.mark.parametrize("name", LAUNCHERS)
def test_a_launcher_never_hands_the_master_secret_to_an_owner_token(name):
    """The trap in the obvious fix, and why these two are not plain widenings.

    Both routes redirect via local_tool_url, which puts the server's MASTER
    CONSOLE_SECRET in the query string. Letting an owner token through would mail Rae the
    master key in a Location header, readable from her own browser history. The owner
    token exists so it can be revoked on its own and NEVER escalates to that secret.

    THIS IS CHECKED STRUCTURALLY, not by string order. A first version of this test
    asserted that `_owner_token_ok` appeared before `local_tool_url` and that a `return`
    sat between them. It PASSED with the escalation reintroduced, because every one of
    those strings was still present. Text presence cannot express "this branch cannot
    reach that call"; the syntax tree can.
    """
    fn = _fn_node(name)
    branches = [n for n in ast.walk(fn)
                if isinstance(n, ast.If)
                and "_owner_token_ok" in ast.dump(n.test)]
    assert len(branches) == 1, f"expected exactly one owner branch, found {len(branches)}"
    owner_if = branches[0]

    # Every path through the owner branch must leave the function.
    assert owner_if.body, "the owner branch is empty, so it falls through to the redirect"
    assert isinstance(owner_if.body[-1], ast.Return), (
        "the owner branch must END in a return; anything else reaches the "
        "secret-bearing redirect below"
    )
    # And that return must not be the redirect itself.
    dumped = ast.dump(owner_if.body[-1])
    assert "local_tool_url" not in dumped and "redirect" not in dumped, (
        "the owner branch returns the redirect, which carries the master secret"
    )
    # It should say what the tab is rather than merely refusing.
    assert "local_tool" in dumped


@pytest.mark.parametrize("name", LAUNCHERS)
def test_a_launcher_still_refuses_an_unknown_caller(name):
    """Widening must not have opened the door to someone holding nothing at all."""
    body = dict(_functions())[name]
    assert '"Unauthorized"' in body and "401" in body


def test_the_secret_bearing_helper_is_still_the_reason_for_that_care():
    """If local_tool_url stops carrying the secret, the launchers can become plain
    widenings and these tests should be revisited deliberately."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "dashboard" / "console_launcher.py").read_text()
    assert "?key=" in src, (
        "local_tool_url no longer puts the secret in the URL; re-read the launcher tests"
    )


def test_no_other_console_gate_has_the_same_hole():
    """The regression guard. A new gate written the old way fails here rather than
    reaching Rae as a fourth round of the same bug."""
    bad = sorted(n for n, b in _gates()
                 if "_owner_token_ok" not in b and n not in EXEMPT)
    assert not bad, (
        "these gates accept the master secret but refuse an owner token: "
        + ", ".join(bad)
        + ". Add `or _owner_token_ok(key)`, or add the name to EXEMPT with the reason."
    )


def test_each_exemption_still_earns_its_place():
    """An EXEMPT entry that stops admitting Rae is worse than no exemption, because the
    list is what stops the scan complaining. Each is pinned to the REASON it was excused."""
    fns = dict(_functions())
    assert "access_tokens" in fns["_appointment_staff_actor"], (
        "excused because it resolves Rae by name in access_tokens; it no longer does"
    )
    assert '"rae"' in fns["_appointment_staff_actor"]
    assert "resolve_actor" in fns["_bos_actor"], (
        "excused because it defers to _bos_rbac.resolve_actor; it no longer does"
    )
    assert "role_for_token" in fns["_bos_actor"]


def test_the_exempt_list_has_not_grown_quietly():
    """Excusing a gate is how this bug comes back. Two is the number that was reasoned
    about; a third needs the same argument made out loud."""
    assert EXEMPT == {"_appointment_staff_actor", "_bos_actor"}


def test_the_owner_check_still_refuses_a_va():
    """The whole point of gating on OWNER rather than on token validity."""
    body = dict(_functions())["_owner_token_ok"]
    assert "_bos_rbac.OWNER" in body
    assert "_role_for_token" in body


def test_the_two_page_routes_are_the_ones_that_produce_the_symptom():
    """Pin the mechanism, not just the fix. A 401 on an API call shows an error; a 401 on
    a PAGE is what makes the console re-prompt for the key."""
    for path in ('@app.route("/console/biofield-intake")',
                 '@app.route("/console/clinical-tags")'):
        assert path in SRC, path
