import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { CandidateApiError } from './candidateApi.ts'
import { fetchLiveJobs, fetchRecommendations, isJobsUnavailableError, isUnauthorizedError } from './jobsApi.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

const URL_EXPECTED = 'http://localhost:8000/api/v1/candidate/jobs/recommendations'
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

const job = {
  id: 'j1', title: 'Data Analyst', company: 'Tarkashi Analytics', location_label: 'New Delhi, Delhi', work_mode: 'onsite',
  employment_type: 'full_time', min_experience_years: 1, max_experience_years: 3, required_skills: ['sql', 'excel'],
  description: 'A fictional listing.', is_sample: true, source: null, source_url: null, retrieved_at: null,
}
const criteria = [
  { name: 'skills', weight: 50, state: 'partial', score_percent: 50, reason: null, matched_skills: ['sql'], missing_skills: ['excel'] },
  { name: 'role', weight: 20, state: 'matched', score_percent: 100, reason: null, matched_skills: [], missing_skills: [] },
  { name: 'experience', weight: 15, state: 'not_evaluated', score_percent: null, reason: 'missing', matched_skills: [], missing_skills: [] },
]
const good = () => ({
  status: 'ok', coverage_percent: 85, sample_data_notice: 'Sample.', matches: [{ job: { ...job }, score_percent: 65, criteria: criteria.map((c) => ({ ...c })) }],
})

function capture(response: Response) {
  const calls: { url: string; init: RequestInit | undefined }[] = []
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return response.clone() }
  return calls
}

test('a valid response is parsed', async () => {
  capture(json(good()))
  const result = await fetchRecommendations()
  assert.equal(result.status, 'ok')
  assert.equal(result.coverage_percent, 85)
  assert.equal(result.matches[0].job.title, 'Data Analyst')
  assert.equal(result.matches[0].criteria[0].matched_skills[0], 'sql')
})

test('empty and non-ok statuses are accepted, and the notice is optional', async () => {
  for (const status of ['insufficient_profile', 'no_jobs'] as const) {
    capture(json({ status, coverage_percent: 0, matches: [] }))
    const result = await fetchRecommendations()
    assert.deepEqual([result.status, result.matches.length, result.sample_data_notice], [status, 0, undefined])
  }
})

test('the request is a plain GET to the exact URL with no body, no extra headers and the shared helper credentials', async () => {
  const calls = capture(json(good()))
  await fetchRecommendations()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, URL_EXPECTED)
  assert.equal(calls[0].init?.method, 'GET')
  assert.equal(calls[0].init?.body, undefined)
  assert.equal(calls[0].init?.headers, undefined)
  assert.equal(calls[0].init?.credentials, undefined)  // same as every other candidate call: default same-origin credentials
  assert.deepEqual(Object.keys(calls[0].init ?? {}).sort(), ['body', 'headers', 'method'])
})

const malformed: [string, (body: ReturnType<typeof good>) => unknown][] = [
  ['unknown status', (b) => ({ ...b, status: 'maybe' })],
  ['missing status', ({ status: _status, ...rest }) => rest],
  ['matches not an array', (b) => ({ ...b, matches: {} })],
  ['matches missing', ({ matches: _matches, ...rest }) => rest],
  ['coverage not a number', (b) => ({ ...b, coverage_percent: '85' })],
  ['notice not a string', (b) => ({ ...b, sample_data_notice: 5 })],
  ['a match that is not an object', (b) => ({ ...b, matches: [null] })],
  ['a job without a title', (b) => ({ ...b, matches: [{ ...b.matches[0], job: { ...job, title: undefined } }] })],
  ['a job with is_sample not boolean', (b) => ({ ...b, matches: [{ ...b.matches[0], job: { ...job, is_sample: 'yes' } }] })],
  ['a job with skills not an array', (b) => ({ ...b, matches: [{ ...b.matches[0], job: { ...job, required_skills: 'sql' } }] })],
  ['a job with skills holding a number', (b) => ({ ...b, matches: [{ ...b.matches[0], job: { ...job, required_skills: ['sql', 3] } }] })],
  ['a job with a string experience bound', (b) => ({ ...b, matches: [{ ...b.matches[0], job: { ...job, min_experience_years: '1' } }] })],
  ['score not a number', (b) => ({ ...b, matches: [{ ...b.matches[0], score_percent: null }] })],
  ['criteria not an array', (b) => ({ ...b, matches: [{ ...b.matches[0], criteria: 'x' }] })],
  ['a criterion with an unknown state', (b) => ({ ...b, matches: [{ ...b.matches[0], criteria: [{ ...criteria[0], state: 'great' }] }] })],
  ['a criterion with an unknown reason', (b) => ({ ...b, matches: [{ ...b.matches[0], criteria: [{ ...criteria[2], reason: 'because' }] }] })],
  ['a criterion with skill lists missing', (b) => ({ ...b, matches: [{ ...b.matches[0], criteria: [{ ...criteria[0], matched_skills: undefined }] }] })],
  ['a body that is an array', () => []],
  ['a body that is null', () => null],
  ['a body that is a string', () => 'ok'],
]

for (const [name, mutate] of malformed) {
  test(`a malformed body is rejected: ${name}`, async () => {
    capture(json(mutate(good())))
    await assert.rejects(fetchRecommendations(), (error: unknown) => {
      assert.ok(error instanceof CandidateApiError)
      assert.equal(error.code, 'INVALID_RESPONSE')
      return true
    })
  })
}

test('an unreadable body is rejected like the other candidate calls', async () => {
  capture(new Response('<html>', { status: 200 }))
  await assert.rejects(fetchRecommendations(), (error: unknown) => error instanceof CandidateApiError && error.code === 'UNREADABLE_RESPONSE')
})

test('a 401 surfaces as the unauthorized error', async () => {
  capture(json({ error: { code: 'UNAUTHENTICATED', message: 'Sign in to continue.' } }, 401))
  await assert.rejects(fetchRecommendations(), (error: unknown) => {
    assert.ok(error instanceof CandidateApiError)
    assert.deepEqual([error.status, error.code], [401, 'UNAUTHENTICATED'])
    assert.equal(isUnauthorizedError(error), true)
    assert.equal(isJobsUnavailableError(error), false)
    return true
  })
})

test('a 503 JOBS_UNAVAILABLE is distinguishable from a network failure and from other 503s', async () => {
  capture(json({ error: { code: 'JOBS_UNAVAILABLE', message: 'Job recommendations are temporarily unavailable.' } }, 503))
  await assert.rejects(fetchRecommendations(), (error: unknown) => {
    assert.ok(error instanceof CandidateApiError)
    assert.equal(isJobsUnavailableError(error), true)
    assert.equal(isUnauthorizedError(error), false)
    assert.notEqual(error.code, 'NETWORK_ERROR')
    return true
  })
  capture(json({ error: { code: 'CANDIDATE_STORAGE_UNAVAILABLE', message: 'x' } }, 503))
  await assert.rejects(fetchRecommendations(), (error: unknown) => isJobsUnavailableError(error) === false)
})

test('a network failure is its own error and neither unauthorized nor jobs-unavailable', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed') }
  await assert.rejects(fetchRecommendations(), (error: unknown) => {
    assert.ok(error instanceof CandidateApiError)
    assert.deepEqual([error.code, error.status], ['NETWORK_ERROR', 0])
    assert.equal(isUnauthorizedError(error), false)
    assert.equal(isJobsUnavailableError(error), false)
    return true
  })
})

test('other failures stay generic', async () => {
  capture(json({ error: { code: 'STORED_PROFILE_INVALID', message: 'x' } }, 500))
  await assert.rejects(fetchRecommendations(), (error: unknown) => isUnauthorizedError(error) === false && isJobsUnavailableError(error) === false)
  capture(json({}, 429))
  await assert.rejects(fetchRecommendations(), (error: unknown) => error instanceof CandidateApiError && error.code === 'REQUEST_FAILED')
})

test('the error helpers ignore errors that did not come from the API', () => {
  for (const value of [new Error('x'), null, undefined, 'x', { status: 401 }, { status: 503, code: 'JOBS_UNAVAILABLE' }]) {
    assert.equal(isUnauthorizedError(value), false)
    assert.equal(isJobsUnavailableError(value), false)
  }
})

// ---- provenance (Task 11b part 2) ----------------------------------------------------------------------------------------------------
const realJob = { ...job, id: 'r1', is_sample: false, source: 'linkedin', source_url: 'https://www.linkedin.com/jobs/view/4000000001', retrieved_at: '2026-11-08T09:30:00Z' }
const withJob = (changes: Record<string, unknown>) => ({ ...good(), matches: [{ ...good().matches[0], job: { ...job, ...changes } }] })

test('a real listing with its provenance is parsed', async () => {
  capture(json(withJob(realJob)))
  const result = await fetchRecommendations()
  const parsed = result.matches[0].job
  assert.deepEqual([parsed.is_sample, parsed.source, parsed.source_url, parsed.retrieved_at], [false, 'linkedin', realJob.source_url, realJob.retrieved_at])
})

test('a sample listing parses with null provenance', async () => {
  capture(json(good()))
  const parsed = (await fetchRecommendations()).matches[0].job
  assert.deepEqual([parsed.is_sample, parsed.source, parsed.source_url, parsed.retrieved_at], [true, null, null, null])
})

const malformedProvenance: [string, Record<string, unknown>][] = [
  ['source missing', { source: undefined }],
  ['source_url missing', { source_url: undefined }],
  ['retrieved_at missing', { retrieved_at: undefined }],
  ['source not a string', { ...realJob, source: 5 }],
  ['source_url not a string', { ...realJob, source_url: 5 }],
  ['retrieved_at not a string', { ...realJob, retrieved_at: 20261108 }],
  ['a sample that carries a source', { source: 'linkedin' }],
  ['a sample that carries a posting link', { source_url: 'https://www.linkedin.com/jobs/view/1' }],
  ['a sample that carries a retrieval time', { retrieved_at: '2026-11-08T09:30:00Z' }],
]

for (const [name, changes] of malformedProvenance) {
  test(`malformed provenance is rejected: ${name}`, async () => {
    capture(json(withJob(changes)))
    await assert.rejects(fetchRecommendations(), (error: unknown) => error instanceof CandidateApiError && error.code === 'INVALID_RESPONSE')
  })
}

test('a real listing with no posting link is still valid (the link is simply not shown)', async () => {
  capture(json(withJob({ ...realJob, source_url: null })))
  assert.equal((await fetchRecommendations()).matches[0].job.source_url, null)
})

// ---- live fetch (Task 12) ----------------------------------------------------------------------------------------------------------
const LIVE_URL = 'http://localhost:8000/api/v1/candidate/jobs/fetch-live'
const liveOk = { status: 'ok', fetched: 25, imported_new: 20, already_present: 3, dropped: 2 }

test('a live fetch is a POST to the exact URL with no body and no extra headers', async () => {
  const calls = capture(json(liveOk))
  assert.deepEqual(await fetchLiveJobs(), liveOk)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, LIVE_URL)
  assert.equal(calls[0].init?.method, 'POST')
  assert.equal(calls[0].init?.body, undefined)
  assert.equal(calls[0].init?.headers, undefined)
  assert.equal(calls[0].init?.credentials, undefined)
})

test('a nothing-found live result is valid', async () => {
  capture(json({ status: 'nothing_found', fetched: 0, imported_new: 0, already_present: 0, dropped: 0 }))
  assert.equal((await fetchLiveJobs()).status, 'nothing_found')
})

const badLive: [string, unknown][] = [
  ['unknown status', { ...liveOk, status: 'done' }],
  ['missing count', { status: 'ok', fetched: 1, imported_new: 1, already_present: 0 }],
  ['negative count', { ...liveOk, dropped: -1 }],
  ['fractional count', { ...liveOk, fetched: 1.5 }],
  ['string count', { ...liveOk, imported_new: '3' }],
  ['array body', []],
  ['null body', null],
]

for (const [name, body] of badLive) {
  test(`a malformed live result is rejected: ${name}`, async () => {
    capture(json(body))
    await assert.rejects(fetchLiveJobs(), (error: unknown) => error instanceof CandidateApiError && error.code === 'INVALID_RESPONSE')
  })
}

test('a 401 on a live fetch surfaces as unauthorized', async () => {
  capture(json({ error: { code: 'UNAUTHENTICATED', message: 'Sign in to continue.' } }, 401))
  await assert.rejects(fetchLiveJobs(), (error: unknown) => isUnauthorizedError(error))
})

test('live-fetch failure codes come through as API errors and are not mistaken for other failures', async () => {
  for (const [status, code] of [[503, 'LIVE_FETCH_NOT_CONFIGURED'], [503, 'LIVE_FETCH_UNAVAILABLE'], [409, 'PROFILE_INCOMPLETE'],
    [429, 'LIVE_FETCH_RATE_LIMITED'], [429, 'LIVE_FETCH_LIMIT_REACHED'], [409, 'LIVE_FETCH_IN_PROGRESS']] as const) {
    capture(json({ error: { code, message: 'x' } }, status))
    await assert.rejects(fetchLiveJobs(), (error: unknown) => error instanceof CandidateApiError && error.code === code
      && error.status === status && !isUnauthorizedError(error) && !isJobsUnavailableError(error))
  }
})

test('a network failure on a live fetch is its own error', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed') }
  await assert.rejects(fetchLiveJobs(), (error: unknown) => error instanceof CandidateApiError && error.code === 'NETWORK_ERROR')
})
