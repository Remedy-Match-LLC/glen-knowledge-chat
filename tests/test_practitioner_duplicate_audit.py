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
