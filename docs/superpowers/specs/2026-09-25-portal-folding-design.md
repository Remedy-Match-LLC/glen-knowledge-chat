# Client portal: folding cards (Stage 1 of 3)

Date: 2026-09-25. Agreed with Glen section by section in the platform tab.

## Why

Portal pages are too long. Clients, Glen and Rae cannot find things. The Scans page stacks
14 cards and is 7,310px tall on a phone.

A fold feature shipped on 2026-09-16 but never worked live. Two defects stopped it:

- `wireCardFolding()` runs while every page is hidden. Every card measures 0px, so no card
  passes the 320px height test and no button appears.
- `_cardFoldKey` builds its key from the heading text. The heading includes the button's own
  "Show"/"Hide" text, so the key changes and a fold is never found again.

This is Stage 1. Stage 2 is layout. Stage 3 is the chat map with "take me there". Both
come later and have their own specs.

## Glen's rulings

1. Every card gets a Hide/Show toggle at its top right, whatever its height.
2. Fold state is stored on the server and follows the person across devices.
3. A staff fold on a client's portal applies to that staff member on that client only.
   It never changes what the client sees.
4. On a person's first visit to a page, the first card is open and the rest are folded.
5. Every page has a Fold all button for clients and staff. After Fold all it becomes
   Restore, which brings back the folds from before.
6. Staff get "Match client's view". It copies the client's folds into the staff view only.
7. Storage is option A: one saved record per viewer per client portal.

## Viewers

A viewer is identified on the server. The page never supplies its own viewer identity.

| Who | How recognised | Viewer id |
|---|---|---|
| The client | the portal link or the client login | `client` |
| Glen | the master console key or its cookie | `staff:master` |
| Rae, or any owner-role staff account | an owner access token or its cookie | `staff:user:<workspace_users.id>` |

Rae's record is keyed by her staff account id. A reissued sign-in link keeps the same id.
The token itself is never stored. VA-role tokens are treated as no staff identity and get
no staff record.

A staff visit is detected with `_portal_open_is_owner()`. Its staff id comes from the same
key that `_present_console_key()` returns.

## Storage

New table `portal_fold_state`, created idempotently like `client_portals`.

| Column | Meaning |
|---|---|
| `portal_email` | the portal owner's email, lower-cased. Tokens are reissued, email is stable |
| `viewer` | viewer id from the table above |
| `state_json` | the saved record, below |
| `updated_at` | ISO time of the last save |

The primary key is (`portal_email`, `viewer`). Writes are upserts, so each viewer has one
row per portal. SQL must run on SQLite in tests and Postgres in production. No
`lastrowid`.

`state_json` holds:

```json
{
  "cards":   {"scans-latest-report": false, "scans-history": true},
  "seen":    ["scans", "home"],
  "before_fold_all": {"scans": {"scans-latest-report": false, "scans-history": true}}
}
```

- `cards` maps a card's fixed name to folded (true) or open (false). A card not listed
  falls back to the first-visit default.
- `seen` lists pages the viewer has opened. It decides when the first-visit default applies.
- `before_fold_all` holds, per page, the card states from just before Fold all. Restore
  applies it and clears that page's entry.

The record is capped at 64 KB and 500 card entries. Unknown keys are dropped on save.

## API

Both routes live beside the other `/api/portal/<token>/...` routes. The portal is resolved
with `_portal_record_for`. An unknown token gets 404.

- `GET /api/portal/<token>/folds` returns the caller's own record, or an empty one.
  A staff caller can add `?of=client` to read the client's record. That is how "Match
  client's view" gets its copy. A client caller asking `?of=client` gets their own record,
  and no other value is accepted.
- `PUT /api/portal/<token>/folds` replaces the caller's own record. The server decides the
  viewer. There is no parameter that lets a staff caller write the client's record.

Match client's view is done in the page: read `?of=client`, then PUT it as the staff
record. The client's row is only ever read by that path.

## On the page

- Every card carries `data-fold-id`, a fixed name written into the markup or the template
  that renders it. A rough grep finds 97 card markers across the seven pages: Home, Scans,
  Remedies, Solutions, Learn, Billing and Account. The plan counts them exactly. A card that
  already has an `id` keeps it as its fold id.
- The toggle is added to every card with a heading. The 320px height test is removed.
- The toggle is placed at the card's top right. It sits outside the heading text, so the
  heading text no longer carries "Show" or "Hide".
- `render()` rebuilds the page on each poll. The fold state is re-applied after every
  render from the in-memory record. The existing delegated click handler stays.
- First visit to a page: when the page is not in `seen`, its first card opens and the rest
  fold. The page is then added to `seen` and saved.
- Each page header gets a Fold all button. Pressing it saves the current states into
  `before_fold_all` for that page, then folds every card. The button then reads Restore.
  Restore applies the saved states and the button reads Fold all again. Restore stays
  available until it is pressed, even if single cards were opened in between.
- Staff also see "Match client's view" in each page header. The server tells the page it
  is a staff view. Clients never receive the button.
- A folded card still shows its heading. A folded page reads as a list of contents.
- Saves are debounced by about 600ms, so a burst of clicks sends one PUT.

## Failure handling

- The page loads with its default layout, then applies the record when it arrives.
- If the GET fails, the page uses the first-visit default and tries again on the next poll.
- If a PUT fails, the fold stays on screen. The next successful save sends the full record,
  so nothing is lost beyond that one attempt.
- A malformed stored record is treated as empty and overwritten on the next save.
- The old browser-stored folds are ignored. They never worked, so nothing is migrated.
  Their `localStorage` keys are removed on load.

## Rollout

- A setting, `PORTAL_FOLDS_V2`, gates the new behaviour. It is off by default and set in
  Doppler, never on Render directly.
- `PORTAL_FOLDS_V2_EMAILS` lists portal emails to enable it for while the main setting is off.
  The first entry is a test portal with made-up data.
- Order: merge, deploy, verify the deploy landed, enable for the test portal, check it live.
  Glen looks at the test portal. Then the main setting goes on for everyone.
- While the setting is off, the old code path runs unchanged.

## Testing

Automated tests prove:

1. Every card in the portal markup and templates has a unique `data-fold-id`. A duplicate
   or missing id fails the test.
2. A saved record comes back on a new request, as it would on a second device.
3. A staff PUT never changes the client's row. The test compares the client's row before
   and after, by exact equality.
4. A client cannot read or write a staff row, and `?of=` accepts nothing but `client`.
5. Restore returns exactly the states from before Fold all.
6. An unknown token gets 404 on both routes.
7. With the setting off, the routes answer 404 and the page runs the old path.

Each guard is broken on purpose and its test watched failing before it is trusted.

A browser check renders a real portal with synthetic data on phone and desktop widths.
It opens each of the seven pages and counts toggles against cards. Zero toggles anywhere
fails the check, which is the 09-16 defect.

The change is client-facing, so it gets three blind review rounds before Glen is asked
to merge.

## Out of scope

- Layout changes, the labelled rail and one section per click. Those are Stage 2.
- The chat opening a card or another page. That is Stage 3.
- Any change to what a card contains.
