# Merge two people

Date: 2026-09-26. Designed with Glen in the platform tab. He chose a general tool
("general "merge two people" tool"), approach A ("a"), a suggested survivor ("suggest"), and
approved each of the four design sections ("yes").

## Why

The same client can exist twice, under two addresses. Peach Goddard is two people today: her
portal, 20 reports, orders, booking and intake sit under her AOL address, and her certification
and one report sit under Gmail. Each address signs in to a different portal.

The existing merge (`_merge_two_people`, applied from the pending-merges queue) copies the
duplicate into the keeper and deletes the duplicate. Nothing else moves, the deleted address is
not recorded, and the hourly GoHighLevel sync recreates it within the hour. Measured on
2026-09-24: 26 merges applied, at least 2 people came back.

## What it must do

1. One person survives. The merged address becomes a second way in: the old portal link, sign-in
   links, password, Google and Apple sign-in all land on the survivor, who sees everything.
2. Every record keyed to the merged address or person moves to the survivor.
3. Every change is logged, and the merge can be undone from the log.
4. Nothing recreates the merged person.
5. Staff see evidence of which address the person actually uses, and the tool suggests the
   survivor and whether to keep mailing the old address. Staff confirm; the tool never merges
   on its own.

Done, for the first case: Peach's AOL portal link opens her Gmail portal with all 21 reports,
the board shows her once, and an hour later she is still one person.

## Data

Three new tables in the `chat_log` schema.

**`person_merges`**: one row per merge.
`id`, `survivor_person_id`, `merged_person_id`, `survivor_email`, `merged_email`,
`merged_person_json` (the whole merged `people` row), `evidence_json`, `suggestion_json`,
`mail_old` (`stop` | `keep`), `applied_by`, `applied_at`, `undone_at`, `undone_by`.

**`email_aliases`**: `alias_email` (primary key), `canonical_email`, `merge_id`.
One lookup, `canonical_email(cx, email)`, returns the canonical address for any address, following
chains (A merged into B, B later merged into C resolves A to C) and refusing a cycle.

**`person_merge_changes`**: one row per changed record.
`merge_id`, `seq`, `table_schema`, `table_name`, `key_json` (the row's primary key or unique
key), `column_name`, `old_value`, `new_value`, `action` (`rekey` | `set_aside`), `row_json` (the
whole row, for `set_aside`), `source` (`apply` | `sweep`).

## How records move

**Discovery.** At apply time the engine lists every table with a text column whose name contains
`email`, or a `person_id` / `people_id` column, in every schema. This is the same discovery the
read-only dry run used (`00 System/scripts/prod-queries/person-merge-dry-run.py`: 211 columns,
111 unique indexes). A table added later is covered without a code change.

**History tables stay as they were.** A short, named list is never rewritten, because rewriting
it would falsify what happened: sent-mail logs, the GoHighLevel write queue, auth events, click
and open logs, and `person_merges` / `person_merge_changes` themselves. The plan names each one;
a test fails if a discovered table is in neither the move set nor this list.

**Rekey.** A row whose address column equals the merged address (case-insensitive, trimmed), or
whose person column equals the merged person id, is updated to the survivor's value. Each update
is one `rekey` change row.

**Clash.** When the update would break a unique rule, because the survivor already holds that
row (for example `carts(email)` or `biofield_reveals(email, scan_date)`), the survivor's row wins.
The merged row is saved whole as a `set_aside` change row, then deleted.

**The person row.** The survivor's empty fields are filled from the merged person and the tags
are combined, reusing the field rules in `_merge_two_people`. Then the merged `people` row is
deleted; its copy is in `person_merges.merged_person_json`.

**The old portal link.** The merged address's portal token is kept as an alias of the survivor's
portal (a `portal_token_aliases` row: `token_hash`, `canonical_email`, `merge_id`).
`get_portal_by_token` falls back to it, so the old link opens the survivor's portal.

**One transaction.** Apply runs in a single transaction. Any error rolls everything back and
nothing is recorded as applied.

**Hourly sweep.** A job re-runs the rekey for every alias, so a record written later under an old
address (for example a checkout typed with the AOL address) moves to the survivor. It logs each
change with `source=sweep`. It self-corrects rather than relying on every flow to check the map.

## Where the old address is recognised

Each entry point looks up `canonical_email` before anything else:

- `/portal/<token>` and `/api/portal/<token>`: through `get_portal_by_token` and the token alias.
- Sign-in link request `/portal/login-request`, password login and password reset: the typed
  address resolves to the survivor. The sign-in email goes to the address that was typed.
- Google and Apple sign-in: `person_by_verified_email` resolves the returned address first.
- Sign-in links already sent to the old address: their `auth_tokens` rows are rekeyed, so they
  keep working until they expire.
- Membership and certification checks by address, starting with `_is_paid_member`.
- Console search: the old address finds the survivor, labelled "merged from <old address>".
- Every path that creates people: `_upsert_person_additive` (the hourly GoHighLevel and Practice
  Better syncs), `upsert_people`, `dashboard.customers`, and
  `portal_identity._get_or_create_person`. An old address adds its tags to the survivor and
  never creates a person.

## Mail

Everything the app sends goes to the survivor's address. In GoHighLevel the old contact is either
marked do-not-contact and tagged `merged-into-<survivor id>`, or left mailable, following the
`mail_old` choice. Undo reverses what the merge did there. The two GoHighLevel contacts are not
merged inside GoHighLevel.

## The console

**Where.** A "Merge with another person" button on the People client page
(`/console/client`), which asks for the other person. A merge waiting in the pending-merges queue
opens the same screen. The queue's current Apply, which deletes and resurrects, is retired.

**Preview**, both addresses side by side:
- record counts per kind: reports, orders, bookings, memberships, scans, intake;
- clashes, and which row would be kept;
- evidence, each dated: last reply received from the address (a search of the practice mailbox),
  last portal sign-in, last sign-in link requested, last email click, and the most recent scan,
  order, intake and booking that arrived with that address.

**Suggestion**, each with a one-line reason:
- Survivor: the address with the most recent sign of a real person. A reply ranks above a
  sign-in, which ranks above an order or scan, which ranks above a click.
- Mailing the old address: `keep` if it shows any activity in the last 90 days, else `stop`.

Staff can switch either. Nothing is written by the preview.

**Apply.** Owners only (Glen, Rae). The result lists what moved and links to the merge record.

**Undo.** A button on the merge record. It replays the change log backwards: rekeys go back,
set-aside rows are restored, the merged person row is recreated from its copy, and the aliases
are removed. A row changed since the merge is skipped and listed rather than overwritten.

## Failure handling

- Apply fails part-way: the transaction rolls back; nothing moved, nothing recorded.
- Both people are the same row, either id is missing, or the merged address is already an alias:
  refused before anything is written.
- An alias chain would loop: refused.
- A sign-in or lookup cannot read the alias table: it behaves as before the merge (exact address),
  and logs the failure.
- Undo cannot restore a row: that row is listed; the rest of the undo completes.

## Testing

1. Merge a synthetic person who has a row in every discovered table, including clashes, then undo
   it. The database afterwards matches the database before, table by table.
2. After a merge, run the hourly sync with the old address in the incoming data: no person is
   created, and the tags land on the survivor.
3. Each entry point with the old address reaches the survivor: old portal link, sign-in link
   request, password login, Google sign-in, membership check, console search.
4. A record written under the old address after the merge is moved by the sweep and logged.
5. Clash: the survivor's row is kept and the merged row is recoverable from the log.
6. Every discovered table is in the move set or the named history list.
7. A failure injected mid-apply leaves the database unchanged.
8. The preview writes nothing.
9. The suggestion picks the address with the more recent reply over one with more clicks.
10. The move and the undo run once against a Postgres copy of the schema, because production
    runs Postgres and the other tests run SQLite.

This touches orders and memberships, so it gets three blind review rounds, with a money brief for
round 2, before Glen is asked to merge.

## First use: Peach Goddard

1. Run the preview for AOL hub 629 and Gmail hub 631 as a read-only production job and send
   Glen the page.
2. Glen confirms the survivor and the mailing choice, and presses Apply himself in the console.
3. An hour later: one person, the AOL link opens all 21 reports, no AOL person recreated.
4. Tell communication which portal holds her reports.

## Out of scope

- Merging the two contacts inside GoHighLevel.
- Automatic merging, or detecting duplicates.
- The 26 merges already applied. Their deleted addresses were never recorded, so they cannot be
  mapped. The two known to have come back can be merged again with this tool, and people can list
  the rest.
