import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  coverageText, criterionLabel, describeCriterion, emptyStateText, employmentTypeLabel, formatExperienceRange, scoreTone, workModeLabel,
} from './jobMatchView.ts'

test('experience ranges', () => {
  assert.equal(formatExperienceRange(0, 1), '0–1 years')
  assert.equal(formatExperienceRange(1, 3), '1–3 years')
  assert.equal(formatExperienceRange(5, null), '5+ years')
  assert.equal(formatExperienceRange(null, 2), 'Up to 2 years')
  assert.equal(formatExperienceRange(null, null), 'Any experience')
})

test('a minimum of 0 is a value, not a missing minimum', () => {
  assert.equal(formatExperienceRange(0, 2), '0–2 years')
  assert.notEqual(formatExperienceRange(0, 2), formatExperienceRange(null, 2))
  assert.equal(formatExperienceRange(0, null), 'Any experience')  // 0 with no maximum excludes nobody
})

test('experience range edge cases do not throw and read sensibly', () => {
  assert.equal(formatExperienceRange(2, 2), '2 years')
  assert.equal(formatExperienceRange(1, 1), '1 year')
  assert.equal(formatExperienceRange(1.5, 3), '1.5–3 years')
  assert.equal(formatExperienceRange(undefined, undefined), 'Any experience')
  assert.equal(formatExperienceRange(Number.NaN, Number.POSITIVE_INFINITY), 'Any experience')
  assert.equal(formatExperienceRange(-1, null), 'Any experience')
  assert.equal(formatExperienceRange(6, 6.0), '6 years')
})

test('work mode labels', () => {
  assert.deepEqual(['remote', 'hybrid', 'onsite'].map(workModeLabel), ['Remote', 'Hybrid', 'On-site'])
})

test('employment type labels', () => {
  assert.deepEqual(
    ['full_time', 'part_time', 'contract', 'internship'].map(employmentTypeLabel),
    ['Full-time', 'Part-time', 'Contract', 'Internship'],
  )
})

test('unknown labels fall back to a safe label without throwing', () => {
  for (const value of ['freelance', '', 'constructor', '__proto__', 'toString', null, undefined, 7, {}]) {
    assert.equal(workModeLabel(value), 'Not specified')
    assert.equal(employmentTypeLabel(value), 'Not specified')
    assert.equal(criterionLabel(value), 'Other')
  }
})

test('criterion labels', () => {
  assert.deepEqual(
    ['skills', 'role', 'experience', 'location', 'work_mode'].map(criterionLabel),
    ['Skills', 'Role', 'Experience', 'Location', 'Work mode'],
  )
})

test('describing a criterion', () => {
  assert.equal(describeCriterion({ state: 'matched', score_percent: 100, reason: null }), 'Match')
  assert.equal(describeCriterion({ state: 'partial', score_percent: 67, reason: null }), 'Partial match (67%)')
  assert.equal(describeCriterion({ state: 'partial', score_percent: 50, reason: null }), 'Partial match (50%)')
  assert.equal(describeCriterion({ state: 'unmatched', score_percent: 0, reason: null }), 'No match')
})

test('every not-evaluated reason has its own text and never shows a percent', () => {
  assert.equal(describeCriterion({ state: 'not_evaluated', score_percent: null, reason: 'missing' }), 'Not evaluated: not provided')
  assert.equal(describeCriterion({ state: 'not_evaluated', score_percent: null, reason: 'declined' }), 'Not evaluated: you chose not to share this')
  assert.equal(describeCriterion({ state: 'not_evaluated', score_percent: null, reason: 'unrecognized' }), 'Not evaluated: we could not recognise this')
  // Even if a percent were present by mistake, it must not be shown for a criterion that was not evaluated.
  const text = describeCriterion({ state: 'not_evaluated', score_percent: 80, reason: 'missing' })
  assert.equal(text.includes('%') || /\d/.test(text), false)
})

test('describing odd criteria falls back without throwing', () => {
  assert.equal(describeCriterion({ state: 'not_evaluated', score_percent: null, reason: null }), 'Not evaluated: not available')
  assert.equal(describeCriterion({ state: 'partial', score_percent: null, reason: null }), 'Partial match')
  assert.equal(describeCriterion({ state: 'weird' as never, score_percent: 5, reason: null }), 'Not available')
  assert.equal(describeCriterion({ state: 'not_evaluated', score_percent: null, reason: 'constructor' as never }), 'Not evaluated: not available')
})

test('coverage text', () => {
  assert.equal(coverageText(70), 'Based on 70% of the match criteria we could evaluate from your profile.')
  assert.equal(coverageText(100), 'Based on 100% of the match criteria we could evaluate from your profile.')
  assert.equal(coverageText(0), 'Based on 0% of the match criteria we could evaluate from your profile.')
  assert.equal(coverageText(Number.NaN), 'Based on 0% of the match criteria we could evaluate from your profile.')
  assert.equal(coverageText(250), 'Based on 100% of the match criteria we could evaluate from your profile.')
  assert.equal(coverageText(-5), 'Based on 0% of the match criteria we could evaluate from your profile.')
})

test('empty state text for each status', () => {
  assert.equal(emptyStateText('insufficient_profile'), 'Add a target role or technical skills to your profile to see matches.')
  assert.equal(emptyStateText('no_jobs'), 'No sample jobs are available right now.')
  assert.equal(emptyStateText('ok'), 'No sample jobs matched your profile.')
  assert.equal(emptyStateText('something-else'), 'No sample jobs matched your profile.')
})

test('score tones switch exactly at 70 and 40', () => {
  assert.deepEqual([100, 70, 69, 55, 40, 39, 0].map(scoreTone), ['high', 'high', 'medium', 'medium', 'medium', 'low', 'low'])
  assert.equal(scoreTone(69.9), 'medium')
  assert.equal(scoreTone(39.9), 'low')
  assert.equal(scoreTone(Number.NaN), 'low')
  assert.equal(scoreTone(-3), 'low')
})

test('no helper output contains the words apply or applied', () => {
  const outputs: string[] = []
  for (const state of ['matched', 'partial', 'unmatched', 'not_evaluated', 'weird'] as const) {
    for (const reason of [null, 'missing', 'declined', 'unrecognized'] as const) {
      outputs.push(describeCriterion({ state: state as never, score_percent: 50, reason }))
    }
  }
  for (const status of ['ok', 'insufficient_profile', 'no_jobs', 'x']) outputs.push(emptyStateText(status))
  for (const value of ['remote', 'hybrid', 'onsite', 'x']) outputs.push(workModeLabel(value))
  for (const value of ['full_time', 'part_time', 'contract', 'internship', 'x']) outputs.push(employmentTypeLabel(value))
  for (const value of ['skills', 'role', 'experience', 'location', 'work_mode', 'x']) outputs.push(criterionLabel(value))
  for (const value of [0, 50, 100]) outputs.push(coverageText(value))
  for (const [min, max] of [[0, 1], [5, null], [null, 2], [null, null]] as const) outputs.push(formatExperienceRange(min, max))
  assert.ok(outputs.length > 40)
  for (const output of outputs) assert.equal(/\bapplie[sd]?\b|\bapply\b|\bapplication/i.test(output), false, output)
})
