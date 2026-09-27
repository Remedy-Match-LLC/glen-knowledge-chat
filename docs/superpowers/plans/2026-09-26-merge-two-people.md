# Merge Two People Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff merge a duplicate person into a survivor: records move, the old address keeps working as a way in, every change is logged and undoable, and nothing recreates the merged person.

**Architecture:** Four small modules in `dashboard/`: aliases and logs (`person_aliases.py`), table discovery (`person_merge_discover.py`), the move engine (`person_merge.py`) and evidence plus suggestion (`person_merge_evidence.py`). `app.py` wires them into the identity entry points, the people-creating paths, three console routes, an hourly sweep and a console page. Every address lookup at an entry point goes through one function, `canonical_email`.

**Tech Stack:** Flask `app.py`, the `dashboard.db` wrapper (SQLite in tests, Postgres in production, `?` placeholders on both), APScheduler, GoHighLevel v1 REST through `_ghl_get`/`_ghl_put`, Gmail read through `dashboard.inbox._get_gmail_service`.

**Spec:** `docs/superpowers/specs/2026-09-26-merge-two-people-design.md`

## Global Constraints

- Never run the bare full pytest suite: it sends real email. Run named files with `PINECONE_API_KEY=pcsk_fake OPENAI_API_KEY=sk-fake ANTHROPIC_API_KEY=sk-ant-fake SECRET_KEY=ci CONSOLE_SECRET=ci-fake-console-secret` exported.
- Run every `tests/*.js` under node before pushing a `static/` change, and grep `tests/` for any markup string changed.
- Stage named files only. Never `git add -A`, never `git stash`.
- Production is Postgres: never `cur.lastrowid`, use `dashboard.dbwrite.insert_returning_id`. No SQLite-only SQL in runtime code paths.
- Apply is one transaction: any exception rolls back everything and records nothing as applied.
- History tables are never rewritten (Task 2's `HISTORY_TABLES`).
- Mail from the app goes to the survivor only. The mailbox search is read-only: `messages.list` and `messages.get(format="metadata")` only, never `modify`. Nothing marks mail read.
- Apply and undo are owners only (`_portal_open_is_owner()`).
- No em dashes and no ALL CAPS in any text a person sees.
- Three blind review rounds before Glen merges; round 2 gets a money brief (orders and memberships move).

## Review Focus

1. The merged address arrives with different case or spaces (`PjtStencil@AOL.com `): every row still moves. Pinned in Task 3.
2. One row holds the merged person in two address columns (a coach thread between the two addresses, or `coach_email` and `member_email`): each column moves independently and the row stays one row. Pinned in Task 3.
3. Apply is pressed twice: the second is refused because the address is already an alias, and nothing moves twice. Pinned in Task 3.
4. A chain, A into B then B into C, and staff undo the first merge: undo is refused while a later merge depends on it, naming that merge. Pinned in Task 3.
5. The sweep runs twice in a row: the second run changes nothing and logs nothing. Pinned in Task 4.

---

### Task 1: Aliases and the merge log tables

**Files:**
- Create: `dashboard/person_aliases.py`
- Test: `tests/test_person_aliases.py`

**Interfaces:**
- Produces: `init_tables(cx)`; `canonical_email(cx, email) -> str`; `add_alias(cx, alias, canonical, merge_id)` (raises `ValueError` on a cycle or an existing alias); `aliases_for_merge(cx, merge_id) -> list[str]`; `remove_merge_aliases(cx, merge_id)`; `add_token_alias(cx, token_hash, canonical, merge_id)`; `token_alias(cx, token_hash) -> str | None`; `all_aliases(cx) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_person_aliases.py
import sqlite3

import pytest

from dashboard import person_aliases as pa


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    pa.init_tables(c)
    return c


def test_unknown_address_is_itself_normalised(cx):
    assert pa.canonical_email(cx, "  Mel@Example.com ") == "mel@example.com"


def test_alias_resolves_and_chains(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    pa.add_alias(cx, "b@x.com", "c@x.com", 2)
    assert pa.canonical_email(cx, "A@X.com") == "c@x.com"


def test_cycle_is_refused(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    with pytest.raises(ValueError):
        pa.add_alias(cx, "b@x.com", "a@x.com", 2)


def test_existing_alias_is_refused(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    with pytest.raises(ValueError):
        pa.add_alias(cx, "a@x.com", "c@x.com", 2)


def test_remove_merge_aliases(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 7)
    pa.add_token_alias(cx, "hash1", "b@x.com", 7)
    pa.remove_merge_aliases(cx, 7)
    assert pa.canonical_email(cx, "a@x.com") == "a@x.com"
    assert pa.token_alias(cx, "hash1") is None


def test_token_alias(cx):
    pa.add_token_alias(cx, "hash1", "b@x.com", 3)
    assert pa.token_alias(cx, "hash1") == "b@x.com"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_person_aliases.py -q`
Expected: FAIL, ImportError.

- [ ] **Step 3: Implement**

```python
# dashboard/person_aliases.py
"""The address map and merge log for merging two people.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Every identity entry
point asks canonical_email() first, so a merged address lands on the survivor."""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS person_merges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        survivor_person_id INTEGER, merged_person_id INTEGER,
        survivor_email TEXT, merged_email TEXT,
        merged_person_json TEXT, survivor_before_json TEXT, survivor_after_json TEXT,
        evidence_json TEXT, suggestion_json TEXT, mail_old TEXT,
        applied_by TEXT, applied_at TEXT, undone_at TEXT, undone_by TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS email_aliases (
        alias_email TEXT PRIMARY KEY, canonical_email TEXT NOT NULL,
        merge_id INTEGER, created_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_token_aliases (
        token_hash TEXT PRIMARY KEY, canonical_email TEXT NOT NULL,
        merge_id INTEGER, created_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS person_merge_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, merge_id INTEGER, seq INTEGER,
        table_name TEXT, key_json TEXT, column_name TEXT,
        old_value TEXT, new_value TEXT, action TEXT, row_json TEXT, source TEXT,
        created_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_pmc_merge ON person_merge_changes(merge_id, seq)")
    cx.commit()


def canonical_email(cx, email, _depth=0):
    e = _norm(email)
    if not e or _depth > 10:
        return e
    row = cx.execute("SELECT canonical_email FROM email_aliases WHERE alias_email=?",
                     (e,)).fetchone()
    return canonical_email(cx, row[0], _depth + 1) if row else e


def add_alias(cx, alias, canonical, merge_id):
    a, c = _norm(alias), _norm(canonical)
    if not a or not c or a == c:
        raise ValueError("an alias needs two different addresses")
    if cx.execute("SELECT 1 FROM email_aliases WHERE alias_email=?", (a,)).fetchone():
        raise ValueError(f"{a} is already merged into another person")
    if canonical_email(cx, c) == a:
        raise ValueError("this merge would make a loop")
    cx.execute("INSERT INTO email_aliases (alias_email, canonical_email, merge_id, created_at) "
               "VALUES (?,?,?,?)", (a, c, merge_id, _now()))


def add_token_alias(cx, token_hash, canonical, merge_id):
    cx.execute("INSERT INTO portal_token_aliases (token_hash, canonical_email, merge_id, created_at) "
               "VALUES (?,?,?,?)", (token_hash, _norm(canonical), merge_id, _now()))


def token_alias(cx, token_hash):
    row = cx.execute("SELECT canonical_email FROM portal_token_aliases WHERE token_hash=?",
                     (token_hash,)).fetchone()
    return canonical_email(cx, row[0]) if row else None


def aliases_for_merge(cx, merge_id):
    return [r[0] for r in cx.execute(
        "SELECT alias_email FROM email_aliases WHERE merge_id=?", (merge_id,))]


def remove_merge_aliases(cx, merge_id):
    cx.execute("DELETE FROM email_aliases WHERE merge_id=?", (merge_id,))
    cx.execute("DELETE FROM portal_token_aliases WHERE merge_id=?", (merge_id,))


def all_aliases(cx):
    return [(r[0], r[1]) for r in cx.execute(
        "SELECT alias_email, canonical_email FROM email_aliases")]
```

- [ ] **Step 4: Run to verify it passes.** Same command. Expected: 6 passed.
- [ ] **Step 5: Commit**

```bash
git add dashboard/person_aliases.py tests/test_person_aliases.py
git commit -m "Merge people: address map and merge log tables"
```

---

### Task 2: Discover what to move

**Files:**
- Create: `dashboard/person_merge_discover.py`
- Test: `tests/test_person_merge_discover.py`

**Interfaces:**
- Produces: `Target(table: str, column: str, kind: "email" | "person")`; `targets(cx) -> list[Target]`; `unique_sets(cx, table) -> list[tuple[str, ...]]` (column tuples of every unique index or primary key); `key_columns(cx, table) -> list[str]` (primary key columns, or every column when there is none); `HISTORY_TABLES: frozenset[str]`; `MERGE_OWN_TABLES: frozenset[str]`.

`HISTORY_TABLES`: `ghl_write_queue`, `portal_auth_events`, `email_click_tokens`, `cadence_clicks`, `weekly_live_invitation_recipients`, `portal_welcome_sent`, `email_suppression`, `pending_merges`. Reasons, in a comment beside each: they record what was sent, what was clicked, what was queued to GoHighLevel, or an address-level block. `email_suppression` especially: moving an AOL block to Gmail would stop mail to the survivor.
`MERGE_OWN_TABLES`: the four tables from Task 1.

Backend: `dashboard.db.backend_of(cx)`. SQLite reads `sqlite_master`, `PRAGMA table_info`, `PRAGMA index_list` and `PRAGMA index_info`. Postgres reads `information_schema.columns` with `table_schema=current_schema()` and `pg_index` joined as in `00 System/scripts/prod-queries/person-merge-dry-run.py`, restricted to `current_schema()`.

An email target is a text column (SQLite declared type containing TEXT or CHAR, or empty; Postgres `text` or `character varying`) whose name contains `email`. A person target is a column named exactly `person_id` or `people_id`.

- [ ] **Step 1: Write the failing test** with a SQLite fixture holding: a `people` table; `orders(id PK, email, person_id)`; `carts(email TEXT PRIMARY KEY, items)`; `reveals(id PK, email, scan_date, UNIQUE(email, scan_date))`; `coach_threads(id PK, coach_email, member_email)`; `ghl_write_queue(id PK, email)`; `notes(body TEXT)` with no key. Assert:
  - `targets` finds `orders.email`, `orders.person_id`, both coach columns, `carts.email`, `reveals.email`, `ghl_write_queue.email`, and not `notes.body`;
  - `unique_sets(cx, "reveals")` contains `("email", "scan_date")`; `unique_sets(cx, "carts")` contains `("email",)`;
  - `key_columns(cx, "orders") == ["id"]`, `key_columns(cx, "notes") == ["body"]`;
  - `"ghl_write_queue" in HISTORY_TABLES`.
  Plus a second test, skipped without `PG_DSN`, that builds the same tables in Postgres (`monkeypatch.setenv("DB_BACKEND", "postgres")`, `db.connect("/data/chat_log.db")`, drop then create) and asserts the same results.
- [ ] **Step 2: Run, verify fail.** `python3 -m pytest tests/test_person_merge_discover.py -q` Expected: ImportError.
- [ ] **Step 3: Implement** as specified above. Quote identifiers with double quotes. Return targets sorted by `(table, column)` so change logs are deterministic.
- [ ] **Step 4: Run, verify pass**, and run the Postgres test with `PG_DSN=postgresql://localhost/migtest`. Expected: pass on both.
- [ ] **Step 5: Commit** `dashboard/person_merge_discover.py tests/test_person_merge_discover.py`, message "Merge people: discover the tables to move".

---

### Task 3: The move engine: preview, apply, undo

**Files:**
- Create: `dashboard/person_merge.py`
- Test: `tests/test_person_merge_engine.py`

**Interfaces:**
- Consumes: Task 1 (`init_tables`, `add_alias`, `add_token_alias`, `canonical_email`, `remove_merge_aliases`, `aliases_for_merge`), Task 2 (`targets`, `unique_sets`, `key_columns`, `HISTORY_TABLES`, `MERGE_OWN_TABLES`).
- Produces:
  - `preview(cx, survivor_id, merged_id) -> dict` with `survivor`, `merged` (`{id, email, name}`), `counts` (`{table: [merged_rows, survivor_rows]}`), `clashes` (`{table: n}`), `portal` (`{"keep": "survivor" | "merged" | None, "reason": str}`). Writes nothing.
  - `apply(cx, *, survivor_id, merged_id, mail_old, evidence, suggestion, applied_by, merge_people_fields) -> int` (merge id). Does not commit; the caller commits or rolls back.
  - `undo(cx, merge_id, undone_by) -> dict` with `restored`, `skipped` (list of `{table, key, reason}`). Does not commit.
  - `sweep(cx) -> int` (rows moved). Does not commit.
  - `MergeRefused(Exception)`.

Rules, in order, inside `apply`:
1. Refuse (`MergeRefused`) when the ids are equal, either person is missing, `mail_old` is not `stop` or `keep`, or the merged address already resolves elsewhere (`canonical_email(cx, m) != m`).
2. Insert the `person_merges` row with `merged_person_json` (the whole merged `people` row) and `survivor_before_json` (the whole survivor row). Use `dbwrite.insert_returning_id`.
3. **Portals.** Read the `client_portals` rows for both addresses. Keep the row with the later `updated_at`; on a tie or a missing date keep the survivor's. Combine content (Glen, 2026-09-26): the kept row's analysis set (`ANALYSIS_KEYS = ("greeting", "video", "layers", "findings", "report_pdf", "audio", "reorder_items", "current_scan_date", "biofield_status", "auto_advance")`) stays as it is; any other key the kept content lacks is copied from the other row's content; the kept row's `content_json` change is logged as a `rekey` on that column (old and new JSON). `preview` reports `portal.from_other` (the copied keys). The other row: add its `token_hash` to `portal_token_aliases` for the survivor address, log it `set_aside` with the whole row, delete it. If the kept row is the merged one: rekey its `email` to the survivor (logged), and set the survivor's `portal_notify_state.portal_token` to the merged row's stored raw token (logged as a `rekey` on that column), so re-sent links carry the kept portal's token. `client_portals` is then skipped by the generic loop.
4. **Generic loop.** For each target not in `HISTORY_TABLES`, `MERGE_OWN_TABLES`, `people` or `client_portals`: select rows where `lower(trim(col)) = merged_email` (email) or `col = merged_id` (person). For each row, a clash exists when any unique set containing `col` already has a row with the survivor value in `col` and equal values in the set's other columns (compare with `IS` on SQLite and `IS NOT DISTINCT FROM` on Postgres). A clash: log `set_aside` with `row_json`, delete by key. Otherwise: log `rekey` with old and new, update by key.
5. **People.** Call `merge_people_fields(cx, survivor_id, merged_id)` (the app passes a wrapper around `_merge_two_people`, which fills the survivor's empty fields, combines tags and deletes the merged row). Then store `survivor_after_json`.
6. `add_alias(merged_email, survivor_email, merge_id)`.

`undo`:
1. Refuse when `undone_at` is set, or when a later un-undone merge has this merge's survivor as its merged person or survivor (name that merge's id).
2. Replay changes in `seq` descending. `rekey`: `UPDATE ... SET col=old WHERE <key> AND col=new`; a zero row count is skipped with reason "changed since the merge". `set_aside`: insert `row_json` back; a unique clash is skipped with reason "a newer row holds this key".
3. Reinsert the merged person from `merged_person_json`, keeping its id. For each survivor column, restore the `survivor_before_json` value only when the current value equals the `survivor_after_json` value; otherwise list it as skipped.
4. `remove_merge_aliases`; set `undone_at`, `undone_by`.

`sweep`: for each `(alias, canonical)` in `all_aliases`, run step 4 for email targets only, with `source="sweep"` and the alias's `merge_id`. A second run finds no rows and logs nothing.

- [ ] **Step 1: Write the failing tests.** The fixture holds the Task 2 tables plus `client_portals`, `portal_notify_state(email PK, portal_token)` and `people(id, email, name, tags)`. The survivor is `mel@gmail.com`, id 2; the merged person is `mel@aol.com`, id 1. `merge_people_fields` is a small fake that deletes id 1. Tests:
  1. `test_preview_writes_nothing`: the database is identical before and after (compare a dump of every table).
  2. `test_apply_moves_rows_and_sets_aside_clashes`: orders move; carts clash, so the survivor's cart is kept and the merged cart row is in the log as `set_aside`; the reveals clash on the same scan date is set aside, and a different scan date moves.
  3. `test_history_tables_untouched`: the `ghl_write_queue` row keeps `mel@aol.com`.
  4. `test_case_and_space_variants_move` (Review Focus 1): an order stored as `' Mel@AOL.com'` moves.
  5. `test_two_columns_on_one_row` (Review Focus 2): a coach thread with `coach_email='mel@aol.com'`, `member_email='kai@x.com'` gets `coach_email='mel@gmail.com'`, still one row.
  6. `test_newer_portal_is_kept_and_both_links_work`: the merged portal is newer. After apply, one `client_portals` row for the survivor holds the merged content, `token_alias(<survivor's old hash>)` is the survivor address, and `portal_notify_state` for the survivor holds the merged raw token.
  6b. `test_portal_content_combines_by_rule`: the newer page has `layers` and `greeting`, the older has different `layers` plus `schedule`. The kept content has the newer `layers` and `greeting`, the older `schedule`, and not the older `layers`. Undo restores both rows' content exactly.
  7. `test_apply_twice_is_refused` (Review Focus 3): the second call raises `MergeRefused`, and the change count is unchanged.
  8. `test_undo_restores_the_database_exactly`: take a dump, apply, undo, take a dump; the two dumps are equal except for the four merge tables, where the aliases are gone and `undone_at` is set.
  9. `test_undo_skips_a_row_changed_since`: after apply, edit one moved order; undo lists it in `skipped` and restores the rest.
  10. `test_undo_refused_while_a_later_merge_depends` (Review Focus 4): A into B, then B into C; undoing the first raises `MergeRefused` naming the second merge's id.
  11. `test_failure_mid_apply_leaves_nothing`: make `merge_people_fields` raise; after `cx.rollback()` the dump equals the one before.
  12. `test_sweep_moves_late_rows_once` (Review Focus 5): after apply, insert an order with `mel@aol.com`; `sweep` returns 1 and logs it with `source="sweep"`; a second `sweep` returns 0.
  13. The same apply-then-undo round trip on Postgres, skipped without `PG_DSN`.
- [ ] **Step 2: Run, verify fail.** Expected: ImportError.
- [ ] **Step 3: Implement** to the rules above. Keep each rule in its own function (`_portal_rule`, `_move_target`, `_restore_change`) so a reviewer can read one at a time.
- [ ] **Step 4: Run, verify pass**, on SQLite and with `PG_DSN=postgresql://localhost/migtest`.
- [ ] **Step 5: Mutate.** Break each in turn and watch a test fail, then restore from a copy: the clash check, the history skip, `lower(trim(...))`, the portal "later wins" comparison, the undo "changed since" guard, the already-an-alias refusal.
- [ ] **Step 6: Commit** `dashboard/person_merge.py tests/test_person_merge_engine.py`, message "Merge people: move engine with preview, apply, undo and sweep".

---

### Task 4: Wire the address map into every entry point

**Files:**
- Modify: `dashboard/client_portal.py` (`get_portal_by_token`), `dashboard/portal_auth.py` (`person_by_verified_email`), `dashboard/portal_identity.py` (`_get_or_create_person`), `dashboard/customers.py` (`find_or_create_by_email`), `app.py`: `client_login_request` (`SELECT id, name FROM people WHERE email=?`), the password login and reset lookups (`SELECT id,name FROM people WHERE lower(email)=?`), `_is_paid_member`, `_upsert_person_additive`, `upsert_people`, `_people_search_query`, and a new hourly job.
- Test: `tests/test_person_merge_entry_points.py`

**Interfaces:**
- Consumes: `person_aliases.canonical_email`, `token_alias`, `init_tables`; `person_merge.sweep`.
- Produces: `app._canonical_email(email) -> str` (opens `LOG_DB`, calls `init_tables` once, returns the input normalised on any error and logs `[person-alias]`); `app._run_person_merge_sweep()`.

Each change is the same shape: normalise the address through the map before the existing lookup. The dashboard functions take `cx`, so they call `person_aliases.canonical_email(cx, email)` directly, wrapped in `try` so a missing table behaves as before.

`get_portal_by_token`: when no row matches the hash, `canon = person_aliases.token_alias(cx, th)`; when set, return `{"email": canon, **get_portal_content_by_email(cx, canon)}`.

`_upsert_person_additive`: when `canonical_email(cx, email) != email`, apply only the incoming `tags` to the survivor, minus any tag starting `merged-into-`, and return without creating or updating anything else. No consent, DND, refusal, bounce or suppression effect reaches the survivor from the old contact.

`_people_search_query`: when `q` contains `@`, also match `id IN (SELECT p.id FROM people p JOIN email_aliases a ON a.canonical_email = p.email WHERE a.alias_email LIKE ?)`. The search result carries `merged_from` for display.

Hourly job, beside `people_sync` in the scheduler block: `scheduler.add_job(_run_person_merge_sweep, "interval", hours=1, id="person_merge_sweep", next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5))`. The job holds `_db_lock`, calls `sweep`, commits, and prints `[person-merge-sweep] moved=N`.

- [ ] **Step 1: Write the failing tests** (Flask test client, the `client` fixture pattern from `tests/test_staff_guard_pass.py`, with an alias `mel@aol.com -> mel@gmail.com` seeded through `person_aliases`):
  1. The old portal link: a token alias for a dropped portal opens `/api/portal/<old token>` and returns the survivor's content.
  2. `POST /portal/login-request` with `mel@aol.com` sends the link (patch `_send_full_report_email`) to `mel@aol.com` and the magic link signs in as the survivor's person id.
  3. `person_by_verified_email(cx, "mel@aol.com")` returns the survivor's id.
  4. `_is_paid_member("mel@aol.com")` is true when the survivor holds the membership.
  5. `_upsert_person_additive(cx, {"email": "mel@aol.com", "tags": ["x", "merged-into-2"], "dnd": True, "email_dnd": "active"})` creates no person, adds tag `x` to the survivor, and changes nothing else on the survivor (compare the row before and after, except `tags` and `updated_at`).
  6. `find_or_create_by_email` and `_get_or_create_person` with the old address return the survivor's id.
  7. `GET /api/people/search?q=mel@aol.com` with the console key returns the survivor.
  8. `_canonical_email` returns the input normalised when the alias table is missing, and does not raise.
- [ ] **Step 2: Run, verify fail. Step 3: Implement. Step 4: Run, verify pass.**
- [ ] **Step 5: Mutate** each entry point's canonical call away and watch its test fail.
- [ ] **Step 6: Run neighbours** (`tests/test_client_portal_routes.py`, `tests/test_portal_auth*.py`, `tests/test_people*.py`, `tests/test_customers*.py`) and compare failing sets with origin/main.
- [ ] **Step 7: Commit** the named files, message "Merge people: old addresses resolve to the survivor at every entry point".

---

### Task 5: Evidence and the suggestion

**Files:**
- Create: `dashboard/person_merge_evidence.py`
- Test: `tests/test_person_merge_evidence.py`

**Interfaces:**
- Produces: `gather(cx, email, reply_lookup) -> dict` of `{signal: iso_date_or_None}` for `reply`, `signin`, `signin_link`, `click`, `scan`, `order`, `intake`, `booking`; `suggest(evidence_by_email: dict, survivor_hint: str, now: datetime) -> dict` with `survivor`, `survivor_reason`, `mail_old`, `mail_reason`; `gmail_last_reply(email) -> str | None`.

Sources, each the latest date for that address: `portal_auth_events` (sign-in, joined through `people.id`), `auth_tokens.created_at` (sign-in link), `email_click_tokens` and `cadence_clicks` (click), `client_scans` (scan), `orders` (order), `intake_responses` (intake), `evox_bookings` (booking). Read the exact date column for each table from its `CREATE TABLE`, and name it in a dict at the top of the module.

`gmail_last_reply`: `svc = inbox._get_gmail_service()`; `svc.users().messages().list(userId="me", q=f"from:{email}", maxResults=1).execute()`; then `messages().get(userId="me", id=..., format="metadata", metadataHeaders=["Date"])`; return `internalDate` as ISO. Any error returns `None`. It never calls `modify`.

`suggest`: rank signals `reply` > `signin` > `signin_link` > (`order`, `scan`, `intake`, `booking`) > `click`. Compare the two addresses on the highest-ranked signal where either has a date; the later date wins. With no signals at all, keep `survivor_hint`. The reason is one sentence, for example "Suggested: gmail. Last reply from it was 2026-09-20; none from aol." `mail_old` is `keep` when the non-survivor has any signal within 90 days of `now`, else `stop`, with a one-sentence reason.

- [ ] **Step 1: Write the failing tests:** a reply outranks more recent clicks; the later reply wins; no signals keeps the hint; old-address activity at 89 days gives `keep` and at 91 days gives `stop`; `gather` reads each source table (SQLite fixture); `gmail_last_reply` with a fake service calls only `list` and `get` (a fake whose `modify` raises).
- [ ] **Step 2 to 4:** fail, implement, pass.
- [ ] **Step 5: Commit** "Merge people: evidence and a suggested survivor".

---

### Task 6: GoHighLevel: stop or keep mailing the old address

**Files:**
- Modify: `app.py` (new `ghl_mark_merged(email, survivor_id, stop)` and `ghl_unmark_merged(email, survivor_id)` beside `ghl_update_tags`)
- Test: `tests/test_person_merge_ghl.py`

**Interfaces:**
- Produces: `ghl_mark_merged(email, survivor_id, stop) -> (contact_id, error)`; `ghl_unmark_merged(email, survivor_id) -> (contact_id, error)`.

`ghl_mark_merged`: look up the contact with `_ghl_get("/contacts/lookup", {"email": email})`; none means `(None, None)`. Add tag `merged-into-<survivor_id>`. When `stop`, also send `"dnd": True` in the same `_ghl_put(f"/contacts/{id}", {...})`. `ghl_unmark_merged` removes the tag and, when the tag was present, sends `"dnd": False`.

Safety: Task 4 makes the sync apply only tags from an aliased address, so this DND never reaches the survivor. A test pins that interaction.

- [ ] **Step 1: Write the failing tests** with `_ghl_get` and `_ghl_put` patched: `stop=True` sends `dnd: True` and the tag; `stop=False` sends only the tag; no contact is a no-op; unmark removes the tag and clears DND. Plus an interaction test: after `ghl_mark_merged`, feeding the sync that contact (`dnd: True`, `email_dnd: "active"`, tags including `merged-into-2`) leaves the survivor's consent, DND and suppression rows unchanged.
- [ ] **Step 2 to 4:** fail, implement, pass.
- [ ] **Step 5: Commit** "Merge people: mark the old GoHighLevel contact".

---

### Task 7: Console routes, the sweep job, and retiring the old apply

**Files:**
- Modify: `app.py`
- Test: `tests/test_person_merge_routes.py`

**Interfaces:**
- Consumes: Tasks 1 to 6.
- Produces:
  - `GET /api/console/people/merge/preview?survivor=<id>&merged=<id>`: console-gated (`_portal_console_ok`). Returns `preview(...)`, the evidence for both addresses, and `suggest(...)`. Writes nothing.
  - `POST /api/console/people/merge`: body `{survivor_id, merged_id, mail_old}`. Owners only (`_portal_open_is_owner()`), else 403. Under `_db_lock`: `apply`, commit; on any exception roll back and return 409 with the reason. After commit, `ghl_mark_merged(merged_email, survivor_id, mail_old == "stop")`, recorded on the merge row's result; a GoHighLevel error does not undo the merge and is shown.
  - `POST /api/console/people/merge/<id>/undo`: owners only. `undo`, commit, then `ghl_unmark_merged`. Returns `restored` and `skipped`.
  - `GET /api/console/people/merges/<id>`: the merge row, its change count and any skipped undo rows.
  - `POST /api/pending-merges/<id>/apply` now returns 409 `{"error": "retired", "open": "/console/merge?survivor=<keeper>&merged=<dupe>"}` and changes nothing.
  - The `person_merge_sweep` scheduler job (Task 4).
  - `merge_people_fields` wrapper: `lambda cx, s, m: _merge_two_people(cx, s, m)`.

- [ ] **Step 1: Write the failing tests:** preview writes nothing and returns a suggestion; a VA token gets 403 on apply and undo; the master key applies, and a second apply is 409; undo restores; the old pending apply is 409 and the pending row is still `pending`; a GoHighLevel failure after apply still returns 200 with the error shown; apply with an injected engine failure returns 409 and the database is unchanged.
- [ ] **Step 2 to 4:** fail, implement, pass. **Step 5: Mutate** the owner check and the rollback.
- [ ] **Step 6: Commit** "Merge people: console routes; the old apply is retired".

---

### Task 8: The console page

**Files:**
- Create: `static/console-merge.html`; route `GET /console/merge` in `app.py` (serves the page with `Cache-Control: no-cache, no-store, must-revalidate`, like `/console/client`)
- Modify: `static/console-client.html` (a "Merge with another person" button beside "Open portal", opening `/console/merge?survivor=<this person's id>`); `static/console.html` and `static/shaira-workspace.html` (the pending-merges Apply opens `/console/merge?survivor=<keeper>&merged=<dupe>`)
- Test: `tests/test_person_merge_page.py` (static checks) and `tests/test_person_merge_e2e.py` (Playwright)

The page: a person picker for the second person (the existing people search route); a two-column preview (counts, clashes, evidence with dates, the portal rule's choice); the suggestion lines; two radio groups preset from the suggestion ("Survivor", "Old address in GoHighLevel: stop mailing / keep mailing"); an Apply button that shows an in-page confirmation naming both people and the counts; the result with a link to the merge record; and on the merge record, an Undo button with the same in-page confirmation. No `window.confirm`. Theme tokens only. Works at 390 px wide.

- [ ] **Step 1: Write the failing tests:** the static checks (the button exists on the client page; the pending Apply links to `/console/merge`; no `confirm(`); a Playwright test against the live-server fixture that loads a seeded preview, checks the suggestion text, applies with the in-page confirmation, sees the result, and undoes.
- [ ] **Step 2 to 4:** fail, implement, pass. Run every node test.
- [ ] **Step 5: Commit** "Merge people: console page".

---

### Task 9: Review, merge, deploy, first use

- [ ] Run every test file from Tasks 1 to 8, the Postgres tests with `PG_DSN=postgresql://localhost/migtest`, every node test, and the neighbour sets. Compare failing sets with origin/main.
- [ ] Three blind review rounds. Round 1: can a merge lose or misplace a client's record? Round 2 (`review_round2.py`, money brief): orders, memberships, carts and invoices across a merge and an undo. Round 3: runs the tests and mutates every guard.
- [ ] Push, open the PR, ask Glen to merge in the platform tab. Deploy and verify by containment.
- [ ] Write `00 System/scripts/prod-queries/person-merge-preview.py`: the Task 3 `preview` plus the Task 5 evidence for hub ids 629 and 631, counts and dates only, no addresses. Run it through `render-readonly-job.py`. Send Glen the result as a page.
- [ ] Glen confirms the survivor and mailing choice and presses Apply himself in `/console/merge`.
- [ ] An hour later, after the hourly sync: one person; no AOL person recreated; the AOL portal link opens the Gmail portal with 21 reports. Check with a vault script `00 System/scripts/verify-person-merge-live.py` that uses the console key and prints counts only.
- [ ] Message communication-9e: the portal that holds her reports, and that her sign-in directions can now use Gmail.
- [ ] Update `platform/STATE.md` and `SESSION_LOG.md`.
