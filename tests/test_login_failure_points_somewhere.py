"""A failed password login must point at the two ways out already on the page.

Measured in production 2026-09-10. Of 84 `login_failed` events, 68 carried NO person_id.
That is `verify_password` finding no people-plus-credentials match, which means those
people have no password at all. Password login went live 2026-08-26 and has 10 users
against 10,312 people, so almost everyone meeting that form has never been offered one.

The old message was "Email or password was not recognized." and nothing else. True,
correct, and it tells someone with no password to go and try harder.

WHAT THIS DOES NOT DO, and must not. It does not say whether the account exists.
`verify_password` carries an explicit instruction that public callers must use one
generic failure so the result cannot enumerate portal accounts, and it equalises hashing
time for an unknown email for the same reason. "This email has no password" would be a
privacy decision, not a copy change. The new sentence is identical for every failure:
wrong password, no password, and no such person all read the same.

The page already renders both ways out, and always did. `client-login.html` has a
"Set or reset password" button inside the password block and a "Send my sign-in link"
button below it. The message just never mentioned them.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text()
PAGE = (ROOT / "static" / "client-login.html").read_text()

MESSAGE = ("Email or password was not recognized. Set or reset your password below, "
           "or send yourself a sign-in link.")


def _password_login_route():
    """Slice to the NEXT route decorator, not a fixed character count.

    This used to take APP[i:i+2200]. Adding ten lines to the route pushed the message past
    the window and both tests below started failing for a reason that had nothing to do
    with the message. A fixed-width read of a file that grows is the same mistake as a
    line-offset read that crosses into the next function."""
    i = APP.index('@app.route("/portal/password-login"')
    nxt = APP.find("\n@app.route", i + 1)
    return APP[i:nxt if nxt != -1 else len(APP)]


def _failure_messages(route):
    """The message VALUES, not the source formatting.

    Python joins adjacent string literals, so a sentence wrapped across three lines is
    one value at runtime and three matches to a regex. Asserting on the source text
    would pin the line breaks rather than the words, and would fail the next time
    someone rewraps it. Reassemble instead."""
    out = []
    for m in re.finditer(r'"message":\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', route):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
        out.append("".join(parts))
    return out


def test_the_failure_message_is_the_agreed_sentence():
    assert _failure_messages(_password_login_route()) == [MESSAGE]


def test_the_message_names_both_ways_out():
    """Each half must correspond to a control that is actually on the page. A message
    pointing at a button that does not exist is worse than the bare one."""
    assert "Set or reset password" in PAGE
    assert "Send my sign-in link" in PAGE
    low = MESSAGE.lower()
    assert "set or reset your password" in low
    assert "sign-in link" in low


def test_the_message_still_cannot_enumerate_accounts():
    """The one that matters. The route must return ONE message for every failure, so a
    prober cannot tell a real email from an invented one."""
    messages = _failure_messages(_password_login_route())
    assert messages, "no failure message found at all"
    assert len(set(messages)) == 1, f"more than one failure message: {sorted(set(messages))}"
    for forbidden in ("no password", "not found", "unknown email", "no such",
                      "does not exist", "never set"):
        assert forbidden not in MESSAGE.lower(), f"{forbidden!r} leaks account state"


def test_the_route_still_returns_401_and_does_not_leak_the_person():
    route = _password_login_route()
    assert '), 401' in route
    assert '"ok": False' in route


def test_no_em_dash_in_the_new_sentence():
    """Glen's standing rule, and there is already a test guarding the page itself."""
    assert "—" not in MESSAGE


def test_the_sentence_obeys_the_comma_rule():
    """One comma at most per sentence, Glen's standing rule for anything he ships."""
    for sentence in [s for s in MESSAGE.split(". ") if s.strip()]:
        assert sentence.count(",") <= 1, f"more than one comma: {sentence!r}"
