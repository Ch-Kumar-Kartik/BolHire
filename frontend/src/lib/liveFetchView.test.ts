import assert from 'node:assert/strict'
import { test } from 'node:test'

import { CandidateApiError } from './candidateApi.ts'
import type { LiveFetchResult } from './jobsApi.ts'
import {
  LIVE_BUTTON_TEXT, LIVE_RUNNING_TEXT, initialLive, liveErrorText, liveReducer, liveResultText, liveStatusText, type LiveState,
} from './liveFetchView.ts'

const ok: LiveFetchResult = { status: 'ok', fetched: 25, imported_new: 20, already_present: 3, dropped: 2 }
const apiError = (code: string, status = 503) => new CandidateApiError('server message with detail', code, status)
const FORBIDDEN = /\bappl(y|ied|ies|ying|ication|ications)\b/i

test('the button and running texts are exactly as specified', () => {
  assert.equal(LIVE_BUTTON_TEXT, 'Find live jobs')
  assert.equal(LIVE_RUNNING_TEXT, 'Searching live listings… this can take up to a minute')
})

test('result texts', () => {
  assert.equal(liveResultText(ok), 'Found 25 live listings: 20 new, 3 already in your inbox. 2 listings could not be used.')
  assert.equal(liveResultText({ ...ok, fetched: 1, imported_new: 1, already_present: 0, dropped: 0 }), 'Found 1 live listing: 1 new, 0 already in your inbox.')
  assert.equal(liveResultText({ ...ok, dropped: 1 }), 'Found 25 live listings: 20 new, 3 already in your inbox. 1 listing could not be used.')
  assert.equal(liveResultText({ status: 'nothing_found', fetched: 3, imported_new: 0, already_present: 0, dropped: 3 }), 'No live listings matched your profile this time.')
})

test('each failure code has a friendly message and nothing technical leaks', () => {
  const cases: [unknown, string][] = [
    [apiError('LIVE_FETCH_NOT_CONFIGURED'), 'Live search is not set up on this server.'],
    [apiError('PROFILE_INCOMPLETE', 409), 'Add a target role and a preferred location to your profile to search live listings.'],
    [apiError('LIVE_FETCH_RATE_LIMITED', 429), 'You searched recently. Please try again later.'],
    [apiError('LIVE_FETCH_IN_PROGRESS', 409), 'A live search is already running. Please wait for it to finish.'],
    [apiError('LIVE_FETCH_LIMIT_REACHED', 429), 'The live search limit for this demo has been reached.'],
    [apiError('LIVE_FETCH_UNAVAILABLE'), 'Live search is not available right now. Please try again later.'],
    [apiError('NETWORK_ERROR', 0), 'Live search is not available right now. Please try again later.'],
    [apiError('INVALID_RESPONSE', 200), 'Live search is not available right now. Please try again later.'],
    [apiError('constructor'), 'Live search is not available right now. Please try again later.'],
    [new Error('boom: stack trace'), 'Live search is not available right now. Please try again later.'],
    [undefined, 'Live search is not available right now. Please try again later.'],
  ]
  for (const [error, expected] of cases) {
    const text = liveErrorText(error)
    assert.equal(text, expected)
    assert.equal(/server message|detail|stack|LIVE_FETCH|[A-Z]{2,}_[A-Z]/.test(text), false, text)
  }
})

test('the state moves idle -> running -> done', () => {
  const running = liveReducer(initialLive, { type: 'start' })
  assert.deepEqual(running, { phase: 'running' })
  assert.deepEqual(liveReducer(running, { type: 'succeeded', result: ok }), { phase: 'done', text: liveResultText(ok) })
})

test('the state moves running -> error with a friendly text', () => {
  const failed = liveReducer({ phase: 'running' }, { type: 'failed', error: apiError('LIVE_FETCH_LIMIT_REACHED', 429) })
  assert.deepEqual(failed, { phase: 'error', text: 'The live search limit for this demo has been reached.' })
})

test('a second start while running changes nothing (the button is single-flight)', () => {
  const running: LiveState = { phase: 'running' }
  assert.equal(liveReducer(running, { type: 'start' }), running)
})

test('a late result or failure after a reset is ignored', () => {
  assert.deepEqual(liveReducer(initialLive, { type: 'succeeded', result: ok }), initialLive)
  assert.deepEqual(liveReducer(initialLive, { type: 'failed', error: apiError('LIVE_FETCH_UNAVAILABLE') }), initialLive)
  const done: LiveState = { phase: 'done', text: 'x' }
  assert.equal(liveReducer(done, { type: 'succeeded', result: ok }), done)
})

test('reset always returns to idle', () => {
  for (const state of [{ phase: 'running' }, { phase: 'done', text: 'x' }, { phase: 'error', text: 'y' }, initialLive] as LiveState[]) {
    assert.deepEqual(liveReducer(state, { type: 'reset' }), initialLive)
  }
})

test('a new search can start after a result or an error', () => {
  assert.deepEqual(liveReducer({ phase: 'done', text: 'x' }, { type: 'start' }), { phase: 'running' })
  assert.deepEqual(liveReducer({ phase: 'error', text: 'y' }, { type: 'start' }), { phase: 'running' })
})

test('the status line shows the running text, the result or the error, and nothing when idle', () => {
  assert.equal(liveStatusText(initialLive), null)
  assert.equal(liveStatusText({ phase: 'running' }), LIVE_RUNNING_TEXT)
  assert.equal(liveStatusText({ phase: 'done', text: 'Found' }), 'Found')
  assert.equal(liveStatusText({ phase: 'error', text: 'Oops' }), 'Oops')
})

test('no live-search text contains apply, applied or application', () => {
  const outputs = [LIVE_BUTTON_TEXT, LIVE_RUNNING_TEXT, liveResultText(ok), liveResultText({ ...ok, status: 'nothing_found' }),
    ...['LIVE_FETCH_NOT_CONFIGURED', 'PROFILE_INCOMPLETE', 'LIVE_FETCH_RATE_LIMITED', 'LIVE_FETCH_IN_PROGRESS', 'LIVE_FETCH_LIMIT_REACHED', 'x']
      .map((code) => liveErrorText(apiError(code)))]
  for (const output of outputs) assert.equal(FORBIDDEN.test(output), false, output)
  assert.equal(FORBIDDEN.test('Apply now'), true)  // the check itself works
})
