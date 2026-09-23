"""Store arrivals: who lands on a product page from a tagged link, and from where.

Glen, 2026-09-21: GrooveKart now links 71 products to their page on the new store, with
utm_source=groovekart. Nothing recorded those visits. This logs them, and nothing else.

Only a visit whose address carries utm_source is recorded. Ordinary browsing is not.
No IP address and no email is stored: the session is the anonymous amg_session cookie.
Known crawlers are skipped so they do not inflate the count. The report counts both
arrivals and distinct sessions, because one person reloading is several arrivals.
"""
import re
from datetime import datetime, timedelta, timezone

MAX_FIELD = 100
MAX_DAYS = 90
# Link-preview fetchers and search crawlers. Matched case-insensitively on the User-Agent.
BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|headless|"
    r"curl/|wget/|python-requests|python-urllib|go-http-client|httpx",
    re.I)


def _now():
    return datetime.now(timezone.utc)


def _clean(value):
    return str(value or "").strip()[:MAX_FIELD]


def init_store_arrivals(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS store_arrivals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, arrived_at TEXT NOT NULL, "
        "product_slug TEXT NOT NULL, utm_source TEXT NOT NULL, "
        "utm_medium TEXT NOT NULL DEFAULT '', utm_campaign TEXT NOT NULL DEFAULT '', "
        "session TEXT NOT NULL DEFAULT '')"
    )
    cx.execute("CREATE INDEX IF NOT EXISTS ix_store_arrivals_at "
               "ON store_arrivals(arrived_at)")
    cx.commit()


def is_bot(user_agent):
    ua = (user_agent or "").strip()
    return not ua or bool(BOT_RE.search(ua))


def should_record(args, user_agent, method):
    """True only for a real GET carrying a utm_source."""
    return (method == "GET" and bool(_clean(args.get("utm_source")))
            and not is_bot(user_agent))


def record(cx, product_slug, args, session):
    cx.execute(
        "INSERT INTO store_arrivals (arrived_at, product_slug, utm_source, utm_medium, "
        "utm_campaign, session) VALUES (?,?,?,?,?,?)",
        (_now().isoformat(), _clean(product_slug), _clean(args.get("utm_source")).lower(),
         _clean(args.get("utm_medium")).lower(), _clean(args.get("utm_campaign")),
         _clean(session)))
    cx.commit()


def clamp_days(raw, default=30):
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(days, MAX_DAYS))


def report(cx, days, source=""):
    """Arrivals in the last `days` days, by source, by product and by day."""
    since = (_now() - timedelta(days=days)).isoformat()
    where = "arrived_at >= ?"
    params = [since]
    src = _clean(source).lower()
    if src:
        where += " AND utm_source = ?"
        params.append(src)

    def rows(select, group):
        return cx.execute(
            f"SELECT {select}, COUNT(*), COUNT(DISTINCT session) FROM store_arrivals "
            f"WHERE {where} GROUP BY {group} ORDER BY COUNT(*) DESC, {group}",
            params).fetchall()

    total = cx.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT session) FROM store_arrivals WHERE {where}",
        params).fetchone()
    by_source = [{"utm_source": r[0], "utm_medium": r[1], "arrivals": r[2], "sessions": r[3]}
                 for r in rows("utm_source, utm_medium", "utm_source, utm_medium")]
    by_product = [{"product_slug": r[0], "arrivals": r[1], "sessions": r[2]}
                  for r in rows("product_slug", "product_slug")]
    by_day = [{"day": r[0], "arrivals": r[1], "sessions": r[2]}
              for r in rows("substr(arrived_at, 1, 10)", "substr(arrived_at, 1, 10)")]
    by_day.sort(key=lambda d: d["day"])
    return {"days": days, "since": since, "source": src,
            "arrivals": total[0], "sessions": total[1],
            "by_source": by_source, "by_product": by_product, "by_day": by_day}
