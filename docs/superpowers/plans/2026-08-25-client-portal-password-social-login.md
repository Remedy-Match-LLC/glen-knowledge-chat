# Client Portal Password + Federated Login Plan

**Date:** 2026-08-25
**Status:** Proposed
**Scope:** A second path into an existing client's portal, alongside today's emailed magic link and durable `/portal/<token>` links.

## Outcome

Clients can open `myhealingoasis.com/portal/login` and choose:

1. Email + password
2. Continue with Google
3. Continue with Apple
4. Email me a sign-in link (the current fallback)

Microsoft and Facebook are provider-ready follow-ons, each controlled by its own feature flag. Every successful method resolves the same `people.id`, creates the existing `client_session`, and redirects to `/portal/me`. Portal content, roles, household access, pricing, and authorization remain behind the current `resolve_identity` seam.

## Decisions and guardrails

- Preserve magic-link and `/portal/<token>` access. This is an additive route, not a migration that can lock out existing clients.
- Use `people.id` as the canonical client identity. Never create a provider-specific portal or authorize from an OAuth profile alone.
- Initial providers: Google and Apple. Add Microsoft next if client demand warrants it. Add Facebook only if analytics show that it will materially reduce login friction.
- Use Authorization Code flow with OpenID Connect, state, nonce, and PKCE. Keep provider client secrets server-side.
- Do not automatically join accounts merely because two providers return the same email. Auto-link only when the provider reports a verified email and the rules below allow it; otherwise require proof through an existing signed-in session or an emailed confirmation.
- Store only provider subject identifiers and minimal display metadata. Do not request contacts, posts, friends, or other social permissions.
- Passwords are hashed with Argon2id through a maintained password-hashing library; never encrypted or stored in `people`.
- Keep login responses non-enumerating. Rate-limit by normalized email, IP, and endpoint, with escalating delays rather than permanent account lockouts.
- Rotate the portal session on every successful authentication. Revoke server-side sessions on logout, password reset, credential removal, or suspected compromise.

## Data model

Add purpose-built tables rather than extending the already broad `people` row:

### `portal_credentials`

- `person_id` — unique foreign key to `people.id`
- `password_hash`
- `password_set_at`
- `password_changed_at`
- `failed_attempts`
- `locked_until`
- `created_at`, `updated_at`

### `portal_external_identities`

- `id`
- `person_id`
- `provider` — `google`, `apple`, later `microsoft` or `facebook`
- `provider_subject` — the provider's stable `sub`; never use email as the provider key
- `email_at_link_time`
- `email_verified_at`
- `created_at`, `last_login_at`
- unique `(provider, provider_subject)`

### `portal_auth_events`

- `id`, `person_id` nullable, `normalized_email_hash` nullable
- `event` — success, failure, reset requested, provider linked/unlinked, session revoked
- `provider`, `ip_hash`, `user_agent`, `created_at`, `metadata`
- short retention policy; no raw OAuth tokens or passwords

Extend the existing `client_session` token record or replace it with a dedicated session table only if needed to support `revoked_at`, `last_seen_at`, and session inventory. Preserve compatibility with `identity_from_session()` during the migration.

## Account linking rules

This is the highest-risk part of the feature.

1. Existing signed-in client choosing “Connect Google/Apple”: link the returned provider subject directly to that `people.id` after state/nonce validation.
2. Signed-out provider login with an already-linked subject: sign in its linked person.
3. Signed-out provider login with no linked subject and a verified email matching exactly one `people` row: send a one-time confirmation to that email before linking. Do not silently link on first use.
4. No matching `people` row: do not provision from the returning-client sign-in action. Show the same neutral response and offer the existing “Create my Healing Oasis” join flow.
5. Missing/unverified provider email, duplicate/ambiguous people records, or an email mismatch: require magic-link confirmation or support review.
6. Apple private-relay email remains a provider alias. Link by Apple `sub`; never overwrite the client's canonical portal email with the relay address.
7. A client must retain at least one usable authentication method. Unlinking the last provider requires first setting a password or confirming email-link access.

## Implementation phases

### Phase 0 — Configuration and migration seam

- Add additive, idempotent migrations for the three tables and indexes.
- Add flags: `PORTAL_PASSWORD_LOGIN_ENABLED`, `PORTAL_GOOGLE_LOGIN_ENABLED`, `PORTAL_APPLE_LOGIN_ENABLED`, plus later per-provider flags.
- Add provider configuration validation at startup without crashing when a disabled provider is unconfigured.
- Add canonical callback URLs under `PORTAL_BASE_URL`; reject unexpected hosts and redirect targets.
- Add a small `dashboard/portal_auth.py` service. Routes should call this service; they should not implement hashing, linking, OAuth verification, or session issuance inline in `app.py`.

### Phase 1 — Password enrollment and recovery

- Do not let a signed-out visitor create a password merely by knowing a portal email.
- Give authenticated clients an Account & Security screen where they can set or change a password.
- Add “Create/reset password” from the login page. It always returns the same neutral message and sends a single-use, short-lived token only for an existing person.
- Confirmation page uses GET to inspect and POST to consume, matching the existing mail-scanner-safe magic-link pattern.
- Validate password length and reject known-compromised/common values. Permit password managers and paste; no composition trivia.
- On reset/change, revoke all other client sessions and issue a new current session.
- Add POST login and logout endpoints. Logout consumes/revokes the server-side session and clears `rm_portal_session` with matching cookie attributes.

### Phase 2 — Google login

- Add `/portal/auth/google/start` and `/portal/auth/google/callback`.
- Generate short-lived state, nonce, PKCE verifier, and a safe post-login destination bound to the browser transaction.
- Exchange the authorization code server-side; validate issuer, audience, signature, expiry, nonce, and verified-email claim.
- Apply the account-linking rules above, then call the same session-issuance helper used by password and magic-link login.
- Provide connect/disconnect controls on Account & Security.

### Phase 3 — Sign in with Apple

- Reuse the provider adapter contract from Google.
- Support Apple's form-post callback and private relay addresses.
- Persist the stable Apple subject; capture the name only on the first callback because Apple may not return it again.
- Validate Apple issuer, audience, signature, expiry, nonce, and state before any lookup or link.

### Phase 4 — Optional providers

- Add Microsoft through the same OpenID Connect adapter if demand exists.
- Add Facebook as a separate OAuth adapter only after confirming required permissions, data-deletion callback, privacy-policy disclosures, and app-review requirements.
- No provider may bypass the common account-linking and session-issuance services.

### Phase 5 — Login and account UI

- Update `static/client-login.html` to show password fields and provider buttons while preserving “Email me a sign-in link” and the existing new-client join block.
- Use one generic error message for bad credentials, unknown emails, disabled accounts, and unlinked providers.
- Add accessible loading, error, keyboard, and focus states. Do not expose which login methods an email has until the client proves control of the account.
- Add Account & Security inside the signed-in portal: authentication methods, add/remove method, active sessions, sign out other sessions, and recent security events.

### Phase 6 — Rollout

1. Deploy schema and inactive code with all new flags off.
2. Enable password enrollment/reset for staff test accounts, then a small client cohort.
3. Enable Google for the cohort; verify linking, logout, recovery, and cross-device behavior.
4. Enable Apple after its production callback/domain configuration is verified.
5. Expand gradually while tracking login completion, fallback-to-magic-link rate, reset volume, linking failures, and suspicious attempts.
6. Keep a one-switch rollback for each method. Disabling a method must not invalidate magic links, token URLs, or already valid client sessions unless there is a security incident.

## File-level implementation map

- `dashboard/portal_identity.py` — retain `resolve_identity`; centralize session creation, rotation, validation, and revocation without changing the returned `Identity` shape.
- New `dashboard/portal_auth.py` — credentials, resets, provider adapters, linking policy, and audit events.
- `app.py` — thin password/reset/logout/provider routes and feature flags; reuse `/portal/me` as the successful destination.
- `static/client-login.html` — multi-method sign-in UI with magic-link fallback and unchanged join separation.
- `static/client-portal.html` plus portal JavaScript — Account & Security surface.
- New migration file — tables, unique constraints, and indexes for both SQLite test/dev and PostgreSQL production behavior.
- `requirements.txt` — add the selected maintained Argon2 and OpenID Connect dependencies, pinned through the project's normal dependency process.

## Tests

### Unit

- Password hashing/verification, transparent rehash, reset expiry/one-time use, and session revocation.
- State/nonce/PKCE generation and validation; issuer/audience/signature/expiry failures.
- All seven account-linking cases, especially mismatched, unverified, relay, and ambiguous emails.
- Provider-subject uniqueness and prevention of linking one provider identity to two people.
- Safe redirect allowlist and exact cookie attributes.

### Route/integration

- Every route is 404/inert behind its own flag.
- Identical public responses for known and unknown emails.
- Password, Google, Apple, and magic link all yield a `client_session` that resolves to the same `Identity` and portal payload.
- Existing `/portal/<token>` precedence over an active session remains unchanged.
- GET does not consume reset/confirmation tokens; POST does.
- Logout and password reset make old sessions unusable.
- OAuth callback replay, state mismatch, nonce mismatch, expired code, provider outage, and user cancellation fail closed without linking.
- PostgreSQL concurrency test proves two simultaneous first-logins cannot create duplicate links or identities.

### End-to-end acceptance

- Existing client signs in with password and sees the same portal as their magic link.
- Existing client links Google while signed in, signs out, then returns with Google.
- First-time Google/Apple use cannot seize an existing portal without email confirmation.
- Apple private-relay login returns to the correct canonical person.
- Unknown visitor is directed to the separate join path without revealing whether an account exists.
- Client can recover through email even if a provider is unavailable.

## Definition of done

- At least password, Google, Apple, and magic link enter the identical portal identity/session path.
- No existing portal URL, role rule, or client record is replaced or duplicated.
- Account linking requires proof and is covered by adversarial tests.
- Sessions can be individually and globally revoked.
- Each method has an independent rollout/rollback flag and production monitoring.
- Support has a documented recovery procedure for lost provider access, changed emails, duplicates, and mistaken links.

## Recommended delivery order

Ship password enrollment/recovery first, then Google, then Apple. This produces a useful second path early, proves the shared session/linking model with the least provider complexity, and leaves the existing email-link path available throughout.
