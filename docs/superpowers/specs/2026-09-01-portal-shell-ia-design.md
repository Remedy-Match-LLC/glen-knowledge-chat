# Client portal shell and information architecture

**Date:** 2026-09-01
**Status:** design approved, ready for an implementation plan
**Surface:** `static/client-portal.html`, `dashboard/portal_view.py`, `dashboard/portal_onboarding.py`, `dashboard/portal_chat.py`, `dashboard/portal_concierge.py`

## Problem

The live client portal is one 7,891-line file. `PORTAL_HUB_ENABLED` is on in production, so the landing page is the hub: up to 20 tiles grouped as Understand, Act, Learn & Ask, Track, routing to 21 `[data-panel]` sections.

Three measured problems.

**One panel holds a quarter of the portal.** 50 `html +=` statements feed the `current` panel, which a client reaches by tapping a tile called My Analysis. 29 distinct cards land there, spanning the biofield report, the invoice for it, remedies, wishlist, orders, membership upsells, the Ambassador programme, Free Product Review, sharing, and caregiver payments. A client who taps My Analysis to read their scan scrolls past a wishlist and an upsell to reach it. Several of those cards also have their own tiles one tap away, so the duplication is visible to the client.

**Two taxonomies disagree.** `portal_onboarding.build_status` already computes a real three-phase journey with per-step `done` and `in_progress`: Discover What Your Body Is Saying (voice, intake, photo, biofield), Match Remedies, Accelerate Healing. The hub discards that structure and re-sorts the same objects into Understand / Act / Learn & Ask / Track, using the journey only to paint a progress bar on four tiles.

**The groups describe the software, not the client.** Understand and Act do not tell anyone where an invoice is. Of the four jobs the portal exists to serve, setup progress is spread across four tiles, the biofield report and its invoice sit inside the `current` panel, and symptom-to-product has no destination at all.

## Two regressions found while surveying

These are defects, not design opinions, and they are fixed as part of this work.

**The concierge was demoted by a flag.** `static/client-portal.html` carries a comment stating the Ask Dr. Glen card was promoted to the top of the portal so it would be the first thing a client sees, always shown. The next line reads `if (_hub) { _askHtml += _askCard; } else { html += _askCard; }`. With the hub on, the card is behind a tile. The intent survived in the comment; the behaviour did not.

**Two surfaces onto one conversation.** The portal ships two Ask Dr. Glen surfaces: the floating launcher at bottom right, ungated and in the DOM for every client, and the panel chat card.

An earlier draft of this spec claimed these were two separate chats and that only one reached the practice. **That was wrong, and it is retracted.** They already share everything that matters: `chatHistory` is declared once at `client-portal.html:7705` and `portal-mentor.js` mutates that same global, both POST to `/api/portal/<token>/chat`, the array is seeded from the persisted thread, and `window.syncMentorHistory` exists specifically to keep the floating panel showing the same messages. Everything a client types in either surface is persisted and reaches the practice.

What is actually wrong is narrower: two entry points render one conversation into two containers with different affordances. The floating panel owns all the voice machinery (speech recognition, spoken replies, auto-guide, continuous conversation). The card owns practitioner-reply rendering, styling a `practitioner` role that the floating panel's renderer collapses into `assistant`, so Glen's own replies look like the AI's there. Adding a third entry point at the top of the page without consolidating would make that worse.

## Decisions

Settled with Glen on 2026-09-01.

1. **A seven-item rail replaces the hub grid.** Collapsed it shows icons; clicking it opens to show labels.
2. **On a phone the rail hides behind a menu button** in a slim sticky header, and opens as an overlay drawer. Tablet and desktop keep the rail permanently on screen. A permanent 44px rail would leave 331px of content, at which width remedy names already truncate.
3. **The chat sits at the top of the page as a single 42px line** that expands into the thread, above the where-you-are banner. A standing transcript panel pushes an unpaid invoice to the fold.
4. **The panel chat is the surviving chat.** The floating launcher stops being a second brain; it becomes the collapsed state of the same thread. Its microphone, spoken replies and continuous-conversation controls move onto the panel chat.

Mockups: https://claude.ai/code/artifact/6739a837-00d0-4988-8eee-8dd2f0ebab8b

## The shell

Three ways in, matching three states a client arrives in.

| Way in | Serves |
|---|---|
| The rail | "I know what I want." Named destinations, always reachable. |
| Home | "Tell me where I am." One banner, one next action, anything unpaid or upcoming. |
| The chat | "I do not know what this is called." Anything without a tile. |

### The seven doors

| Door | Absorbs |
|---|---|
| Home | Where you are, next step, upcoming appointments, onboarding |
| Scans & Reports | 5-Element Voice, biofield analysis, healing path, what your scan matched, formulation matches, written report, audio walkthrough, personal message from Dr. Glen, scan history |
| Find Solutions | Symptom and condition finder, shop, recommendations, practitioner recommends |
| My Remedies | Your Remedies, order your remedies, wishlist, cart, Healing Oasis, Life Stress Essences, Premier Research Labs, Fullscript |
| Billing | Your invoice, options and pricing, orders, history and receipts, orders you are paying for, caregiver payments |
| Learn & Ask | Courses, Body Map, Clinical Theory brain, the full live-events calendar |
| Account | Photo, preferences, intake, clinical record, practitioner account, sharing, family notifications, referrals, Ambassador, Free Product Review, membership and offers |

### Where the existing panels go

All 21 `[data-panel]` sections keep working. The rail changes which door reveals them; `showTab()` remains the router.

| Existing panel | Door |
|---|---|
| `hub` | replaced by Home |
| `current` | split; see below |
| `voice`, `history` | Scans & Reports |
| `shop` | Find Solutions |
| `remedies`, `oasis`, `cart` | My Remedies |
| `orders` | Billing |
| `ask`, `bodymap`, `classes` | Learn & Ask |
| `account`, `photo`, `intake`, `records`, `refer`, `referrals`, `offers`, `finder` | Account |
| `calendar` | surfaced on Home, full view under Learn & Ask |

### Splitting the `current` panel

This is the bulk of the work. Each of the 29 cards moves to the door that owns it. Nothing is deleted.

- **Scans & Reports:** Preparing your Biofield Analysis, Your scan analysis, Your healing path, Your formulation matches, Your written report, Your audio walkthrough, Your personal message from Dr. Glen, Scan history, Curious what your body is asking for, Order your first Biofield Analysis, Biofield Consult
- **Billing:** Your invoice, Your options & pricing, Your orders
- **My Remedies:** Your Remedies, Order your remedies, Your wishlist, Your Life Stress Essences, Premier Research Labs options, Fullscript
- **Find Solutions:** Your practitioner recommends
- **Account:** Your practitioner account, Sharing, Family notifications, Your preferences, Free Product Review, See everything your membership unlocks, More savings ahead, Everything your membership unlocks

The two membership upsell cards ("More savings ahead", "Everything your membership unlocks") say substantially the same thing in two places on one screen today. Merging them into one card under Account is in scope.

### Home

Deliberately thin. A client who arrives with nothing outstanding should see a short page.

- The where-you-are banner, driven by `portal_onboarding.build_status`. Current phase, one next action, a progress bar. A client mid-setup sees the step list expanded; a client who has finished sees a single line.
- Time-sensitive items only: an unpaid invoice, an appointment in the next seven days, a report published since their last visit.
- Nothing else. Everything else is one click away and does not need to be shouted about on arrival.

Getting-set-up progress lives here rather than taking a rail slot.

## The chat

### Merge

`portal_chat_messages` is the single thread. The floating launcher keeps its position and becomes the collapsed state of that thread rather than a separate context. Its microphone, spoken-reply and continuous-conversation controls move to the panel chat. `portal_concierge.system_prompt` is unchanged; it already grounds answers in the client's own findings, layers and owned remedies.

### Routing is the requirement

The chat must navigate, not only reply. Asked where an invoice is, it opens Billing. It must not write a paragraph about invoices.

`showTab()` already resolves a panel name against the DOM and falls back to Home when the target is absent, so this is intent mapping onto existing destinations rather than new plumbing. The concierge returns an optional destination alongside its text; the client sees a short confirming sentence and the panel opens.

This is the load-bearing part of putting a chat at the top. A client who types a question and gets prose where they expected to arrive somewhere stops using it, and the top of every page is then spent on something they ignore. Answer quality matters less than routing accuracy.

## Find Solutions

The only door requiring new UI rather than relocation.

What exists today is `.ob-triage-form`, described in the source as a compact glaucoma-pilot triage form, rendered inline under the Match phase of the onboarding block. It is a pilot scoped to one condition, and it is not reachable as a destination. `condition_triage` and `/api/portal/<token>/triage` back it.

Find Solutions needs a real front door: enter a symptom or condition, see what supports it, order. This should be specified separately once the shell is in place, because the chat may serve the same job better for clients who would not guess the practice's word for their symptom. Building the shell first tells us how much of this load the chat already carries.

## Rollout

Behind a new flag, staged so each step is separately reversible. `PORTAL_HUB_ENABLED` stays on and unchanged until the last step, so a rollback is a flag flip rather than a revert.

1. **Rail chrome.** The rail, the phone header and drawer, and the mapping of the existing 21 panels onto seven doors. No cards move. Hub still reachable.
2. **Chat to the top.** Merge the two chats, move the composer to the top as a single line, add intent routing.
3. **Split `current`.** Move the 27 cards to their doors. Merge the duplicate upsell cards.
4. **Home.** Reduce to banner, next action and time-sensitive items.
5. **Retire the hub grid.** Flip the flag once the above is verified with a real portal.
6. **Find Solutions.** Separate spec.

Flipping a flag in production is two deploys: merge, then set the Doppler value in `prd`.

## Testing

Existing coverage to keep green: `test_portal_hub_flag.py`, `test_portal_card_state.py`, `test_client_portal_routes.py`, `test_portal_load_resilience.py`, `test_portal_library_render.py`, `test_portal_identity.py`.

New coverage:

- Every rail door resolves to a `[data-panel]` that exists in the DOM. A door pointing at an absent panel silently bounces to Home today, so assert the door set and the panel set match exactly rather than asserting each door individually.
- Each of the 29 relocated cards renders under its new door and no longer renders under `current`. Assert both halves; a card that renders in both places is the duplication this work exists to remove.
- The chat composer renders at the top of Home regardless of flag state. This is the regression that a comment claimed and the code contradicted, so it gets a test rather than a comment.
- Routing: a known intent returns its destination panel, and an unknown intent returns no destination rather than a wrong one.
- Render verification against a real portal, not a payload. Use `PORTAL_TEST_LINK` from Doppler `prd`, and check the phone drawer at 375px through the CSSOM rather than by asserting on source strings.

## Out of scope

- Restyling the portal. Tokens, cards and typography stay as they are; this changes structure only.
- The practitioner portal and affiliate portal shells.
- Any change to what `portal_view.get_portal_view` returns. The payload is sufficient; this is a presentation change. If a door needs data the payload lacks, that is a finding to raise, not to route around client-side.

## Open questions

- Does Learn & Ask hold the full chat thread, or does the top-of-page chat expand in place everywhere and Learn & Ask hold only courses, Body Map and the brain? Leaning to the latter, so there is one chat surface rather than two again.
- Account absorbs eleven cards, which is the largest door and the least coherent. It may want a second level. Worth revisiting after step 3, when the real weight is visible.
