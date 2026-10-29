# Architecture

This file records approved architectural decisions only. Source code remains the authority for behavior.

## Candidate identity and authentication (approved for Task 9.2)

- Candidates authenticate with a recovery key; there is no username, email, or phone. A lost key cannot be recovered, and this must be disclosed to users before saving is enabled.
- Only domain-separated SHA-256 verifiers of recovery keys and session tokens are stored. Sessions are opaque, database-backed, and capped at a fixed 7 days.
- Identity lifetime equals profile lifetime. Expired identities are inaccessible immediately; physical deletion is a separate scheduled job. A key whose identity expired may create a fresh identity and never restores expired data.
- Planned for Task 9.3 (not implemented): key issuance is stateless and happens before saving, so a lost save response is recovered by retrying with the same key. Identity creation must then be idempotent, never overwrite an existing profile, and replace an expired identity holding the same key hash inside the same transaction.
- Cookie authentication assumes a same-origin `/api/*` proxy through Vercel. This is the preferred target, not a confirmed deployment commitment; a cross-origin deployment would require revisiting cookies, CORS, and CSRF.
- Every state-changing auth request must carry an `Origin` header exactly matching the normalized `AUTH_TRUSTED_ORIGIN`, enforced by one router-level guard. Insecure cookies are allowed only for local http development.
- Login rate limiting is a per-client, per-process best-effort control. Its behaviour depends on the unverified deployment topology (trusted proxy hops, direct backend access); that is a deployment gate, not a decision.
- Anonymous General Chat and Candidate Intake never depend on the database or the auth cookie.
- Undecided: profile retention period and renewal rules (Task 9.3), Railway scheduling for cleanup, and the Vercel rewrite's cookie, header, upload, and timeout behavior.
