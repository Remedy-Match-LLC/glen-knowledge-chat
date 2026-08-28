"""Guard: no registered route may shadow a published practitioner slug.

A root-level catch-all shares its namespace with the whole application. Without
this test, adding a route named after a live practitioner silently takes their
URL away, and we would hear about it from the practitioner rather than from CI.
"""
import os
import sqlite3

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import practitioner_slugs as ps


def _seed(db_path, slugs, aliases=()):
    cx = sqlite3.connect(db_path)
    cx.execute("CREATE TABLE IF NOT EXISTS affiliate_signups ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,"
               " slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'approved')")
    for i, s in enumerate(slugs):
        cx.execute("INSERT INTO affiliate_signups (name,email,slug,status)"
                   " VALUES (?,?,?,'approved')", (s, f"{i}@x.com", s))
    ps.init_tables(cx)
    for a, c in aliases:
        cx.execute("INSERT INTO practitioner_slug_aliases"
                   " (alias,canonical_slug,created_at) VALUES (?,?,'2026-08-27')",
                   (a, c))
    cx.commit()
    cx.close()


def _published_slugs(db_path):
    cx = sqlite3.connect(db_path)
    ps.init_tables(cx)
    canonical = {r[0] for r in cx.execute(
        "SELECT slug FROM affiliate_signups WHERE status='approved'")}
    alias = {r[0] for r in cx.execute(
        "SELECT alias FROM practitioner_slug_aliases")}
    cx.close()
    return canonical | alias


def test_no_route_shadows_a_published_slug(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db, ["mary-boyd"], aliases=[("healing-oasis-hilo", "mary-boyd")])
    reserved = ps.route_segments(appmod.app.url_map)
    collisions = sorted(_published_slugs(db) & reserved)
    assert collisions == [], (
        f"These published practitioner slugs are shadowed by routes: {collisions}. "
        "Either rename the route or migrate the practitioner and 301 the old slug.")


def test_guard_detects_a_planted_collision(tmp_path):
    """Mutation test: prove the guard bites. A slug named after a real route
    segment MUST be reported, or the assertion above is decorative."""
    db = str(tmp_path / "chat_log.db")
    real_segment = sorted(ps.route_segments(appmod.app.url_map))[0]
    _seed(db, [real_segment])
    reserved = ps.route_segments(appmod.app.url_map)
    collisions = sorted(_published_slugs(db) & reserved)
    assert collisions == [real_segment]
