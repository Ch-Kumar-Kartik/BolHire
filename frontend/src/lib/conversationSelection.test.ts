import assert from 'node:assert/strict'
import { test } from 'node:test'

import { selectWorkflow } from './conversationSelection.ts'

test('switching modes drops the previous session', () => {
  const intake = selectWorkflow({ workflow: 'general', sessionId: 'general-session' }, 'candidate_intake')
  assert.deepEqual(intake, { workflow: 'candidate_intake', sessionId: null })
  assert.deepEqual(selectWorkflow({ ...intake, sessionId: 'intake-session' }, 'general'), {
    workflow: 'general', sessionId: null,
  })
})

test('selecting the current mode keeps its session', () => {
  const current = { workflow: 'candidate_intake' as const, sessionId: 'intake-session' }
  assert.equal(selectWorkflow(current, 'candidate_intake'), current)
})
