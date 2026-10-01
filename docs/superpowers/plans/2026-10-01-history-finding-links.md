# History and Energetic Findings Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep each client's history apart from their E4L energetic findings, store which findings link to which reported conditions, and show those links to Glen on the authoring page.

**Architecture:** A new vault module `02 Skills/history_links.py` owns three new `e4l.db` tables. It builds a client's history from People, intake, approved document extractions and the tag ledger, then links each scan's findings to that history through a Glen-reviewed finding-to-condition table, using `remedy_tiers.condition_met`. deploy-chat gains one read-only API route (attribute sources) and a read-only panel on the local Biofield authoring page.

**Tech Stack:** Python 3, sqlite3, Flask (deploy-chat), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-01-history-finding-links-design.md` (deploy-chat, branch `sess/e5ac8379`). Read it before starting.

## Global Constraints

- Two repos. Vault code goes in a vault worktree: `git -C ~/AI-Training worktree add -b sess/e5ac8379-history-links ~/worktrees/vault-history-links main`. deploy-chat code goes in `~/worktrees/wt-deploy-chat-e5ac8379` on branch `sess/e5ac8379`. Never edit `~/AI-Training/02 Skills/` directly.
- Vault tests: `cd "<worktree>/02 Skills" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/<file>`. Never run the full suite: it sends real email.
- deploy-chat API tests need the app env: `doppler run -p remedy-match -c prd -- env DATA_DIR="$HOME/deploy-chat" ~/.venvs/deploy-chat311/bin/python -m pytest -q tests/<file>`. Name test files explicitly, as a bash array if more than one.
- Mutation checks: copy the file first (`cp f f.orig`), mutate, run, restore with `mv f.orig f`. Never `git checkout`, `git stash` or `git restore` to undo a mutation.
- The console key travels in the `X-Console-Key` header only, never a URL.
- Fail closed everywhere: no record, a read error, or more than one person on an address means no history and no links.
- Nothing reaches a client, and nothing feeds remedy choice. `remedy_tiers` and `e4l_synthesis.tier_check` are not modified.
- Chat-derived history is stored with `confirmed = 0` and the label "from chat, unconfirmed".
- `terrain:` People tags are stored with the label "from GoHighLevel, origin unknown".
- Never print client names or emails in any script output. Counts only.
- Glen reads HTML. Any document for him is markdown rendered with `python3 "$HOME/AI-Training/00 System/render-md.py" <file.md>`; give him the `.html` path.
- Stage named files only. Never `git add -A`.

## Review Focus

- A ledger tag `focus:glaucoma-suspect` must link to "high eye pressure" and never to "glaucoma". Test in Task 4.
- If the attributes read fails but the People read succeeds, no People-field history may be written or retired. Test in Task 6.
- A scan with no findings loaded yet must leave its existing links alone. Test in Task 7.
- A test whose E4L client is a merged duplicate must show the canonical person's links. Test in Task 9.
- A note or phrase containing `<script>` must render escaped. Test in Task 9.

---

### Task 1: Tables and seed from the remedy rows

**Files:**
- Create: `02 Skills/history_links.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Produces: `init_tables(cx) -> None`; `seed_from_remedy_rows(cx) -> int` (rows inserted).

- [ ] **Step 1: Write the failing test**

```python
"""History vs energetic findings (spec 2026-10-01, deploy-chat docs/superpowers/specs)."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import history_links as HL  # noqa: E402

TODAY = "2026-10-01"


def _map_db():
    cx = sqlite3.connect(":memory:")
    cx.executescript(
        "CREATE TABLE e4l_formulation_map(item_code TEXT, finding_pattern TEXT,"
        " formulation_id INTEGER, priority INTEGER, order_tier INTEGER,"
        " qualifying_conditions TEXT);")
    cx.executemany("INSERT INTO e4l_formulation_map VALUES (?,?,?,?,?,?)", [
        ("ED5", "ED5", 1, 1, 2, '["Leaky gut", "brain fog"]'),
        ("ED5", "ED5", 2, 2, 2, '["leaky gut"]'),            # duplicate pair
        ("ET6", "ET6", 3, 1, 2, '["spike protein exposure"]'),
        ("ED2", "ED2", 4, 1, 1, '["irregular heartbeat"]'),  # first order: not a seed
        (None, "MR", 5, 1, 2, '["acidic ph"]'),              # pattern row: no code
        ("ES1", "ES1", 6, 1, 2, "not json")])
    return cx


def test_seed_takes_second_order_pairs_once():
    cx = _map_db()
    assert HL.seed_from_remedy_rows(cx) == 3
    rows = cx.execute("SELECT item_code, condition, origin, reviewed, removed, note"
                      " FROM finding_conditions ORDER BY item_code, condition").fetchall()
    assert rows == [("ED5", "brain fog", "remedy-row", 0, 0, ""),
                    ("ED5", "leaky gut", "remedy-row", 0, 0, ""),
                    ("ET6", "spike protein exposure", "remedy-row", 0, 0, "")]
    assert HL.seed_from_remedy_rows(cx) == 0                 # idempotent


def test_reseed_never_restores_a_removed_row():
    cx = _map_db()
    HL.seed_from_remedy_rows(cx)
    cx.execute("UPDATE finding_conditions SET removed=1 WHERE condition='brain fog'")
    HL.seed_from_remedy_rows(cx)
    assert cx.execute("SELECT removed FROM finding_conditions"
                      " WHERE condition='brain fog'").fetchone() == (1,)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_history_links.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'history_links'`.

- [ ] **Step 3: Write the module with the schema and seed**

```python
"""History and energetic findings, kept apart and linked (Glen, 2026-10-01).

Spec: deploy-chat docs/superpowers/specs/2026-10-01-history-finding-links-design.md

History is what the client reports or what is recorded about them. Energetic
findings are what an E4L scan shows (e4l_scan_results, unchanged). This module
owns three tables in e4l.db:

  client_history         one row per reported condition per client, with its source
  finding_conditions     Glen-reviewed: which finding code goes with which condition
  finding_history_links  per scan: this finding links to this history row, via this
                         finding_conditions row

Everything fails CLOSED: no record, a read error, or a household address means no
history and no links. Nothing here reaches a client or feeds remedy choice.
"""
import os
import sqlite3
import urllib.error
import urllib.parse

import remedy_tiers as T

SCHEMA = """
CREATE TABLE IF NOT EXISTS client_history(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    phrase      TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'structured',   -- 'structured' | 'narrative'
    source      TEXT NOT NULL,
    source_ref  TEXT NOT NULL DEFAULT '',
    confirmed   INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT,
    last_seen   TEXT,
    retired_at  TEXT,
    UNIQUE(client_id, phrase, source, source_ref));
CREATE TABLE IF NOT EXISTS finding_conditions(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code   TEXT NOT NULL,
    condition   TEXT NOT NULL,
    origin      TEXT NOT NULL,                        -- 'remedy-row' | 'drafted' | 'glen'
    note        TEXT NOT NULL DEFAULT '',
    reviewed    INTEGER NOT NULL DEFAULT 0,
    removed     INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT,
    UNIQUE(item_code, condition));
CREATE TABLE IF NOT EXISTS finding_history_links(
    scan_id              INTEGER NOT NULL,
    item_code            TEXT NOT NULL,
    history_id           INTEGER NOT NULL,
    finding_condition_id INTEGER NOT NULL,
    linked_at            TEXT,
    UNIQUE(scan_id, item_code, history_id, finding_condition_id));
"""


def init_tables(cx):
    cx.executescript(SCHEMA)


def _condition(text):
    """Stored form of a condition: lowercase, trimmed, inner spaces collapsed."""
    return " ".join(str(text or "").lower().split())


def seed_from_remedy_rows(cx):
    """Insert each (finding code, condition) pair named by a second-order remedy row.
    INSERT OR IGNORE, so a row Glen removed or annotated is never touched."""
    init_tables(cx)
    n = 0
    rows = cx.execute(
        "SELECT item_code, qualifying_conditions FROM e4l_formulation_map"
        " WHERE order_tier=2 AND trim(coalesce(item_code,''))<>''").fetchall()
    for code, raw in rows:
        for c in T.parse_conditions(raw):
            c = _condition(c)
            if c:
                n += cx.execute(
                    "INSERT OR IGNORE INTO finding_conditions(item_code, condition, origin)"
                    " VALUES (?,?,'remedy-row')", (code.strip(), c)).rowcount
    cx.commit()
    return n
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_history_links.py`
Expected: 2 passed.

- [ ] **Step 5: Check the live seed count, read-only**

```bash
cd "$HOME/worktrees/vault-history-links/02 Skills" && PYTHONDONTWRITEBYTECODE=1 python3 - <<'EOF'
import sqlite3, history_links as HL
src = sqlite3.connect("file:" + __import__("os").path.expanduser("~/AI-Training/e4l.db") + "?mode=ro", uri=True)
mem = sqlite3.connect(":memory:"); src.backup(mem)
print("seeded", HL.seed_from_remedy_rows(mem),
      "codes", mem.execute("SELECT count(DISTINCT item_code) FROM finding_conditions").fetchone()[0])
EOF
```
Expected: `seeded 393 codes 86` (measured 2026-10-01; a different number means the map changed, so say so).

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: tables and seed from second-order remedy rows"
```

---

### Task 2: Draft conditions for the uncovered finding codes

**Files:**
- Create: `02 Skills/finding-condition-drafts.py`
- Modify: `02 Skills/history_links.py` (add `codes_to_draft`, `load_drafts`)
- Test: `02 Skills/tests/test_history_links.py`
- Create (data, by the implementer): `clinical/remedy-order-review/finding-condition-drafts.json`

**Interfaces:**
- Consumes: `init_tables`, `_condition` (Task 1).
- Produces: `codes_to_draft(cx) -> dict` with keys `codes` (list of `{item_code, name, full_name, description}`) and `vocabulary` (sorted list of condition phrases); `load_drafts(cx, drafts) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_links.py`:

```python
def _items(cx):
    cx.executescript(
        "CREATE TABLE e4l_items(item_code TEXT PRIMARY KEY, name TEXT, full_name TEXT,"
        " e4l_description TEXT);"
        "INSERT INTO e4l_items VALUES ('ED5','Circulation','ED5 Circulation','blood flow');"
        "INSERT INTO e4l_items VALUES ('ED9','Stomach','ED9 Stomach Driver','stomach');"
        "INSERT INTO e4l_items VALUES ('ET6','Spike','ET6 Spike','spike');")
    return cx


def test_codes_to_draft_lists_only_uncovered_codes_and_the_vocabulary():
    cx = _items(_map_db())
    HL.seed_from_remedy_rows(cx)
    out = HL.codes_to_draft(cx)
    assert [c["item_code"] for c in out["codes"]] == ["ED9"]
    assert out["codes"][0] == {"item_code": "ED9", "name": "Stomach",
                               "full_name": "ED9 Stomach Driver", "description": "stomach"}
    assert out["vocabulary"] == ["brain fog", "leaky gut", "spike protein exposure"]


def test_load_drafts_inserts_valid_rows_as_drafted_and_skips_the_rest():
    cx = _items(_map_db())
    HL.seed_from_remedy_rows(cx)
    n = HL.load_drafts(cx, [
        {"item_code": "ED9", "condition": " Macular  Degeneration "},
        {"item_code": "ED9", "condition": ""},                 # blank
        {"item_code": "XX1", "condition": "fatigue"},          # unknown code
        {"item_code": "ED5", "condition": "leaky gut"},        # already seeded
        "not a dict"])
    assert n == 1
    assert cx.execute("SELECT item_code, condition, origin, reviewed FROM finding_conditions"
                      " WHERE origin='drafted'").fetchall() == [
        ("ED9", "macular degeneration", "drafted", 0)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_history_links.py`
Expected: 2 FAIL with `AttributeError: module 'history_links' has no attribute 'codes_to_draft'`.

- [ ] **Step 3: Implement**

Append to `history_links.py`:

```python
def codes_to_draft(cx):
    """Finding codes with no finding_conditions row, and the condition phrases already
    in use, for drafting. Finding descriptions only: no client data."""
    init_tables(cx)
    covered = {r[0] for r in cx.execute("SELECT DISTINCT item_code FROM finding_conditions")}
    codes = [{"item_code": c, "name": n or "", "full_name": f or "", "description": d or ""}
             for c, n, f, d in cx.execute(
                 "SELECT item_code, name, full_name, e4l_description FROM e4l_items"
                 " ORDER BY item_code").fetchall()
             if c not in covered]
    vocab = sorted({r[0] for r in cx.execute("SELECT condition FROM finding_conditions")})
    return {"codes": codes, "vocabulary": vocab}


def load_drafts(cx, drafts):
    """Insert Claude's drafted rows, origin 'drafted', unreviewed. A code that is not in
    e4l_items, a blank condition, or a pair that already exists is skipped."""
    init_tables(cx)
    known = {r[0] for r in cx.execute("SELECT item_code FROM e4l_items")}
    n = 0
    for d in drafts or []:
        if not isinstance(d, dict):
            continue
        code, cond = str(d.get("item_code") or "").strip(), _condition(d.get("condition"))
        if code in known and cond:
            n += cx.execute("INSERT OR IGNORE INTO finding_conditions(item_code, condition,"
                            " origin) VALUES (?,?,'drafted')", (code, cond)).rowcount
    cx.commit()
    return n
```

Create `02 Skills/finding-condition-drafts.py`:

```python
#!/usr/bin/env python3
"""Draft conditions for finding codes the remedy rows do not cover.

  python3 "02 Skills/finding-condition-drafts.py" export     # writes the codes to draft
  python3 "02 Skills/finding-condition-drafts.py" load       # dry run: counts only
  python3 "02 Skills/finding-condition-drafts.py" load --apply

Files live in clinical/remedy-order-review/. Descriptions only, no client data.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history_links as HL  # noqa: E402

VAULT = os.path.expanduser("~/AI-Training")
E4L_DB = os.path.join(VAULT, "e4l.db")
DIR = os.path.join(VAULT, "clinical", "remedy-order-review")
EXPORT = os.path.join(DIR, "finding-codes-to-draft.json")
DRAFTS = os.path.join(DIR, "finding-condition-drafts.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "load"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.action == "export":
        src = sqlite3.connect(f"file:{E4L_DB}?mode=ro", uri=True)
        mem = sqlite3.connect(":memory:")
        src.backup(mem)
        HL.seed_from_remedy_rows(mem)          # covered = seeded, as it will be live
        out = HL.codes_to_draft(mem)
        with open(EXPORT, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"{len(out['codes'])} codes to draft -> {EXPORT}")
        return
    drafts = json.load(open(DRAFTS, encoding="utf-8"))
    if a.apply:
        cx = sqlite3.connect(E4L_DB)
    else:
        cx = sqlite3.connect(":memory:")
        sqlite3.connect(f"file:{E4L_DB}?mode=ro", uri=True).backup(cx)
    HL.seed_from_remedy_rows(cx)
    n = HL.load_drafts(cx, drafts)
    print(f"{'wrote' if a.apply else 'would write'} {n} drafted rows of {len(drafts)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_history_links.py`
Expected: 4 passed.

- [ ] **Step 5: Export and draft**

Run: `cd ~/worktrees/vault-history-links && python3 "02 Skills/finding-condition-drafts.py" export`
Expected: `176 codes to draft -> .../finding-codes-to-draft.json` (measured 2026-10-01).

Note: the script writes into the live vault folder `~/AI-Training/clinical/remedy-order-review/`, because `VAULT` is the live vault. That is intended. These two JSON files are review material, not code.

Then draft. Read the export. For each code, write zero or more conditions it plausibly corresponds to, from its name, full name and description. Prefer a phrase from `vocabulary` when one fits. Use plain condition words the way a client would report them ("macular degeneration", "acid reflux"). Never use a diagnosis the description does not support. A code with no plausible condition gets no row. Write the result to `clinical/remedy-order-review/finding-condition-drafts.json` as a list of `{"item_code": ..., "condition": ...}`.

Then: `python3 "02 Skills/finding-condition-drafts.py" load` and report the dry-run count. Do NOT run `--apply` yet: the drafts land in `e4l.db` in Task 11, after the review round.

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/finding-condition-drafts.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: export codes to draft and load drafted conditions"
```

---

### Task 3: Read-only attributes route in deploy-chat

**Files:**
- Modify: `app.py` (add a route directly after `get_person`, near line 45047)
- Test: `tests/test_people_attributes_api.py`

**Interfaces:**
- Produces: `GET /api/people/<id>/attributes` with header `X-Console-Key`, returning `{"attributes": [{"field", "value", "source"}]}`. 401 without the key in the header, 404 for an unknown id.

- [ ] **Step 1: Write the failing test**

```python
"""GET /api/people/<id>/attributes: canonical attributes WITH their source.

Isolated: monkeypatches app.LOG_DB to a temp sqlite db and app.CONSOLE_SECRET.
Run via:
  doppler run -p remedy-match -c prd -- env DATA_DIR="$HOME/deploy-chat" \
    ~/.venvs/deploy-chat311/bin/python -m pytest tests/test_people_attributes_api.py
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("requires app env (use doppler run)", allow_module_level=True)

import app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = str(tmp_path / "chat_log.db")
    with sqlite3.connect(p) as cx:
        cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " email TEXT UNIQUE, tags TEXT DEFAULT '[]')")
        cx.execute("INSERT INTO people(id, email) VALUES (7, 'A@x.com')")
        cx.execute("INSERT INTO people(id, email) VALUES (8, 'b@x.com')")
        from dashboard import canonical_tags as ct
        ct.init_tables(cx)
        cx.executemany(
            "INSERT INTO person_attributes(email, field, value, value_norm, source, added_at)"
            " VALUES (?,?,?,?,?,?)", [
                ("a@x.com", "conditions", "Glaucoma", "glaucoma", "document:41", "t"),
                ("a@x.com", "conditions", "Acid reflux", "acid reflux", "console", "t"),
                ("b@x.com", "conditions", "Gout", "gout", "document:9", "t")])
        cx.commit()
    monkeypatch.setattr(app, "LOG_DB", p)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    return app.app.test_client()


def test_returns_this_persons_attributes_with_source(client):
    r = client.get("/api/people/7/attributes", headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    assert r.get_json() == {"attributes": [
        {"field": "conditions", "value": "Acid reflux", "source": "console"},
        {"field": "conditions", "value": "Glaucoma", "source": "document:41"}]}


def test_key_only_in_the_header(client):
    assert client.get("/api/people/7/attributes").status_code == 401
    assert client.get("/api/people/7/attributes?key=testkey").status_code == 401
    assert client.get("/api/people/7/attributes",
                      headers={"X-Console-Key": "wrong"}).status_code == 401


def test_unknown_person_is_404(client):
    assert client.get("/api/people/99/attributes",
                      headers={"X-Console-Key": "testkey"}).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/worktrees/wt-deploy-chat-e5ac8379 && doppler run -p remedy-match -c prd -- env DATA_DIR="$HOME/deploy-chat" ~/.venvs/deploy-chat311/bin/python -m pytest -q tests/test_people_attributes_api.py`
Expected: FAIL, 404 where 200 was expected.

- [ ] **Step 3: Implement the route**

Insert after the `get_person` function in `app.py`:

```python
@app.route("/api/people/<int:person_id>/attributes", methods=["GET"])
def get_person_attributes(person_id):
    """The client's canonical clinical attributes WITH their source, for the vault's
    history builder (history vs energetic findings, spec 2026-10-01). /api/people/<id>
    blends these into the People fields and drops the source; the builder needs it to
    label an uploaded report as such. Console key in the header only: this route never
    reads ?key=, which Render's access log would keep."""
    key = request.headers.get("X-Console-Key", "")
    if not CONSOLE_SECRET or (key != CONSOLE_SECRET and not _owner_token_ok(key)):
        return jsonify({"error": "Unauthorized"}), 401
    from dashboard import canonical_tags as _ct
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        row = cx.execute("SELECT email FROM people WHERE id=?", (person_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        _ct.init_tables(cx)
        rows = cx.execute(
            "SELECT field, value, source FROM person_attributes WHERE email=?"
            " ORDER BY field, value",
            ((row["email"] or "").strip().lower(),)).fetchall()
    return jsonify({"attributes": [{"field": r["field"], "value": r["value"],
                                    "source": r["source"] or ""} for r in rows]})
```

- [ ] **Step 4: Run the test to verify it passes**

Run the Step 2 command. Expected: 3 passed.

- [ ] **Step 5: Mutate the header guard**

Copy `app.py`, change `key = request.headers.get("X-Console-Key", "")` to `key = request.headers.get("X-Console-Key", "") or request.args.get("key", "")`, run, expect `test_key_only_in_the_header` to fail, restore with `mv`.

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/wt-deploy-chat-e5ac8379 && git add app.py tests/test_people_attributes_api.py && git commit -m "People: read-only attributes route with source, for the history builder"
```

---

### Task 4: History rows from each part of the client's file

**Files:**
- Modify: `02 Skills/history_links.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Produces, each returning a list of dicts `{phrase, source, source_ref, confirmed, kind}`:
  `people_rows(person, attributes)`, `tag_rows(person)`, `intake_rows(intake)`, `ledger_history(ledger)` where `ledger` is a list of `(source, tag, status)` tuples.
- Produces constants `TAG_SOURCES`, `LEDGER_SOURCES`, `PEOPLE_FIELDS`.

- [ ] **Step 1: Write the failing tests**

```python
def _p(rows):
    return sorted((r["phrase"], r["source"], r["source_ref"], r["confirmed"], r["kind"])
                  for r in rows)


def test_people_rows_split_uploaded_reports_from_the_record():
    person = {"conditions": '["Glaucoma", "Acid reflux"]', "terrain_concerns": ["Stress"],
              "body_systems": ""}
    attrs = [{"field": "conditions", "value": "Glaucoma", "source": "document:41"},
             {"field": "conditions", "value": "Acid reflux", "source": "console"},
             {"field": "goals", "value": "Sleep", "source": "document:41"}]
    assert _p(HL.people_rows(person, attrs)) == [
        ("acid reflux", "people:conditions", "conditions", 1, "structured"),
        ("glaucoma", "document", "41", 1, "structured"),
        ("stress", "people:conditions", "terrain_concerns", 1, "structured")]


def test_tag_rows_keep_history_prefixes_only():
    person = {"tags": ["pb:hypertension", "system:digestive", "regulation:low",
                       "concern:sleep", "terrain:Aging/Rejuvenation", "clin:focus-afib",
                       "state:e4l-has-account", "type:client", "untagged"]}
    assert _p(HL.tag_rows(person)) == [
        ("aging rejuvenation", "ghl:terrain", "terrain:aging/rejuvenation", 1, "structured"),
        ("digestive", "scoreapp", "system:digestive", 1, "structured"),
        ("hypertension", "pb", "pb:hypertension", 1, "structured"),
        ("low", "scoreapp", "regulation:low", 1, "structured"),
        ("sleep", "scoreapp", "concern:sleep", 1, "structured")]


def test_intake_rows_keep_narrative_whole_and_leave_out_allergies():
    intake = {"health_concerns": [{"concern": "Bloating"}],
              "diagnoses": [{"diagnosis": "Hashimoto's"}],
              "allergies": [{"allergen": "Mold"}],
              "vaccinations": "Covid vaccine 2021",
              "supplements": [{"brand": "X", "name": "Candida Cleanse", "reason": ""}],
              "medications": [{"medication": "Timolol", "reason": "eye pressure"}]}
    assert _p(HL.intake_rows(intake)) == [
        ("bloating", "intake", "health_concerns", 1, "structured"),
        ("candida cleanse", "intake:narrative", "supplements", 1, "narrative"),
        ("covid vaccine 2021", "intake:narrative", "vaccinations", 1, "narrative"),
        ("eye pressure", "intake:narrative", "medications", 1, "narrative"),
        ("hashimoto s", "intake", "diagnoses", 1, "structured"),
        ("timolol", "intake:narrative", "medications", 1, "narrative"),
        ("x", "intake:narrative", "supplements", 1, "narrative")]


def test_ledger_history_maps_sources_and_marks_chat_unconfirmed():
    ledger = [("intake:glen-directed", "focus:glaucoma-suspect", "active"),
              ("pb-intake", "focus:nocturia", "active"),
              ("pb-intake", "focus:sleep", "suggested"),          # not active
              ("chatbot:query_log", "focus:tinnitus", "suggested"),
              ("email", "focus:gout", "retired"),
              ("e4l:scan_results", "system:skeletal", "active"),   # a finding
              ("clinician", "condition:oral-neoplasia", "active")]
    assert _p(HL.ledger_history(ledger)) == [
        ("frequent night urination", "ledger:intake", "focus:nocturia", 1, "structured"),
        ("high eye pressure", "glen", "focus:glaucoma-suspect", 1, "structured"),
        ("oral neoplasia", "glen", "condition:oral-neoplasia", 1, "structured"),
        ("tinnitus", "chat", "focus:tinnitus", 0, "structured")]


def test_a_glaucoma_suspect_tag_never_meets_glaucoma():
    import remedy_tiers as T
    (row,) = HL.ledger_history([("intake:glen-directed", "focus:glaucoma-suspect", "active")])
    facts = {"structured": [row["phrase"]], "narrative": "", "age": None, "sex": ""}
    assert T.condition_met("high eye pressure", facts)
    assert not T.condition_met("glaucoma", facts)
```

- [ ] **Step 2: Run to verify they fail**

Expected: the new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `history_links.py`:

```python
PEOPLE_FIELDS = ("conditions", "terrain_concerns", "body_systems")

# People tag prefix -> history source. Anything else on People tags is not history:
# clin: copies the ledger, state: is a status flag, type:/consent: describe the account.
TAG_SOURCES = {"pb": "pb", "system": "scoreapp", "regulation": "scoreapp",
               "concern": "scoreapp", "terrain": "ghl:terrain"}

# Ledger source -> history source. e4l:* sources are findings and never appear here.
LEDGER_SOURCES = {
    "intake:glen-directed": "glen", "clinician": "glen", "note:internal": "glen",
    "pb-intake": "ledger:intake", "pb-intake-backfill": "ledger:intake",
    "pb-intake:self-report": "ledger:intake",
    "chatbot:query_log": "chat", "journal:portal": "chat", "email": "chat"}


def _row(phrase, source, ref="", confirmed=1, kind="structured"):
    return {"phrase": phrase, "source": source, "source_ref": str(ref),
            "confirmed": confirmed, "kind": kind}


def people_rows(person, attributes):
    """People conditions, terrain concerns and body systems. A value that came from an
    approved document extraction is labelled 'document' with the document id; the
    People GET blends those in without their source."""
    docs = {}
    for a in attributes or []:
        src = str(a.get("source") or "")
        if a.get("field") in PEOPLE_FIELDS and src.startswith("document:"):
            p = T.normalize(a.get("value"))
            if p:
                docs.setdefault(p, src.split(":", 1)[1])
    rows = [_row(p, "document", ref) for p, ref in docs.items()]
    for f in PEOPLE_FIELDS:
        for v in T._as_list((person or {}).get(f)):
            p = T.normalize(v)
            if p and p not in docs:
                rows.append(_row(p, "people:conditions", f))
    return rows


def tag_rows(person):
    rows = []
    for t in T._as_list((person or {}).get("tags")):
        s = str(t).strip()
        if ":" not in s:
            continue
        src = TAG_SOURCES.get(s.split(":", 1)[0].strip().lower())
        p = T._tag_text(s)
        if src and p:
            rows.append(_row(p, src, s.lower()))
    return rows


def intake_rows(intake):
    """Health concerns and diagnoses as structured history. Vaccinations, supplements
    and medications stay whole as narrative: remedy_tiers searches those only for the
    conditions Glen approved for narrative matching. Allergies are left out on purpose:
    a mold allergy is not mold exposure."""
    a = intake or {}
    rows = []
    for key, col in (("health_concerns", "concern"), ("diagnoses", "diagnosis")):
        for v in T._rows(a, key, col):
            p = T.normalize(v)
            if p:
                rows.append(_row(p, "intake", key))
    narrative = [("vaccinations", str(a.get("vaccinations") or ""))]
    narrative += [("supplements", x) for x in T._rows(a, "supplements", "brand", "name", "reason")]
    narrative += [("medications", x) for x in T._rows(a, "medications", "medication", "reason")]
    narrative += [("otc_drugs", x) for x in T._rows(a, "otc_drugs", "medication", "reason")]
    for key, text in narrative:
        p = T.normalize(text)
        if p:
            rows.append(_row(p, "intake:narrative", key, kind="narrative"))
    return rows


def ledger_history(ledger):
    """Ledger tags from intake, from Glen, and from chat. Active only, except chat,
    where a suggested tag is kept too, unconfirmed. A tag Glen approved in
    remedy_tiers.TAG_CONDITIONS stores its approved phrases, so 'glaucoma suspect'
    is stored as 'high eye pressure' and never meets 'glaucoma'."""
    rows = []
    for source, tag, status in ledger or []:
        src = LEDGER_SOURCES.get(source)
        if not src:
            continue
        chat = src == "chat"
        if status != "active" and not (chat and status == "suggested"):
            continue
        t = str(tag or "").strip().lower()
        for p in T.TAG_CONDITIONS.get(t) or [T._tag_text(t)]:
            p = T.normalize(p)
            if p:
                rows.append(_row(p, src, t, confirmed=0 if chat else 1))
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: history rows from People, tags, intake and ledger"
```

---

### Task 5: Write history additively, retire what disappeared

**Files:**
- Modify: `02 Skills/history_links.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Consumes: the row dicts from Task 4.
- Produces: `write_history(cx, client_id, rows, sources_read, today) -> None`. Only rows whose source is in `sources_read` are written, and only those sources can be retired. The caller commits.

- [ ] **Step 1: Write the failing tests**

```python
def _hist(cx):
    return sorted(cx.execute("SELECT phrase, source, first_seen, last_seen, retired_at"
                             " FROM client_history").fetchall())


def test_write_history_inserts_updates_and_retires():
    cx = sqlite3.connect(":memory:")
    HL.init_tables(cx)
    a = HL._row("bloating", "intake", "health_concerns")
    b = HL._row("gout", "pb", "pb:gout")
    HL.write_history(cx, 1, [a, b], {"intake", "pb"}, "2026-09-01")
    HL.write_history(cx, 1, [a], {"intake", "pb"}, TODAY)
    assert _hist(cx) == [("bloating", "intake", "2026-09-01", TODAY, None),
                         ("gout", "pb", "2026-09-01", "2026-09-01", TODAY)]
    HL.write_history(cx, 1, [a, b], {"intake", "pb"}, "2026-10-02")   # it came back
    assert _hist(cx)[1] == ("gout", "pb", "2026-09-01", "2026-10-02", None)


def test_a_source_that_was_not_read_is_neither_written_nor_retired():
    cx = sqlite3.connect(":memory:")
    HL.init_tables(cx)
    HL.write_history(cx, 1, [HL._row("glaucoma", "document", "41")], {"document"}, "2026-09-01")
    HL.write_history(cx, 1, [HL._row("gout", "document", "9")], {"pb"}, TODAY)
    assert _hist(cx) == [("glaucoma", "document", "2026-09-01", "2026-09-01", None)]


def test_write_history_keeps_clients_apart():
    cx = sqlite3.connect(":memory:")
    HL.init_tables(cx)
    HL.write_history(cx, 1, [HL._row("gout", "pb", "pb:gout")], {"pb"}, TODAY)
    HL.write_history(cx, 2, [], {"pb"}, TODAY)
    assert _hist(cx) == [("gout", "pb", TODAY, TODAY, None)]
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL with `AttributeError: ... 'write_history'`.

- [ ] **Step 3: Implement**

```python
def write_history(cx, client_id, rows, sources_read, today):
    """Upsert this client's history rows; retire rows of a source that was read but
    no longer names them. A source that failed to read is left exactly as it was."""
    init_tables(cx)
    seen = set()
    for r in rows:
        if r["source"] not in sources_read:
            continue
        key = (r["phrase"], r["source"], r["source_ref"])
        if key in seen:
            continue
        seen.add(key)
        cx.execute(
            "INSERT INTO client_history(client_id, phrase, kind, source, source_ref,"
            " confirmed, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(client_id, phrase, source, source_ref) DO UPDATE SET"
            " last_seen=excluded.last_seen, retired_at=NULL,"
            " confirmed=excluded.confirmed, kind=excluded.kind",
            (client_id, r["phrase"], r["kind"], r["source"], r["source_ref"],
             r["confirmed"], today, today))
    for hid, phrase, source, ref in cx.execute(
            "SELECT id, phrase, source, source_ref FROM client_history"
            " WHERE client_id=? AND retired_at IS NULL", (client_id,)).fetchall():
        if source in sources_read and (phrase, source, ref) not in seen:
            cx.execute("UPDATE client_history SET retired_at=? WHERE id=?", (today, hid))
```

- [ ] **Step 4: Run the tests to verify they pass**

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: write history additively and retire per source read"
```

---

### Task 6: Fetch the client's file and build their history

**Files:**
- Modify: `02 Skills/history_links.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Consumes: `e4l_reveal_lib.PEOPLE_URL`, `PERSON_URL`, `INTAKE_URL`, `_get_json(url, key, opener)`; `e4l_synthesis.identities_on_email(cx, email)`, `merge_group(cx, client_id)`; Task 4 and 5 functions.
- Produces: `fetch_client_inputs(email, opener=None) -> dict | None` with keys `person`, `intake`, `attributes`, `read` (a set drawn from `{"person", "intake", "attributes"}`); `read_ledger(cx, client_ids) -> list[(source, tag, status)]`; `build_client_history(cx, email, today, opener=None) -> bool` (True only when the People read succeeded and history was written).

- [ ] **Step 1: Write the failing tests**

```python
import json as _json  # noqa: E402

import e4l_synthesis as E  # noqa: E402


def _opener(person, intake=None, attrs=None, fail=()):
    import io
    import urllib.error

    def op(req, timeout=None):
        u = req.full_url
        assert "key=" not in u and req.get_header("X-console-key") == "k"
        if "/api/people?" in u:
            return io.BytesIO(_json.dumps({"people": [{"email": "a@x.com", "id": 7}]}).encode())
        if u.endswith("/attributes"):
            if "attributes" in fail:
                raise urllib.error.HTTPError(u, 500, "x", {}, None)
            return io.BytesIO(_json.dumps({"attributes": attrs or []}).encode())
        if "/api/people/" in u:
            return io.BytesIO(_json.dumps(person).encode())
        if "intake" in fail:
            raise OSError("down")
        if intake is None:
            raise urllib.error.HTTPError(u, 404, "nf", {}, None)
        return io.BytesIO(_json.dumps({"answers": intake}).encode())
    return op


def _clients(*rows):
    cx = sqlite3.connect(":memory:")
    cx.executescript("CREATE TABLE e4l_clients(client_id INTEGER PRIMARY KEY, email TEXT,"
                     " archived_at TEXT); CREATE TABLE client_clinical_tags(client_id"
                     " INTEGER, axis TEXT, tag TEXT, status TEXT, source TEXT);")
    E.init_identity_merges(cx)
    cx.executemany("INSERT INTO e4l_clients VALUES (?,?,NULL)", rows)
    HL.init_tables(cx)
    return cx


def test_build_writes_every_source(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    cx = _clients((1, "a@x.com"))
    cx.execute("INSERT INTO client_clinical_tags VALUES"
               " (1,'status','focus:afib','active','intake:glen-directed')")
    cx.execute("INSERT INTO client_clinical_tags VALUES"
               " (1,'status','system:skeletal','active','e4l:scan_results')")
    op = _opener({"id": 7, "conditions": ["Glaucoma"], "tags": ["pb:gout"]},
                 {"health_concerns": [{"concern": "Bloating"}]},
                 [{"field": "conditions", "value": "Glaucoma", "source": "document:41"}])
    assert HL.build_client_history(cx, "a@x.com", TODAY, op) is True
    assert sorted(cx.execute("SELECT phrase, source FROM client_history"
                             " WHERE client_id=1").fetchall()) == [
        ("bloating", "intake"), ("glaucoma", "document"), ("gout", "pb"),
        ("irregular heartbeat", "glen")]                  # approved tag phrase


def test_attributes_failing_leaves_people_fields_untouched(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    cx = _clients((1, "a@x.com"))
    HL.write_history(cx, 1, [HL._row("acid reflux", "people:conditions", "conditions")],
                     {"people:conditions"}, "2026-09-01")
    op = _opener({"id": 7, "conditions": ["Glaucoma"]}, {}, fail=("attributes",))
    assert HL.build_client_history(cx, "a@x.com", TODAY, op) is True
    assert cx.execute("SELECT phrase, retired_at FROM client_history"
                      " WHERE source='people:conditions'").fetchall() == [("acid reflux", None)]


def test_intake_failing_leaves_intake_rows_untouched(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    cx = _clients((1, "a@x.com"))
    HL.write_history(cx, 1, [HL._row("bloating", "intake", "health_concerns")],
                     {"intake"}, "2026-09-01")
    assert HL.build_client_history(cx, "a@x.com", TODAY,
                                   _opener({"id": 7}, fail=("intake",))) is True
    assert cx.execute("SELECT retired_at FROM client_history").fetchall() == [(None,)]


def test_build_fails_closed(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    op = _opener({"id": 7, "tags": ["pb:gout"]})
    two = _clients((1, "a@x.com"), (2, "a@x.com"))                  # household
    assert HL.build_client_history(two, "a@x.com", TODAY, op) is False
    none = _clients((1, "b@x.com"))
    assert HL.build_client_history(none, "a@x.com", TODAY, op) is False
    assert HL.build_client_history(_clients((1, "")), "", TODAY, op) is False

    def boom(req, timeout=None):
        raise OSError("down")
    one = _clients((1, "a@x.com"))
    assert HL.build_client_history(one, "a@x.com", TODAY, boom) is False
    monkeypatch.delenv("CONSOLE_SECRET")
    assert HL.build_client_history(one, "a@x.com", TODAY, op) is False
    for cx in (two, none, one):
        assert cx.execute("SELECT count(*) FROM client_history").fetchone() == (0,)
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL with `AttributeError: ... 'build_client_history'`.

- [ ] **Step 3: Implement**

```python
def fetch_client_inputs(email, opener=None):
    """The client's People record, intake answers and attribute sources. None when
    the key is missing, the lookup fails, or the address does not hold exactly one
    People record. Intake and attributes are read separately: a failure there is
    recorded by leaving it out of `read`, never by guessing."""
    import urllib.request
    import e4l_reveal_lib as R
    opener = opener or urllib.request.urlopen
    try:
        key = os.environ["CONSOLE_SECRET"]
        q = urllib.parse.quote(email)
        ppl = R._get_json(R.PEOPLE_URL + q, key, opener).get("people") or []
        same = [p for p in ppl if (p.get("email") or "").strip().lower() == email.lower()]
        if len(same) != 1 or not same[0].get("id"):
            return None
        pid = int(same[0]["id"])
        person = R._get_json(R.PERSON_URL + str(pid), key, opener)
    except Exception:
        return None
    out = {"person": person, "intake": {}, "attributes": [], "read": {"person"}}
    try:
        out["intake"] = (R._get_json(R.INTAKE_URL + q, key, opener) or {}).get("answers") or {}
        out["read"].add("intake")
    except urllib.error.HTTPError as ex:
        if ex.code == 404:                      # no intake on file is a clean read
            out["read"].add("intake")
    except Exception:
        pass
    try:
        out["attributes"] = (R._get_json(R.PERSON_URL + str(pid) + "/attributes", key,
                                         opener) or {}).get("attributes") or []
        out["read"].add("attributes")
    except Exception:
        pass
    return out


def read_ledger(cx, client_ids):
    ids = sorted(client_ids)
    marks = ",".join("?" * len(ids))
    return cx.execute(
        f"SELECT source, tag, status FROM client_clinical_tags WHERE client_id IN ({marks})"
        " AND axis='status'", ids).fetchall()


def build_client_history(cx, email, today, opener=None):
    """Read the client's whole file and write their history. True only when the
    People read succeeded and history was written. A household address, no client,
    or any failure before the write means False and nothing written."""
    import e4l_synthesis as E
    email = (email or "").strip()
    if not email:
        return False
    try:
        if E.identities_on_email(cx, email) != 1:
            return False
        cid = cx.execute("SELECT client_id FROM e4l_clients"
                         " WHERE lower(trim(email))=lower(trim(?))", (email,)).fetchone()[0]
        group = E.merge_group(cx, cid)
    except Exception:
        return False
    inputs = fetch_client_inputs(email, opener)
    if inputs is None:
        return False
    person, read = inputs["person"], set()
    rows = tag_rows(person)
    read |= {"pb", "scoreapp", "ghl:terrain"}
    if "attributes" in inputs["read"]:
        rows += people_rows(person, inputs["attributes"])
        read |= {"people:conditions", "document"}
    if "intake" in inputs["read"]:
        rows += intake_rows(inputs["intake"])
        read |= {"intake", "intake:narrative"}
    try:
        rows += ledger_history(read_ledger(cx, group))
        read |= {"glen", "ledger:intake", "chat"}
    except Exception:
        pass
    try:
        write_history(cx, cid, rows, read, today)
        cx.commit()
    except Exception:
        cx.rollback()
        return False
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: fetch the client's file and build history, fail closed"
```

---

### Task 7: Link a scan's findings to the history

**Files:**
- Modify: `02 Skills/history_links.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Consumes: `remedy_tiers.condition_met(condition, facts)`; `e4l_synthesis.merge_group`.
- Produces: `link_scan(cx, scan_id, history_ok, today) -> int | None` (links written, or None when nothing was touched); `process_scan(cx, email, scan_id, today, opener=None) -> int | None`, which never raises.

- [ ] **Step 1: Write the failing tests**

```python
def _scan_db():
    cx = _clients((1, "a@x.com"), (2, "a2@x.com"))
    cx.executescript(
        "CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER, scan_date TEXT);"
        "CREATE TABLE e4l_scan_results(scan_id INTEGER, item_code TEXT, priority_rank INTEGER);"
        "INSERT INTO e4l_scans VALUES (10, 1, '2026-09-01');"
        "INSERT INTO e4l_scans VALUES (11, 2, '2026-09-02');"
        "INSERT INTO e4l_scans VALUES (12, 1, '2026-09-03');"
        "INSERT INTO e4l_scan_results VALUES (10,'ED5',1),(10,'EI3',2),(10,'ET6',3);"
        "INSERT INTO e4l_scan_results VALUES (11,'ED5',1);")
    cx.executemany("INSERT INTO finding_conditions(item_code, condition, origin) VALUES (?,?,?)", [
        ("ED5", "leaky gut", "remedy-row"), ("EI3", "leaky gut", "drafted"),
        ("ET6", "covid vaccination", "remedy-row"), ("ED5", "glaucoma", "drafted")])
    HL.write_history(cx, 1, [
        HL._row("intestinal permeability", "intake", "health_concerns"),
        HL._row("no glaucoma", "intake", "diagnoses"),
        HL._row("covid vaccine 2021", "intake:narrative", "vaccinations", kind="narrative")],
        {"intake", "intake:narrative"}, TODAY)
    return cx


def _links(cx, scan_id):
    return sorted(cx.execute(
        "SELECT l.item_code, h.phrase, f.condition FROM finding_history_links l"
        " JOIN client_history h ON h.id=l.history_id"
        " JOIN finding_conditions f ON f.id=l.finding_condition_id WHERE l.scan_id=?",
        (scan_id,)).fetchall())


def test_several_findings_link_to_one_condition_and_negation_holds():
    cx = _scan_db()
    assert HL.link_scan(cx, 10, True, TODAY) == 3
    assert _links(cx, 10) == [
        ("ED5", "intestinal permeability", "leaky gut"),
        ("EI3", "intestinal permeability", "leaky gut"),
        ("ET6", "covid vaccine 2021", "covid vaccination")]


def test_a_removed_row_makes_no_link():
    cx = _scan_db()
    cx.execute("UPDATE finding_conditions SET removed=1 WHERE item_code='EI3'")
    HL.link_scan(cx, 10, True, TODAY)
    assert [x[0] for x in _links(cx, 10)] == ["ED5", "ET6"]


def test_failed_history_or_no_findings_keeps_old_links():
    cx = _scan_db()
    HL.link_scan(cx, 10, True, TODAY)
    assert HL.link_scan(cx, 10, False, TODAY) is None
    assert len(_links(cx, 10)) == 3
    cx.execute("INSERT INTO finding_history_links VALUES (12,'ED5',1,1,'x')")
    assert HL.link_scan(cx, 12, True, TODAY) is None          # no findings loaded
    assert len(_links(cx, 12)) == 1
    assert HL.link_scan(cx, 99, True, TODAY) is None          # no such scan


def test_a_successful_empty_result_replaces_old_links():
    cx = _scan_db()
    HL.link_scan(cx, 10, True, TODAY)
    HL.write_history(cx, 1, [], {"intake", "intake:narrative"}, TODAY)   # all retired
    assert HL.link_scan(cx, 10, True, TODAY) == 0
    assert _links(cx, 10) == []


def test_links_use_the_merge_group_and_never_another_person():
    cx = _scan_db()
    assert HL.link_scan(cx, 11, True, TODAY) == 0              # client 2 has no history
    cx.execute("INSERT INTO e4l_identity_merges(dup_client_id, canonical_client_id)"
               " VALUES (2, 1)")
    assert HL.link_scan(cx, 11, True, TODAY) == 1


def test_process_scan_never_raises(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    cx = _scan_db()

    def boom(req, timeout=None):
        raise OSError("down")
    assert HL.process_scan(cx, "a@x.com", 10, TODAY, boom) is None
    assert HL.process_scan(sqlite3.connect(":memory:"), "a@x.com", 10, TODAY, boom) is None
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL with `AttributeError: ... 'link_scan'`.

- [ ] **Step 3: Implement**

```python
def _facts(phrase, kind):
    if kind == "narrative":
        return {"structured": [], "narrative": phrase, "age": None, "sex": ""}
    return {"structured": [phrase], "narrative": "", "age": None, "sex": ""}


def link_scan(cx, scan_id, history_ok, today):
    """Replace this scan's links in one step. Touches nothing (returns None) when the
    history read failed, the scan is unknown, or its findings are not loaded yet, so
    a failed run never erases links a good run made."""
    import e4l_synthesis as E
    if not history_ok:
        return None
    init_tables(cx)
    row = cx.execute("SELECT client_id FROM e4l_scans WHERE scan_id=?", (scan_id,)).fetchone()
    if not row:
        return None
    codes = [r[0] for r in cx.execute(
        "SELECT DISTINCT item_code FROM e4l_scan_results WHERE scan_id=?", (scan_id,))]
    if not codes:
        return None
    group = sorted(E.merge_group(cx, row[0]))
    hist = cx.execute(
        f"SELECT id, phrase, kind FROM client_history WHERE client_id IN"
        f" ({','.join('?' * len(group))}) AND retired_at IS NULL", group).fetchall()
    fcs = cx.execute(
        f"SELECT id, item_code, condition FROM finding_conditions WHERE removed=0"
        f" AND item_code IN ({','.join('?' * len(codes))})", codes).fetchall()
    links = [(scan_id, code, hid, fid, today)
             for fid, code, cond in fcs
             for hid, phrase, kind in hist
             if T.condition_met(cond, _facts(phrase, kind))]
    cx.execute("SAVEPOINT link_scan")
    try:
        cx.execute("DELETE FROM finding_history_links WHERE scan_id=?", (scan_id,))
        cx.executemany("INSERT OR IGNORE INTO finding_history_links VALUES (?,?,?,?,?)", links)
        cx.execute("RELEASE link_scan")
    except Exception:
        cx.execute("ROLLBACK TO link_scan")
        cx.execute("RELEASE link_scan")
        raise
    cx.commit()
    return len(links)


def process_scan(cx, email, scan_id, today, opener=None):
    """Build the client's history, then link this scan. Never raises into the scan
    script that calls it: a failure is printed and nothing is lost."""
    try:
        ok = build_client_history(cx, email, today, opener)
        return link_scan(cx, scan_id, ok, today)
    except Exception as ex:  # noqa: BLE001
        print(f"[history-links] scan {scan_id} not linked ({type(ex).__name__})", flush=True)
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: link a scan's findings to history, replace in one step"
```

---

### Task 8: Run on every new scan, and backfill past scans

**Files:**
- Modify: `02 Skills/e4l-reveal-push.py`, `02 Skills/e4l-analysis-fulfill.py`, `02 Skills/scan-pull-fulfill.py`, `02 Skills/e4l-portal-import.py`
- Create: `02 Skills/history-links-backfill.py`
- Test: `02 Skills/tests/test_history_links.py`

**Interfaces:**
- Consumes: `process_scan`, `build_client_history`, `link_scan`.
- Produces: `history-links-backfill.py` with `run(cx, today, opener=None) -> dict` of counts.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_scan_script_links_its_scan():
    import re
    here = os.path.join(os.path.dirname(__file__), "..")
    want = {"e4l-reveal-push.py": "HL.process_scan(cx, email, scan[\"scan_id\"], _today)",
            "e4l-analysis-fulfill.py": "HL.process_scan(cx, email, scan[\"scan_id\"], today)",
            "scan-pull-fulfill.py": "HL.process_scan(cx, email, scan[\"scan_id\"], today)",
            "e4l-portal-import.py": "HL.process_scan(cx, a.email, scan[\"scan_id\"], _today)"}
    for f, call in want.items():
        src = open(os.path.join(here, f), encoding="utf-8").read()
        assert "import history_links as HL" in src, f
        assert src.count(call) == 1, f
        # after the draft is built from the same connection, never before the scan resolves
        assert src.index(call) > src.index("fetch_client_facts("), f


def _backfill():
    import importlib.util
    p = os.path.join(os.path.dirname(__file__), "..", "history-links-backfill.py")
    spec = importlib.util.spec_from_file_location("history_links_backfill", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_backfill_fetches_each_client_once_and_counts(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    cx = _scan_db()
    calls = []
    real = HL.fetch_client_inputs

    def counting(email, opener=None):
        calls.append(email)
        return real(email, opener)
    monkeypatch.setattr(HL, "fetch_client_inputs", counting)
    op = _opener({"id": 7, "tags": ["pb:intestinal-permeability"]}, {})
    out = _backfill().run(cx, TODAY, op)
    assert calls.count("a@x.com") == 1
    assert out["scans"] == 3 and out["clients_with_history"] >= 1
    assert out["links"] == len(_links(cx, 10)) + len(_links(cx, 11)) + len(_links(cx, 12))
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`assert "import history_links as HL" in src`, and no backfill file).

- [ ] **Step 3: Wire the four scripts**

In each, add `import history_links as HL  # noqa: E402` on the line after the existing `import e4l_synthesis as E` line (or after `from e4l_reveal_lib import ...` where that is the last local import). Then insert one line, at the indentation of the statement that follows:

- `e4l-reveal-push.py`: immediately before `payload = build_payload(content, email, scan["scan_date"], ...)`:
  `HL.process_scan(cx, email, scan["scan_id"], _today)`
- `e4l-analysis-fulfill.py`: immediately before `return rp.build_payload(content, email, scan["scan_date"], ...)`:
  `HL.process_scan(cx, email, scan["scan_id"], today)`
- `scan-pull-fulfill.py`: immediately before `payload = rp.build_payload(content, email, scan["scan_date"], ...)`:
  `HL.process_scan(cx, email, scan["scan_id"], today)`
- `e4l-portal-import.py`: immediately before `name = (cx.execute("SELECT name FROM e4l_clients ...`:
  `HL.process_scan(cx, a.email, scan["scan_id"], _today)`

Each of these `cx` values is opened writable (`sqlite3.connect(E4L_DB)`); confirm that by reading the lines above the call. If any is opened `?mode=ro`, stop and report it.

- [ ] **Step 4: Write the backfill script**

```python
#!/usr/bin/env python3
"""Build history and links for every past scan (spec 2026-10-01).

Dry run by default: runs on an in-memory copy of e4l.db and prints counts only.
--apply writes e4l.db. Prints no names or addresses.

  doppler run --project remedy-match --config prd -- python3 "02 Skills/history-links-backfill.py"
  doppler run --project remedy-match --config prd -- python3 "02 Skills/history-links-backfill.py" --apply
"""
import argparse
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history_links as HL  # noqa: E402

E4L_DB = os.path.expanduser("~/AI-Training/e4l.db")


def run(cx, today, opener=None):
    HL.init_tables(cx)
    HL.seed_from_remedy_rows(cx)
    scans = cx.execute(
        "SELECT s.scan_id, c.email FROM e4l_scans s JOIN e4l_clients c"
        " ON c.client_id=s.client_id WHERE trim(coalesce(c.email,''))<>''"
        " ORDER BY s.scan_date").fetchall()
    built, out = {}, {"scans": 0, "linked_scans": 0, "links": 0, "untouched_scans": 0}
    for scan_id, email in scans:
        out["scans"] += 1
        key = email.strip().lower()
        if key not in built:
            try:
                built[key] = HL.build_client_history(cx, email, today, opener)
            except Exception:
                built[key] = False
        try:
            n = HL.link_scan(cx, scan_id, built[key], today)
        except Exception:
            n = None
        if n is None:
            out["untouched_scans"] += 1
        else:
            out["linked_scans"] += 1
            out["links"] += n
    out["clients_with_history"] = sum(1 for v in built.values() if v)
    out["clients_without_history"] = sum(1 for v in built.values() if not v)
    out["history_rows_by_source"] = dict(cx.execute(
        "SELECT source, count(*) FROM client_history WHERE retired_at IS NULL"
        " GROUP BY source ORDER BY source").fetchall())
    out["links_from_unreviewed_rows"] = cx.execute(
        "SELECT count(*) FROM finding_history_links l JOIN finding_conditions f"
        " ON f.id=l.finding_condition_id WHERE f.reviewed=0").fetchone()[0]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.apply:
        cx = sqlite3.connect(E4L_DB)
    else:
        cx = sqlite3.connect(":memory:")
        sqlite3.connect(f"file:{E4L_DB}?mode=ro", uri=True).backup(cx)
    out = run(cx, datetime.date.today().isoformat())
    print(("APPLIED" if a.apply else "DRY RUN, nothing written"), flush=True)
    for k, v in out.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_history_links.py tests/test_remedy_tiers.py`
Expected: all pass (24 new, 44 tier).

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/e4l-reveal-push.py" "02 Skills/e4l-analysis-fulfill.py" "02 Skills/scan-pull-fulfill.py" "02 Skills/e4l-portal-import.py" "02 Skills/history-links-backfill.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: link each new scan; dry-run backfill for past scans"
```

---

### Task 9: The panel on the authoring page

**Files:**
- Create: `dashboard/history_links.py` (deploy-chat)
- Modify: `dashboard/biofield_report_html.py:2142` (`render_author_html` gains `history_links_html=""`, placed right after `render_clinical_checklist(...)`)
- Modify: `biofield_local_app.py:1276` (the `/author/<test_id>` route computes and passes it)
- Test: `tests/test_history_links_panel.py`

**Interfaces:**
- Consumes: the three tables, `e4l_scans`, `e4l_items`, `e4l_identity_merges` in the local `e4l.db`.
- Produces: `render_panel(e4l_db_path, e4l_client_id, date_test) -> str` (HTML, or `""` on any failure or when there is nothing to say).

- [ ] **Step 1: Write the failing tests**

```python
"""History-to-finding links panel (spec 2026-10-01). Pure: no app env needed."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import history_links as HP  # noqa: E402


def _db(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.executescript("""
    CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER, scan_date TEXT);
    CREATE TABLE e4l_items(item_code TEXT PRIMARY KEY, name TEXT, full_name TEXT);
    CREATE TABLE e4l_identity_merges(dup_client_id INTEGER PRIMARY KEY,
        canonical_client_id INTEGER NOT NULL, note TEXT, confirmed_at TEXT,
        route_reports INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE client_history(id INTEGER PRIMARY KEY, client_id INTEGER, phrase TEXT,
        kind TEXT, source TEXT, source_ref TEXT, confirmed INTEGER, first_seen TEXT,
        last_seen TEXT, retired_at TEXT);
    CREATE TABLE finding_conditions(id INTEGER PRIMARY KEY, item_code TEXT, condition TEXT,
        origin TEXT, note TEXT, reviewed INTEGER, removed INTEGER, reviewed_at TEXT);
    CREATE TABLE finding_history_links(scan_id INTEGER, item_code TEXT, history_id INTEGER,
        finding_condition_id INTEGER, linked_at TEXT);
    INSERT INTO e4l_scans VALUES (10, 1, '2026-08-01'), (11, 1, '2026-09-01'),
                                 (12, 1, '2026-12-01'), (20, 5, '2026-09-05');
    INSERT INTO e4l_items VALUES ('ED5','Circulation','ED5 Circulation'),
                                 ('ED9','Stomach','ED9 Stomach Driver');
    INSERT INTO client_history VALUES
      (1,1,'macular degeneration','structured','document','41',1,'x','x',NULL),
      (2,1,'tinnitus','structured','chat','focus:tinnitus',0,'x','x',NULL),
      (3,1,'gout','structured','pb','pb:gout',1,'x','x',NULL),
      (4,1,'old thing','structured','pb','pb:old',1,'x','x','2026-09-01');
    INSERT INTO finding_conditions VALUES
      (1,'ED9','macular degeneration','glen','Stomach <b>carries</b> metals',1,0,'x'),
      (2,'ED5','tinnitus','drafted','',0,0,NULL);
    INSERT INTO finding_history_links VALUES (11,'ED9',1,1,'x'), (11,'ED5',2,2,'x'),
                                             (10,'ED5',3,2,'x');
    """)
    cx.commit()
    return p


def test_panel_shows_the_scan_on_or_before_the_test_date(tmp_path):
    html = HP.render_panel(_db(tmp_path), "1", "2026-09-15")
    assert "2026-09-01" in html and "2026-12-01" not in html
    assert "macular degeneration" in html and "ED9 Stomach Driver" in html
    assert "uploaded report" in html
    assert "from chat, unconfirmed" in html
    assert html.count("unreviewed") == 1                      # ED5 row only
    assert "gout" not in html                                  # linked on another scan
    assert "1 reported condition no finding links to" in html  # gout; retired not counted


def test_notes_and_phrases_are_escaped(tmp_path):
    html = HP.render_panel(_db(tmp_path), "1", "2026-09-15")
    assert "Stomach &lt;b&gt;carries&lt;/b&gt; metals" in html
    assert "<b>carries" not in html


def test_a_merged_duplicate_shows_the_canonical_persons_links(tmp_path):
    p = _db(tmp_path)
    with sqlite3.connect(p) as cx:
        cx.execute("INSERT INTO e4l_identity_merges(dup_client_id, canonical_client_id)"
                   " VALUES (5, 1)")
    # 2026-09-03: before client 5's own scan 20, so the canonical scan 11 is the latest
    assert "macular degeneration" in HP.render_panel(p, "5", "2026-09-03")


def test_panel_is_empty_when_it_cannot_say_anything(tmp_path):
    p = _db(tmp_path)
    assert HP.render_panel(p, "", "2026-09-15") == ""
    assert HP.render_panel(p, "abc", "2026-09-15") == ""
    assert HP.render_panel(p, "1", "2026-07-01") == ""        # no scan yet
    assert HP.render_panel(str(tmp_path / "missing.db"), "1", "2026-09-15") == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/worktrees/wt-deploy-chat-e5ac8379 && ~/.venvs/deploy-chat311/bin/python -m pytest -q tests/test_history_links_panel.py`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement the panel**

```python
"""History-to-finding links on the authoring page (spec 2026-10-01).

Reads the local e4l.db read-only and writes nothing. The vault's
02 Skills/history_links.py owns the tables. Any failure renders nothing, so the
authoring page never breaks on this panel. Glen's view only: no client sees it.
"""
import html
import os
import sqlite3

SOURCE_LABELS = {
    "people:conditions": "People record", "document": "uploaded report",
    "intake": "intake", "intake:narrative": "intake", "pb": "Practice Better",
    "scoreapp": "ScoreApp quiz", "ghl:terrain": "from GoHighLevel, origin unknown",
    "glen": "your tag", "ledger:intake": "intake tag", "chat": "from chat, unconfirmed"}


def _e(x):
    return html.escape(str(x or ""), quote=True)


def _group(cx, cid):
    """Same rule as e4l_synthesis.merge_group in the vault."""
    try:
        canon = {int(d): int(c) for d, c in cx.execute(
            "SELECT dup_client_id, canonical_client_id FROM e4l_identity_merges")}
    except sqlite3.Error:
        return {cid}
    c = canon.get(cid, cid)
    return {cid, c} | {d for d, can in canon.items() if can == c}


def render_panel(e4l_db_path, e4l_client_id, date_test):
    try:
        cid = int(str(e4l_client_id or "").strip())
    except ValueError:
        return ""
    if not os.path.exists(e4l_db_path):
        return ""
    try:
        with sqlite3.connect(f"file:{e4l_db_path}?mode=ro", uri=True) as cx:
            return _render(cx, cid, str(date_test or "").strip()[:10])
    except sqlite3.Error:
        return ""


def _render(cx, cid, date_test):
    group = sorted(_group(cx, cid))
    marks = ",".join("?" * len(group))
    q = (f"SELECT scan_id, scan_date FROM e4l_scans WHERE client_id IN ({marks})"
         + (" AND scan_date<=?" if date_test else "") + " ORDER BY scan_date DESC LIMIT 1")
    scan = cx.execute(q, group + ([date_test] if date_test else [])).fetchone()
    if not scan:
        return ""
    scan_id, scan_date = scan
    rows = cx.execute(
        "SELECT h.id, h.phrase, h.source, l.item_code, coalesce(i.full_name, i.name, l.item_code),"
        " f.reviewed, f.note FROM finding_history_links l"
        " JOIN client_history h ON h.id=l.history_id"
        " JOIN finding_conditions f ON f.id=l.finding_condition_id"
        " LEFT JOIN e4l_items i ON i.item_code=l.item_code"
        " WHERE l.scan_id=? ORDER BY h.phrase, l.item_code", (scan_id,)).fetchall()
    linked_ids = {r[0] for r in rows}
    active = cx.execute(
        f"SELECT id, phrase FROM client_history WHERE client_id IN ({marks})"
        " AND retired_at IS NULL", group).fetchall()
    unlinked = len({p for i, p in active if i not in linked_ids}
                   - {r[1] for r in rows})
    by_condition = {}
    for hid, phrase, source, code, name, reviewed, note in rows:
        c = by_condition.setdefault(phrase, {"sources": set(), "findings": []})
        c["sources"].add(SOURCE_LABELS.get(source, source))
        c["findings"].append((name, reviewed, note))
    items = []
    for phrase, c in by_condition.items():
        finds = "".join(
            f"<li>{_e(name)}" + ("" if reviewed else " <span class=food>unreviewed</span>")
            + (f"<div class=food>{_e(note)}</div>" if note else "") + "</li>"
            for name, reviewed, note in c["findings"])
        items.append(f"<li><b>{_e(phrase)}</b> <span class=food>"
                     f"{_e(', '.join(sorted(c['sources'])))}</span><ul>{finds}</ul></li>")
    if not items and not unlinked:
        return ""
    noun = "condition" if unlinked == 1 else "conditions"
    return ("<section class=clinical-summary><div class=clinical-head>"
            f"<h3>History and findings, scan of {_e(scan_date)}</h3></div>"
            + (f"<ul>{''.join(items)}</ul>" if items else "<p class=food>No links on this scan.</p>")
            + f"<p class=food>{unlinked} reported {noun} no finding links to.</p></section>")
```

- [ ] **Step 4: Run the panel tests to verify they pass**

Expected: 4 passed.

- [ ] **Step 5: Wire it into the authoring page**

In `dashboard/biofield_report_html.py`, add `history_links_html=""` as the last keyword parameter of `render_author_html`, and change `+ render_clinical_checklist(...)` so the call is followed by `+ history_links_html` before `+ chain + session + narrative_section`.

In `biofield_local_app.py`, in the `/author/<test_id>` route just before `return Response(render_author_html(...`, add:

```python
        try:
            from dashboard import history_links as _hlp
            _hl_html = _hlp.render_panel(e4l_db, rep.get("e4l_client_id") or "",
                                         rep.get("date") or "")
        except Exception as _he:
            print(f"[history-links] panel skipped: {_he!r}", flush=True)
            _hl_html = ""
```

and pass `history_links_html=_hl_html` in the `render_author_html(...)` call. Read `authored_report` (`dashboard/biofield_authoring.py:833`) to confirm the test date key is `date`; if it is named differently, use that key and say so.

Add to `tests/test_biofield_author_html.py`:

```python
def test_history_links_panel_sits_after_the_clinical_summary():
    from dashboard.biofield_report_html import render_author_html
    page = render_author_html({"client": {"name": "A", "email": "a@x.com"}, "layers": [],
                               "test_id": "a1"}, clinical_checklist=[],
                               history_links_html="<section id=hlpanel></section>")
    assert "<section id=hlpanel></section>" in page
```

Run: `~/.venvs/deploy-chat311/bin/python -m pytest -q tests/test_history_links_panel.py tests/test_biofield_author_html.py`. Expected: all pass. If the existing author test file needs more report keys to render, copy the minimal report dict its other tests use.

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/wt-deploy-chat-e5ac8379 && git add dashboard/history_links.py dashboard/biofield_report_html.py biofield_local_app.py tests/test_history_links_panel.py tests/test_biofield_author_html.py && git commit -m "Authoring page: history-to-finding links panel, read-only"
```

---

### Task 10: The review page and its apply script

**Files:**
- Create: `02 Skills/apply-finding-condition-review.py` (vault)
- Modify: `02 Skills/history_links.py` (add `apply_review`)
- Test: `02 Skills/tests/test_history_links.py`
- Create: the review page, an Artifact (see Step 5)

**Interfaces:**
- Produces: `apply_review(cx, decisions, today) -> dict` where `decisions = {"rows": [{"id": int, "keep": bool, "note": str}], "added": [{"item_code": str, "condition": str, "note": str}]}` and the result counts `reviewed`, `removed`, `added`, `skipped`.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_review_marks_rows_and_adds_glens_rows():
    cx = _items(_map_db())
    HL.seed_from_remedy_rows(cx)
    ids = dict(cx.execute("SELECT condition, id FROM finding_conditions"))
    out = HL.apply_review(cx, {
        "rows": [{"id": ids["leaky gut"], "keep": True, "note": "  gut  "},
                 {"id": ids["brain fog"], "keep": False, "note": ""},
                 {"id": 999, "keep": True, "note": ""}],
        "added": [{"item_code": "ED9", "condition": "Macular degeneration",
                   "note": "Stomach Meridian carries heavy metal stresses to the Macula"},
                  {"item_code": "NOPE", "condition": "x", "note": ""}]}, TODAY)
    assert out == {"reviewed": 2, "removed": 1, "added": 1, "skipped": 2}
    got = dict((c, (r, m, n)) for c, r, m, n in cx.execute(
        "SELECT condition, reviewed, removed, note FROM finding_conditions"))
    assert got["leaky gut"] == (1, 0, "gut")
    assert got["brain fog"] == (1, 1, "")
    assert got["macular degeneration"] == (
        1, 0, "Stomach Meridian carries heavy metal stresses to the Macula")
    assert got["spike protein exposure"] == (0, 0, "")        # untouched stays unreviewed
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL with `AttributeError: ... 'apply_review'`.

- [ ] **Step 3: Implement**

```python
def apply_review(cx, decisions, today):
    """Write Glen's review. Rows he did not touch stay unreviewed. A removed row is
    kept with removed=1, so a re-seed never brings it back."""
    init_tables(cx)
    out = {"reviewed": 0, "removed": 0, "added": 0, "skipped": 0}
    for d in (decisions or {}).get("rows") or []:
        keep = bool(d.get("keep"))
        n = cx.execute(
            "UPDATE finding_conditions SET reviewed=1, removed=?, note=?, reviewed_at=?"
            " WHERE id=?", (0 if keep else 1, str(d.get("note") or "").strip(), today,
                            int(d.get("id") or 0))).rowcount
        if not n:
            out["skipped"] += 1
            continue
        out["reviewed"] += 1
        out["removed"] += 0 if keep else 1
    known = {r[0] for r in cx.execute("SELECT item_code FROM e4l_items")}
    for d in (decisions or {}).get("added") or []:
        code, cond = str(d.get("item_code") or "").strip(), _condition(d.get("condition"))
        if code not in known or not cond:
            out["skipped"] += 1
            continue
        cx.execute(
            "INSERT INTO finding_conditions(item_code, condition, origin, note, reviewed,"
            " removed, reviewed_at) VALUES (?,?,'glen',?,1,0,?)"
            " ON CONFLICT(item_code, condition) DO UPDATE SET note=excluded.note,"
            " reviewed=1, removed=0, reviewed_at=excluded.reviewed_at",
            (code, cond, str(d.get("note") or "").strip(), today))
        out["added"] += 1
    cx.commit()
    return out
```

Create `02 Skills/apply-finding-condition-review.py`:

```python
#!/usr/bin/env python3
"""Write Glen's finding-to-condition review into e4l.db. Dry run by default.

  python3 "02 Skills/apply-finding-condition-review.py" decisions.json
  python3 "02 Skills/apply-finding-condition-review.py" decisions.json --apply

decisions.json is exported from the review page's store (see the plan, Task 10).
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history_links as HL  # noqa: E402

E4L_DB = os.path.expanduser("~/AI-Training/e4l.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    decisions = json.load(open(a.decisions, encoding="utf-8"))
    if a.apply:
        cx = sqlite3.connect(E4L_DB)
    else:
        cx = sqlite3.connect(":memory:")
        sqlite3.connect(f"file:{E4L_DB}?mode=ro", uri=True).backup(cx)
    out = HL.apply_review(cx, decisions, datetime.date.today().isoformat())
    print(("APPLIED" if a.apply else "DRY RUN, nothing written"), out)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Expected: all of `tests/test_history_links.py` pass.

- [ ] **Step 5: Build the review page**

Load the `artifact-design` and `artifact-capabilities` skills first. Model the page on the remedy order review page (https://claude.ai/artifact/Wdh65XPT9pbWtErtGKcg9y): read it with the Artifact tool's `read` action and reuse its structure and store pattern.

Content, from `e4l.db` after Task 11's seed and drafts are applied: one block per finding code, with code, full name and description, then its `finding_conditions` rows. Each row has the condition, its origin in words ("from a remedy row", "Claude's draft"), a keep or remove choice, and a note box. Each finding has an "add condition" box with its own note box. A filter for "unreviewed only". The page saves to its store under `rows/<id>` as `{keep, note}` and `added/<n>` as `{item_code, condition, note}`.

To apply: read the store with `ArtifactData`, write `{"rows": [...], "added": [...]}` to `~/AI-Training/clinical/remedy-order-review/finding-condition-decisions.json`, run the script dry, show Glen the counts, then `--apply` on his yes. No client data goes on this page: findings and conditions only.

- [ ] **Step 6: Commit**

```bash
cd ~/worktrees/vault-history-links && git add "02 Skills/history_links.py" "02 Skills/apply-finding-condition-review.py" "02 Skills/tests/test_history_links.py" && git commit -m "History links: apply Glen's finding-to-condition review"
```

---

### Task 11: Prove it, review it, ship it

**Files:** none new.

- [ ] **Step 1: Mutate every guard**

With the `cp` and `mv` rule, break each of these one at a time and confirm a test fails:
`TAG_SOURCES` dropping `terrain`; `LEDGER_SOURCES` dropping a chat source; chat `confirmed=0` changed to 1; the `status != "active"` filter; the `document:` split in `people_rows`; `sources_read` filter in `write_history`; the retire condition; the household check and the blank email check in `build_client_history`; the `"attributes" in inputs["read"]` gate; the 404-is-clean intake branch; `history_ok` gate, no-scan gate and no-findings gate in `link_scan`; `removed=0` filter; the merge group in `link_scan`; the `kind` narrative facts; `process_scan`'s catch-all; the header-only check in the attributes route; `html.escape` in the panel; the scan-date filter in the panel. Report any survivor and add a test for it.

- [ ] **Step 2: One review round**

A fresh Claude subagent, read-only, gets both diffs, the spec and this plan. Its question: "How could this show Glen a link that is wrong, hide a true one, mix two people's history, or erase links?" Verify every finding before fixing it.

- [ ] **Step 3: Backfill dry run, then report to Glen**

Run: `cd ~/worktrees/vault-history-links && doppler run --project remedy-match --config prd -- python3 "02 Skills/history-links-backfill.py"`. Show Glen the counts with the drafted-row count from Task 2, in one short message.

- [ ] **Step 4: Ship, in this order, each on Glen's yes**

1. Open the deploy-chat PR from `sess/e5ac8379` (spec, plan, route, panel). Ask Glen to merge it in the platform tab. After deploy, verify the live route: `GET https://illtowell.com/api/people/<a test person id>/attributes` with the header returns 200, and without it 401. The platform tab restarts the local Biofield app, because `biofield_local_app.py` changed.
2. Merge the vault branch `sess/e5ac8379-history-links` into vault `main`, after checking the touched files are clean there and unchanged since branching.
3. Back up e4l.db (`cp e4l.db e4l.db.bak-$(date +%Y%m%d-%H%M%S)-pre-history-links`), then run `finding-condition-drafts.py load --apply`, then the backfill with `--apply`.
4. Open one authored test for a client with links, and check the panel renders on the page.
5. Build the review page (Task 10, Step 5) and give Glen its link.
