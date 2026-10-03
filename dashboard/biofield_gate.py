"""Readiness-gate state for the Biofield checkout flow (pure: cx + injected lookups)."""
from dashboard import biofield_store as _store


def gate_state(cx, email, *, has_intake, has_fresh_scan=None, has_photo=None,
               scan_window_days=7):
    """Compute the readiness gate state for `email`.
    has_intake(email) -> bool: an injected auto-check (e.g. an inbound_leads lookup).
    has_fresh_scan(email) -> bool: optional injected auto-check for a fresh voice scan.
    has_photo(email) -> bool: optional injected auto-check, e.g. a portal photo.
    Returns {paid, booked, items:{photo,intake,scan -> {status}}, booking_unlocked}."""
    row = _store.get(cx, email) or {}
    paid = bool(row.get("paid_at"))
    booked = bool(row.get("booked_at"))

    photo = bool(row.get("photo_on_file"))
    if has_photo:
        photo = photo or bool(has_photo(email))
    intake = bool(row.get("intake_confirmed")) or bool(has_intake(email))
    # Before payment, the injected freshness check alone decides: it applies the
    # window to a self-confirmation too, so this page and the checkout agree. After
    # payment the old rule stands, a confirmation counts as it always did, so no paid
    # client loses a booking button they already had.
    if has_fresh_scan and not paid:
        scan = bool(has_fresh_scan(email))
    else:
        scan = bool(row.get("scan_confirmed"))
        if has_fresh_scan:
            scan = scan or bool(has_fresh_scan(email))

    def item(ok):
        return {"status": "green" if ok else "needed"}

    items = {"photo": item(photo), "intake": item(intake), "scan": item(scan)}
    booking_unlocked = bool(paid and photo and intake and scan)
    return {"paid": paid, "booked": booked, "items": items,
            "booking_unlocked": booking_unlocked}
