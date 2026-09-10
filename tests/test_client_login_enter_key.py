"""Enter in the password box must check the password, not email a sign-in link.

static/client-login.html puts the password box and the "Send my sign-in link"
button in one form, and that button is the form's default. Enter in the password
box therefore ran implicit submission and mailed a link WITHOUT ever checking the
password. A password manager fills both fields and the user presses Enter, which
on 2026-09-10 looked to Glen like signing in did nothing at all.

The page has no JS test harness, so these are structural. The behaviour itself was
verified in Chrome against the served file: Enter in the password box called
/portal/password-login and no submit event fired, while the link button and Enter
in the email field both still called /portal/login-request.

Note for anyone changing this: `event.submitter` cannot tell the two apart. Implicit
submission reports the form's DEFAULT BUTTON as the submitter, not None, so a
submit-side test silently never fires. That was tried first and it failed live.
"""
import pathlib
import re

PAGE = pathlib.Path(__file__).resolve().parents[1] / "static" / "client-login.html"


def _src():
    return PAGE.read_text(encoding="utf-8")


def test_the_password_box_is_still_inside_the_submitting_form():
    """If it ever moves out, the handler below is dead weight and should go too."""
    src = _src()
    form = src[src.index('<form id="loginForm">'):src.index("</form>")]
    assert 'id="password"' in form, "the password box left the form; revisit the Enter fix"
    assert 'id="go"' in form and 'type="submit"' in form


def test_enter_in_the_password_box_is_intercepted():
    src = _src()
    handler = re.search(
        r"getElementById\('password'\)\.addEventListener\('keydown'.*?\}\);",
        src, re.S)
    assert handler, "no keydown handler on the password box"
    body = handler.group(0)
    assert "preventDefault" in body, "Enter still reaches implicit form submission"
    assert "signInWithPassword" in body, "Enter does not run the password sign-in"


def test_the_sign_in_button_and_the_key_share_one_function():
    """Two copies of the sign-in call would drift apart."""
    src = _src()
    assert src.count("signInWithPassword") == 3, \
        "expected one definition plus the click and keydown wirings"
    assert "async function signInWithPassword()" in src


def test_the_link_button_still_sends_a_link():
    """The control. The fix must not swallow the path that already worked."""
    src = _src()
    assert "/portal/login-request" in src
    submit = src[src.index('form.addEventListener("submit"'):]
    assert "/portal/login-request" in submit[:900], \
        "the submit handler no longer requests a sign-in link"


def test_no_em_dash_in_anything_a_client_reads():
    """Glen's standing rule. Four were in this page's error copy."""
    src = _src()
    offenders = [ln.strip() for ln in src.splitlines()
                 if "—" in ln and not ln.strip().startswith("/*")]
    assert not offenders, f"em dash in client-facing copy: {offenders}"
