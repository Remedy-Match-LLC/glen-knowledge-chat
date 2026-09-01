# Sequence Engine — in-house email automation

**Date:** 2026-08-30
**Status:** Slice 1 shipped; slices 2-5 open. Revised 2026-09-01 to extend the
existing vault content-email system rather than duplicate it.
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

We are also already most of the way in-house, in **two** places.

In deploy-chat: `dashboard/ghl_email.py` posts directly to the conversations API,
and `scripts/weekly_live_invitation.py` sends the weekly community invitations
with batching, suppression checks, and per-recipient idempotency.

In the vault: `03 Marketing/ghl-email-automation/` is a complete content-email
system — `ghl_send.py`, `run_campaign.py`, `router.py`, `profiler.py`,
`variants.py`, `sent_log.py`, a launchd cadence, and campaigns stored as markdown
with front matter. Its idempotency is already `UNIQUE(contact_id, campaign_id)`
with `INSERT OR IGNORE`, and its copy already lives in the vault. See
[[project_vault_content_email_system]].

**This spec was first written without knowing that existed**, and proposed a near
duplicate of it. Corrected 2026-09-01: the engine EXTENDS these, and the sections
below say for each piece whether it is reused, moved, or new.

## Non-goals

- **Replacing the mail transport.** We keep sending through GHL's Mailgun relay.
  `mail.remedymatch.com` has months of reputation behind it and targeted sends
  currently open at 80–87%. Moving to raw SMTP resets that to zero, and we would
  also lose GHL's delivery status and per-message open tracking.
- **Replacing the CRM.** GHL stays the contact database and the DND/unsubscribe
  system of record.
- **Migrating every workflow at once.** Sequences move over one at a time, each
  only after the GHL original is located and paused.
- **Replacing the vault's one-shot campaign sender.** `ghl_send.py send` blasts a
  tag on a schedule and is the right tool for weekly content. The engine handles
  the thing it cannot do: a multi-step drip whose clock starts per contact at
  enrollment. The two coexist and share the copy format and the unsubscribe
  footer.

---

## What is reused, moved, and new

| Piece | Where it is now | Decision |
|---|---|---|
| Copy as markdown + front matter | vault `campaigns/<slot>/*.md` | **Reuse the format**, add `delay_days` and `step` |
| Front-matter parser | vault `variants.py` `_parse` | **Reuse the conventions**; the push script parses the same shape |
| Per-recipient idempotency | vault `sent_log.py`, `UNIQUE(contact_id, campaign_id)` | **Reuse the pattern**, widened to `(slug, step_no, email)` |
| Unsubscribe footer | `dashboard/unsubscribe.py` + vault `unsub.py` | **Already shared** (slice 1, shipped) — pinned signature vector |
| Send transport | `dashboard/ghl_email.send_via_ghl` | **Reuse unchanged** |
| Suppression + DND check | `email_suppression`, `_dnd_email` | **Reuse unchanged** |
| One-shot campaign to a tag | vault `run_campaign.py` | **Leave alone** — still the right tool for weekly content |
| Freshness gate | vault `freshness.py` | **Deliberately not used** by the engine (see below) |
| Per-contact enrollment with day offsets | nowhere | **New** |
| Reliable scheduling | vault launchd | **Moved to Render cron** (see below) |

**Freshness does not apply to a drip.** The vault's `refreshed:` gate exists
because weekly content goes stale: blasting a three-week-old "today at 3 PM"
invitation is wrong. A drip step is evergreen by design — the client receives it
on their day 4 whether it was written last week or last year. Applying a
staleness gate would silently stop the sequence, which is exactly the failure the
gate was built to reveal.

**Scheduling moves to Render, and the reason is evidence, not preference.** The
vault cadence runs under launchd on Glen's Mac. It sent nothing from 2026-07-24
to 2026-08-31 and nobody noticed, because the Mac has to be awake, the log is
local, and until 2026-09-01 a skip was silent. A drip owes each contact a send on
a specific day; that needs a scheduler that runs whether or not a laptop is open.
Render already runs the charge cron and others.

So the split is: **vault owns the copy, Render owns the clock.**

## Copy lives in the vault, pushed to Postgres

The engine runs on Render and cannot read the vault on Glen's Mac, so vault files
are the **source** and Postgres is the **serving copy**.

```
00 System/sequences/<sequence-slug>/
  sequence.md        # name, trigger kind, active flag
  01-<step-slug>.md  # subject + delay_days in frontmatter, markdown body
  02-<step-slug>.md
```

The front matter follows the vault's existing convention (`segment:`, `subject:`,
`tag:`, `from:` parsed by `variants.py`) plus `delay_days:`. A person who can edit
a campaign can edit a sequence step without learning a second format.

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

**This WAS a live gap, closed by slice 1 on 2026-08-31.** Audited 2026-08-30 across
every outbound subject with 20+ sends: **5,440 sends across 13 subjects carry no
unsubscribe link**, all `source: app`. Eight subjects do carry one (409 sends).

The 13 split into roughly 4,660 promotional sends — the Wellness Whispering
invitations (1,912), storm-update community calls (1,013), Wednesday sessions
(958), "Your Energy 4 Life experience now goes deeper" (503), and four editorial
pieces (274) — and roughly 780 transactional ones (scan-ready and portal-ready
notices, and a link correction). Only the promotional group clearly needs a
link; where exactly that line sits is Glen's call, not this document's.

Mitigating, and worth stating precisely: `weekly_live_invitation.py` already
checks `email_suppression.is_suppressed` **and** GHL's `dnd`/`dndSettings`
before every send. Existing opt-outs are honored. The gap is that these emails
give a recipient no way to create one.

**Therefore unsubscribe was built first**, as a shared helper on the bulk send
path rather than inside the engine, and the existing senders were retrofitted
before any new sequence exists. The engine inherits it: a sequence step passes its
slug as the `scope`, and the same signed link works, because the vault sender and
the server verifier pin the same signature vector.

Still outstanding from that audit: the ~780 transactional sends were deliberately
left without a footer, and whether that line sits in the right place is Glen's
call, not this document's.

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

1. ~~**Unsubscribe, shared, and retrofit.**~~ **SHIPPED 2026-08-31** (PR #1509 +
   vault commit). Signed token, `/email/unsubscribe` route confirming on GET and
   recording on POST, opt-out into `email_suppression` as `bounce_type='optout'`,
   opt-in `send_bulk(..., unsubscribe_scope=)`, and retrofits to
   `weekly_live_invitation.py` and the vault `ghl_send.py`. From identity
   consolidated on `drglen@mail.remedymatch.com` with a writer-side guard.
   Verified in production: signed link 200, forged 400.

   Two things it cost that were not in the estimate, both worth remembering: the
   vault sender had to be found first (this spec nearly duplicated it), and the
   alert channel it depends on turned out to be dead — `POST /api/todos` had been
   silently discarding every row on Postgres since July (PRs #1511, #1520).

2. **Schema + push script.** Tables, `scripts/sequence_push.py`, a sequence
   defined in the vault and visible in Postgres. Sends nothing.
3. **Runner + idempotency, dark.** Due-step calculation, claim-before-send,
   `--dry-run`. Render cron registered but the sequence inactive. Still sends
   nothing.
4. **First live sequence.** A new sequence, small audience, watched.
5. **Nurture migration.** Blocked on locating and pausing the GHL original, which
   is a Glen-only task: the automation UI is a cross-origin iframe that browser
   automation cannot read, and every relevant API returns 403. Fingerprint to
   match: ~40 enrolled, Contact Created trigger, five Send Email steps with waits
   of 4/6/8/7 days, active 2026-07-08 to 2026-08-28.

Slices 2 and 3 cannot email anyone at all, which is what makes them safe to ship
without a client-facing review.

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
