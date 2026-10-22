"""When a test's client was emailed their report.

Glen, 2026-10-22: Intake sections start closed, and a section he opens stays open
"until the report and invoice is done and client notified". The notice is the report
email: the "Publish to portal" button, or the first-portal email that printing or
emailing the report sends. Recorded only when the email was actually delivered, which
the portal reports as `emailed` (a gmail-api, smtp or ghl send).
"""


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_test_notified (
        test_id TEXT PRIMARY KEY, notified_at TEXT NOT NULL)""")


def mark(cx, test_id):
    """Record the first delivered report email for this test. A later one keeps it."""
    ensure_schema(cx)
    cx.execute("INSERT OR IGNORE INTO biofield_test_notified (test_id, notified_at) "
               "VALUES (?, CURRENT_TIMESTAMP)", (str(test_id),))
    cx.commit()


def notified_at(cx, test_id):
    ensure_schema(cx)
    row = cx.execute("SELECT notified_at FROM biofield_test_notified WHERE test_id=?",
                     (str(test_id),)).fetchone()
    return row[0] if row else ""
