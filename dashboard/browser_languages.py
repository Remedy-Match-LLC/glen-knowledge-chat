"""Which languages visitors' browsers ask for, as daily counts only.

Step 0 of the translate button (Glen, 2026-10-12: "start step 0"). Two weeks of
these counts decide which languages to offer first.

Each home or begin page load adds 1 to (day, language). Nothing else is kept: no
address, no visitor, no page. "none" counts loads whose browser sent no language,
so the language counts can be read against how many loads they cover.
"""
import datetime
import re

try:
    from zoneinfo import ZoneInfo
    _HST = ZoneInfo("Pacific/Honolulu")
except Exception:  # noqa: BLE001 - no tz database: fall back to a fixed offset
    _HST = datetime.timezone(datetime.timedelta(hours=-10))

TABLE = "browser_language_daily"
NONE = "none"
_LANG = re.compile(r"^[a-z]{2,3}$")
_BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless|curl|wget|python|"
                  r"httpclient|go-http|java/|okhttp|axios|node-fetch|uptime", re.I)
_ready = False


def counted_path(path):
    """The home page and the begin pages: public, and served by our own code."""
    path = path or ""
    return path == "/" or path == "/begin" or path.startswith("/begin/")


def is_bot(user_agent):
    ua = (user_agent or "").strip()
    return not ua or bool(_BOT.search(ua))


def primary_language(header):
    """The language the browser prefers most, as its 2 or 3 letter code ("es").

    "es-MX,es;q=0.9,en;q=0.8" is "es". The highest weight wins; ties keep the
    browser's order. NONE when the header is absent or holds nothing usable."""
    best, best_q = None, -1.0
    for part in str(header or "").split(","):
        bits = [b.strip() for b in part.split(";")]
        tag = bits[0].lower()
        q = 1.0
        for b in bits[1:]:
            if b.lower().startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        code = tag.split("-")[0]
        if not _LANG.match(code) or q <= 0:
            continue
        if q > best_q:
            best, best_q = code, q
    return best or NONE


def today_hst(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(_HST).date().isoformat()


def ensure_table(cx):
    cx.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ("
               "day TEXT NOT NULL, lang TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, "
               "PRIMARY KEY (day, lang))")


def record(cx, lang, day=None):
    """Add one load for this language today. Commits."""
    global _ready
    if not _ready:
        ensure_table(cx)
        _ready = True
    cx.execute(f"INSERT INTO {TABLE} (day, lang, n) VALUES (?, ?, 1) "
               f"ON CONFLICT(day, lang) DO UPDATE SET n = {TABLE}.n + 1",
               (day or today_hst(), lang))
    cx.commit()


def totals(cx, since=None):
    """{lang: loads} from `since` (an ISO day) onward, most first."""
    ensure_table(cx)
    sql, args = f"SELECT lang, SUM(n) FROM {TABLE}", ()
    if since:
        sql, args = sql + " WHERE day >= ?", (since,)
    rows = cx.execute(sql + " GROUP BY lang", args).fetchall()
    return dict(sorted(((r[0], int(r[1])) for r in rows), key=lambda kv: (-kv[1], kv[0])))
