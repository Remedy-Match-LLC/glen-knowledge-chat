"""Zoom Server-to-Server OAuth + meeting registration helpers. Stdlib-only."""
import json, base64, re, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

_TOKEN_CACHE = {}  # client_id -> (token, expiry_epoch)


def get_token(account_id, client_id, client_secret, *, _now=None):
    import time
    now = _now if _now is not None else time.time()
    cached = _TOKEN_CACHE.get(client_id)
    if cached and cached[1] > now:
        return cached[0]
    url = "https://zoom.us/oauth/token?" + urllib.parse.urlencode(
        {"grant_type": "account_credentials", "account_id": account_id})
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={"Authorization": f"Basic {basic}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    tok = d["access_token"]
    _TOKEN_CACHE[client_id] = (tok, now + int(d.get("expires_in", 3600)) - 300)
    return tok


def create_meeting(token, *, host, topic, start_iso, duration_min,
                   timezone="Pacific/Honolulu", waiting_room=True,
                   registration_required=False, opener=None, recurrence=None):
    opener = opener or urllib.request.urlopen
    settings = {"waiting_room": waiting_room, "join_before_host": False}
    if registration_required:
        # approval_type=0 means registration is required and automatically approved.
        # Each approved registrant receives an individual join_url from Zoom.
        settings["approval_type"] = 0
        if recurrence:
            # Register once for the series and use the same private join URL for
            # every occurrence.  Weekly event rows still retain occurrence IDs
            # for participant-report reconciliation.
            settings["registration_type"] = 1
    body = {"topic": topic, "type": 8 if recurrence else 2, "start_time": start_iso,
            "duration": int(duration_min), "timezone": timezone,
            "settings": settings}
    if recurrence:
        body["recurrence"] = recurrence
    req = urllib.request.Request(
        f"https://api.zoom.us/v2/users/{host}/meetings",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with opener(req, timeout=30) as r:
        d = json.load(r)
    return {"join_url": d.get("join_url"), "meeting_id": str(d.get("id") or ""),
            "start_url": d.get("start_url"),
            "registration_url": d.get("registration_url"),
            "type": d.get("type"), "occurrences": d.get("occurrences") or []}


def get_meeting(token, meeting_id, *, opener=None):
    """Return the meeting metadata needed to recover weekly occurrences."""
    opener = opener or urllib.request.urlopen
    encoded_id = urllib.parse.quote(str(meeting_id or "").strip(), safe="")
    req = urllib.request.Request(
        f"https://api.zoom.us/v2/meetings/{encoded_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with opener(req, timeout=30) as r:
        d = json.load(r)
    return {"meeting_id": str(d.get("id") or meeting_id or ""),
            "registration_url": d.get("registration_url") or "",
            "type": d.get("type"), "occurrences": d.get("occurrences") or [],
            "settings": d.get("settings") or {}}


def occurrence_id_for(meeting, target_start, *, timezone="Pacific/Honolulu"):
    """Pick the Zoom occurrence matching one concrete local start time."""
    target = target_start
    if target.tzinfo is None:
        target = target.replace(tzinfo=ZoneInfo(timezone))
    target = target.astimezone(ZoneInfo(timezone)).replace(second=0, microsecond=0)
    for occurrence in meeting.get("occurrences") or []:
        raw = occurrence.get("start_time") or ""
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.astimezone(ZoneInfo(timezone)).replace(second=0, microsecond=0) == target:
            return str(occurrence.get("occurrence_id") or "")
    return ""


def add_meeting_registrant(token, *, meeting_id, email, first_name,
                           last_name="", occurrence_id="", opener=None):
    """Register one attendee and return Zoom's private registrant join link."""
    opener = opener or urllib.request.urlopen
    meeting_id = str(meeting_id or "").strip()
    email = str(email or "").strip().lower()
    if not meeting_id or "@" not in email:
        raise ValueError("meeting_id and email are required")
    body = {"email": email, "first_name": (first_name or email.split("@", 1)[0]).strip(),
            "last_name": (last_name or "").strip()}
    if occurrence_id:
        body["occurrence_ids"] = str(occurrence_id)
    encoded_id = urllib.parse.quote(meeting_id, safe="")
    req = urllib.request.Request(
        f"https://api.zoom.us/v2/meetings/{encoded_id}/registrants",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with opener(req, timeout=30) as r:
        d = json.load(r)
    return {"registrant_id": str(d.get("registrant_id") or ""),
            "join_url": d.get("join_url") or "",
            "start_time": d.get("start_time") or "",
            "topic": d.get("topic") or ""}


def meeting_id_from_url(value):
    """Extract a numeric Zoom meeting id from common web join URLs."""
    raw = str(value or "").strip()
    match = re.search(r"(?:/j/|/wc/join/)(\d{9,11})(?:\D|$)", raw)
    return match.group(1) if match else ""
