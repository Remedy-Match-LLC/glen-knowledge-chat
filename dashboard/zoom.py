"""Zoom Server-to-Server OAuth + meeting registration helpers. Stdlib-only."""
import json, base64, re, urllib.request, urllib.parse

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
            "registration_url": d.get("registration_url")}


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
