# Administrator, access tokens, and browser sessions

`SYSTEM_PAT` is the single deployment administrator. A legacy `BRAINPAT_TOKEN`
may temporarily alias it; different values fail startup with `SYSTEM_PAT_CONFLICT`.
Missing production credentials and the old default credential fail closed.
Rotate any administrator credential previously shared in chat or exposed in logs.

Brains and Workspaces are resources and contain no new credentials. Administrators
issue named scoped credentials through `POST /system/access-tokens`. Copy the
one-time response securely, then use `Authorization: Bearer` (or compatibility
`BrainPAT`) against the discovered workspace API/Agent/MCP endpoint. Listings
contain metadata only. Revocation and expiry also invalidate derived sessions.
Deleting/recreating a brain does not reactivate an old token for that name.

Existing Brain PATs temporarily authenticate only their own resource. Run
`python -m scripts.migrate_legacy_credentials` to report migration status without
printing secrets. For PostgreSQL, `--migrate BRAIN_ID` atomically moves a legacy
secret into the hash-only registry and removes the plaintext Brain representations.
This preserves the existing agent credential; it **does not rotate an exposed
secret**. Issue a new named token, update its agents, then revoke the migrated token
via `DELETE /system/access-tokens/{id}`. Do not automate rotation or deletion of
legitimate production credentials. Mongo legacy PATs require operator-managed
rotation; the atomic PostgreSQL migration tool intentionally refuses that backend.

The console exchanges its submitted access token for an opaque Redis session.
Cookies are HttpOnly, Secure, SameSite=Strict, host-only, and expire after eight
hours by default (`CONSOLE_SESSION_TTL_SECONDS`, bounded to 5 minutes–24 hours).
Use HTTPS for browser access, including local integration. Cookie-authenticated
mutations require the session CSRF value in `X-CSRF-Token`. Direct token requests
do not use CSRF. Session and OAuth records retain principal references, never the
submitted administrator or workspace token. OAuth sessions from older releases
must be reauthorized because their plaintext credential mapping is no longer read.

Canonical routes are `POST /system/brains`, `POST /system/workspaces`,
`GET /brains/{slug}/agent/capabilities`, and the workspace Native/Agent OpenAPI
URLs. `/meta/login-info` is deprecated; use `/auth/me`. Do not guess `/brains`,
`/agent/capabilities`, or workspace `/api/meta/login-info` aliases.
