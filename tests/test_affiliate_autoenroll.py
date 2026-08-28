import sqlite3
from dashboard import affiliate_dashboard as ad

def _db():
    cx = sqlite3.connect(":memory:")
    cx.execute("""CREATE TABLE affiliate_signups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, organization TEXT DEFAULT '', website TEXT DEFAULT '',
        promo_method TEXT DEFAULT '', slug TEXT NOT NULL UNIQUE, token TEXT NOT NULL UNIQUE,
        status TEXT DEFAULT 'approved', notes TEXT DEFAULT '', referred_by TEXT DEFAULT '',
        short_url TEXT DEFAULT '', gifting_activated_at TEXT)""")
    cx.execute("""CREATE TABLE referral_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE, description TEXT DEFAULT '', utm_source TEXT NOT NULL,
        utm_medium TEXT DEFAULT 'referral', utm_campaign TEXT DEFAULT '', active INTEGER DEFAULT 1)""")
    return cx

def test_ensure_affiliate_creates_approved_row_once():
    cx = _db()
    r1 = ad.ensure_affiliate(cx, "Jo@Example.com", name="Jo Rae")
    assert r1["status"] == "approved"
    assert r1["slug"] and r1["short_url"] == ""
    assert r1["email"] == "jo@example.com"
    r2 = ad.ensure_affiliate(cx, "jo@example.com", name="Jo Rae")
    assert r2["slug"] == r1["slug"]
    assert cx.execute("SELECT COUNT(*) FROM affiliate_signups").fetchone()[0] == 1
    assert cx.execute("SELECT COUNT(*) FROM referral_sources WHERE slug=?", (r1["slug"],)).fetchone()[0] == 1

def test_ensure_affiliate_empty_email_returns_none():
    cx = _db()
    assert ad.ensure_affiliate(cx, "", name="x") is None

def test_ensure_affiliate_slug_collision_suffixes():
    cx = _db()
    cx.execute("INSERT INTO affiliate_signups (created_at,name,email,slug,token,status) "
               "VALUES ('t','x','x@x.com','jo-rae','tok0','approved')")
    r = ad.ensure_affiliate(cx, "jo2@example.com", name="Jo Rae")
    assert r["slug"] != "jo-rae" and r["slug"].startswith("jo-rae-")

def test_ensure_affiliate_no_name_uses_email_localpart():
    cx = _db()
    r = ad.ensure_affiliate(cx, "solo@example.com")
    assert r["slug"] == "solo"

def test_autoenroll_flag(monkeypatch):
    monkeypatch.delenv("AFFILIATE_AUTOENROLL_ENABLED", raising=False)
    assert ad.autoenroll_enabled() is False
    monkeypatch.setenv("AFFILIATE_AUTOENROLL_ENABLED", "true")
    assert ad.autoenroll_enabled() is True

def test_ensure_affiliate_persists_across_connections(tmp_path):
    import sqlite3
    dbp = str(tmp_path / "aff.db")
    cx = sqlite3.connect(dbp)
    cx.execute("""CREATE TABLE affiliate_signups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, organization TEXT DEFAULT '', website TEXT DEFAULT '',
        promo_method TEXT DEFAULT '', slug TEXT NOT NULL UNIQUE, token TEXT NOT NULL UNIQUE,
        status TEXT DEFAULT 'approved', notes TEXT DEFAULT '', referred_by TEXT DEFAULT '',
        short_url TEXT DEFAULT '', gifting_activated_at TEXT)""")
    cx.execute("""CREATE TABLE referral_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE, description TEXT DEFAULT '', utm_source TEXT NOT NULL,
        utm_medium TEXT DEFAULT 'referral', utm_campaign TEXT DEFAULT '', active INTEGER DEFAULT 1)""")
    cx.commit()
    ad.ensure_affiliate(cx, "p@x.com", name="P")
    cx2 = sqlite3.connect(dbp)  # separate connection: only sees COMMITTED rows
    assert cx2.execute("SELECT COUNT(*) FROM affiliate_signups WHERE email='p@x.com'").fetchone()[0] == 1

from dashboard import portal_view as pv

def test_ambassador_block_autoenrolls_when_flag_on(monkeypatch):
    cx = _db()
    monkeypatch.setenv("AFFILIATE_AUTOENROLL_ENABLED", "true")
    block = pv._ambassador_block(cx, "new@example.com", "https://q.example/quiz", "https://illtowell.com")
    assert block["status"] == "enrolled"
    assert block["slug"]
    assert block["referral_url"].startswith("https://q.example/quiz?utm_source=")
    assert block["recruit_url"] == f"https://illtowell.com/affiliate?ref={block['slug']}"
    assert cx.execute("SELECT COUNT(*) FROM affiliate_signups WHERE lower(email)='new@example.com'").fetchone()[0] == 1

def test_ambassador_block_shows_cta_when_flag_off(monkeypatch):
    cx = _db()
    monkeypatch.delenv("AFFILIATE_AUTOENROLL_ENABLED", raising=False)
    block = pv._ambassador_block(cx, "new@example.com", "https://q.example/quiz", "https://illtowell.com")
    assert block["status"] == "none"
    assert block["signup_url"] == "https://illtowell.com/affiliate"
    assert cx.execute("SELECT COUNT(*) FROM affiliate_signups").fetchone()[0] == 0

def test_backfill_from_people_and_portals_idempotent():
    cx = _db()
    cx.execute("CREATE TABLE client_portals (id INTEGER PRIMARY KEY, email TEXT, name TEXT)")
    cx.execute("INSERT INTO client_portals (email, name) VALUES ('a@x.com','A'),('b@x.com','B'),('a@x.com','A2')")
    n1 = ad.backfill_affiliates_from_people(cx)
    assert n1 == 2
    n2 = ad.backfill_affiliates_from_people(cx)
    assert n2 == 0
    assert cx.execute("SELECT COUNT(*) FROM affiliate_signups").fetchone()[0] == 2


# --- the minter must never emit a slug the practitioner-site reader refuses ---
# dashboard/practitioner_slugs.check_shape + check_not_reserved gate what
# myhealingoasis.com/<slug> will serve. A slug that fails either is a URL the
# practitioner was given and the site 404s. Fix it at the WRITER.
from dashboard import practitioner_slugs as ps  # noqa: E402


def _mint(cx, name="", email="x@example.com"):
    slug, _token = ad._mint_affiliate_slug(cx, name, email)
    return slug


def _assert_servable(slug):
    ps.check_shape(slug)
    ps.check_not_reserved(slug, ps.EXTRA_RESERVED)


# s[29] is a hyphen, so a 30-char cut lands exactly on a word boundary.
_BOUNDARY_NAME = "Abcde Fghij Klmno Pqrst Uvwxy Z"


def test_mint_long_name_truncating_on_a_word_boundary_has_no_trailing_hyphen():
    """.strip('-') used to run BEFORE [:30], so a cut landing on a boundary
    left a trailing hyphen -- a shape check_shape rejects."""
    slug = _mint(_db(), name=_BOUNDARY_NAME)
    assert not slug.endswith("-")
    _assert_servable(slug)


def test_mint_long_boundary_name_on_collision_has_no_doubled_hyphen():
    """The collision suffix used to be appended to that trailing hyphen."""
    cx = _db()
    first = _mint(cx, name=_BOUNDARY_NAME)
    cx.execute("INSERT INTO affiliate_signups (created_at,name,email,slug,token)"
               " VALUES ('t','x','x@x.com',?,'tok0')", (first,))
    cx.commit()
    second = _mint(cx, name=_BOUNDARY_NAME)
    assert second != first
    assert "--" not in second
    _assert_servable(second)


def test_mint_short_name_reaches_the_minimum_length():
    """'Jo' -> 'jo' is 2 chars, under practitioner_slugs.MIN_LEN."""
    slug = _mint(_db(), name="Jo")
    assert len(slug) >= ps.MIN_LEN
    _assert_servable(slug)


def test_mint_email_localpart_landing_on_a_reserved_word_is_not_emitted_bare():
    """support@clinic.com with no name used to mint the bare word 'support'."""
    slug = _mint(_db(), name="", email="support@clinic.com")
    assert slug != "support"
    assert slug.startswith("support-")
    _assert_servable(slug)


def test_mint_no_material_at_all_still_emits_a_servable_slug():
    slug = _mint(_db(), name="", email="")
    _assert_servable(slug)


def test_mint_stays_within_max_len_on_a_long_name_with_collisions():
    """base is 30 chars; a suffix must not push it past MAX_LEN."""
    cx = _db()
    name = "Wilhelmina Bartholomew Fitzgerald Montgomery"
    seen = set()
    for i in range(4):
        slug = _mint(cx, name=name)
        _assert_servable(slug)
        assert len(slug) <= ps.MAX_LEN
        assert slug not in seen
        seen.add(slug)
        cx.execute("INSERT INTO affiliate_signups (created_at,name,email,slug,token)"
                   " VALUES ('t','x',?,?,?)", (f"{i}@x.com", slug, f"tok{i}"))
        cx.commit()
