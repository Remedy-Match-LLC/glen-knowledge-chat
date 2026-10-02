# History and energetic findings, kept apart and linked

Design approved section by section with Glen on 2026-10-01. This is the written spec for his review.

## Why

A client's file holds two kinds of information. **History** is what the client reports or what is
recorded about them. **Energetic findings** are what an E4L scan shows. Today the two are mixed. The
tag ledger gives a scan-derived `system:` tag and a quiz-reported `system:` tag the same name, and only
a source string tells them apart. Matching between them happens only while a report is being built,
and the result is thrown away. Nothing records which finding matched which reported condition.

Glen: "We should separate the history from the energetic findings. Track both and track the
relationship between them, i.e. where energetic findings correlate with the history."

## What Glen chose

- **The first use is per client.** Glen sees where a scan corresponds with what the client reported.
  The cross-client view (which findings tend to go with which conditions) comes later. The storage
  must support it, but nothing is built for it now.
- **Glen's view only.** Links show in the local Biofield app, never to a client. A client-facing line
  would be a diagnostic claim and needs the client-copy rules and Glen's review first.
- **A reviewed finding-to-condition table decides what links.** It is seeded from the second-order
  remedy rows and drafted for the remaining finding codes. Glen reviews it.
- **The history reads the client's entire file.**
- **One condition can link to several findings on one scan.**
- **Each finding-to-condition row carries an optional note** on the nature of the relationship.

## What gets stored

All four live in `e4l.db`, beside the scans. `e4l.db` already syncs to the server.

### 1. `client_history`

One row per reported condition per client.

| Column | Meaning |
|---|---|
| `id` | row id |
| `client_id` | the E4L client |
| `phrase` | the condition, normalised the way `remedy_tiers.normalize` does |
| `source` | where it came from (list below) |
| `source_ref` | which record within that source, e.g. a document id or a chat log id |
| `confirmed` | 1 if Glen set or confirmed it, or it came from a source he controls; 0 for chat |
| `first_seen`, `last_seen` | dates |
| `retired_at` | set when the condition no longer appears in its source; never deleted |

Unique on `(client_id, phrase, source, source_ref)`.

**Sources**, all of the client's file:

| `source` | Read from | `confirmed` |
|---|---|---|
| `people:conditions` | People `conditions`, `terrain_concerns`, `body_systems` | 1 |
| `intake` | intake answers: health concerns, diagnoses; vaccinations, supplements and medications | 1 |
| `pb` | People `pb:` tags (Practice Better) | 1 |
| `scoreapp` | People `system:`, `regulation:` and `concern:` tags from the ScoreApp quiz | 1 |
| `ghl:terrain` | People `terrain:` tags. Label shown: "from GoHighLevel, origin unknown" | 1 |
| `document` | uploaded clinical reports, approved document extractions only | 1 |
| `glen` | ledger tags from `intake:glen-directed`, `clinician`, `note:internal` | 1 |
| `ledger:intake` | ledger tags from `pb-intake`, `pb-intake-backfill`, `pb-intake:self-report` | 1 |
| `chat` | ledger tags from `chatbot:query_log`, `journal:portal`, `email` | **0** |

Excluded on purpose: ledger tags whose source starts `e4l:` (those are findings), People `clin:` tags
(copies of the ledger), `state:` tags (status flags), and the intake's allergy answers (a mold
allergy is not mold exposure). A tag that names an allergy, such as `pb:food-allergy`, is history and
is kept. Free-text narrative fields are not split into phrases. Where the tier matcher reads them
today (supplements, medications, vaccinations), they are stored whole as `intake` rows and searched only
for the narrative-approved conditions, as `remedy_tiers` already does.

**Chat is unconfirmed.** A client asking "what helps glaucoma?" has not said they have it. Chat rows
are kept, shown with the label "from chat, unconfirmed", and never feed remedy choice.

### 2. Energetic findings

Unchanged. They stay in `e4l_scan_results` (`scan_id`, `item_code`, `priority_rank`, `section_context`),
with `e4l_items` as the lookup.

### 3. `finding_conditions`

The reviewed table that decides what links.

| Column | Meaning |
|---|---|
| `id` | row id |
| `item_code` | the finding code, e.g. `ED5` |
| `condition` | a condition phrase |
| `origin` | `remedy-row` (seeded from a second-order row) or `drafted` (Claude) or `glen` (he added it) |
| `note` | optional, Glen's own words on the nature of the relationship |
| `reviewed` | 0 until Glen reviews the row; 1 after |
| `removed` | 1 if Glen removed it. Kept, so a re-seed does not bring it back |
| `reviewed_at` | date |

Unique on `(item_code, condition)`.

**Seed.** The 200 second-order rows give 393 finding-and-condition pairs across 86 of the 262 finding
codes. Claude drafts conditions for the other 176 codes from each finding's description and name,
choosing phrases that already appear in `e4l_formulation_map` where one fits. All seeded rows start
with `reviewed = 0`.

**The note** sits on the general row, so every link made from that row carries it. Glen's example,
for AMD and the Stomach Driver: "Stomach Meridian typically carries heavy metal stresses directly to
the Macula from the Stomach, which is responsible for ionizing both nutrient and toxic minerals
boosting their absorption/bioavailability." Notes are stored as written, for Glen's view. Later client
reporting may use them as source material. Any client wording goes through the client-copy rules and
his review first.

### 4. `finding_history_links`

| Column | Meaning |
|---|---|
| `scan_id` | the scan |
| `item_code` | the finding on that scan |
| `history_id` | the `client_history` row it matched |
| `finding_condition_id` | the `finding_conditions` row that made the match |
| `linked_at` | when the link was computed |

Unique on `(scan_id, item_code, history_id, finding_condition_id)`. Several findings on one scan may
link to the same history row. The note and the reviewed flag are read through `finding_condition_id`,
so a note Glen writes later shows on links made earlier.

## How links are made

1. **Build the history.** For one client, read the People record, intake answers, approved document
   extractions and the ledger, and write `client_history` additively. A phrase no longer present in its
   source gets `retired_at`. The household rule applies: if `identities_on_email` is not 1, the client
   gets no history rows written and no links. The People and intake reads use the same calls as
   `fetch_client_facts`, with the console key in the header.
2. **Match.** For each finding on the scan, take its `finding_conditions` rows that are not `removed`.
   For each row, check the condition against the client's active history with
   `remedy_tiers.condition_met`, which handles synonyms and negation. The history is passed per row so
   the match knows which `client_history` row satisfied it.
3. **Store.** Replace that scan's links in one transaction. If the run fails, or the scan has findings
   but the history read failed, the old links stay. An empty result replaces old links only when the
   history read succeeded.
4. **When.** As each scan is processed, from the same scripts that build scan drafts. A one-time
   backfill runs over past scans, dry run first, with counts shown to Glen before it writes.

Links made from unreviewed rows are stored and shown, marked "unreviewed".

## What Glen sees

### Review page for `finding_conditions`

Built like the remedy order review page. One block per finding code, showing its name and description,
then its condition rows. For each row: the condition, its origin, a keep or remove choice, and a note
box. An "add condition" box per finding. Saves to the page's own store, then a script writes the
reviewed rows into `e4l.db` after Glen says so.

### Panel on the Biofield test page

On the local Biofield app's test page, beside the Clinical Summary. For the test's scan:

- Each reported condition that links, with the findings on that scan that link to it.
- The condition's source label: Practice Better, your tag, uploaded report, ScoreApp quiz,
  "from GoHighLevel, origin unknown", or "from chat, unconfirmed".
- "Unreviewed" beside any link whose row Glen has not reviewed.
- The note, under the link, when the row has one.
- One line at the bottom: how many reported conditions no finding links to.

The panel reads `e4l.db`; it writes nothing.

## Safety

- **Fails closed.** No record, a read error, or a household address means no history and no links.
- **Reaches no client and no remedy choice.** The links show only on Glen's local page. Remedy tiers
  are unchanged and do not read these tables.
- **Never deletes on an empty read.** See step 3.
- **Client data stays local.** Nothing here is sent to a model. The draft conditions for the 176 codes
  come from finding descriptions, which hold no client data.

## Testing

Tests are written before the code. They cover:

- the household rule, at history build and at linking;
- negation: "no glaucoma" makes no history row that links;
- chat rows stored with `confirmed = 0` and labelled;
- `e4l:` ledger tags, `clin:` and `state:` People tags never becoming history;
- several findings on one scan linking to one condition;
- a removed `finding_conditions` row making no link, and a re-seed not restoring it;
- a rerun that reads no history keeping the old links;
- a later note showing on an earlier link.

Each guard is broken on purpose to prove a test catches it. One review round runs before merge: the
change shows Glen something, so the three-round rule does not bind, but it reads client data from many
sources.

## Not in this spec

- The cross-client view of which findings go with which conditions.
- Any client-facing use of links or notes.
- Feeding links or confirmed chat history into remedy choice.
- Finding where GoHighLevel writes `terrain:` tags.

## Where the code goes

- `02 Skills/` in the vault: the history builder, the linker, the seed and backfill scripts, and their
  tests. These run where `e4l.db` and the scan scripts are.
- deploy-chat `biofield_local_app.py` and a new `dashboard/history_links.py`: the panel on the test page.
- The review page: an artifact like the remedy order review, with its own store.
