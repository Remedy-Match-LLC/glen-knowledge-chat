# MyHealingOasis Deployment Runbook

A feature is complete only after it is **merged, deployed, and verified**. “Implemented”
or “committed” must never be used as a synonym for “live.”

## Release states

1. **Implemented** — code exists on a clean branch based on current `origin/main`.
2. **Tested** — focused tests, PostgreSQL-sensitive tests, and the release contract pass.
3. **Merged** — reviewed commit is reachable from `origin/main`.
4. **Deployed** — the hosting provider reports the expected Git SHA.
5. **Verified** — customer, member, and staff paths pass on the production hostname.

The release owner records the branch, commit SHA, deployment ID, and smoke-test results in
the deployment manifest. If any item is missing, the state remains incomplete.

## Required pre-merge checks

- Run the focused tests for each changed feature.
- Run `python3 -m pytest -q tests/test_surface_check.py tests/test_surface_check_flags.py`.
- For database changes, exercise SQLite and the PostgreSQL compatibility path; never
  swallow DDL errors inside a transaction or use SQLite-only `lastrowid`.
- Search customer-facing assets for deprecated destinations:
  `practicebetter.io`, `skool.com`, and `clientclub.net`.
- Review the diff from `origin/main`, not from an old feature branch.

## Required production verification

- Confirm the deployed Git SHA matches the approved commit.
- Load an individual client portal at `myhealingoasis.com` as a free user and as a paid or
  certification member.
- Confirm Upcoming Live Events renders, times localize, and gated links stay gated.
- Confirm Private Appointments loads; verify client and staff authorization separately.
- Confirm no customer-facing link routes to Practice Better, Skool, ClientClub, or a GHL
  community portal. GHL remains the communication/tagging system, not the client portal.
- Run the surface checker once immediately rather than waiting for its daily schedule.

## Independent monitoring

The daily cron runs `scripts/surface_check.py` outside the web service. It checks both HTTP
health and the deployed portal asset contract. Missing calendar/appointment code or a
deprecated destination sends an owner alert. A web service must not be its own only monitor.

## Rollback rule

If a production smoke test fails, stop promotion, record the failing deployment and route,
and restore the last verified release using the hosting provider’s recoverable rollback.
Do not patch an unidentified dirty worktree directly into production.
