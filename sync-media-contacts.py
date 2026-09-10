#!/usr/bin/env python3
"""
Run from LOCAL Mac (not Render) to feed PR/media contacts from the vault CSV
into the People hub. The vault CSV is not on Render, so this runs locally and
POSTs to /api/people?merge_tags=1 (additive, idempotent) tagged type:pr-media.

Source: ~/AI-Training/marketing/04 Copy/pr-pitches/media-contacts.csv  (override with
        MEDIA_CONTACTS_CSV env var). Rows without an email are skipped (the
        vault CSV stays the full record; email is the hub's dedup/automation key).

Usage:
  doppler run --project remedy-match --config prd -- python3 sync-media-contacts.py [--dry-run]
"""
import csv
import json
import os
import subprocess
import sys

RENDER_URL = os.environ.get("RENDER_URL", "https://glen-knowledge-chat.onrender.com")
CONSOLE_SECRET = os.environ.get("CONSOLE_SECRET", os.environ.get("WEBHOOK_SECRET", ""))
DEFAULT_CSV = os.path.expanduser("~/AI-Training/marketing/04 Copy/pr-pitches/media-contacts.csv")
CSV_PATH = os.environ.get("MEDIA_CONTACTS_CSV", DEFAULT_CSV)
DRY_RUN = "--dry-run" in sys.argv


# An honorific in contact_name used to become the first name. "Dr. Randy Schulman"
# wrote first_name="Dr." over a hub row that already held "Dr." in title. The hub's
# additive upsert overwrites a scalar whenever the incoming value is non-empty, so a
# rerun degraded the record every time. Map each spelling to the stored form.
HONORIFICS = {
    "dr": "Dr.", "dr.": "Dr.", "doctor": "Dr.",
    "prof": "Prof.", "prof.": "Prof.", "professor": "Prof.",
    "mr": "Mr.", "mr.": "Mr.", "mrs": "Mrs.", "mrs.": "Mrs.",
    "ms": "Ms.", "ms.": "Ms.", "miss": "Miss",
}

# A cell holding two people, or a person plus their outlet. The row is addressed to
# whoever comes first, so first/last are read from the part before the separator.
# The full cell stays in `name`.
CO_NAME_SEPARATORS = (" & ", " / ", "/", " + ", " and ")


def split_contact_name(name):
    """Split one contact_name cell into (title, first_name, last_name).

    Any part is "" when the cell does not carry it. Everything after the first
    given name is the family name, so "Esther Joy Van Der Werf" keeps its family
    name whole.
    """
    name = (name or "").strip()
    title = ""
    while True:
        parts = name.split(None, 1)
        if len(parts) < 2 or parts[0].lower() not in HONORIFICS:
            break
        title = HONORIFICS[parts[0].lower()]
        name = parts[1].strip()
    for sep in CO_NAME_SEPARATORS:
        head = name.split(sep, 1)[0].strip()
        if head:
            name = head
    parts = name.split(None, 1)
    first = parts[0] if parts else ""
    last = parts[1].strip() if len(parts) > 1 else ""
    return title, first, last


def row_to_person(row):
    """Map a media-contacts.csv row to a people upsert dict, or None if no email."""
    email = (row.get("email") or "").strip().lower()
    if not email:
        return None
    name = (row.get("contact_name") or "").strip()
    title, first, last = split_contact_name(name)
    outlet = (row.get("outlet") or "").strip()
    person = {
        "email": email, "name": name, "first_name": first, "last_name": last,
        "phone": (row.get("phone") or "").strip(),
        "organizations": [outlet] if outlet else [],
        "source": "media-outreach",
        "tags": ["type:pr-media", "consent:cold-no-consent", "source:media-outreach"],
    }
    if title:  # omitted when absent, so a blank never lands on a stored title
        person["title"] = title
    return person


def fetch_stored_phones(emails):
    """Return {email: stored phone} for the emails already in the hub.

    One GET each. The list is the six or so rows of the vault CSV, so the cost is
    a handful of calls. An email that is not there is simply absent from the map.
    """
    stored = {}
    for email in emails:
        cmd = ["curl", "-s", "-G", f"{RENDER_URL}/api/people",
               "--data-urlencode", f"q={email}",
               "-H", f"X-Console-Key: {CONSOLE_SECRET}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        try:
            people = json.loads(r.stdout).get("people", [])
        except Exception:
            continue
        for person in people:
            if (person.get("email") or "").strip().lower() == email:
                stored[email] = (person.get("phone") or "").strip()
                break
    return stored


def drop_stored_phones(persons, stored):
    """Make the phone fill-only: send it only where the hub has none.

    The hub's additive upsert overwrites a scalar whenever the incoming value is
    non-empty, which is right for a blank field and wrong for a phone that another
    source already normalised. The CSV holds the number as it was typed.
    """
    out = []
    for p in persons:
        p = dict(p)
        if not (p.get("phone") or "").strip() or stored.get(p.get("email"), ""):
            p.pop("phone", None)
        out.append(p)
    return out


def post_people(persons):
    url = f"{RENDER_URL}/api/people?merge_tags=1"
    cmd = ["curl", "-s", "-X", "POST", url,
           "-H", "Content-Type: application/json",
           "-H", f"X-Console-Key: {CONSOLE_SECRET}",
           "-d", json.dumps(persons)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout), None
    except Exception:
        return None, r.stdout[:300]


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"ERROR: CSV not found at {CSV_PATH}")
    persons, skipped = [], 0
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            person = row_to_person(row)
            if person:
                persons.append(person)
            else:
                skipped += 1
    print(f"Read {CSV_PATH}: {len(persons)} with email, {skipped} skipped (no email).")
    if DRY_RUN:
        stored_dry = {}
        if CONSOLE_SECRET:
            stored_dry = fetch_stored_phones(sorted({p["email"] for p in persons}))
            persons = drop_stored_phones(persons, stored_dry)
        for p in persons:
            phone = p.get("phone") or (
                "(hub keeps its own)" if stored_dry.get(p["email"]) else "(none in CSV)")
            print(f"  would upsert: {p['email']}  {p['organizations']}  "
                  f"{p.get('title','')} {p['first_name']} {p['last_name']} | {phone} | {p['tags']}")
        print("Dry run, nothing posted.")
        return
    if not persons:
        print("Nothing to post.")
        return
    if not CONSOLE_SECRET:
        sys.exit("ERROR: CONSOLE_SECRET not set (run via doppler).")
    stored = fetch_stored_phones(sorted({p["email"] for p in persons}))
    kept = persons
    persons = drop_stored_phones(persons, stored)
    held = [p["email"] for p, q in zip(kept, persons) if "phone" in p and "phone" not in q
            and stored.get(p["email"], "")]
    if held:
        print(f"Kept the stored phone for: {', '.join(sorted(set(held)))}")
    result, err = post_people(persons)
    if err:
        sys.exit(f"POST failed: {err}")
    print(f"Posted: {result}")


if __name__ == "__main__":
    main()
