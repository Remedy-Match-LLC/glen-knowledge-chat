import sqlite3


def _person(cx, email="password@example.com"):
    from dashboard import portal_identity as pi
    pi._ensure_people_table(cx)
    cx.execute(
        "INSERT INTO people (email,name,roles,created_at,updated_at) VALUES (?,?,?,?,?)",
        (email, "Password Client", '["client"]', "t", "t"),
    )
    cx.commit()
    return cx.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()[0]


def test_password_hash_roundtrip_and_wrong_password(tmp_path):
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(str(tmp_path / "auth.db"))
    pid = _person(cx)
    pa.set_password(cx, pid, "a correct horse battery staple", email="password@example.com")
    encoded = cx.execute("SELECT password_hash FROM portal_credentials WHERE person_id=?", (pid,)).fetchone()[0]
    assert encoded.startswith("$argon2id$")
    assert "correct horse" not in encoded
    assert pa.verify_password(cx, "password@example.com", "a correct horse battery staple") == pid
    assert pa.verify_password(cx, "password@example.com", "definitely incorrect") is None


def test_password_policy_rejects_short_values():
    from dashboard import portal_auth as pa
    ok, message = pa.validate_password("too-short")
    assert not ok
    assert "12" in message


def test_password_reset_is_one_time_and_revokes_old_sessions(tmp_path):
    from dashboard import portal_auth as pa, portal_identity as pi
    cx = sqlite3.connect(str(tmp_path / "auth.db"))
    pid = _person(cx, "reset@example.com")
    old_session = pi.create_client_session(cx, pid, "reset@example.com")
    reset = pa.create_password_reset(cx, pid, "reset@example.com")
    assert pa.validate_password_reset(cx, reset) == pid
    assert pa.consume_password_reset(cx, reset, "new secure password value") == pid
    assert pa.consume_password_reset(cx, reset, "another secure password") is None
    assert pi.identity_from_session(cx, old_session) is None
    assert pa.verify_password(cx, "reset@example.com", "new secure password value") == pid


def test_logout_revokes_only_presented_session(tmp_path):
    from dashboard import portal_auth as pa, portal_identity as pi
    cx = sqlite3.connect(str(tmp_path / "auth.db"))
    pid = _person(cx, "logout@example.com")
    one = pi.create_client_session(cx, pid, "logout@example.com")
    two = pi.create_client_session(cx, pid, "logout@example.com")
    assert pa.revoke_session(cx, one)
    assert pi.identity_from_session(cx, one) is None
    assert pi.identity_from_session(cx, two) is not None


def test_repeated_failures_temporarily_lock_password(tmp_path):
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(str(tmp_path / "auth.db"))
    pid = _person(cx, "locked@example.com")
    pa.set_password(cx, pid, "the right password is long")
    for _ in range(pa.MAX_FAILED_ATTEMPTS):
        assert pa.verify_password(cx, "locked@example.com", "the wrong password value") is None
    locked_until = cx.execute(
        "SELECT locked_until FROM portal_credentials WHERE person_id=?", (pid,)).fetchone()[0]
    assert locked_until
    assert pa.verify_password(cx, "locked@example.com", "the right password is long") is None
