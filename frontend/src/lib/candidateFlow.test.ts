import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { PreviewResult } from './candidateApi.ts'
import {
  canSave, CONSENT_LABEL, discardsKey, flowReducer, initialFlow, messageFor, needsFreshPreview, PROVIDER_NOTICE, type FlowAction, type FlowState,
} from './candidateFlow.ts'

const preview = (digest = 'a'.repeat(64), canSaveIt = true, version = 'candidate-profile-v1:en-IN'): PreviewResult => ({
  profile: { name: 'Asha', target_role: null, experience_years: null, experience_level: null, technical_skills: null, preferred_location: null, work_mode: null, declined_fields: [] },
  profile_digest: digest, can_save: canSaveIt, consent: { version, language: 'en-IN', retention_days: 90, text: 'notice' },
})
const run = (...actions: FlowAction[]): FlowState => actions.reduce(flowReducer, initialFlow)
const ready = (): FlowState => run(
  { type: 'previewLoaded', preview: preview() }, { type: 'keyIssued', recoveryKey: 'KEY' },
  { type: 'keyRecordedChanged', value: true }, { type: 'consentChanged', value: true })

test('saving needs a saveable preview, an issued key, the recorded confirmation and consent, all together', () => {
  assert.equal(canSave(ready()), true)
  assert.equal(canSave(initialFlow), false)
  assert.equal(canSave(run({ type: 'previewLoaded', preview: preview('a'.repeat(64), false) }, { type: 'keyIssued', recoveryKey: 'K' },
    { type: 'keyRecordedChanged', value: true }, { type: 'consentChanged', value: true })), false)
  assert.equal(canSave(run({ type: 'previewLoaded', preview: preview() }, { type: 'keyIssued', recoveryKey: 'K' }, { type: 'consentChanged', value: true })), false)
  assert.equal(canSave(run({ type: 'previewLoaded', preview: preview() }, { type: 'keyIssued', recoveryKey: 'K' }, { type: 'keyRecordedChanged', value: true })), false)
  assert.equal(canSave({ ...ready(), busy: true }), false)
  assert.equal(canSave({ ...ready(), done: { created: true } }), false)
})

test('checkboxes cannot be ticked before a key exists', () => {
  const state = run({ type: 'previewLoaded', preview: preview() }, { type: 'keyRecordedChanged', value: true }, { type: 'consentChanged', value: true })
  assert.equal(state.keyRecorded, false)
  assert.equal(state.consentAccepted, false)
})

test('a reissued key voids the earlier confirmations because the old key stops working', () => {
  const again = flowReducer(ready(), { type: 'keyIssued', recoveryKey: 'NEW' })
  assert.deepEqual([again.recoveryKey, again.keyRecorded, again.consentAccepted, canSave(again)], ['NEW', false, false, false])
})

test('changed profile content or a new notice voids consent but keeps the key', () => {
  const same = flowReducer(ready(), { type: 'previewLoaded', preview: preview() })
  assert.equal(same.consentAccepted, true)
  const edited = flowReducer(ready(), { type: 'previewLoaded', preview: preview('b'.repeat(64)) })
  assert.deepEqual([edited.consentAccepted, edited.recoveryKey, edited.keyRecorded], [false, 'KEY', true])
  const notice = flowReducer(ready(), { type: 'previewLoaded', preview: preview('a'.repeat(64), true, 'candidate-profile-v2:en-IN') })
  assert.equal(notice.consentAccepted, false)
})

test('a successful save clears the key and everything else from memory', () => {
  const done = flowReducer(ready(), { type: 'saved', created: true })
  assert.deepEqual(done, { ...initialFlow, done: { created: true } })
  assert.equal(JSON.stringify(done).includes('KEY'), false)
})

test('a failure keeps the key for a retry unless the server says the key is gone', () => {
  const kept = flowReducer({ ...ready(), busy: true }, { type: 'failed', message: 'x' })
  assert.deepEqual([kept.busy, kept.error, kept.recoveryKey, canSave(kept)], [false, 'x', 'KEY', true])
  const dropped = flowReducer(ready(), { type: 'failed', message: 'y', discardKey: true })
  assert.deepEqual([dropped.recoveryKey, dropped.keyRecorded, dropped.consentAccepted], [null, false, false])
})

test('reset forgets everything, including the key', () => {
  assert.deepEqual(flowReducer(ready(), { type: 'reset' }), initialFlow)
})

test('server error codes map to friendly, key-free messages and the right recovery action', () => {
  assert.match(messageFor('PROFILE_CHANGED', 'fallback'), /Review it again/)
  assert.equal(messageFor('SOMETHING_NEW', 'fallback'), 'fallback')
  for (const code of ['RECOVERY_KEY_NOT_ISSUED', 'RECOVERY_KEY_UNAVAILABLE', 'SESSION_NOT_FOUND', 'SESSION_EXPIRED']) assert.equal(discardsKey(code), true)
  for (const code of ['PROFILE_CHANGED', 'CANDIDATE_STORAGE_UNAVAILABLE', 'NETWORK_ERROR']) assert.equal(discardsKey(code), false)
  assert.deepEqual(['PROFILE_CHANGED', 'CONSENT_VERSION_OUTDATED', 'PROFILE_EMPTY'].map(needsFreshPreview), [true, true, false])
})

test('the provider notice and consent label exist for both languages', () => {
  for (const language of ['en-IN', 'hi-IN'] as const) {
    assert.ok(PROVIDER_NOTICE[language].text.length > 40 && PROVIDER_NOTICE[language].acknowledge && CONSENT_LABEL[language])
  }
  assert.notEqual(PROVIDER_NOTICE['en-IN'].text, PROVIDER_NOTICE['hi-IN'].text)
})
