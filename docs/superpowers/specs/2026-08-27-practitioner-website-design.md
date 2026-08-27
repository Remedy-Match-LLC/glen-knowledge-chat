# Practitioner Website - expand the public storefront into a full practice web presence - Design

**Date:** 2026-08-27
**Status:** Approved in brainstorm with Glen 2026-08-27. Not yet planned or implemented.
**Repo:** deploy-chat
**Beta tenant:** Mary Boyd, RN (`my_mary_boyd@yahoo.com`), health coach, practice grown by word-of-mouth referral.

## Problem

Practitioners have no web presence. What they have is `/p/<slug>`, a single page that renders a name, a practice name, a bio paragraph, and the profit disclosure. Mary needs a primary practice web presence: the thing she hands out on a card, that strangers find in search, and that her existing clients can text to a friend. Her clients are actively asking how they can refer people to her, and today there is nothing good to send them.

This is a **multi-tenant product where Mary is tenant #1**, not a bespoke site. `00 Projects/practitioner-panel/` shows practitioner recruiting already running in waves against CSO/AANP/ACAM lists, so every decision here is made for the fiftieth practitioner, not the first.

**Primary conversion is a consult booking with the practitioner.** Products are the secondary action. Confirmed with Glen: "both, but to Mary first."

## What exists today

- **`/p/<slug>`** (`app.py:18609`) serves `static/practitioner-storefront.html`, sets the `rm_ref` attribution cookie for 90 days, and sets `X-Robots-Tag: noindex`. `/api/p/<slug>` (`app.py:18591`) returns the payload.
- **`static/practitioner-storefront.html` is 48 lines and entirely client-side rendered.** The served HTML has an empty `<h1>`, an empty bio, and even `document.title` is assigned by JavaScript after a `fetch`.
- **`dashboard/public_surface.py`** builds the payload from `PRACTITIONER_PUBLIC_FIELDS`, a fail-closed whitelist of 12 keys. Of those, only 6 are practitioner-authored (`PROFILE_PUBLIC_FIELDS` in `dashboard/practitioner_profile.py`). `services`, `location`, `photo_url` and `logo_url` are in the payload but **never rendered on the page**.
- **`save_profile`** (`dashboard/practitioner_profile.py:102`) writes to the live `practitioners` row and stamps `profile_self_authored_at=now()` in the same UPDATE. **Save is publish. There is no draft and no review.**
- **Slugs are auto-minted, not chosen.** `_mint_affiliate_slug` (`dashboard/affiliate_dashboard.py:144`) slugifies the name and appends random hex on collision. No reserved-word check; uniqueness tested only against `affiliate_signups`, never against the route table.
- **Booking is hardcoded to Glen and Rae.** `/api/consult/availability` and `/api/consult/book` (`app.py:29071`, `app.py:29096`) pass `practitioner="glen"` as a literal, read the `GLEN_CONSULT_HOURS` constant (`app.py:428`), and branch at `app.py:29173`. `SESSION_TYPES` (`dashboard/appointment_proposals.py:6`) hardcodes two entries owned by `"glen"` and `"rae"`. The flow is gated on an EVOX token, paid membership, a paid test purchase, and a submitted intake.
- **`PUBLIC_SURFACE_ENABLED` is `true` in prd.** This surface is live, not dark.
- **Domains:** `PUBLIC_BASE_URL=https://illtowell.com` (funnel), `PORTAL_BASE_URL=https://myhealingoasis.com` (client portal). One Flask service, `glen-knowledge-chat`, serves both, with **1,050 registered routes** across ~90 distinct top-level path segments.
- **No `robots.txt` exists.** Two scoped sitemaps exist as a pattern: `/learn/sitemap.xml` (`app.py:9067`) and `/mentors/sitemap.xml` (`app.py:9136`), both bound to `PUBLIC_BASE_URL`.

## Non-goals

- **No practitioner CMS.** Arbitrary page and post authoring is explicitly out of scope. Glen has parked it as a **future upsell tier**. Do not begin building it as part of this work.
- **No testimonials and no outcome claims in v1.** This is the highest-risk content category on an indexable page carrying Remedy Match's domain, and it is the first thing a practitioner will ask for. Excluding it is a decision, not an omission.
- **No money for practitioner coaching flows through the platform.** Confirmed with Glen. Mary bills her clients however she already does. The site books time; it does not take payment for time. This also means we are **not** building a payout rail: `wallet_ledger.entry_type` is constrained by CHECK to `earn_order`, `earn_dropship`, `spend_order`, `spend_module` (`migrations/wallet.sql`), and it stays that way.
- **No changes to the existing gated consult flow.** See section 3.

---

## Section 1: Domain, URL shape, and slug governance

**Practitioner sites live on `myhealingoasis.com`.** `app.py:373` states the split explicitly: myhealingoasis.com is the client portal surface and "the practitioner portal is a different surface and stays on PUBLIC_BASE_URL." A visitor to Mary's page is a prospective **client**, so Healing Oasis is the correct brand. Mary's own admin stays on illtowell.com.

**URL shape: `myhealingoasis.com/<slug>`.** The catch-all registers **last** so every named route wins on match order, and it is **host-gated** with the existing `_on_portal_host()` helper (`app.py:392`) so it cannot shadow anything on illtowell.com. `/p/<slug>` stays alive as a permanent redirect so no already-distributed link breaks.

**Two kinds of slug: one canonical, zero or more alternates.** Confirmed with Glen 2026-08-27.

**Canonical slug** is the standard personal-name form, minted as today by `_mint_affiliate_slug`. **Mary's canonical slug is `mary-boyd`.** (`mary-boyd-rn` was an illustration in conversation, never a reservation.) The canonical slug is what `/<slug>` serves, what the canonical tag points at, and what the sitemap lists. It is **immutable after publish**, because it goes on a printed card.

**Alternate slugs** are practitioner-chosen vanity names, typically a business or practice name, and they **301 to the canonical**. They never serve content and never appear in the sitemap, so they cannot create duplicate-content competition with the canonical URL. Alternates are additive: once published, an alternate forwards forever even if the practitioner later adds another, since the whole point is that it may be in print somewhere.

**Both kinds live in one namespace** and every claim, canonical or alternate, is validated against:
1. A **reserved-word blocklist** seeded from the real top-level route segments plus a buffer of words we may want later.
2. Uniqueness across the combined set of all canonical slugs and all alternates. An alternate must not collide with another practitioner's canonical.
3. Shape: lowercase, 3-40 chars, no leading/trailing/doubled hyphens.
4. Glen's review before it goes live (profanity, impersonation, credential claims).

**Credential suffixes are verified, not self-asserted.** This rule now attaches to the alternate-claim flow, since the canonical is a plain name. An alternate like `mary-boyd-rn` puts a licensure claim in a URL on a domain Remedy Match owns. Active RN: fine and good credentialing. Lapsed, or a title implying a scope she is not practicing, and it is Remedy Match's exposure. Verify before approving any alternate carrying a credential or a protected title.

**Known inconsistency to fix:** `static/practitioner-settings.html:153` currently tells practitioners their storefront is at `illtowell.com/p/your-slug`. That contradicts `PORTAL_BASE_URL` and must be updated as part of this work.

**Required guard.** A root catch-all means any future route can silently steal a live practitioner's URL, and we would hear it from a practitioner, not from CI. Ship a test that walks the app's registered URL map and fails if any rule collides with any published slug, canonical or alternate. **Mutation-test it:** plant a deliberate collision, confirm the test goes red, then remove it. A guard never observed failing is not known to work.

---

## Section 2: Content model and publish gate

**Split draft from live.** The practitioner edits a draft, submits it, and the approved live version is what `/<slug>` renders. The gate sits at the writer, not as a filter at the reader, matching how `public_surface.py` already reasons: fail closed, never filter a private payload down.

**Mechanism general, policy conservative.** Build per-field review policy support; set the beta policy to "everything is reviewed." At one practitioner that costs Glen minutes. At fifty, relax low-risk fields (`accepting_clients`, `location`) by changing policy, not schema. Building it the other way round is how these leak.

**The whitelist stays.** Every new public field is a deliberate decision to publish something, reviewed as a privacy change, not a refactor.

**New fields beyond today's six:**
- `tagline` (~120 chars) - also seeds the page title.
- `how_i_work` - longer prose. The existing 600-char `MAX_BIO` is one paragraph and cannot carry a page doing SEO work.
- `credentials` - structured and verified, not free text. Generalizes the `-rn` problem from section 1.
- `seo_title`, `seo_description` - derived from tagline and bio by default so the practitioner never has to think about them; overridable by Glen.

**Bugs to fix in `save_profile` while we are in it:**
- **`logo_url` is a dead field.** It is read by `profile_for_slug` and sits in the public whitelist, but the UPDATE statement in `save_profile` never writes it. It can only be populated out of band today.
- **`photo_url` is unvalidated.** Accepted as a raw stripped string with no scheme or host check, so a practitioner can point their public page's image at any third-party server. On an indexed page that is an external dependency and a tracking vector on Remedy Match's domain.

**Both optional additions accepted by Glen:**
1. **Claim-language lint** over submitted drafts. It **annotates** the review queue with flagged phrasing so Glen's review is fast and consistent. It **never blocks** - a lint that blocks gets worked around.
2. **Practitioner assets uploaded to our own storage**, not hotlinked. Fixes the dead `logo_url` write and removes the third-party image dependency in the same change.

---

## Section 3: Multi-tenant booking

**The booking core is already multi-tenant. Only the callers are not.**

- `evox_bookings` already carries `practitioner TEXT NOT NULL DEFAULT 'rae'` (`dashboard/evox.py:28`).
- `ux_evox_active_slot` is a UNIQUE index on `(practitioner, start_ts) WHERE status='booked'` (`evox.py:34`). This is a correct per-practitioner double-booking guard **enforced in the database**, so it holds across processes. Adding Mary needs no schema change to get it.
- `available_slots(days, office_spec, busy, booked, now, duration_min)` (`evox.py:126`) is fully parameterized on the hours spec.
- `parse_office_hours` (`evox.py:87`) takes a string like `"1-7:09:00-17:00"`, so per-practitioner hours are a value in a row, not new logic.
- `booked_starts` (`evox.py:152`) and `rae_busy_intervals` (`evox.py:158`) both already take a `practitioner` argument.

Generalization is therefore confined to four named places: `GLEN_CONSULT_HOURS` (`app.py:428`), the `practitioner="glen"` literals in the two consult handlers, the branch at `app.py:29173`, and `SESSION_TYPES` (`appointment_proposals.py:6`). These become per-practitioner data.

**Mary's booking gets its own public route. We do NOT add a bypass flag to the gated one.** `/api/consult/book` requires an EVOX token, paid membership, a paid test purchase, and a submitted intake, in sequence. A person Mary's client just texted has none of these and no account. A bypass parameter on that route is one bad call site away from letting anyone skip the paid-test gate. The precedent is the shared `_price_cart` operator stop that 400'd 79 products at client checkout for six days. New public route, shared primitives, shared UNIQUE index, gated flow untouched.

**Per-practitioner booking data:** office-hours spec, session types (Mary needs at least one, e.g. a free 20-minute intro call), duration, medium, timezone.

**The practitioner supplies all of this herself, so this section ships a data-entry form.** Confirmed with Glen 2026-08-27: these are not values we configure on her behalf. The practitioner portal gains a booking-configuration surface covering session types (label, duration, medium), weekly office hours, timezone, and buffer/notice rules. It is subject to the same draft-and-review treatment as the profile content in section 2 only where it is publicly visible; hours and availability are operational rather than published claims and do not need Glen's approval to change.

**Calendar sync: full Google OAuth.** Glen chose this over the cheaper ICS-feed option; recorded as his decision.

What already exists: an `oauth_tokens` table keyed by `name` (rows like `glen_gmail`, `rae_gmail`, `inbox_gmail`, see `dashboard/gmail_token.py`), and `console_push_cron.py:393` already requests `calendar.readonly` and `calendar.events`. The Google Cloud project exists and calendar scopes are already in use. Per-practitioner tokens are a `name` convention, not a new storage model.

What must be built: a **web consent flow**. Existing tokens are minted by a CLI script (`scripts/gmail_reauth_full.py`) for accounts we control. Mary clicking "connect my calendar" needs a redirect endpoint, a callback with CSRF state, per-practitioner token rows, refresh handling, and revocation.

**Schedule risk we do not control.** `calendar.events` is a Google **sensitive scope**. Until the OAuth app passes Google verification, third parties can only connect as one of 100 test users, behind an "unverified app" warning, and **testing-mode refresh tokens expire after 7 days** - Mary's sync would silently die a week in. Verification needs a verified domain, a published privacy policy, and a demo video, and runs on Google's clock. Plan, confirmed with Glen 2026-08-27: **start Google verification now**, in parallel with the build, since the latency is Google's and not ours. Mary connects as a test user in the meantime so the beta is not blocked.

**Fail closed on a dead token.** If we cannot read a practitioner's busy time, stop offering slots. Do not offer them blindly. Failing open here double-books a real person.

**Timezone is a first-class requirement, not a detail.** The availability math runs through `_hst_now()` and `appointment_proposals` defaults `proposed_timezone` to `Pacific/Honolulu`. Correct for Glen; wrong for Mary and for arbitrary visitors. Slots are computed in the practitioner's timezone and rendered in the visitor's. Tests use a deliberately non-Hawaii practitioner.

**No payment means no-show cost is unbounded.** Mitigation is the whole of: confirmation email, reminder, working cancel link. `build_ics` already exists (`evox.py:206`) for the calendar invite.

---

## Section 4: Referral attribution

Glen's rule: **public referrers (no portal) get no affiliate benefit; members (have a portal) do.** The free member level carries the portal, so the dividing line is portal existence, not payment.

**Public referrer, no portal.** Nothing is written to the referral tables. The only record is `rm_ref` pointing at the practitioner. No benefit, no lineage row, no collision to solve. This is the majority case for a word-of-mouth practice and costs almost nothing to build.

**Member referrer with a portal.** A normal referral row, exactly as the system works today; benefit flows through the existing points path. Untouched.

**The attributable link IS the membership check.** Glen's framing: the link is only obtainable from the portal, so possessing one proves membership. The gate sits at **issuance**, where the portal already authenticates. There is no evaluation at reward time and no lapse rule - lapsing from paid drops to free, which still has the portal and still has the link.

**Gate on `is_member`, NOT `_is_paid_member`.** These are different predicates and confusing them inverts the rule:
- `is_member(session_id, email)` (`app.py:1312`) is **Tier-1: ToS agreed** (`journey_state.tos_agreed_at`), unioned across email and session. Its docstring calls it "deliberately distinct from the paid-coaching membership." **This is the correct gate.**
- `_is_paid_member(email)` (`app.py:6814`) is an active paid membership excluding trial, and exists to decide volume pricing. Using it would deny the link to exactly the free-tier members Glen intends to include.

Pin confirmed by Glen 2026-08-27: **all members agree to the ToS**, so ToS agreement and membership are the same population and the ambiguity raised in brainstorming is void. The gate reads `is_member`, which is also the consent gate the codebase already uses wherever it hands out individualized advice and ordering.

**Do NOT change the `referral_redemptions` primary key.** An earlier draft of this design proposed a composite `(referee_email, kind)` key. That is wrong:
- `referee_email TEXT PRIMARY KEY` (`dashboard/referrals.py:19`) is deliberate, and **pinned by existing tests**: `tests/test_referral_kind.py:33` writes a second row commented `# same referee PK`, and `tests/test_portal_referral_capture.py:30` asserts an existing ambassador lineage is `# unchanged` after a dispensary capture.
- `owner_of_referee` (`referrals.py:93`) documents the PK as the invariant that makes the two-tier L2 lineage hop unambiguous. A composite key makes it return an arbitrary row.

**Practitioner earnings were never at risk.** `settle_dispensary_l2` (`dashboard/dispensary_rewards.py:32`) states in its docstring that it "resolves the practitioner from `order['practitioner_id']` (stamped at checkout), NOT from the patient's referral row." Margin rides on the order.

**What IS at risk is durable future-order attribution.** `_capture_portal_referral` (`app.py:7479`) exists so "all this patient's future orders attribute to the practitioner," and `record_redemption` uses `INSERT OR IGNORE` (`referrals.py:71`) whose return value that caller discards. If a member-referral row wins first touch, the practitioner never gets that row and her claim on future orders falls back to the 90-day `rm_ref` cookie instead of a durable record.

**Therefore: practitioner attribution moves to its own durable record**, separate from the person-to-person lineage table. This leaves the tested invariant intact, stops overloading a lineage table with a relationship that is not lineage, and mirrors the public/member distinction. Existing `kind='dispensary_portal'` rows stay where they are - no risky migration - and the new record is backfilled from them, with precedent in `dashboard/referral_backfill.py`.

Shape of that record: one row per `(patient_email, practitioner_id)` with a first-touch `created_at`, the `source` that established it (storefront visit, booking, order), and the `order_ref` if one existed. `patient_email` is UNIQUE - a patient has one attributed practitioner at a time - which makes first-touch enforceable by the database rather than by application logic, the same way `ux_evox_active_slot` does for bookings. Writes use `INSERT OR IGNORE` and **callers must check the return value**; discarding it is precisely the bug that makes the current dropped attribution silent.

**The referral surface is a card in the client portal, not a page on the public site.** The people asking how to refer are already signed in. The card shows their link, a copy button, a share action, and an honest count of who they have referred. `get_or_create_code` already mints the code with no changes. The public site's job in this flow is only to be a landing page worth arriving at.

**Anti-abuse.** `resolve()` (`referrals.py:60`) already blocks the identical-address self-referral (`owner == ref`). Since members earn and the public does not, the incentive to self-refer via a second address rises. Spec a check beyond the identical-address case.

---

## Section 5: Indexing, previews, and the SEO surface

**Server-render the practitioner pages.** This is the highest-value single change in the build, and its primary payoff is the referral motion, not search. Link-preview bots for iMessage, WhatsApp, Facebook and Slack **do not execute JavaScript at all**. Today, when Mary's client texts her link, the preview card is blank: no name, no photo, no description. Title, meta description, Open Graph and Twitter tags, `h1`, photo and bio must all be present in the initial HTML response. The JSON API stays for portal-side surfaces that legitimately need it.

**Separate `live` from `indexed` as two independent flags.** A page is publishable and shareable the moment Glen approves it, while staying `noindex` until it clears a minimum content bar: tagline, bio, and photo present. A page carrying only a name is thin content, bad for the practitioner and a drag on the hosting domain. This also lets the beta go live and be shared long before anything is submitted to Google.

**`noindex` remains the default**, lifted only for approved, published, content-complete profiles. Drafts, pending review, scraped rows never self-authored, and the sample portal all keep it.

**Host-aware `robots.txt`**, using the `_on_portal_host()` pattern: myhealingoasis.com allows practitioner paths, illtowell.com unchanged.

**A practitioner sitemap on the portal host**, following the `render_sitemap_xml(rows, base)` pattern from `app.py:9067`/`app.py:9136`, listing only indexable profiles. It must bind to `PORTAL_BASE_URL`, not `PUBLIC_BASE_URL` - the two existing sitemaps hardcode the funnel host, and copying that would emit wrong-host URLs for every practitioner.

**Canonical tag** on every practitioner page pointing at the practitioner's **canonical** slug at `https://myhealingoasis.com/<canonical-slug>`, collapsing the legacy `/p/<slug>` path, every alternate slug, and any host duplication to one URL.

**Structured data: `Person` plus `ProfessionalService`.** Deliberately **not** `MedicalBusiness` or `Physician`. Those schema types assert a medical practice to Google in machine-readable form. For a health coach that is inaccurate and it is not a claim to emit from Remedy Match's domain. Same reasoning as credential verification in section 1.

---

## Testing strategy

- **Assert on raw HTTP response bytes, not a rendered DOM**, for every server-rendering and meta-tag assertion. A browser-driven test would pass on the JavaScript path and hide the exact defect being fixed, because the bots that matter never run it.
- **Mutation-test every guard**, not just exercise it. For each of: the slug/route collision guard, the `noindex` guard, and the `(practitioner, start_ts)` UNIQUE index - plant the violation, confirm the test goes red, then remove it.
- **Booking concurrency:** two simultaneous bookings of the same slot; exactly one succeeds.
- **Timezone:** all availability tests use a non-Hawaii practitioner and a third, different visitor timezone.
- **Membership gate:** a free-tier member gets a link, a paid member gets a link, a non-member gets none.
- **Two-party attribution:** a member referral and a practitioner attribution both survive one conversion, with neither silently dropped.
- **Publish gate:** an unapproved draft is absent from `/<slug>`, from the sitemap, and carries `noindex`.

## Rollout

**This spec is too large for a single implementation plan and must be decomposed.** Each of the five sections becomes its own plan, spec-to-plan-to-implementation, in the sequence below. Section 3 (booking) is the largest and should be split again at the OAuth boundary: multi-tenant slots and hours first, calendar sync second.

Everything ships behind flags, dark, with Mary as the only enabled tenant. Note that a flag flip in Doppler plus a merge is **two deploys**, not one.

Sequence: server-rendering and slug governance first (they unblock sharing, which is the live demand), then the publish gate, then booking, then referral attribution, then indexing last - indexing is the only step that is hard to walk back, since a page Google has crawled stays in the index after the page changes.

## Resolved in review, 2026-08-27

All four items raised at first review are settled and folded into the sections above.

1. **Mary's slug** is the standard `mary-boyd`. `mary-boyd-rn` was an example, not a reservation. She may claim an alternate, such as a practice name, which forwards to the canonical. See section 1.
2. **The `is_member` pin holds.** All members agree to the ToS, so the two populations are identical. See section 4.
3. **Booking details come from the practitioner**, so section 3 now ships a data-entry form rather than assuming we configure her hours and session types.
4. **Google OAuth verification starts now**, in parallel with the build.

## Still needed before implementation

- Nothing blocking. The remaining unknowns (Mary's actual session types, hours, and timezone) are data she enters through the form specced in section 3, not design decisions.
