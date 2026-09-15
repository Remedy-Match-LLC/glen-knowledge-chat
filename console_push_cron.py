#!/usr/bin/env python3
"""
Console Push — hourly cron script that pulls action items from Gmail/GHL
and POSTs them to the Render console API as todos.

Cron (add via `crontab -e`):
  0 * * * *  /usr/bin/python3 "/Users/doctor_glen_macbook_pro/AI-Training/02 Skills/console-push.py" >> /tmp/console-push.log 2>&1

Requires CONSOLE_SECRET env var (or doppler: CONSOLE_SECRET or WEBHOOK_SECRET).
"""

import os, sys, json, base64, re, subprocess, requests, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import anthropic as _ant

# ── Config ────────────────────────────────────────────────────────────────────
RENDER_BASE = 'https://glen-knowledge-chat.onrender.com'
CONSOLE_URL = f'{RENDER_BASE}/api/todos'

def _get_secret(name):
    try:
        return subprocess.check_output(['doppler','secrets','get',name,'--plain'],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return os.environ.get(name, '')

CONSOLE_SECRET  = _get_secret('CONSOLE_SECRET') or _get_secret('WEBHOOK_SECRET')
GHL_API_KEY     = _get_secret('GHL_API_KEY')
ANTHROPIC_KEY   = _get_secret('ANTHROPIC_API_KEY')
LOCATION_ID    = 'AODZ6MycyxiIGjqnxjQ2'

# On Render, token paths are injected via env vars (written from DB by scheduler)
GLEN_TOKEN = Path(os.environ.get('GLEN_TOKEN_PATH',
    str(Path.home()/'.config'/'google'/'token.json')))
RAE_TOKEN  = Path(os.environ.get('RAE_TOKEN_PATH',
    str(Path.home()/'.config'/'gmail-triage'/'token-rae.json')))
CAL_TOKEN  = Path(os.environ.get('CALENDAR_TOKEN_PATH',
    str(Path.home()/'.config'/'gmail-triage'/'token-calendar.json')))
# Glen's token (google-auth.py) includes full scope set
SCOPES_GLEN = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar.readonly',
]
# Rae's token has only gmail.readonly
SCOPES_READ = ['https://www.googleapis.com/auth/gmail.readonly']

HEADERS = {'X-Console-Key': CONSOLE_SECRET, 'Content-Type': 'application/json'}

# ── Gmail helpers ─────────────────────────────────────────────────────────────
def _gmail_service(token_file, scopes=None):
    if scopes is None:
        scopes = SCOPES_READ
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f'  Gmail init failed ({token_file.name}): {e}')
        return None

def _search(service, query, days=2, max_results=20):
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    try:
        r = service.users().messages().list(
            userId='me', q=f'{query} after:{after}', maxResults=max_results
        ).execute()
        return r.get('messages', [])
    except Exception:
        return []

def _msg_meta(service, msg_id):
    try:
        m = service.users().messages().get(
            userId='me', id=msg_id, format='metadata',
            metadataHeaders=['From','Subject','Date']
        ).execute()
        h = {x['name']: x['value'] for x in m['payload']['headers']}
        age_s = ''
        ms = int(m.get('internalDate', 0))
        if ms:
            h_ago = (datetime.now(timezone.utc).timestamp() - ms/1000) / 3600
            age_s = f'{int(h_ago)}h ago' if h_ago < 24 else f'{int(h_ago/24)}d ago'
        return {'from': h.get('From',''), 'subject': h.get('Subject',''),
                'snippet': m.get('snippet','')[:120], 'age': age_s, 'id': msg_id}
    except Exception:
        return None

# ── Starred email helpers ─────────────────────────────────────────────────────
def _full_body(service, msg_id):
    """Return plaintext body of a message (walks multipart)."""
    try:
        msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        def _walk(part):
            if part.get('mimeType') == 'text/plain':
                data = part.get('body', {}).get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
            for p in part.get('parts', []):
                r = _walk(p)
                if r: return r
            return ''
        return _walk(msg.get('payload', {}))
    except Exception:
        return ''

def _bullet_summary(subject, from_addr, body):
    """Call Claude Haiku for bullet summary + action line + verbatim core message.

    Returns (bullets, action, core_message). core_message is the actual
    human-written content stripped of notification chrome (e.g. the real message
    inside a Practice Better notification), '' if there is none.
    """
    if not ANTHROPIC_KEY:
        return '', '', ''
    try:
        cl = _ant.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = (
            f"Summarize this email for Dr. Glen Swartwout in 2-4 brief bullets (key info only), "
            f"then one ACTION line, then MESSAGE: the actual human-written message content "
            f"verbatim, stripped of notification chrome, signatures, app-store links, and "
            f"boilerplate. If it is a notification wrapping a real message (e.g. Practice "
            f"Better), return only that real message. If there is no human-written message, "
            f"leave MESSAGE empty.\n\n"
            f"From: {from_addr}\nSubject: {subject}\nBody:\n{body[:1800]}\n\n"
            f"Format exactly:\nBULLETS:\n• ...\nACTION: ...\nMESSAGE: ..."
        )
        msg = cl.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = msg.content[0].text
        bullets, action, message = '', '', ''
        if 'BULLETS:' in text and 'ACTION:' in text:
            bullets = text.split('BULLETS:')[1].split('ACTION:')[0].strip()
            rest = text.split('ACTION:')[1]
            if 'MESSAGE:' in rest:
                action  = rest.split('MESSAGE:')[0].strip()
                message = rest.split('MESSAGE:')[1].strip()
            else:
                action = rest.strip()
        else:
            bullets = text[:250]
        return bullets, action, message
    except Exception as e:
        print(f'  Claude summary error: {e}')
        return '', '', ''

def triage_starred(service):
    """Pull Glen's starred emails (past 7 days) and generate bullet summaries."""
    print('\n[STARRED] Fetching starred emails...')
    after = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    try:
        r = service.users().messages().list(
            userId='me', q=f'is:starred after:{after}', maxResults=20
        ).execute()
        msgs = r.get('messages', [])
    except Exception as e:
        print(f'  Starred fetch error: {e}')
        return []
    print(f'  {len(msgs)} starred message(s)')
    todos = []
    for m in msgs:
        meta = _msg_meta(service, m['id'])
        if not meta: continue
        body = _full_body(service, m['id'])
        bullets, action, core_message = _bullet_summary(meta['subject'], meta['from'], body)
        todos.append({
            'owner':     'glen',
            'category':  '★ Starred',
            'title':     (meta['subject'] or '(no subject)')[:120],
            'body':      body[:800],
            'priority':  'high',
            'source':    'glen-starred',
            'ai_summary':   bullets,
            'action_note':  action,
            'core_message': core_message,
            'suggested_reply': '',
            'dedup_key': f'glen:starred:{m["id"]}',
        })
    return todos

# ── Dedup helpers ─────────────────────────────────────────────────────────────
def _post_todos(items):
    if not items:
        return
    r = requests.post(CONSOLE_URL, headers=HEADERS, json=items, timeout=15)
    try:
        d = r.json()
        print(f'  Posted {len(items)} items → {d.get("inserted",0)} new')
    except Exception:
        print(f'  POST failed: {r.status_code}')

# ── Glen's Gmail triage ───────────────────────────────────────────────────────
GLEN_CATEGORIES = [
    {
        'name': 'Payments', 'owner': 'glen', 'priority': 'high',
        'query': '(from:authorizenet.com OR from:wise.com OR from:paypal.com OR from:intuit.com) (subject:receipt OR subject:payment OR subject:transaction OR subject:invoice)',
        'action': 'Confirm payment received — log to QuickBooks',
    },
    {
        'name': 'CTD / Strategic Profits', 'owner': 'glen', 'priority': 'normal',
        'query': '(from:strategicprofits.com OR from:mg.strategicprofits.com OR from:kaneandamy.com) -subject:"Payment Due" -subject:"spot isn\'t confirmed" -subject:"Action Needed"',
        'action': 'Review for action items, deadlines, or event registrations',
    },
    {
        'name': 'Facebook Ads', 'owner': 'glen', 'priority': 'normal',
        'query': '(from:facebookmail.com OR from:meta.com) -from:groupupdates@facebookmail.com -from:notifications@facebookmail.com subject:(policy OR billing OR suspended OR disabled OR payment OR warning OR account)',
        'action': 'Check for policy alerts, billing issues, or test results',
    },
    {
        'name': 'Academia / Citations', 'owner': 'glen', 'priority': 'low',
        'query': 'from:academia-mail.com OR from:academia.edu',
        'action': 'Check for new citation alerts',
    },
]

# Rae's categories (uses suerae1111@gmail.com token when available)
RAE_CATEGORIES = [
    {
        'name': 'Orders / Fulfillment', 'owner': 'rae', 'priority': 'high',
        'query': '(from:groovekart.com OR from:groovekartmail.com OR subject:order OR subject:shipment OR subject:tracking) -from:noreply',
        'action': 'Process / fulfill / update tracking',
    },
    {
        'name': 'Scheduling', 'owner': 'rae', 'priority': 'high',
        'query': '(subject:appointment OR subject:schedule OR subject:booking OR subject:calendar OR from:calendly.com)',
        'action': 'Confirm or reschedule',
    },
    {
        'name': 'Client Comms', 'owner': 'rae', 'priority': 'normal',
        'query': '(subject:"Healing Oasis" OR subject:consultation OR subject:client) -from:practicebetter.io',
        'action': 'Reply or route',
    },
    {
        'name': 'Financial', 'owner': 'rae', 'priority': 'high',
        'query': '(from:authorizenet.com OR from:wise.com OR from:paypal.com OR from:intuit.com OR from:quickbooks.com) (subject:receipt OR subject:payment OR subject:invoice OR subject:statement)',
        'action': 'Log to QuickBooks / reconcile',
    },
]

def triage_pb(service):
    """
    Practice Better logic: almost all PB emails are new-signup notifications.
    Only alert if unread message count exceeds signup count (extra = real portal note).
    """
    print('\n[PRACTICE BETTER] Checking...')
    after = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    try:
        def _count(q, days=7):
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
            r = service.users().messages().list(
                userId='me', q=f'{q} after:{cutoff}', maxResults=50
            ).execute()
            return len(r.get('messages', []))

        # Signups: 7-day window (captures the full recent cohort)
        signups  = _count('from:practicebetter.io (subject:"is now on Practice Better" OR subject:"completed a form") -subject:"sent you a message"', days=7)
        # Messages: only UNREAD notifications (already-read = already handled)
        messages = _count('from:practicebetter.io subject:"sent you a message" is:unread', days=7)
        print(f'  {signups} new signup(s), {messages} unread PB message(s)')

        extra = messages - signups
        if extra > 0:
            return [{
                'owner':     'glen',
                'category':  'Practice Better — Message',
                'title':     f'Check Practice Better portal — {extra} message(s) beyond signup count',
                'body':      f'{messages} unread message notification(s) vs {signups} new signup(s) in the last 7 days.\nLog in at Practice Better to read and reply.',
                'priority':  'high',
                'source':    'pb-check',
                'dedup_key': f'pb:extra:{datetime.now(timezone.utc).strftime("%Y-%m-%d")}',
            }]
    except Exception as e:
        print(f'  PB check error: {e}')
    return []


def triage_gmail(owner, token_file, categories, scopes=None):
    print(f'\n[{owner.upper()}] Gmail triage...')
    if not token_file.exists():
        print(f'  No token at {token_file} — skipping')
        return []

    service = _gmail_service(token_file, scopes)
    if not service:
        return []

    todos = []
    for cat in categories:
        days = cat.get('days', 2)
        msgs = _search(service, cat['query'], days=days)
        if not msgs:
            continue
        print(f'  {cat["name"]}: {len(msgs)} message(s)')
        for m in msgs:
            meta = _msg_meta(service, m['id'])
            if not meta:
                continue
            title = meta['subject'] or '(no subject)'
            body_parts = []
            if meta.get('from'):
                body_parts.append(f'From: {meta["from"]}')
            if meta.get('snippet'):
                body_parts.append(meta['snippet'])
            body_parts.append(f'Action: {cat["action"]}')
            todos.append({
                'owner':     cat['owner'],
                'category':  cat['name'],
                'title':     title[:120],
                'body':      '\n'.join(body_parts),
                'priority':  cat['priority'],
                'source':    f'{owner}-gmail',
                'dedup_key': f'{owner}:gmail:{m["id"]}',
            })
    return todos

# ── GHL open tasks → Shaira ───────────────────────────────────────────────────
def fetch_ghl_tasks():
    """Pull open GHL tasks via contact search, route PB ones to Shaira, rest to Glen."""
    if not GHL_API_KEY:
        print('  GHL_API_KEY not set — skipping GHL tasks')
        return []
    print('\n[GHL] Fetching open tasks...')
    # GHL v1 has no bulk tasks endpoint; search contacts with tasks via v2 search
    # Uses v1 contact search + tasks per contact (limit to recent active contacts)
    try:
        r = requests.get(
            'https://rest.gohighlevel.com/v1/contacts/',
            headers={'Authorization': f'Bearer {GHL_API_KEY}'},
            params={'locationId': LOCATION_ID, 'limit': 100},
            timeout=10
        )
        if r.status_code != 200:
            print(f'  GHL contacts error: {r.status_code} — skipping')
            return []
        contacts = r.json().get('contacts', [])
    except Exception as e:
        print(f'  GHL contacts fetch error: {e}')
        return []

    todos = []
    for contact in contacts:
        cid = contact.get('id', '')
        if not cid:
            continue
        try:
            tr = requests.get(
                f'https://rest.gohighlevel.com/v1/contacts/{cid}/tasks/',
                headers={'Authorization': f'Bearer {GHL_API_KEY}'},
                timeout=5
            )
            if tr.status_code != 200:
                continue
            tasks = tr.json().get('tasks', [])
            for t in tasks:
                if t.get('completed'):
                    continue
                title = t.get('title', '(no title)')
                body  = t.get('body', '')
                due   = t.get('dueDate', '')
                contact_name = f"{contact.get('firstName','')} {contact.get('lastName','')}".strip()
                owner = 'shaira' if 'practice better' in title.lower() or 'add email' in title.lower() else 'glen'
                todos.append({
                    'owner':    owner,
                    'category': 'GHL Admin',
                    'title':    title[:120],
                    'body':     f'Contact: {contact_name}\n' + body[:300] + (f'\nDue: {due[:10]}' if due else ''),
                    'priority': 'high' if 'practice better' in title.lower() else 'normal',
                    'source':   'ghl-tasks',
                    'dedup_key': f'ghl:task:{t.get("id","")}',
                })
        except Exception:
            continue

    print(f'  {len(todos)} open task(s)')
    return todos

# ── Google Calendar ───────────────────────────────────────────────────────────
CAL_TOKEN = Path.home() / '.config' / 'gmail-triage' / 'token-calendar.json'
CAL_SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events',
]

def _cal_service():
    if not CAL_TOKEN.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(CAL_TOKEN), CAL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f'  Calendar init failed: {e}')
        return None

def push_calendar_events(days=14):
    print('\n[CALENDAR] Fetching events...')
    svc = _cal_service()
    if not svc:
        print('  No calendar token — skipping')
        return

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(days=days)
    try:
        cal_list = svc.calendarList().list().execute().get('items', [])
    except Exception as e:
        print(f'  calendarList error: {e}')
        return

    events = []
    for cal in cal_list:
        cal_id   = cal['id']
        cal_name = cal.get('summary', cal_id)
        page_token = None
        while True:
            try:
                result = svc.events().list(
                    calendarId=cal_id,
                    timeMin=today_start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy='startTime',
                    maxResults=2500,
                    pageToken=page_token,
                ).execute()
            except Exception:
                break
            for ev in result.get('items', []):
                start   = ev.get('start', {})
                end_    = ev.get('end', {})
                summary = ev.get('summary', '(no title)')
                is_flight  = any(w in summary.lower() for w in ['flight', 'wn ', 'aa ', 'ua ', 'dl ', 'southwest', 'american airlines', 'united airlines', 'delta'])
                is_payment = bool(re.search(r'\$[\d,]+\.?\d*', summary))
                # Payment reminders (dollar amounts in title) belong to Rae
                event_owner = 'rae' if is_payment else 'glen'
                base_ev = {
                    'google_cal_id':   cal_id,
                    'google_event_id': ev['id'],
                    'calendar_name':   cal_name,
                    'summary':         summary,
                    'start':           start.get('dateTime') or start.get('date', ''),
                    'end':             end_.get('dateTime')  or end_.get('date', ''),
                    'location':        ev.get('location', ''),
                    'owner':           event_owner,
                }
                events.append(base_ev)
                # Mirror flights to Rae's calendar
                if is_flight:
                    rae_ev = dict(base_ev)
                    rae_ev['google_event_id'] = ev['id'] + '_rae'
                    rae_ev['owner'] = 'rae'
                    events.append(rae_ev)
            page_token = result.get('nextPageToken')
            if not page_token:
                break

    print(f'  {len(events)} upcoming event(s) across {len(cal_list)} calendar(s)')
    if not events:
        return

    r = requests.post(
        f'{RENDER_BASE}/api/calendar',
        headers=HEADERS,
        json=events,
        timeout=15
    )
    try:
        print(f'  Calendar push: {r.json().get("upserted",0)} upserted')
    except Exception:
        print(f'  Calendar push: {r.status_code}')

def process_delete_queue():
    """Check Render for calendar events marked delete_requested, delete from Google."""
    print('\n[CALENDAR DELETE QUEUE] Checking...')
    svc = _cal_service()
    if not svc:
        return

    try:
        r = requests.get(
            f'{RENDER_BASE}/api/calendar/delete-queue',
            headers=HEADERS,
            timeout=10
        )
        queue = r.json().get('queue', [])
    except Exception as e:
        print(f'  Delete queue fetch error: {e}')
        return

    if not queue:
        print('  Nothing to delete')
        return

    cleared = []
    for item in queue:
        try:
            svc.events().delete(
                calendarId=item['cal_id'],
                eventId=item['event_id']
            ).execute()
            print(f'  Deleted: {item["summary"]}')
            cleared.append(item['id'])
        except Exception as e:
            print(f'  Delete failed ({item["summary"]}): {e}')

    if cleared:
        requests.post(
            f'{RENDER_BASE}/api/calendar/delete-queue/clear',
            headers=HEADERS,
            json={'ids': cleared},
            timeout=10
        )
        print(f'  Cleared {len(cleared)} from queue')

# ── Remedy Match mail, via Gmail ─────────────────────────────────────────────
# remedymatch.com mail moved to Google Workspace on 2026-09-14. Mail to support@, drglen@ and
# rae@ is forwarded to Glen's Gmail, and each keeps its own Delivered-To. This replaced two
# GrooveKart IMAP pulls. Those logged in to a server whose certificate had expired without
# checking it, and fetching marked the mail read. A Gmail search marks nothing read.
REMEDY_ADDRESSES = ('support@remedymatch.com', 'drglen@remedymatch.com', 'rae@remedymatch.com')
REMEDY_QUERY = '(' + ' OR '.join(f'deliveredto:{a}' for a in REMEDY_ADDRESSES) + ') is:unread'
PERSONAL_DOMAINS = ('@gmail', '@yahoo', '@hotmail', '@outlook', '@icloud', '@me.com', '@aol')


def triage_remedy_gmail(service, days=3, max_results=30):
    """Unread mail to the remedymatch.com addresses, as console todos."""
    print('\n[REMEDY MATCH] Searching Gmail...')
    msgs = _search(service, REMEDY_QUERY, days=days, max_results=max_results)
    print(f'  {len(msgs)} unread in last {days}d')
    todos = []
    for m in msgs:
        meta = _msg_meta(service, m['id'])
        if not meta:
            continue
        subject = (meta.get('subject') or '').strip() or '(no subject)'
        from_addr = meta.get('from', '')
        body = _full_body(service, m['id']).strip()[:800]
        bullets, action, core_message = _bullet_summary(subject, from_addr, body)
        # Owner: orders -> Rae, personal-domain senders -> Rae, else -> Glen. Unchanged rule.
        is_order = 'new order' in subject.lower() or 'order confirmed' in subject.lower()
        personal_domain = any(x in from_addr.lower() for x in PERSONAL_DOMAINS)
        todos.append({
            'owner':    'rae' if (is_order or personal_domain) else 'glen',
            'category': 'Remedy Match Support',
            'title':    subject[:120],
            'body':     f'From: {from_addr}\n\n{body}',
            'priority': 'high',
            'source':   'remedy-gmail',
            'ai_summary':    bullets,
            'action_note':   action,
            'core_message':  core_message,
            'suggested_reply': '',
            'dedup_key': f"remedy:gmail:{m['id']}",
        })
    return todos


# ── GHL → People sync ────────────────────────────────────────────────────────
GHL_FIELD_MAP = {
    "1lkRpyfPcZNrTBpCzJnk": "terrain_concerns",
    "6Z8AK3c4Z56HcJpV5bft": "request",
    "BywF1IMDoVyg9kEvLOBL": "birth_time",
    "HwkLqsLPUrpPzsjKu38Q": "surgeries",
    "I4Enwr40l0s9vW5auWMK": "challenges",
    "Icll73HcO6QFyCbqLGPS": "budget",
    "PPomHxQW6jaj5vf0r8Sx": "personal_history",
    "UIoZLhStWzI84krSl0tZ": "roles",
    "bwLAZCPo7hByZ7xQEKvN": "title",
    "cTtOuUiZN8lQjBzyrwb4": "birthplace",
    "fx3khczY6JEhAODOV9os": "gender",
    "ghiyQnT354WRKL1csRfm": "resources",
    "h91bcznkcDa2994aNfwb": "healing_response",
    "icNJnKoS1OW0r4apHmbs": "form_completed_by",
    "kmIvkDLwMTvogkWkkF4X": "family_history",
    "q12KidO5toCrpPtSY3Mj": "medications",
    "quRxBSJr4S6XF4gRAGsC": "goals",
    "uk6jYxfE45gKT2FBsqPo": "conditions",
    "vR79NGSTFxn3WZ34VXGW": "body_systems",
    "zW4bdPaR6GMUKt1jtR7U": "issue_duration",
    "eE8sWQAEy4stBPMS1jV3": "investment",
    "xyGLzfZyHSw26rxlEbRl": "interests",
    "DsbMjwrQqecAsShUJ49b": "profession",
    "FFChZTwhu9nqlFKTULjB": "organizations",
    "Hu7x2xN60nOG3fMT0uZY": "island",
}

# ── GHL v2 per-channel email DND ─────────────────────────────────────────────
# v1 /contacts/ carries only the all-channel `dnd` flag. It read false on all 13 of
# 25 sampled contacts whose EMAIL DND was active (2026-09-15), so email DND never
# reached the hub. v2 POST /contacts/search returns dndSettings per channel, 100
# contacts a request: about 44 requests for the location's ~4,300 contacts, against
# ~4,300 for a per-contact GET. Measured 2026-09-15: 12 pages read in 9.3 s.
GHL_V2_BASE = 'https://services.leadconnectorhq.com'
GHL_V2_VERSION = '2021-07-28'
# GHL v2 sits behind Cloudflare, which refuses Python's default user agent with
# error 1010. This is the identity dashboard/ghl_email.py sends.
GHL_V2_UA = 'RemedyMatch/1.0 (+https://illtowell.com)'
GHL_V2_PAGE = 100
GHL_V2_MAX_PAGES = 200      # 20,000 contacts, a runaway stop well above the ~4,300
GHL_V2_PAUSE_SECS = 0.2     # ~5 req/s, far under GHL's 100 per 10 s burst limit


def _email_dnd_of(contact):
    """(status, message) of a v2 contact's dndSettings.Email, lowercased status."""
    settings = contact.get('dndSettings') or {}
    em = settings.get('Email') or settings.get('email') or {}
    if not isinstance(em, dict):
        return '', ''
    return (str(em.get('status') or '').strip().lower(),
            str(em.get('message') or '').strip())


def fetch_email_dnd_v2():
    """{contact_id: (status, message)} for every contact in the location, or None.

    None on ANY failure: no token, a non-200, a transport error, a bad body, or the
    page cap. A partial read is discarded too, so a failed run sends exactly the
    payload the sync sent before this existed. It never unblocks anyone: the hub
    only ever adds blocks from these fields."""
    token = _get_secret('GHL_PIT')
    if not token:
        print('  GHL_PIT not set — email DND not carried this run')
        return None
    location = _get_secret('GHL_LOCATION_ID') or LOCATION_ID
    headers = {
        'Authorization': f'Bearer {token}',
        'Version': GHL_V2_VERSION,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': GHL_V2_UA,
    }
    out, after = {}, None
    try:
        for _ in range(GHL_V2_MAX_PAGES):
            body = {'locationId': location, 'pageLimit': GHL_V2_PAGE}
            if after:
                body['searchAfter'] = after
            r = requests.post(f'{GHL_V2_BASE}/contacts/search', headers=headers,
                              json=body, timeout=30)
            if r.status_code != 200:
                print(f'  GHL v2 contact search error: {r.status_code} — '
                      'email DND not carried this run')
                return None
            page = (r.json() or {}).get('contacts') or []
            for c in page:
                if c.get('id'):
                    out[c['id']] = _email_dnd_of(c)
            after = page[-1].get('searchAfter') if page else None
            if len(page) < GHL_V2_PAGE or not after:
                return out
            time.sleep(GHL_V2_PAUSE_SECS)
    except Exception as e:
        print(f'  GHL v2 contact search failed: {e!r} — email DND not carried this run')
        return None
    print('  GHL v2 contact search hit the page cap — email DND not carried this run')
    return None


def sync_people_from_ghl(batch_size=100):
    """Sync GHL contacts → Render people table."""
    if not GHL_API_KEY:
        print('  GHL_API_KEY not set — skipping people sync')
        return
    print('\n[PEOPLE] Syncing from GHL...')
    # Read once per run, before paging v1. None means "carry nothing new".
    email_dnd = fetch_email_dnd_v2()
    page, total_synced = 1, 0
    while True:
        try:
            r = requests.get(
                'https://rest.gohighlevel.com/v1/contacts/',
                headers={'Authorization': f'Bearer {GHL_API_KEY}'},
                params={'locationId': LOCATION_ID, 'limit': batch_size, 'page': page},
                timeout=15
            )
            if r.status_code != 200:
                print(f'  GHL contacts error: {r.status_code}')
                break
            contacts = r.json().get('contacts', [])
            if not contacts:
                break
        except Exception as e:
            print(f'  GHL fetch error: {e}')
            break

        people = []
        for c in contacts:
            email = (c.get('email') or '').strip().lower()
            if not email:
                continue
            # Parse custom fields
            cf = {}
            for f in c.get('customField', []):
                fname = GHL_FIELD_MAP.get(f['id'])
                if fname:
                    cf[fname] = f.get('value', '')

            first = (c.get('firstName') or '').strip()
            last  = (c.get('lastName')  or '').strip()
            name  = f'{first} {last}'.strip() or email

            # island: use GHL island field if set, else derive from city
            island = cf.get('island','')
            city = (c.get('city') or '').lower()
            if not island and (c.get('state') or '').upper() in ('HI','HAWAII'):
                if any(x in city for x in ['hilo','keaau','pahoa','volcano','kona','waimea','kamuela']):
                    island = 'Hawaii'
                elif any(x in city for x in ['maui','lahaina','kahului','wailuku','kihei']):
                    island = 'Maui'
                elif 'kauai' in city:
                    island = 'Kauai'
                elif any(x in city for x in ['honolulu','pearl','kailua','kaneohe']):
                    island = 'Oahu'

            # organizations: parse from field (comma or newline separated)
            orgs_raw = cf.get('organizations','')
            orgs = [o.strip() for o in re.split(r'[,\n]+', orgs_raw) if o.strip()] if orgs_raw else []

            person = {
                'email':       email,
                'first_name':  first,
                'last_name':   last,
                'name':        name,
                'phone':       (c.get('phone') or '').strip(),
                'dob':         (c.get('dateOfBirth') or '')[:10],
                'city':        c.get('city') or '',
                'state':       c.get('state') or '',
                'country':     c.get('country') or '',
                'island':      island,
                'source':      c.get('source') or '',
                'ghl_id':      c.get('id') or '',
                'tags':        c.get('tags', []),
                'dnd':         bool(c.get('dnd')),   # GHL unsubscribe/DND → revokes consent in the hub
                'organizations': orgs,
                'last_contact_date': (c.get('dateUpdated') or '')[:10],
            }
            # Per-channel email DND from v2. The hub decides what it means: an
            # address-level block, or a refusal only alongside a refusal signal.
            if email_dnd is not None:
                status, message = email_dnd.get(c.get('id') or '', ('', ''))
                if status:
                    person['email_dnd'] = status
                    person['email_dnd_message'] = message
            # merge custom fields
            for k, v in cf.items():
                if k != 'organizations' and k != 'island':
                    person[k] = v

            people.append(person)

        if people:
            # merge_tags=1: union GHL tags with existing local tags instead of
            # overwriting, so contact-type/consent tags set by the People-hub
            # feeders (type:*, consent:*, source:practitioner-finder, …) survive
            # this GHL->people sync. GHL is not yet the source of those tags
            # (Phase 2), so a plain overwrite here would wipe them every run.
            r2 = requests.post(
                f'{RENDER_BASE}/api/people?merge_tags=1',
                headers=HEADERS, json=people, timeout=20
            )
            try:
                d = r2.json()
                total_synced += d.get('inserted', 0) + d.get('updated', 0)
            except Exception:
                print(f'  People upsert error: {r2.status_code}')

        if len(contacts) < batch_size:
            break
        page += 1

    print(f'  {total_synced} people synced')


# ── Main ──────────────────────────────────────────────────────────────────────
PEOPLE_ONLY = '--people-only'
SKIP_PEOPLE = '--skip-people'


def run_mode(argv):
    """'full' with no flag, which is what the Mac backstop runs. 'people' for --people-only.
    'skip-people' for --skip-people. Render runs the people sync as its own job with its own
    time budget, so a slow Gmail step can no longer starve it."""
    args = set(argv or [])
    if PEOPLE_ONLY in args and SKIP_PEOPLE in args:
        raise SystemExit(f'{PEOPLE_ONLY} and {SKIP_PEOPLE} cannot be combined')
    if PEOPLE_ONLY in args:
        return 'people'
    if SKIP_PEOPLE in args:
        return 'skip-people'
    return 'full'


def main(argv=None):
    mode = run_mode(sys.argv[1:] if argv is None else argv)
    print(f'\n{"="*55}')
    print(f'Console Push  |  {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  {mode}')
    print(f'{"="*55}')

    if mode == 'people':
        sync_people_from_ghl()
        print(f'\nDone.\n')
        return

    all_todos = []

    # Glen's Gmail
    glen_service = None
    if GLEN_TOKEN.exists():
        glen_service = _gmail_service(GLEN_TOKEN, SCOPES_GLEN)
    all_todos += triage_gmail('glen', GLEN_TOKEN, GLEN_CATEGORIES, SCOPES_GLEN)
    if glen_service:
        all_todos += triage_pb(glen_service)
        all_todos += triage_starred(glen_service)

    # Rae's Gmail (only if token exists)
    if RAE_TOKEN.exists():
        all_todos += triage_gmail('rae', RAE_TOKEN, RAE_CATEGORIES)
    else:
        print(f'\n[RAE] No token at {RAE_TOKEN} — run setup to add Rae\'s Gmail')

    # Mail to support@, drglen@ and rae@, from Glen's Gmail. The GrooveKart IMAP pulls were
    # retired on 2026-09-14 when remedymatch.com mail moved to Google Workspace.
    if glen_service:
        all_todos += triage_remedy_gmail(glen_service)

    # GHL tasks
    all_todos += fetch_ghl_tasks()

    print(f'\nTotal items to push: {len(all_todos)}')
    _post_todos(all_todos)

    # People sync (GHL → Render)
    if mode != 'skip-people':
        sync_people_from_ghl()

    # Calendar
    push_calendar_events(days=14)
    process_delete_queue()

    # Projects kanban (00 System/PROJECTS.md → /api/projects/upload)
    push_projects_md()

    # Task Board snapshot (02 Skills/task-board/tasks.json → /console/taskboard)
    push_task_board()

    print(f'\nDone.\n')


def push_projects_md():
    """Upload the latest 00 System/PROJECTS.md to the console for /console/projects."""
    vault_root = Path(os.environ.get('VAULT_ROOT', str(Path.home() / 'AI-Training')))
    md_path = vault_root / '00 System' / 'PROJECTS.md'
    if not md_path.exists():
        print(f'[projects] skipped — {md_path} not found')
        return
    try:
        body = md_path.read_bytes()
        r = requests.post(
            f'{RENDER_BASE}/api/projects/upload',
            data=body,
            headers={
                'X-Console-Key': CONSOLE_SECRET,
                'Content-Type': 'text/markdown',
            },
            timeout=15,
        )
        if r.ok:
            payload = r.json().get('data', {})
            print(f'[projects] uploaded {payload.get("bytes", "?")} bytes to {payload.get("path", "?")}')
        else:
            print(f'[projects] upload failed: {r.status_code} {r.text[:200]}')
    except Exception as e:
        print(f'[projects] upload error: {e}')


def push_task_board():
    """Upload the local Task Board snapshot (tasks.json, generated by the task-board
    launchd refresh) to the console for /console/taskboard. Prod can't scan the vault."""
    vault_root = Path(os.environ.get('VAULT_ROOT', str(Path.home() / 'AI-Training')))
    tj_path = vault_root / '02 Skills' / 'task-board' / 'tasks.json'
    if not tj_path.exists():
        print(f'[taskboard] skipped — {tj_path} not found')
        return
    try:
        data = json.loads(tj_path.read_text())
        cards = data.get('cards', [])
        r = requests.post(
            f'{RENDER_BASE}/api/console/taskboard/sync',
            json={'generated_at': data.get('generated_at'), 'cards': cards},
            headers=HEADERS,
            timeout=20,
        )
        if r.ok:
            print(f'[taskboard] synced {r.json().get("upserted", len(cards))} cards')
        else:
            print(f'[taskboard] sync failed: {r.status_code} {r.text[:200]}')
    except Exception as e:
        print(f'[taskboard] sync error: {e}')


if __name__ == '__main__':
    main()
