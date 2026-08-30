# Sequence Engine — in-house email automation

**Date:** 2026-08-30
**Status:** Approved for planning
**Goal:** Own the authoring, scheduling and sending of automated email sequences,
so copy lives in version-controlled text we can read, test and edit, instead of
inside GoHighLevel workflows that no API can reach.

---

## Why

GHL's automation layer is opaque to us. Probed on 2026-08-30, every relevant
endpoint returns **403 on all three tokens** (`GHL_PIT`, `GHL_CONTENT_PIT`,
`GHL_API_KEY`), v1 and v2 alike: `workflows`, `campaigns`, `emails/builder`,
`emails/schedule`, `contacts/{id}`, and every contact sub-resource. The delivered
email record carries `source: "workflow"` and `provider: "mailgun"` and nothing
else — no workflow id, no template id, no automation name. There is no
programmatic path from a sent email back to the automation that sent it, and the
automation UI will not render under browser automation.

The cost is concrete. A first-name merge field rendering empty has sent
`Aloha ,` to real clients **98 times across 7 subjects**, and an email still
offers "our Practice Better community" as a current benefit (32 sends, latest
2026-08-28) more than a year after that platform stopped being current. Both are
one-line fixes that a test would have caught. Instead they required reading
delivered mail to find, and they remain unfixed because the workflow containing
them cannot be located by name in the GHL UI.

We are also already most of the way in-house. `dashboard/ghl_email.py` posts
directly to the conversations API, and `scripts/weekly_live_invitation.py` sends
the weekly community invitations with batching, suppression checks, and
per-recipient idempotency. This spec generalizes that script rather than
introducing a new mechanism.

## Non-goals

- **Replacing the mail transport.** We keep sending through GHL's Mailgun relay.
  `mail.remedymatch.com` has months of reputation behind it and targeted sends
  currently open at 80–87%. Moving to raw SMTP resets that to zero, and we would
  also lose GHL's delivery status and per-message open tracking.
- **Replacing the CRM.** GHL stays the contact database and the DND/unsubscribe
  system of record.
- **Migrating every workflow at once.** Sequences move over one at a time, each
  only after the GHL original is located and paused.

---

## Copy lives in the vault, pushed to Postgres

The engine runs on Render and cannot read the vault on Glen's Mac, so vault files
are the **source** and Postgres is the **serving copy**.

```
00 System/sequences/<sequence-slug>/
  sequence.md        # name, trigger kind, active flag
  01-<step-slug>.md  # subject + delay_days in frontmatter, markdown body
  02-<step-slug>.md
```

Enrollment is by trigger, not by a stored audience query. A sequence never
selects its own recipients; something calls `enroll()`. This keeps the blast
radius of a copy edit to zero.

A push script (`scripts/sequence_push.py`) reads the tree and upserts it into
`sequences` and `sequence_steps`. Editing copy is therefore an edit plus one
command — **no deploy**. This matters because a flag flip already costs two
deploys in this system, and copy changes should not be gated on that.

The vault's hourly auto-snapshot gives version history for free.

**Rejected alternative:** copy as files inside the deploy-chat repo. Simpler to
serve, but every typo fix becomes a deploy, and Glen edits in the vault.

## Schema

Four tables, following existing repo conventions (`dashboard/db.py`, Postgres in
production, SQLite locally).

| Table | Key | Purpose |
|---|---|---|
| `sequences` | `slug` PK | name, trigger kind, active flag |
| `sequence_steps` | `UNIQUE(slug, step_no)` | subject, body, `delay_days` |
| `sequence_enrollments` | `UNIQUE(slug, email)` | who is on which sequence, `enrolled_at`, status |
| `sequence_sends` | `UNIQUE(slug, step_no, email)` | the idempotency ledger |

Two levels of opt-out, deliberately kept in different places. A **per-sequence**
opt-out sets `sequence_enrollments.status = 'unsubscribed'`, stopping that
sequence and nothing else. A **global** opt-out writes to the existing
`email_suppression` table, which every sender in the codebase already consults.
Neither is inferred from the other.

`delay_days` on each step is measured **from enrollment**, cumulative, not from
the previous step. Storing the offset from enrollment means editing step 3's
delay cannot silently shift steps 4 and 5 for people already mid-flight.

`sequence_sends` is the load-bearing table. Its unique key is what makes two
overlapping cron runs safe, using the same `INSERT ... ON CONFLICT` pattern as
`weekly_live_invitation_recipients`.

## The runner

A Render cron (`type: cron`, every 15 minutes) invoking
`scripts/sequence_runner.py`. Per tick, for each active sequence:

1. Find enrollments whose next unsent step is due
   (`enrolled_at + delay_days <= now`).
2. Skip the recipient if suppressed (`email_suppression.is_suppressed`) or
   unsubscribed from this sequence.
3. **Claim** the send: `INSERT INTO sequence_sends (..., status='claimed')
   ON CONFLICT DO NOTHING`. If no row was inserted, another process owns it —
   skip.
4. Send via `ghl_email.send_via_ghl`.
5. Mark the row `sent` with the returned message id, or `failed` with the error.

Claim-before-send means a crash between steps 3 and 4 leaves a stuck row rather
than a duplicate. **Under-sending is the correct failure mode**; a duplicate goes
to a real client and cannot be recalled. A reaper releases rows still `claimed`
after 60 minutes.

### Guards

`send_via_ghl` already returns early under `PYTEST_CURRENT_TEST`, because this
path has two live mutations (a contact upsert and a message send). The runner
gets the same guard at its own entry point rather than relying on the transport's
— a bare full-suite run has sent real email before.

The runner also needs a `--dry-run` that reports exactly who would receive what,
because blast radius must be measurable before it is real.

## Enrollment

A sequence declares its trigger in `sequence.md`. Two kinds at the start:

- `manual` — enrolled by an explicit call or a console action.
- `on_contact_created` — enrolled when our app creates a contact.

**The producer must be wired, not merely designed.** A table plus a runner with
nothing calling `enroll()` is a feature that cannot fire. The plan names the
call site for each trigger kind, and a test asserts that every active sequence
has at least one reachable enrollment path.

## Unsubscribe

GHL appends an unsubscribe footer to workflow email. It does **not** append one
to messages sent through the conversations API.

Verified 2026-08-30 by reading delivered bodies. Every `source: "workflow"`
message carries a real `services.msgsndr.com/emails/builder/unsubscribe-view`
token link. Every `source: "app"` message — anything sent through the
conversations API, including our own `weekly_live_invitation.py` — carries none
unless the sender wrote one into the body itself.

**This is a live gap, not just a design constraint.** It predates this engine and
is being audited separately; the engine must not add to it.

So the engine must append its own. Requirements:

- Every sequence email carries an unsubscribe link.
- The link is a signed, non-guessable token; it does not put an email address in
  a query string.
- Clicking it suppresses that address for that sequence immediately, with a
  global-opt-out option.
- The runner checks that suppression before every send, not only at enrollment.

This is in scope from the start. It is a legal requirement for commercial mail,
not a refinement.

## Migrating an existing sequence

Per sequence, in this order:

1. **Reconstruct positions** from delivered mail. For the nurture sequence we can
   already read, per contact, exactly which of the five steps they received and
   when.
2. **Locate and pause the GHL workflow.** Blocking. Ours must not start before
   theirs stops or people receive both.
3. **Enroll** the in-flight contacts at their reconstructed step, backdating
   `enrolled_at` so the remaining offsets land correctly.
4. **Enable** the sequence and watch the first tick with `--dry-run` first.

The nurture sequence is **not** the first migration. Its GHL original cannot
currently be found in the workflow list, so step 2 is blocked. The engine gets
built and proven on a new sequence, and the nurture one moves once that workflow
is located.

## Delivery slices

This is more than one plan's worth of work. Sliced so each lands green and
useful on its own:

1. **Schema + push script.** Tables, `sequence_push.py`, a sequence defined in
   the vault and visible in Postgres. Sends nothing.
2. **Runner + idempotency, dark.** Due-step calculation, claim-before-send,
   `--dry-run`. Cron registered but the sequence inactive. Still sends nothing.
3. **Unsubscribe.** Token, route, per-sequence and global opt-out, enforced at
   send time.
4. **First live sequence.** A new sequence, small audience, watched.
5. **Nurture migration.** Blocked on locating and pausing the GHL original.

Nothing in slices 1–3 can email a client, which is what makes them safe to ship
incrementally.

## Testing

- Due-step calculation across offsets, including a step edited mid-flight.
- Idempotency: two concurrent runners over the same enrollment send once.
- Suppression honored at send time, not only at enrollment.
- Unsubscribe token round-trip, and that a suppressed address is skipped.
- Position reconstruction from a fixture of delivered mail.
- Fixtures built from the modules' own DDL and writers, never hand-typed schema.

Guard tests are mutation-tested: break the guard, confirm the test goes red.

## Risks

| Risk | Mitigation |
|---|---|
| Double-send during migration | GHL workflow paused first; blocking gate |
| Duplicate send from concurrent crons | `UNIQUE(slug, step_no, email)` + claim-before-send |
| Test run sends real mail | Guard at the runner entry, not only the transport |
| Copy edit not reaching production | Push script reports what it changed; runner logs the step version it sent |
| Deliverability regression | Transport unchanged; same relay, same From identity |
