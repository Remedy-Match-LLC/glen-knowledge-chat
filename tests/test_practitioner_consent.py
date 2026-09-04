"""Which wording a client actually agreed to.

The consent existed but was a bare 0/1. Glen widened the wording on 2026-09-03
from "wellness results" to "wellness results and activity including purchases",
and a bare flag cannot tell the two cohorts apart: everyone who ticked the old
box agreed to results, not to their purchase history.

Glen's ruling: existing consenters keep the OLD scope until they re-affirm. That
is only possible if the version is recorded at the moment of consent, which is
why this is cheap now and impossible retroactively.
"""
import pytest

from dashboard import practitioner_consent as pc


def test_the_current_wording_names_purchases():
    assert "purchase" in pc.CURRENT_TEXT.lower()
    assert pc.CURRENT_VERSION


def test_the_current_version_covers_purchases():
    assert pc.covers_purchases(pc.CURRENT_VERSION) is True


def test_an_unversioned_consent_does_not_cover_purchases():
    """Every row written before today. They agreed to wellness results only."""
    for legacy in (None, "", "  "):
        assert pc.covers_purchases(legacy) is False


def test_an_unknown_version_never_covers_purchases():
    """Fail closed: an unrecognised version is not a licence to share more."""
    for junk in ("2099-v9", "yes", "1", 7, {"v": 1}):
        assert pc.covers_purchases(junk) is False


def test_the_legacy_wording_is_still_available_to_show_someone():
    """A client on the old consent must be shown what THEY agreed to, not the
    text someone else agreed to later."""
    assert "purchase" not in pc.text_for(None).lower()
    assert pc.text_for(None) != pc.CURRENT_TEXT
    assert pc.text_for(pc.CURRENT_VERSION) == pc.CURRENT_TEXT


def test_text_for_an_unknown_version_falls_back_to_the_narrowest():
    assert pc.text_for("2099-v9") == pc.text_for(None)


def test_versions_are_ordered_narrowest_first_so_scope_only_widens():
    """A later version must never describe LESS than an earlier one, or a
    re-affirmation would quietly shrink what the client agreed to."""
    scopes = [set(pc.SCOPES[v]) for v in pc.VERSION_ORDER]
    for narrow, wide in zip(scopes, scopes[1:]):
        assert narrow <= wide, "a later consent version dropped a scope"


def test_results_are_covered_by_every_version_including_the_legacy_one():
    # The continuity roster relies on this: widening the wording must not
    # invalidate the consent that already gates it.
    assert pc.covers_results(None) is True
    assert pc.covers_results(pc.CURRENT_VERSION) is True


# --- storage: the version has to be recorded where the consent is -------------

def _cx(tmp_path):
    """Build the table with the module's OWN DDL and EVERY migration it defines.

    Hand-picking two migrations left out the ones create_membership actually
    writes to, so the fixture failed correct code. Discovering them keeps this
    honest as more are added.
    """
    from dashboard import db, subscriptions as subs
    cx = db.connect(str(tmp_path / "c.db"))
    subs.init_subscriptions_table(cx)
    for name in sorted(n for n in dir(subs) if n.startswith("migrate_")):
        getattr(subs, name)(cx)
    return cx


def test_a_new_consent_records_which_wording_it_was(tmp_path):
    from dashboard import subscriptions as subs
    cx = _cx(tmp_path)
    sid = subs.create_membership(
        cx, email="a@example.com", stripe_customer_id="c", stripe_payment_method_id="pm",
        amount_cents=1000, next_charge_date="2026-10-01",
        attributed_practitioner_id="pid-1", practitioner_share_consent=1,
        practitioner_consent_version=pc.CURRENT_VERSION)
    row = cx.execute("SELECT practitioner_share_consent, practitioner_consent_version "
                     "FROM subscriptions WHERE id=?", (sid,)).fetchone()
    cx.close()
    assert row[0] == 1
    assert row[1] == pc.CURRENT_VERSION
    assert pc.covers_purchases(row[1]) is True


def test_declining_records_no_version(tmp_path):
    """A version on a row that never consented would be a false record of assent."""
    from dashboard import subscriptions as subs
    cx = _cx(tmp_path)
    sid = subs.create_membership(
        cx, email="b@example.com", stripe_customer_id="c", stripe_payment_method_id="pm",
        amount_cents=1000, next_charge_date="2026-10-01",
        attributed_practitioner_id="pid-1", practitioner_share_consent=0,
        practitioner_consent_version=pc.CURRENT_VERSION)
    row = cx.execute("SELECT practitioner_share_consent, practitioner_consent_version "
                     "FROM subscriptions WHERE id=?", (sid,)).fetchone()
    cx.close()
    assert row[0] == 0 and not row[1]


def test_an_existing_row_reads_as_the_old_scope(tmp_path):
    """The migration must not backfill anyone into the new wording."""
    from dashboard import subscriptions as subs
    cx = _cx(tmp_path)
    sid = subs.create_membership(
        cx, email="c@example.com", stripe_customer_id="c", stripe_payment_method_id="pm",
        amount_cents=1000, next_charge_date="2026-10-01",
        attributed_practitioner_id="pid-1", practitioner_share_consent=1)
    ver = cx.execute("SELECT practitioner_consent_version FROM subscriptions WHERE id=?",
                     (sid,)).fetchone()[0]
    cx.close()
    assert not ver, "an unversioned consent must stay unversioned"
    assert pc.covers_results(ver) is True
    assert pc.covers_purchases(ver) is False


def test_the_migration_is_idempotent(tmp_path):
    from dashboard import subscriptions as subs
    cx = _cx(tmp_path)
    subs.migrate_add_consent_version_column(cx)   # again
    cols = [r[1] for r in cx.execute("PRAGMA table_info(subscriptions)")]
    cx.close()
    assert cols.count("practitioner_consent_version") == 1


def test_the_signup_checkbox_shows_exactly_the_current_wording():
    """The copy a client ticks and the text the version names must be the same
    string. If they drift, the record says they agreed to something they never
    read, which is the whole failure this versioning exists to prevent."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    html = (root / "static" / "practitioner-client.html").read_text()
    assert pc.CURRENT_TEXT in html, "signup copy does not match CURRENT_TEXT"
    assert pc.TEXTS["legacy"] not in html, "the superseded wording is still on the page"


def test_the_version_is_stamped_server_side_never_taken_from_the_request():
    """A client must not be able to name the wording it agreed to.

    Parsed, not grepped: both writers must read the constant, and neither may
    pull a version out of the request body.
    """
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)

    def _version_args(fn):
        out = []
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                for k in c.keywords:
                    if k.arg == "practitioner_consent_version":
                        out.append(ast.unparse(k.value))
        return out

    stamped = [a for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
               for a in _version_args(f)]
    assert stamped, "nothing stamps a consent version any more"
    for expr in stamped:
        assert "_pconsent.CURRENT_VERSION" in expr, expr
        assert "request" not in expr and "body" not in expr and "md.get" not in expr, expr
    # and the raw SQL writer does the same
    assert "practitioner_consent_version=?" in src
    assert 'ADD COLUMN practitioner_consent_version TEXT' in src


def test_the_prepay_writer_stores_no_version_when_consent_was_declined():
    """The one writer I cannot execute here: raw SQL against prepay_term_grants,
    inside a Stripe fulfilment path. The subscriptions writer and set_consent are
    both covered behaviourally above; this holds the shape of the third.

    `X if share_consent else None` is what keeps a version off a row that said no,
    and a version on a declining row would be a false record of assent.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    i = src.index("practitioner_consent_version=?")
    window = src[i:i + 400]
    assert "_pconsent.CURRENT_VERSION if share_consent else None" in window, window[:300]


# --- the client can see it and take it back -----------------------------------

def _seed(tmp_path, consent=1, version=None):
    from dashboard import subscriptions as subs
    cx = _cx(tmp_path)
    subs.create_membership(
        cx, email="Pat@Example.com", stripe_customer_id="c", stripe_payment_method_id="pm",
        amount_cents=1000, next_charge_date="2026-10-01",
        attributed_practitioner_id="pid-1", practitioner_share_consent=consent,
        practitioner_consent_version=version)
    return cx


def test_the_portal_can_show_what_they_agreed_to(tmp_path):
    from dashboard import continuity_view as cv
    cx = _seed(tmp_path, 1, pc.CURRENT_VERSION)
    state = cv.consent_state(cx, "pat@example.com")
    cx.close()
    assert len(state) == 1
    assert state[0]["practitioner_id"] == "pid-1" and state[0]["consent"] is True
    assert pc.text_for(state[0]["version"]) == pc.CURRENT_TEXT


def test_a_legacy_consent_shows_the_wording_they_actually_agreed_to(tmp_path):
    from dashboard import continuity_view as cv
    cx = _seed(tmp_path, 1, None)
    state = cv.consent_state(cx, "pat@example.com")
    cx.close()
    assert state[0]["consent"] is True
    assert pc.text_for(state[0]["version"]) == pc.TEXTS["legacy"]
    assert pc.covers_purchases(state[0]["version"]) is False


def test_a_client_with_no_attributed_practitioner_sees_nothing(tmp_path):
    from dashboard import continuity_view as cv
    cx = _cx(tmp_path)
    assert cv.consent_state(cx, "nobody@example.com") == []
    assert cv.consent_state(cx, "") == []
    cx.close()


def test_withdrawing_consent_closes_the_gate_and_clears_the_version(tmp_path):
    """The point of a toggle: it has to actually revoke access."""
    from dashboard import continuity_view as cv
    cx = _seed(tmp_path, 1, pc.CURRENT_VERSION)
    assert cv.authorized_patient(cx, "pid-1", "pat@example.com") is True
    assert cv.set_consent(cx, "pat@example.com", "pid-1", False, pc.CURRENT_VERSION) >= 1
    assert cv.authorized_patient(cx, "pid-1", "pat@example.com") is False
    state = cv.consent_state(cx, "pat@example.com")
    cx.close()
    assert state[0]["consent"] is False and state[0]["version"] == ""


def test_granting_through_the_portal_stamps_the_current_wording(tmp_path):
    """Agreeing HERE is agreeing to what this page says today, so a legacy
    consenter who re-affirms moves to the new scope."""
    from dashboard import continuity_view as cv
    cx = _seed(tmp_path, 1, None)
    assert pc.covers_purchases(cv.consent_state(cx, "pat@example.com")[0]["version"]) is False
    cv.set_consent(cx, "pat@example.com", "pid-1", True, pc.CURRENT_VERSION)
    state = cv.consent_state(cx, "pat@example.com")
    cx.close()
    assert pc.covers_purchases(state[0]["version"]) is True


def test_consent_cannot_be_set_for_someone_else(tmp_path):
    from dashboard import continuity_view as cv
    cx = _seed(tmp_path, 1, pc.CURRENT_VERSION)
    assert cv.set_consent(cx, "someone-else@example.com", "pid-1", False, None) == 0
    assert cv.authorized_patient(cx, "pid-1", "pat@example.com") is True
    assert cv.set_consent(cx, "", "pid-1", False, None) == 0
    cx.close()


def test_the_portal_renders_a_practitioner_sharing_control():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "static" / "client-portal.html").read_text()
    assert "pr-share" in html and "practitioner_consents" in html
    assert "/practitioner-consent" in html
    # It must show what THEY agreed to when consented, not today's copy.
    assert "c.consent ? c.text : c.current_text" in html
    # And say plainly that it can be withdrawn.
    assert "turn this off at any time" in html


def test_the_consent_route_is_token_scoped_and_stamps_server_side():
    """A client may revoke their own consent and no one else's, and may not name
    the wording they agreed to."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    fn = next(f for f in ast.walk(ast.parse(src)) if isinstance(f, ast.FunctionDef)
              and f.name == "api_portal_practitioner_consent")
    body = ast.unparse(fn)
    assert "_portal_record_for(cx, token)" in body, "route is not token-scoped"
    assert "_pconsent.CURRENT_VERSION" in body, "version is not stamped server-side"
    for forbidden in ("data.get('version')", "data.get('email')", "data.get('consent_version')"):
        assert forbidden not in body, forbidden


def test_the_portal_payload_sends_each_clients_own_wording():
    """Server side of the same promise the toggle makes.

    Sending CURRENT_TEXT for every row would tell a legacy consenter they had
    agreed to share their purchases, which they had not. app.py needs a live
    Postgres to import, so this is asserted structurally: the payload must call
    text_for() on the ROW's version, not read the constant.
    """
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Subscript)
                       and isinstance(t.slice, ast.Constant) and t.slice.value == "text"
                       for t in n.targets)]
    assert assigns, "the portal payload no longer sets a consent text"
    for a in assigns:
        expr = ast.unparse(a.value)
        assert "text_for" in expr, f"payload text is not per-version: {expr}"
        assert "version" in expr, f"payload text ignores the row's version: {expr}"


def test_the_version_column_arrives_wherever_the_consent_column_does(tmp_path):
    """Ten places build this table, and create_membership writes both columns.

    Adding the version as its own migration meant a caller that ran only the
    consent migration got a fresh table missing a column the writer needs, which
    is a 500 on signup, not a test failure. Caught by test_biofield_trial.
    """
    from dashboard import db, subscriptions as subs
    cx = db.connect(str(tmp_path / "one.db"))
    subs.init_subscriptions_table(cx)
    subs.migrate_add_membership_columns(cx)
    subs.migrate_add_term_cap_column(cx)
    subs.migrate_add_attribution_column(cx)
    subs.migrate_add_consent_column(cx)          # and nothing else
    cols = [r[1] for r in cx.execute("PRAGMA table_info(subscriptions)")]
    assert "practitioner_consent_version" in cols
    # and the writer works against exactly that schema
    subs.create_membership(
        cx, email="z@example.com", stripe_customer_id="c", stripe_payment_method_id="pm",
        amount_cents=1000, next_charge_date="2026-10-01",
        attributed_practitioner_id="pid-1", practitioner_share_consent=1,
        practitioner_consent_version=pc.CURRENT_VERSION)
    cx.close()
