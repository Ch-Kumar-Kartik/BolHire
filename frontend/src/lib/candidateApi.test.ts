import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { CandidateApiError, fetchSavedProfile, issueRecoveryKey, previewProfile, saveProfile, signIn, signOut } from './candidateApi.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

const profile = { name: 'Asha', target_role: null, experience_years: 2, experience_level: null, technical_skills: ['Python'], preferred_location: null, work_mode: null, declined_fields: ['work_mode'] }
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const KEY = 'ABCDE-FGHJK-MNPQR-STVWX-YZ012-3'

function capture(response: Response) {
  const calls: { url: string; init: RequestInit | undefined }[] = []
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return response.clone() }
  return calls
}

test('preview posts only the session and language and validates the response shape', async () => {
  const calls = capture(json({ profile, profile_digest: 'd'.repeat(64), can_save: true, consent: { version: 'v', language: 'hi-IN', retention_days: 90, text: 'notice' } }))
  const result = await previewProfile('s1', 'hi-IN')
  assert.equal(result.consent.language, 'hi-IN')
  assert.equal(calls[0].url, 'http://localhost:8000/api/v1/candidate/intake/preview')
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { session_id: 's1', language: 'hi-IN' })
  assert.equal(calls[0].init?.method, 'POST')
})

test('save sends the key, digest, version and explicit consent, and never a profile or a candidate id', async () => {
  const calls = capture(json({ created: false }))
  const result = await saveProfile({ sessionId: 's1', recoveryKey: KEY, profileDigest: 'd'.repeat(64), consentVersion: 'candidate-profile-v1:en-IN' })
  assert.deepEqual(result, { created: false })
  const body = JSON.parse(String(calls[0].init?.body))
  assert.deepEqual(Object.keys(body).sort(), ['consent_accepted', 'consent_version', 'profile_digest', 'recovery_key', 'session_id'])
  assert.equal(body.consent_accepted, true)
  assert.equal(calls[0].url.includes(KEY), false)  // the key is only ever in the body
  assert.equal(calls[0].init?.credentials, undefined)  // default same-origin credentials: no cross-origin cookie handling
})

test('issue, sign in, sign out and profile retrieval use the expected routes and methods', async () => {
  let calls = capture(json({ recovery_key: KEY }))
  assert.equal(await issueRecoveryKey('s1'), KEY)
  assert.equal(calls[0].url.endsWith('/api/v1/candidate/recovery-key'), true)
  calls = capture(json({ authenticated: true }))
  await signIn(KEY)
  assert.equal(calls[0].url.endsWith('/api/v1/auth/login'), true)
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { recovery_key: KEY })
  calls = capture(json({ authenticated: false }))
  await signOut()
  assert.equal(calls[0].url.endsWith('/api/v1/auth/logout'), true)
  calls = capture(json({ profile, expires_at: '2027-02-04T00:00:00Z', consent_version: 'v', consented_at: '2026-11-06T00:00:00Z' }))
  const saved = await fetchSavedProfile()
  assert.equal(saved.profile.name, 'Asha')
  assert.equal(calls[0].init?.method, 'GET')
  assert.equal(calls[0].init?.body, undefined)
})

test('server errors carry their code and status, and messages never contain request data', async () => {
  capture(json({ error: { code: 'PROFILE_CHANGED', message: 'The profile changed after you reviewed it.' } }, 409))
  await assert.rejects(saveProfile({ sessionId: 's', recoveryKey: KEY, profileDigest: 'd'.repeat(64), consentVersion: 'v' }), (error: unknown) => {
    assert.ok(error instanceof CandidateApiError)
    assert.deepEqual([error.code, error.status], ['PROFILE_CHANGED', 409])
    assert.equal(error.message.includes(KEY), false)
    return true
  })
  capture(json({ detail: 'weird' }, 500))
  await assert.rejects(signIn(KEY), (error: unknown) => error instanceof CandidateApiError && error.code === 'REQUEST_FAILED' && error.status === 500 && !error.message.includes(KEY))
})

test('network failures and unreadable or malformed responses are reported without leaking the key', async () => {
  globalThis.fetch = async () => { throw new TypeError(`failed to fetch with ${KEY}`) }
  await assert.rejects(signIn(KEY), (error: unknown) => error instanceof CandidateApiError && error.code === 'NETWORK_ERROR' && !error.message.includes(KEY))
  capture(new Response('<html>', { status: 200 }))
  await assert.rejects(fetchSavedProfile(), (error: unknown) => error instanceof CandidateApiError && error.code === 'UNREADABLE_RESPONSE')
  capture(json({ profile: { name: 5 }, expires_at: 'x' }))
  await assert.rejects(fetchSavedProfile(), (error: unknown) => error instanceof CandidateApiError && error.code === 'INVALID_RESPONSE')
  capture(json({ created: 'yes' }))
  await assert.rejects(saveProfile({ sessionId: 's', recoveryKey: KEY, profileDigest: 'd'.repeat(64), consentVersion: 'v' }), (error: unknown) => error instanceof CandidateApiError && error.code === 'INVALID_RESPONSE')
  capture(json({ recovery_key: 12 }))
  await assert.rejects(issueRecoveryKey('s'), (error: unknown) => error instanceof CandidateApiError && error.code === 'INVALID_RESPONSE')
})
