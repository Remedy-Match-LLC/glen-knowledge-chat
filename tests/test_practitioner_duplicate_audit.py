"""The duplicate-email audit's public-visibility and completeness fields.

The audit exists so a duplicate practitioner row can be retired safely. Which
row to keep is a guess without knowing which one the public can actually see:
the finder reads v_practitioners_public, which filters on
`removal_requested = false AND lat IS NOT NULL`. Retiring the visible twin of a
pair removes that practitioner from the directory, and nothing reports it.
"""


def test_the_audit_reports_public_visibility_and_completeness():
    """Choosing which duplicate row to retire is a guess without these.

    v_practitioners_public filters on `removal_requested = false AND lat IS NOT
    NULL`, so a row with coordinates is one a person can actually find in the
    directory. Retiring that one instead of its empty twin removes the
    practitioner from the finder, and nothing would report it.
    """
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "rich", "name": "A B", "email": "e@x.com", "lat": 21.3, "lng": -157.8,
         "removal_requested": False, "city": "Hilo", "state": "Hawaii",
         "phone": "808", "website": "w", "source_url": "u"},
        {"id": "thin", "name": "A B", "email": "e@x.com", "lat": None,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    g = out["groups"][0]
    by = {r["id"]: r for r in g["rows"]}

    assert by["rich"]["finder_listed"] is True
    assert by["thin"]["finder_listed"] is False
    assert g["finder_listed_count"] == 1
    # The richer row must be identifiable as such, or "keep the better one"
    # degrades to "keep whichever sorted first".
    assert by["rich"]["completeness"] > by["thin"]["completeness"]


def test_a_row_already_marked_removed_is_not_counted_as_listed():
    """removal_requested is how a row is retired without deleting it. A retired
    row still has its coordinates, so coordinates alone would keep counting it
    as publicly visible long after it stopped being so."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "gone", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": True},
        {"id": "live", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    g = out["groups"][0]
    by = {r["id"]: r for r in g["rows"]}
    assert by["gone"]["finder_listed"] is False
    assert by["live"]["finder_listed"] is True
    assert g["finder_listed_count"] == 1
    assert out["finder_duplicates"] == 0


def test_two_publicly_visible_rows_are_flagged_as_a_finder_duplicate():
    """The case a visitor actually sees: the same practitioner listed twice."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [{"id": i, "name": "A B", "email": "e@x.com", "lat": 21.3,
             "removal_requested": False} for i in ("one", "two")]
    out = group_duplicates(rows, {})
    assert out["groups"][0]["finder_listed_count"] == 2
    assert out["finder_duplicates"] == 1


def test_a_row_hidden_as_a_duplicate_is_not_counted_as_listed():
    """duplicate_of is the third term of the view's filter. A hidden duplicate
    keeps its coordinates on purpose, so coordinates alone would go on counting
    it as publicly visible after it stopped being so, and the audit would report
    a finder duplicate that has already been dealt with."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "keep", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False, "duplicate_of": None},
        {"id": "hidden", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False, "duplicate_of": "keep"},
    ]
    out = group_duplicates(rows, {})
    g = out["groups"][0]
    by = {r["id"]: r for r in g["rows"]}
    assert by["keep"]["finder_listed"] is True
    assert by["hidden"]["finder_listed"] is False
    assert by["hidden"]["has_coords"] is True        # nothing was taken away
    assert by["hidden"]["removal_requested"] is False  # and nobody asked to leave
    assert g["finder_listed_count"] == 1
    assert out["finder_duplicates"] == 0


def test_the_audit_reports_which_row_each_duplicate_was_folded_into():
    """Without this the audit says a group is clean and cannot show why."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "keep", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "hidden", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False, "duplicate_of": "keep"},
    ]
    by = {r["id"]: r for r in group_duplicates(rows, {})["groups"][0]["rows"]}
    assert by["hidden"]["duplicate_of"] == "keep"
    assert by["keep"]["duplicate_of"] is None


def test_marking_one_of_a_visible_pair_drops_the_finder_duplicate_count():
    """The number the whole exercise is measured by: 386 practitioners listed
    more than once. Marking one row of a pair must move it."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [{"id": i, "name": "A B", "email": "e@x.com", "lat": 21.3,
             "removal_requested": False} for i in ("one", "two")]
    assert group_duplicates(rows, {})["finder_duplicates"] == 1
    rows[1]["duplicate_of"] = "one"
    after = group_duplicates(rows, {})
    assert after["finder_duplicates"] == 0
    assert after["groups"][0]["finder_listed_count"] == 1
    assert after["groups"][0]["count"] == 2          # both rows still reported


# ── one clinic email, several different practitioners ────────────────────────
# The headline said 398 emails carried a duplicate. 205 of those groups were
# different people sharing one clinic address, which is the normal shape of a
# scraped directory and not a duplicate at all. plan-practitioner-dedupe.py
# already clustered by email THEN by name for exactly this reason; the audit did
# not, so its number answered the portal-account question and was quoted for the
# directory one.

def test_two_different_people_at_one_clinic_email_are_not_a_finder_duplicate():
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "a", "name": "Ann Bauder", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "b", "name": "Rae Luscombe", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    g = out["groups"][0]
    assert out["finder_duplicates"] == 0
    assert g["shared_clinic"] is True
    assert g["listed_people"] == 2
    # Both rows are still publicly listed, and the audit must still say so.
    assert g["finder_listed_count"] == 2
    assert out["emails_with_multiple_listings"] == 1
    assert out["shared_clinic_emails"] == 1


def test_the_same_person_twice_at_a_clinic_email_is_still_a_finder_duplicate():
    """A shared mailbox must not hide a real duplicate sitting inside it."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "a", "name": "Ann Bauder", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "b", "name": "Ann Bauder", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "c", "name": "Rae Luscombe", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    assert out["finder_duplicates"] == 1
    assert out["groups"][0]["listed_people"] == 2


def test_a_middle_initial_or_a_credential_is_the_same_person():
    """The 19 pairs Glen answered on 2026-09-10 were all of this shape."""
    from dashboard.practitioner_admin import group_duplicates
    for other in ("Lisa M. Butler", "Lisa Butler DDS", "Dr. Lisa Butler"):
        rows = [
            {"id": "a", "name": "Lisa Butler", "email": "e@x.com", "lat": 21.3,
             "removal_requested": False},
            {"id": "b", "name": other, "email": "e@x.com", "lat": 21.3,
             "removal_requested": False},
        ]
        out = group_duplicates(rows, {})
        assert out["finder_duplicates"] == 1, other
        assert out["groups"][0]["shared_clinic"] is False, other


def test_a_hidden_duplicate_stops_counting_as_a_second_person():
    """Hiding one of a pair must clear the group, not leave it reading as two."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "keep", "name": "Lisa Butler", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "hidden", "name": "Lisa M. Butler", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False, "duplicate_of": "keep"},
    ]
    out = group_duplicates(rows, {})
    assert out["finder_duplicates"] == 0
    assert out["groups"][0]["listed_people"] == 1
    assert out["groups"][0]["shared_clinic"] is False


def test_a_one_word_name_is_not_folded_into_every_other_one_word_name():
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "a", "name": "Kaiser", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "b", "name": "Queens", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    assert out["finder_duplicates"] == 0
    assert out["groups"][0]["listed_people"] == 2


def test_the_old_headline_is_kept_under_a_name_that_says_what_it_counts():
    """finder_duplicates changed meaning on 2026-09-11, so the old number keeps a
    key of its own rather than disappearing."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "a", "name": "Ann Bauder", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "b", "name": "Rae Luscombe", "email": "clinic@x.com", "lat": 21.3,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    assert out["emails_with_multiple_listings"] == 1
    assert out["finder_duplicates"] == 0


# ── a pair that has been looked at and kept ──────────────────────────────────
# All seven groups the 2026-09-11 headline flagged were pairs Glen had already
# decided to keep: six second offices restored on 2026-09-10, plus Lisa Anne
# Arnold's two Cape Cod offices. The audit had no way to record that decision, so
# it re-asked the same question every run. second_office is that record. It does
# NOT hide the row — both offices stay in the finder, which is the whole point.

def test_a_second_office_is_not_counted_as_the_same_person_twice():
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "main", "name": "Minh Luu", "email": "e@x.com", "lat": 21.3,
         "city": "Cypress", "removal_requested": False},
        {"id": "second", "name": "Minh Luu", "email": "e@x.com", "lat": 21.4,
         "city": "Katy", "removal_requested": False, "second_office": True},
    ]
    out = group_duplicates(rows, {})
    g = out["groups"][0]
    assert out["finder_duplicates"] == 0
    assert g["second_offices"] == 1
    # Both are still public. Marking a second office must never hide it.
    assert g["finder_listed_count"] == 2
    assert {r["id"] for r in g["rows"] if r["finder_listed"]} == {"main", "second"}


def test_a_third_unreviewed_listing_is_still_a_duplicate():
    """Deciding one pair must not vouch for a row nobody looked at."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "main", "name": "Minh Luu", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "second", "name": "Minh Luu", "email": "e@x.com", "lat": 21.4,
         "removal_requested": False, "second_office": True},
        {"id": "stray", "name": "Minh Luu", "email": "e@x.com", "lat": 21.5,
         "removal_requested": False},
    ]
    out = group_duplicates(rows, {})
    assert out["finder_duplicates"] == 1
    assert out["groups"][0]["second_offices"] == 1


def test_the_flag_is_reported_per_row_so_the_decision_is_visible():
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "main", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "second", "name": "A B", "email": "e@x.com", "lat": 21.4,
         "removal_requested": False, "second_office": True},
    ]
    by = {r["id"]: r for r in group_duplicates(rows, {})["groups"][0]["rows"]}
    assert by["second"]["second_office"] is True
    assert by["main"]["second_office"] is False


def test_a_missing_column_reads_as_not_a_second_office():
    """The migration is applied to production by hand, after this deploys. In
    that window the key is absent and the audit must behave exactly as before."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [{"id": i, "name": "A B", "email": "e@x.com", "lat": 21.3,
             "removal_requested": False} for i in ("one", "two")]
    out = group_duplicates(rows, {})
    assert out["finder_duplicates"] == 1
    assert out["groups"][0]["second_offices"] == 0


def test_a_second_office_that_is_not_listed_is_not_counted():
    """A row already hidden as a duplicate is not a second office to report."""
    from dashboard.practitioner_admin import group_duplicates
    rows = [
        {"id": "main", "name": "A B", "email": "e@x.com", "lat": 21.3,
         "removal_requested": False},
        {"id": "second", "name": "A B", "email": "e@x.com", "lat": 21.4,
         "removal_requested": False, "second_office": True, "duplicate_of": "main"},
    ]
    out = group_duplicates(rows, {})
    assert out["groups"][0]["second_offices"] == 0
    assert out["finder_duplicates"] == 0


def test_the_migration_never_touches_the_public_view():
    """second_office must not reach v_practitioners_public.

    duplicate_of is the third term of that view's WHERE, and it HIDES a row. If
    second_office were added the same way, every office marked as reviewed would
    silently leave the directory, which is the exact opposite of the decision it
    records. This asserts on the migration text because the mistake is a
    one-line edit away and nothing else would catch it.
    """
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[1]
           / "migrations" / "practitioners-second-office.sql").read_text().lower()
    assert "second_office" in sql
    statements = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    body = "\n".join(statements)
    assert "create or replace view" not in body
    assert "v_practitioners_public" not in body
