# Architecture

This file records approved architectural decisions only. Source code remains the authority for behavior.

## Candidate identity and authentication (approved for Task 9.2)

- Candidates authenticate with a recovery key; there is no username, email, or phone. A lost key cannot be recovered, and this must be disclosed to users before saving is enabled.
- Only domain-separated SHA-256 verifiers of recovery keys and session tokens are stored. Sessions are opaque, database-backed, and capped at a fixed 7 days.
- Identity lifetime equals profile lifetime. Expired identities are inaccessible immediately; physical deletion is a separate scheduled job. A key whose identity expired may create a fresh identity and never restores expired data.
- Task 9.3 (demo scope): the server issues a recovery key bound to the in-memory intake session (only its hash is kept), issuance is stateless with respect to the database, and a lost save response is recovered by retrying with the same key. Saving is idempotent, never overwrites a profile, and an expired identity that cleanup has not deleted makes the key fail closed instead of being reclaimed. The profile is always taken from the server-side session, and the client cannot choose a candidate id.
- Retention is 90 days from the successful save, measured with the database clock. Login and save retries do not renew it, and there is no renewal. Expired profiles are inaccessible immediately; cleanup is physical deletion only.
- Consent is explicit, versioned, and language-specific, recorded as the notice version and a database timestamp. It is never inferred from speech or model output.
- Local development is same-origin through the Vite `/api` proxy; no CORS credential handling is used.
- Cookie authentication assumes a same-origin `/api/*` proxy through Vercel. This is the preferred target, not a confirmed deployment commitment; a cross-origin deployment would require revisiting cookies, CORS, and CSRF.
- Every state-changing auth request must carry an `Origin` header exactly matching the normalized `AUTH_TRUSTED_ORIGIN`, enforced by one router-level guard. Insecure cookies are allowed only for local http development.
- Login rate limiting is a per-client, per-process best-effort control. Its behaviour depends on the unverified deployment topology (trusted proxy hops, direct backend access); that is a deployment gate, not a decision.
- Anonymous General Chat and Candidate Intake never depend on the database or the auth cookie.
- Not production-ready: no profile deletion, editing, or renewal; pending intake sessions are in memory and assume one backend process. Undecided or unverified: Railway scheduling for cleanup, the Vercel rewrite's cookie, header, upload, and timeout behavior, and the real forwarding-header chain.
