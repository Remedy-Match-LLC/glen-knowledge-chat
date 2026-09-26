# Client portal: staff must confirm before acting as the client

Date: 2026-09-26. Design agreed with Glen in the platform tab ("yes"). His rule, given in the
primary tab: "require a confirmation first."

## Why

On 2026-09-26 Glen opened a client's portal from the console and clicked a welcome-call slot.
It booked a real call and emailed the client and Rae. The portal treats a staff visit exactly
like the client's own. The client never did anything.

A survey of the portal found about 80 actions that write on the client's behalf. Only three know
staff are viewing: the report-open receipt, the invoice-open receipt and the fold record.
Opening `/portal/<token>` from the console also puts the client's `rm_portal_session` cookie in
the staff browser, valid 30 days. That browser then acts as the client on other pages too.

## Glen's rules

1. Every client action a staff member takes in a client's portal asks for confirmation first.
2. A visible banner shows on every staff visit.

## Who counts as staff

A request is a staff request when it presents any console credential: the master key, a
per-user access token of any role, or the console login cookie. This is wider than
`_portal_open_is_owner()`, which leaves out VA tokens. A VA must not act as a client
unannounced either.

Staff folds are the one exception. They write the staff member's own fold record, never the
client's, so they need no confirmation.

## How the guard works

### The portal marks its own requests

The portal page wraps `window.fetch` once. Every request the page sends carries the header
`X-Portal-View: 1`. Console pages never send it. That is how the server tells "staff acting
inside a client's portal" from "staff working in the console", where staff edit client records
on purpose.

### The server refuses unconfirmed writes

A `before_request` hook runs when all three are true:

- the request is a staff request,
- it carries `X-Portal-View: 1`,
- its method is POST, PUT, PATCH or DELETE.

When they are, the hook does one of three things:

- **Exempt:** `PUT /api/portal/<token>/folds` passes untouched.
- **Background:** requests on the background list (below) get `204 No Content` without running.
  Nothing is recorded as the client, and staff are never asked.
- **Everything else:** unless the request carries `X-Staff-Confirmed: 1`, the hook answers
  `409` with `{"staff_confirm": {"action": <plain description>, "client": <display name>}}`.
  Nothing runs.

Because the rule keys on the header and method, not a route list, a client action added later
is guarded without anyone remembering to add it.

### The page asks, then re-sends

The fetch wrapper sees the `409 staff_confirm` answer. It shows an in-page dialog, never a
browser `confirm()`:

> **Do this as Peach?**
> This books a real appointment and emails Peach and Rae.
> [Do it as Peach] [Cancel]

"Do it" re-sends the same request once, with `X-Staff-Confirmed: 1`, and returns that response
to the caller as if nothing happened. "Cancel" returns the original 409 to the caller, whose
normal error handling shows that nothing happened. Each action is confirmed on its own. There
is no "confirm all" switch.

### Plain descriptions

The server holds a short map from route pattern to description, used in the dialog. The most
serious actions get specific wording:

| Route | Description |
|---|---|
| `/api/onboarding/book`, `/api/consult/book` | books a real appointment and emails {client} and Rae |
| `/api/portal/*/appointment-proposals*` | sends an appointment request or reply to the team |
| `/calendar/register` | registers {client} for a live session with Zoom |
| `/api/portal/*/checkout`, `/biofield/checkout`, `/portal/offer/*/checkout`, `*/subscribe`, `*/caregiver-pay`, `/api/community/coach-subscribe` | starts a payment as {client} |
| `*/cancel` | cancels {client}'s subscription or plan |
| `/api/portal/*/chat` | sends a chat message as {client} |
| `/api/intake/submit` | submits {client}'s intake form |
| `*/add-to-invoice`, `*/order-add`, `*/cart*`, `*/set-qty`, `*/set-format*` | changes {client}'s cart or invoice |
| `*consent*`, `*/agree-tos`, `*/sharing*`, `*/notify-pref`, `*/scan-prefs`, `*/cc-pref` | changes {client}'s consent or preferences |
| `*/photo*`, `*/documents*`, `*/purity-photo`, `*/five-voice*` | uploads a file to {client}'s record |
| `/api/coach-thread/*`, `/api/peer*`, `/api/community/coach-request` | sends a message or request as {client} to another member |

Anything not in the map reads "This changes {client}'s account." Those are the exact phrases
the dialog shows.

### Background writes are skipped for staff

These run without the client doing anything, so staff are never asked. The server skips them
for staff and answers 204:

- `POST /api/portal/<t>/cards/<k>/open` (card-opened mark)
- `POST /api/portal/<t>/process-request` (the automatic queue nudge on page load)
- `POST /api/intake/save-draft` (intake autosave)
- `POST /api/portal/<t>/recommendation-section`, `/eye-vision-state`, `/scene-pref`
  (collapse and preference toggles)

Some GET requests also write. Each checks for a staff request and skips its write:

- `GET /api/portal/<token>`: `notify_state.mark_engaged`
- `GET /api/coach-thread/member` and `GET /api/peer-thread/<id>`: `mark_read`, and thread creation
- `GET /api/peer/state`: `set_optin(False)`

### No client sign-in for staff

`GET /portal/<token>` no longer creates the client's `rm_portal_session` cookie on a staff
request. The token in the address already opens the page, so staff lose nothing. This does
not remove cookies already set in staff browsers. Those expire within 30 days, and staff can
clear them by signing out of the portal. The release note says so.

### The banner

`GET /api/portal/<token>` adds `"staff_view": {"client": <display name>}` to its answer for a
staff request. The page then shows a fixed banner at the top of every door:

> You are viewing Peach's portal as staff. Anything you do here asks first.

The banner cannot be dismissed. It sits above the shell composer and does not cover it.

## Failure handling

- The page cannot show the dialog (a script error): the caller gets the 409, the action does
  not happen, and nothing is written. The guard fails closed.
- A request without `X-Portal-View` from a staff browser is not stopped. That only happens off
  the portal page, such as the console, which is the intended place for staff edits.
- The client's own requests carry no console credential, so nothing changes for clients.

## Out of scope

- A per-visit "act as this client" unlock. Glen chose confirm-first.
- Portal pages opened by a practitioner (non-staff) account. Those are not staff requests.
- Removing client cookies already set in staff browsers.

## Testing

1. For each guarded class (booking, payment, chat, intake submit, cart, consent, upload,
   member message): a staff request with `X-Portal-View` and no confirmation gets 409, and
   nothing is written or sent. Patch every sender; assert no email, no Zoom call, no Stripe call.
2. The same request with `X-Staff-Confirmed: 1` runs normally.
3. The client's own request, without a console credential, runs normally and never sees 409.
4. A console request without `X-Portal-View` is untouched.
5. Background routes answer 204 for staff and write nothing. Assert the rows are unchanged.
6. The GETs with side effects write nothing for staff. `mark_engaged` is unchanged, and
   `mark_read` is not called.
7. `GET /portal/<token>` for staff sets no `rm_portal_session` cookie. For a client it still does.
8. A staff fold save still works without confirmation.
9. A VA token counts as staff.
10. Browser check: staff open a portal, see the banner, and click a booking slot. The dialog
    shows the booking description. Cancel leaves nothing booked, and "Do it" books once.
11. Coverage check: every non-GET `fetch(` in the portal page and its scripts goes through the
    wrapper. The wrapper is installed before any other script runs.

This is client-facing, so it gets three blind review rounds before Glen is asked to merge.
