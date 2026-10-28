# Local candidate profile verification

For local development only. Never set `BOLHIRE_DEV_PROFILE_INSPECTION=1` in deployment or expose this server through a proxy, tunnel, or port forward. The setting defaults to disabled; only the exact value `1` registers the route. Provider selection does not enable it.

Start the local backend from `backend` with the intended provider settings loaded:

```sh
BOLHIRE_DEV_PROFILE_INSPECTION=1 uv run --env-file ../.env uvicorn app.main:app --host 127.0.0.1 --no-proxy-headers
```

Enabling this on a new server process cannot recover a previous process's sessions. Use a single worker and keep the same process running throughout verification.

After separately authorizing one billable test, submit one synthetic candidate utterance to the existing text endpoint with `workflow="candidate_intake"`. Save its returned session ID locally. Within 30 minutes, inspect it using `POST /api/v1/dev/candidate-profile` with JSON body `{"session_id":"<returned ID>"}`. For example, enter the ID without putting it in shell history or the URL:

```sh
read -r -s intake_session_id
printf '{"session_id":"%s"}' "$intake_session_id" | curl --silent --show-error --json @- http://127.0.0.1:8000/api/v1/dev/candidate-profile
unset intake_session_id
```

The response contains exactly `name`, `target_role`, `experience_years`, `experience_level`, `technical_skills`, `preferred_location`, `work_mode`, and `declined_fields`. Unknown values are null; skills and declined fields serialize as arrays when present. For the synthetic Asha fresher example, compare every field with the expected profile, including unknown numeric experience, unknown skills, and no declined fields. This independently reads the committed profile rather than inferring it from the follow-up question.

Inspection invokes no providers, exposes no conversation history, and does not refresh `last_activity`, alter the profile, or append messages. Existing lazy expiration still applies: an expired access deletes the expired session and returns 410 `SESSION_EXPIRED`; later accesses return 404 `SESSION_NOT_FOUND`. A missing session returns 404 and an active general-chat session returns 409 `SESSION_WORKFLOW_MISMATCH`.

Access requires a numeric loopback client IP (IPv4 or IPv6). Missing/nonlocal peers and requests with `Forwarded` or any `X-Forwarded-*` header receive 403. CORS is not an access control. The route is omitted from OpenAPI and successful responses use `Cache-Control: no-store`. Session IDs stay in request bodies so standard access logs do not record them. Do not enable request/response body logging or save real candidate data in test artifacts. Disable the setting after verification. In every deployment, leave `BOLHIRE_DEV_PROFILE_INSPECTION` unset or explicitly set it to `0`.
