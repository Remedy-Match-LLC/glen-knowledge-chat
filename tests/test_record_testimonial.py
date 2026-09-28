"""Staff record a testimonial a client sent another way (email, letter), as the client wrote it.

Glen, 2026-09-27, via communication-9e: "Be sure to record her testimonial in our testimonials
for potential use in chat, websites, social media, etc." Recording is not publishing: consent is
NOT given unless the client gave it, so nothing reaches the chat, the site or social media
until she does and Glen approves it."""
import sqlite3

import pytest

SECRET = "test-secret"
VA = "va-token-testimonial"
OWNER = {"X-Console-Key": SECRET}
TEXT = "I attribute all of this to the Vitamins that they suggested."


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    appmod.app.config["TESTING"] = True
    scored = []
    from dashboard import review_scoring as rs
    monkeypatch.setattr(rs, "score_review", lambda client, ctx, body, strip=None: scored.append(body) or {
        "compliance_ok": False, "reasons": "health claim", "quality_points": 0,
        "recommend_publish": False, "compliance_score": 3, "publication_score": 5,
        "authenticity_score": 9, "specificity_score": 8})
    appmod._init_workspace_schema()
    with appmod.db.connect(appmod.LOG_DB) as c:
        c.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?)",
                  ("shaira", "shaira", "workspace:shaira"))
        uid = c.execute("SELECT id FROM workspace_users WHERE name='shaira'").fetchone()[0]
        c.execute("INSERT INTO access_tokens (token, user_id) VALUES (?,?)", (VA, uid))
        c.commit()
    return appmod.app.test_client(), appmod, scored


def _body(**kw):
    b = {"email": "Rebecca@Example.com", "name": "Rebecca Navo", "body": TEXT,
         "source": "email:1a06422aceb72496"}
    b.update(kw)
    return b


def _row(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    return dict(cx.execute("SELECT * FROM product_reviews").fetchone())


def test_owner_records_it_without_consent_or_rating(client):
    c, appmod, scored = client
    r = c.post("/api/console/testimonials/record", json=_body(), headers=OWNER)
    assert r.status_code == 200, r.get_data(as_text=True)
    rid = r.get_json()["review_id"]
    row = _row(appmod)
    assert row["id"] == rid
    assert row["kind"] == "testimonial" and row["product_slug"] == "_results"
    assert row["email"] == "rebecca@example.com" and row["name"] == "Rebecca Navo"
    assert row["body"] == TEXT                      # verbatim, nothing edited
    assert row["consent_public"] == 0 and row["rating"] == 0 and row["status"] == "pending"
    assert row["source_tag"] == "staff:email:1a06422aceb72496"
    assert row["compliance_score"] == 3             # the scorer saw the verbatim text
    assert scored == [TEXT]


def test_consent_is_recorded_only_when_the_client_gave_it(client):
    c, appmod, _ = client
    c.post("/api/console/testimonials/record", json=_body(consent_public=True), headers=OWNER)
    assert _row(appmod)["consent_public"] == 1


def test_an_existing_testimonial_is_never_overwritten(client):
    c, appmod, _ = client
    c.post("/api/console/testimonials/record", json=_body(), headers=OWNER)
    r = c.post("/api/console/testimonials/record", json=_body(body="replaced"), headers=OWNER)
    assert r.status_code == 409
    assert _row(appmod)["body"] == TEXT


def test_rating_must_be_one_to_five_when_given(client):
    c, _, _ = client
    assert c.post("/api/console/testimonials/record", json=_body(rating=9),
                  headers=OWNER).status_code == 400


@pytest.mark.parametrize("missing", ["email", "body"])
def test_email_and_text_are_required(client, missing):
    c, _, _ = client
    assert c.post("/api/console/testimonials/record", json=_body(**{missing: ""}),
                  headers=OWNER).status_code == 400


def test_only_owners_record(client):
    c, appmod, _ = client
    assert c.post("/api/console/testimonials/record", json=_body(),
                  headers={"X-Console-Key": VA}).status_code == 403
    assert c.post("/api/console/testimonials/record", json=_body()).status_code == 403


def test_the_source_note_can_never_be_a_certification_tag(client):
    """Approving a testimonial whose source_tag is a cert cohort grants a level (review)."""
    c, appmod, _ = client
    c.post("/api/console/testimonials/record", json=_body(source="ash-cert-l1 <b>"), headers=OWNER)
    tag = _row(appmod)["source_tag"]
    assert tag.startswith("staff:") and "<" not in tag and " " not in tag


# ── The client approved an edited wording (Rebecca Navo, 2026-09-28) ─────────
# She consented in writing to Glen's edited version. Recording refuses to overwrite, so an
# owner replaces the wording through its own route: the original stays on the row, the
# consent message is referenced, the text is scored again, and it stays PENDING.
EDITED = "I have worked with Rae and Dr. Glen Swartwout for six to seven years."


def _recorded(c):
    return c.post("/api/console/testimonials/record", json=_body(), headers=OWNER).get_json()["review_id"]


def _approved(**kw):
    b = {"body": EDITED, "name": "Rebecca Navo, California Real Estate Agent",
         "consent_public": True, "consent_ref": "gmail:1a0e5f5262f92c39"}
    b.update(kw)
    return b


def test_owner_records_the_client_approved_wording(client):
    c, appmod, scored = client
    rid = _recorded(c)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    assert r.status_code == 200, r.get_data(as_text=True)
    row = _row(appmod)
    assert row["body"] == EDITED and row["name"] == "Rebecca Navo, California Real Estate Agent"
    assert row["consent_public"] == 1 and row["status"] == "pending"
    assert row["original_body"] == TEXT
    assert row["consent_ref"] == "gmail:1a0e5f5262f92c39"
    assert scored[-1] == EDITED                      # the new wording was scored


def test_a_second_edit_keeps_the_first_original(client):
    c, appmod, _ = client
    rid = _recorded(c)
    c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(body="Again."),
           headers=OWNER)
    row = _row(appmod)
    assert row["body"] == "Again." and row["original_body"] == TEXT


@pytest.mark.parametrize("bad", [{"consent_public": False}, {"consent_public": "yes"},
                                 {"consent_ref": ""}, {"body": "  "}])
def test_it_needs_the_wording_consent_and_its_reference(client, bad):
    c, appmod, _ = client
    rid = _recorded(c)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(**bad),
               headers=OWNER)
    assert r.status_code == 400
    assert _row(appmod)["body"] == TEXT and _row(appmod)["consent_public"] == 0


def test_only_owners_and_only_pending(client):
    c, appmod, _ = client
    rid = _recorded(c)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(),
               headers={"X-Console-Key": VA})
    assert r.status_code == 403
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE product_reviews SET status='approved' WHERE id=?", (rid,))
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    assert r.status_code == 409
    assert c.post("/api/console/testimonials/999/client-approved", json=_approved(),
                  headers=OWNER).status_code == 404
    assert _row(appmod)["body"] == TEXT


def test_a_failed_score_changes_nothing(client, monkeypatch):
    """Round 2: the wording and consent were saved before scoring, beside the old score."""
    c, appmod, _ = client
    rid = _recorded(c)
    from dashboard import review_scoring as rs

    def boom(*a, **k):
        raise RuntimeError("scorer down")
    monkeypatch.setattr(rs, "score_review", boom)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    assert r.status_code >= 500 or r.get_json().get("ok") is False
    row = _row(appmod)
    assert row["body"] == TEXT and row["consent_public"] == 0


def test_approval_landing_mid_request_is_not_overwritten(client, monkeypatch):
    """Round 2: approval between the read and the write left the edit unsaved but still
    wrote the new wording's scores onto the approved row and answered ok."""
    c, appmod, _ = client
    rid = _recorded(c)
    from dashboard import review_scoring as rs
    real = rs.score_review

    def approve_then_score(*a, **k):
        with sqlite3.connect(appmod.LOG_DB) as cx:
            cx.execute("UPDATE product_reviews SET status='approved', compliance_score=7 WHERE id=?",
                       (rid,))
        return real(*a, **k)
    monkeypatch.setattr(rs, "score_review", approve_then_score)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    assert r.status_code == 409
    row = _row(appmod)
    assert row["body"] == TEXT and row["compliance_score"] == 7 and row["consent_public"] == 0


def test_a_resubmission_drops_the_old_consent_reference(client):
    """Round 1: a later submission replaced the words but kept the consent message id, so the
    row cited a written consent for words it no longer held."""
    c, appmod, _ = client
    rid = _recorded(c)
    c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(), headers=OWNER)
    from dashboard import product_reviews as pr
    with sqlite3.connect(appmod.LOG_DB) as cx:
        pr.upsert_review(cx, "_results", "rebecca@example.com", "Rebecca", 5, "New words.",
                         kind="testimonial")
    row = _row(appmod)
    assert row["body"] == "New words." and row["consent_ref"] == "" and row["original_body"] == ""


def test_an_overlong_wording_is_refused(client):
    c, appmod, _ = client
    rid = _recorded(c)
    r = c.post(f"/api/console/testimonials/{rid}/client-approved", json=_approved(body="x" * 5001),
               headers=OWNER)
    assert r.status_code == 400 and _row(appmod)["body"] == TEXT
