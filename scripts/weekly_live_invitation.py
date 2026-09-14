#!/usr/bin/env python3
"""Prepare and send the Wednesday community invitation through GHL.

The script is intended for a Render one-off job so it has the same production
database and secret environment as MyHealingOasis.  It is idempotent by logical
campaign ID and schedules batches of at most 100 at 15-minute intervals.
"""

import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Render invokes operational scripts by file path as well as with ``python -m``.
# Keep the repository root importable in both modes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must precede `import app`: a one-off job inherits DATA_DIR, and without this the
# import starts the web scheduler, whose console push runs immediately.
os.environ["NO_BACKGROUND_SCHEDULER"] = "1"

import app as appmod
from dashboard import client_portal, email_suppression
from scripts import live_invitation_allowlist as allowlist


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_CONTACTS_VERSION = "2021-07-28"
GHL_MESSAGES_VERSION = "2021-04-15"
HST = ZoneInfo("Pacific/Honolulu")
PORTAL_BASE = "https://myhealingoasis.com"
FROM_ADDRESS = "Dr. Glen Swartwout <drglen@mail.remedymatch.com>"
SOURCE_TAGS = ("pb:member", "e4l account")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _email(value):
    # One rule for the audience and for the fingerprinted list, or a changed
    # rule would silently match nobody.
    return allowlist.normalize_email(value)


def _api(method, path, version, body=None, *, write=False):
    token = os.environ.get("GHL_PIT" if write else "GHL_CONTENT_PIT")
    if not token:
        raise RuntimeError("required GHL private integration token is missing")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(GHL_BASE + path, data=data, method=method)
    for key, value in {
        "Authorization": "Bearer " + token,
        "Version": version,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; RemedyMatch-LiveEvents/1.0)",
    }.items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw[:500]}
        return exc.code, payload


def _contacts_by_tag(tag):
    contacts, search_after = [], None
    while True:
        body = {"locationId": os.environ["GHL_LOCATION_ID"], "pageLimit": 100,
                "filters": [{"field": "tags", "operator": "contains", "value": tag}]}
        if search_after:
            body["searchAfter"] = search_after
        status, data = _api("POST", "/contacts/search", GHL_CONTACTS_VERSION, body)
        if status >= 400:
            raise RuntimeError(f"GHL contact search for {tag!r} failed ({status})")
        page = data.get("contacts") or data.get("items") or []
        if not page:
            break
        contacts.extend(page)
        search_after = page[-1].get("searchAfter")
        if not search_after:
            break
        time.sleep(0.2)
    return contacts


def _find_contact(email):
    status, data = _api("POST", "/contacts/search", GHL_CONTACTS_VERSION, {
        "locationId": os.environ["GHL_LOCATION_ID"], "pageLimit": 1,
        "filters": [{"field": "email", "operator": "eq", "value": email}],
    })
    rows = data.get("contacts") or data.get("items") or []
    return rows[0] if status < 400 and rows else None


def _create_contact(email, name=""):
    parts = [part for part in (name or "").strip().split() if part]
    status, data = _api("POST", "/contacts/", GHL_CONTACTS_VERSION, {
        "locationId": os.environ["GHL_LOCATION_ID"], "email": email,
        "name": name or None, "firstName": parts[0] if parts else None,
        "lastName": " ".join(parts[1:]) if len(parts) > 1 else None,
        "source": "MyHealingOasis weekly live community",
    }, write=True)
    if status >= 400:
        # HighLevel can report the authoritative membership email as an
        # additional address on an existing contact.  Reuse that contact instead
        # of creating a duplicate; _send pins emailTo to this verified address.
        existing_id = ((data.get("meta") or {}).get("contactId") or "").strip()
        if status == 400 and existing_id:
            return {"id": existing_id, "email": email}
        return None
    return data.get("contact") or data


def _dnd_email(contact):
    if contact.get("dnd") is True:
        return True
    settings = contact.get("dndSettings") or {}
    email_setting = settings.get("Email") or settings.get("email") or {}
    status = str(email_setting.get("status") or "").lower()
    return status in {"active", "enabled", "dnd", "opted_out", "opt_out"}


def _authoritative_access_sets():
    with appmod.db.connect(appmod.LOG_DB) as cx:
        candidates = {row[0] for row in cx.execute(
            "SELECT DISTINCT lower(email) FROM memberships "
            "WHERE email IS NOT NULL AND trim(email)<>''").fetchall()}
        # A family plan grants membership with no memberships row, so its holder
        # and covered members never reached _is_paid_member and were told Group
        # Coaching was an upgrade. Measured 2026-09-13: 2 of 3 family-plan
        # members were missing. Read them the way the members board does.
        try:
            from dashboard import family_plan as _fp
            from dashboard import household as _hh
            _fp.init_family_plan_table(cx)
            _hh.init_household_tables(cx)
            for plan in _fp.list_active(cx):
                holder = plan.get("caregiver_email") or ""
                candidates.add(holder)
                candidates.update(m.get("email") or "" for m in _hh.members_for(cx, holder))
        except Exception as exc:
            raise RuntimeError(f"family plan roster unavailable: {exc}") from exc
    candidates = {_email(email) for email in candidates}
    candidates.discard("")
    paid = {email for email in candidates if appmod._is_paid_member(email)}
    paid.add("drglenswartwout@gmail.com")
    certification = set()
    try:
        from db_supabase import supabase_cursor
        with supabase_cursor() as cur:
            # The alias is load-bearing: RealDictCursor names an unaliased lower(email)
            # "lower", so row.get("email") read nothing and all 15 coaches were dropped.
            cur.execute("SELECT lower(email) AS email FROM practitioners "
                        "WHERE portal_role='coach' AND email IS NOT NULL")
            certification = {
                _email(row.get("email") if hasattr(row, "get") else row[0])
                for row in cur.fetchall()
            }
            certification.discard("")
    except Exception as exc:
        raise RuntimeError(f"certification roster unavailable: {exc}") from exc
    return paid, certification


def _build_audience(*, create_missing_contacts=False, allowed=None):
    """``allowed``, when given, is a predicate over an email. Members outside it
    are never looked up or created in GHL, and cannot block the run."""
    by_email, tag_sets = {}, {}
    for tag in SOURCE_TAGS:
        tag_sets[tag] = set()
        for contact in _contacts_by_tag(tag):
            email = _email(contact.get("email"))
            if not email:
                continue
            tag_sets[tag].add(email)
            by_email.setdefault(email, contact)
    paid, certification = _authoritative_access_sets()
    missing_ghl = []
    for email in sorted(paid | certification):
        if email in by_email:
            continue
        if allowed is not None and not allowed(email):
            continue
        contact = _find_contact(email)
        if not contact and create_missing_contacts:
            with appmod.db.connect(appmod.LOG_DB) as cx:
                row = cx.execute("SELECT name FROM people WHERE lower(email)=? LIMIT 1",
                                 (email,)).fetchone()
            contact = _create_contact(email, (row[0] if row else "") or "")
        if contact:
            by_email[email] = contact
        else:
            missing_ghl.append(email)
    return by_email, tag_sets, paid, certification, missing_ghl


def _event_gate(target_date):
    day = target_date.isoformat()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.row_factory = appmod.sqlite3.Row
        series_rows = cx.execute(
            "SELECT series_key,zoom_meeting_id,registration_required,recurring "
            "FROM live_event_series ORDER BY series_key").fetchall()
        series = {row["series_key"]: dict(row) for row in series_rows}
        group = cx.execute(
            "SELECT id,start,location,zoom_meeting_id,zoom_occurrence_id,"
            "zoom_registration_required FROM calendar_events "
            "WHERE status='visible' AND lower(summary) LIKE '%group coaching%' "
            "AND start LIKE ? ORDER BY id DESC LIMIT 1", (day + "%",)).fetchone()
        master = cx.execute(
            "SELECT id,start_ts,zoom_meeting_id,zoom_occurrence_id,registration_required "
            "FROM masterclass_events WHERE lower(topic) LIKE '%wellness whispering%' "
            "AND start_ts LIKE ? ORDER BY id DESC LIMIT 1", (day + "%",)).fetchone()
    issues = []
    for key in ("group-coaching", "free-masterclass"):
        row = series.get(key)
        if not row or not row.get("zoom_meeting_id") or not row.get("registration_required") or not row.get("recurring"):
            issues.append(f"{key} stable series missing")
    if not group:
        issues.append("Group Coaching portal occurrence missing")
    elif (not group["zoom_occurrence_id"] or not group["zoom_registration_required"]
          or str(group["location"] or "").lower().startswith("http")):
        issues.append("Group Coaching occurrence is not identity-safe")
    if not master:
        issues.append("MasterClass portal occurrence missing")
    elif not master["zoom_occurrence_id"] or not master["registration_required"]:
        issues.append("MasterClass occurrence is not identity-safe")
    if group and master and group["zoom_meeting_id"] == master["zoom_meeting_id"]:
        issues.append("Zoom series meeting IDs are not distinct")
    return {"ok": not issues, "issues": issues,
            "group_meeting_id": group["zoom_meeting_id"] if group else "",
            "group_occurrence_id": group["zoom_occurrence_id"] if group else "",
            "masterclass_meeting_id": master["zoom_meeting_id"] if master else "",
            "masterclass_occurrence_id": master["zoom_occurrence_id"] if master else ""}


def _copy(first_name, portal_url, eligible, target_date, email=""):
    date_label = target_date.strftime("%A, %B %-d")
    greeting = f"Aloha {first_name}," if first_name else "Aloha,"
    if eligible:
        access = ("Group Coaching is included with your certification or active full membership.\n"
                  "The Free Wellness Whispering MasterClass is also open to you.")
    else:
        access = ("The Free Wellness Whispering MasterClass is open to you.\n"
                  "Group Coaching is a certification/full-membership upgrade benefit; "
                  "your current access does not include the private session.")
    text = (f"{greeting}\n\nThis {date_label}, our MentorshipU community activities are:\n\n"
            "2:00 PM HST: Group Coaching\n"
            "3:00 PM HST: Free Wellness Whispering MasterClass\n\n"
            f"{access}\n\nOpen your private MyHealingOasis Upcoming Live Events page to RSVP, "
            "add the sessions to your calendar, and receive your own private Zoom join link:\n\n"
            f"{portal_url}\n\nPlease do not share your private portal or join link.\n\n"
            "With aloha,\nDr. Glen Swartwout")
    escaped = html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>")
    body_html = '<div style="font-family:Arial,sans-serif;font-size:16px;line-height:1.55"><p>' + escaped + "</p></div>"
    if email:
        # Appended AFTER html.escape so the anchor stays markup. GHL adds no
        # unsubscribe footer to conversations-API mail; this sender adds its own.
        from dashboard import unsubscribe as _un
        text = text + _un.footer_text(email, "weekly-live")
        body_html = body_html + _un.footer_html(email, "weekly-live")
    return text, body_html


EM_DASH_FORMS = ("—", "&mdash;", "&#8212;")


def _subject(target_date):
    return f"Wednesday live community sessions: {target_date.strftime('%B %-d')}"


def _refuse_em_dash(subject, target_date):
    """Glen, 2026-09-13: no em dashes in this invitation, subject or body.

    Checked once, before the event gate and before any contact is read, so a dry
    run fails too and no campaign can stop halfway. The sample uses a placeholder
    name and link because only the fixed wording is ours to control.
    """
    parts = [subject]
    for eligible in (True, False):
        parts.extend(_copy("Friend", f"{PORTAL_BASE}/portal/check", eligible,
                           target_date, "check@example.com"))
    for part in parts:
        if any(form in part for form in EM_DASH_FORMS):
            raise RuntimeError("em dash found in the invitation subject or copy; "
                               "use a colon, comma or full stop")


def _init_run_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS weekly_live_invitation_runs (
        campaign_id TEXT PRIMARY KEY,target_date TEXT,campaign_name TEXT,subject TEXT,
        status TEXT,counts_json TEXT,started_at TEXT,updated_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS weekly_live_invitation_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,campaign_id TEXT,email TEXT,contact_id TEXT,
        message_id TEXT,status TEXT,error TEXT,queued_at TEXT,updated_at TEXT,
        UNIQUE(campaign_id,email))""")
    cx.commit()


def _existing_status(cx, campaign_id, email):
    row = cx.execute(
        "SELECT status FROM weekly_live_invitation_recipients "
        "WHERE campaign_id=? AND email=?", (campaign_id, email)).fetchone()
    return (row[0] if row else "") or ""


def _record_recipient(cx, campaign_id, email, contact_id, message_id, status, error=""):
    now = _now()
    cx.execute(
        "INSERT INTO weekly_live_invitation_recipients "
        "(campaign_id,email,contact_id,message_id,status,error,queued_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,email) DO UPDATE SET "
        "contact_id=excluded.contact_id,message_id=excluded.message_id,"
        "status=excluded.status,error=excluded.error,updated_at=excluded.updated_at",
        (campaign_id, email, contact_id, message_id, status, error[:500],
         now if status == "queued" else None, now))
    cx.commit()


def _send(contact_id, subject, text, body_html, *, email_to="",
          scheduled_timestamp=None):
    body = {
        "type": "Email", "contactId": contact_id, "subject": subject,
        "html": body_html, "message": text, "emailFrom": FROM_ADDRESS,
    }
    if email_to:
        body["emailTo"] = email_to
    if scheduled_timestamp is not None:
        body["scheduledTimestamp"] = int(scheduled_timestamp)
    status, data = _api("POST", "/conversations/messages", GHL_MESSAGES_VERSION,
                        body, write=True)
    nested_message = data.get("message")
    if not isinstance(nested_message, dict):
        nested_message = {}
    message_id = (data.get("messageId") or data.get("id")
                  or nested_message.get("id") or "")
    return status, message_id, data


def run(args):
    target_date = datetime.fromisoformat(args.date).date()
    campaign_id = f"weekly-live-community-{target_date.isoformat()}"
    campaign_name = f"Weekly Live Community | {target_date.isoformat()}"
    subject = _subject(target_date)
    _refuse_em_dash(subject, target_date)
    # The hard rule: mail only what filter_audience.py returned, because it reads
    # both consent stores and this script reads neither fully. A send without the
    # list refuses before any contact is read.
    only_list = getattr(args, "only_list", None)
    if args.send and not only_list:
        raise RuntimeError("--send requires --only-list from live_invitation_allowlist.py")
    fingerprints = allowlist.decode(only_list) if only_list else None
    allowed = ((lambda email: allowlist.allows(fingerprints, email))
               if fingerprints is not None else None)
    gate = _event_gate(target_date)
    if not gate["ok"]:
        raise RuntimeError("event safety gate failed: " + "; ".join(gate["issues"]))
    audience, tag_sets, paid, certification, missing_ghl = _build_audience(
        create_missing_contacts=args.send, allowed=allowed)
    built_total = len(audience)
    if allowed is not None:
        audience = {email: contact for email, contact in audience.items()
                    if allowed(email)}
    eligible = paid | certification
    counts = {"pb_member": len(tag_sets["pb:member"]),
              "e4l_account": len(tag_sets["e4l account"]),
              "certification": len(certification), "paid_full": len(paid),
              "deduplicated_total": built_total,
              "group_eligible": len(set(audience) & eligible),
              "missing_ghl_contact": len(missing_ghl),
              "suppressed": 0, "dnd": 0, "queued": 0,
              "failed": 0, "already_queued": 0, "portal_created_or_recovered": 0}
    if fingerprints is not None:
        counts.update({"allowlist_size": len(fingerprints),
                       "allowlist_matched": len(audience),
                       "allowlist_not_in_audience": len(fingerprints) - len(audience),
                       "outside_allowlist_skipped": built_total - len(audience)})
        if args.send and not audience:
            raise RuntimeError("no audience member matches the list; "
                               "check the list was built with the production key")
    if args.dry_run:
        with appmod.db.connect(appmod.LOG_DB) as cx:
            client_portal.init_client_portal_table(cx)
            counts["missing_portal"] = sum(1 for email in audience if not cx.execute(
                "SELECT 1 FROM client_portals WHERE lower(email)=?", (email,)).fetchone())
        print(json.dumps({"status": "DRY_RUN", "campaign_id": campaign_id,
                          "campaign_name": campaign_name, "subject": subject,
                          "counts": counts, "event_gate": gate}, sort_keys=True))
        return 0

    if missing_ghl:
        raise RuntimeError(f"{len(missing_ghl)} authoritative members have no GHL contact")
    with appmod.db.connect(appmod.LOG_DB) as cx:
        _init_run_tables(cx)
        cx.execute(
            "INSERT INTO weekly_live_invitation_runs "
            "(campaign_id,target_date,campaign_name,subject,status,counts_json,started_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET "
            "campaign_name=excluded.campaign_name,subject=excluded.subject,"
            "status=excluded.status,counts_json=excluded.counts_json,updated_at=excluded.updated_at",
            (campaign_id, target_date.isoformat(), campaign_name, subject, "sending",
             json.dumps(counts, sort_keys=True), _now(), _now()))
        cx.commit()
        sendable = []
        for email, contact in sorted(audience.items()):
            prior = _existing_status(cx, campaign_id, email)
            if prior in {"queued", "sent", "delivered", "opened", "clicked"}:
                counts["already_queued"] += 1
                continue
            if email_suppression.is_suppressed(cx, email):
                counts["suppressed"] += 1
                _record_recipient(cx, campaign_id, email, contact.get("id") or "", "",
                                  "suppressed")
                continue
            if _dnd_email(contact):
                counts["dnd"] += 1
                _record_recipient(cx, campaign_id, email, contact.get("id") or "", "", "dnd")
                continue
            sendable.append((email, contact))

        schedule_anchor = int(time.time())
        for batch_no, offset in enumerate(range(0, len(sendable), 100), start=1):
            batch = sendable[offset:offset + 100]
            scheduled_timestamp = (None if batch_no == 1 else
                                   schedule_anchor + (batch_no - 1) * 900)
            for email, contact in batch:
                token = client_portal.ensure_token(
                    cx, email, (contact.get("name") or
                                " ".join(filter(None, [contact.get("firstName"),
                                                       contact.get("lastName")]))))
                counts["portal_created_or_recovered"] += 1
                portal_url = f"{PORTAL_BASE}/portal/{token}"
                first = (contact.get("firstName") or "").strip()
                text, body_html = _copy(first, portal_url, email in eligible,
                                        target_date, email)
                if any(bad in (text + body_html).lower()
                       for bad in ("practicebetter", "practice better", "skool", "zoom.us/")):
                    raise RuntimeError("deprecated or private destination found in invitation copy")
                status, message_id, response = _send(
                    contact.get("id") or "", subject, text, body_html,
                    email_to=email, scheduled_timestamp=scheduled_timestamp)
                if status < 400 and message_id:
                    counts["queued"] += 1
                    _record_recipient(cx, campaign_id, email, contact.get("id") or "",
                                      message_id, "queued")
                else:
                    counts["failed"] += 1
                    _record_recipient(
                        cx, campaign_id, email, contact.get("id") or "", message_id,
                        "failed", f"HTTP {status}: {json.dumps(response)[:350]}")
                time.sleep(0.3)
            print(json.dumps({"campaign_id": campaign_id, "batch": batch_no,
                              "batch_size": len(batch), "queued_total": counts["queued"],
                              "failed_total": counts["failed"], "time_hst":
                              datetime.now(HST).isoformat(), "scheduled_for_hst":
                              (datetime.fromtimestamp(scheduled_timestamp, HST).isoformat()
                               if scheduled_timestamp else "immediate")},
                             sort_keys=True), flush=True)

        final = "verified_queued" if counts["failed"] == 0 else "needs_attention"
        cx.execute("UPDATE weekly_live_invitation_runs SET status=?,counts_json=?,updated_at=? "
                   "WHERE campaign_id=?",
                   (final, json.dumps(counts, sort_keys=True), _now(), campaign_id))
        cx.commit()
    print(json.dumps({"status": final, "campaign_id": campaign_id,
                      "campaign_name": campaign_name, "subject": subject,
                      "counts": counts, "event_gate": gate,
                      "sender": FROM_ADDRESS, "batch_size": 100,
                      "batch_interval_minutes": 15}, sort_keys=True))
    return 0 if final == "verified_queued" else 2


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--date", required=True, help="Wednesday HST date, YYYY-MM-DD")
    parser.add_argument("--only-list", dest="only_list", default=None,
                        help="fingerprints from live_invitation_allowlist.py; required with --send")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}), flush=True)
        raise


if __name__ == "__main__":
    main()
