import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { CriterionResult, JobMatch, JobSummary } from './jobsApi.ts'
import { jobMetaLine, shownWorkMode, visibleCriteria, whySuggestedText } from './jobMatchView.ts'

const sample: JobSummary = {
  id: 's', title: 'Data Analyst', company: 'Tarkashi Analytics', location_label: 'New Delhi, Delhi', work_mode: 'onsite',
  employment_type: 'full_time', min_experience_years: 1, max_experience_years: 3, required_skills: ['sql'], description: 'd',
  is_sample: true, source: null, source_url: null, retrieved_at: null,
}
const real: JobSummary = { ...sample, id: 'r', is_sample: false, source: 'linkedin', source_url: 'https://www.linkedin.com/jobs/view/1', retrieved_at: '2026-11-08T09:30:00Z' }

const criterion = (name: string, state: CriterionResult['state'], extra: Partial<CriterionResult> = {}): CriterionResult => ({
  name, weight: 10, state, score_percent: state === 'not_evaluated' ? null : 50, reason: state === 'not_evaluated' ? 'missing' : null,
  matched_skills: [], missing_skills: [], ...extra,
})
const ALL = [criterion('skills', 'partial', { missing_skills: ['excel'] }), criterion('role', 'matched'), criterion('experience', 'matched'),
  criterion('location', 'matched'), criterion('work_mode', 'matched')]
const matchOf = (job: JobSummary, criteria = ALL): JobMatch => ({ job, score_percent: 60, criteria })

test('a sample keeps its work mode on the meta line', () => {
  assert.equal(jobMetaLine(sample), 'Tarkashi Analytics · New Delhi, Delhi · On-site')
  for (const [mode, label] of [['remote', 'Remote'], ['hybrid', 'Hybrid'], ['onsite', 'On-site']]) {
    assert.equal(shownWorkMode({ is_sample: true, work_mode: mode }), label)
  }
})

test('a real listing never shows a work mode, whatever value it holds', () => {
  assert.equal(jobMetaLine(real), 'Tarkashi Analytics · New Delhi, Delhi')
  for (const mode of ['remote', 'hybrid', 'onsite', 'office', '', 'constructor']) {
    assert.equal(shownWorkMode({ is_sample: false, work_mode: mode }), null)
    const line = jobMetaLine({ ...real, work_mode: mode })
    assert.equal(/on-site|remote|hybrid|not specified/i.test(line), false, line)
  }
})

test('the meta line has no dangling separator when parts are empty', () => {
  assert.equal(jobMetaLine({ ...real, location_label: '' }), 'Tarkashi Analytics')
  assert.equal(jobMetaLine({ ...sample, location_label: '' }), 'Tarkashi Analytics · On-site')
  assert.equal(jobMetaLine({ ...real, company: '', location_label: '' }), '')
})

test('a sample keeps all five criteria', () => {
  assert.deepEqual(visibleCriteria(matchOf(sample)).map((c) => c.name), ['skills', 'role', 'experience', 'location', 'work_mode'])
})

test('a real listing hides only the work-mode criterion and keeps the order of the rest', () => {
  assert.deepEqual(visibleCriteria(matchOf(real)).map((c) => c.name), ['skills', 'role', 'experience', 'location'])
})

test('hiding the criterion does not change the input', () => {
  const match = matchOf(real)
  visibleCriteria(match)
  assert.equal(match.criteria.length, 5)
})

test('a not-evaluated work-mode criterion is hidden for a real listing too', () => {
  const criteria = [...ALL.slice(0, 4), criterion('work_mode', 'not_evaluated')]
  assert.equal(visibleCriteria(matchOf(real, criteria)).some((c) => c.name === 'work_mode'), false)
})

test('the why-suggested text mentions work mode for a sample', () => {
  assert.match(whySuggestedText([matchOf(sample)]), /work mode/)
  const weak = [...ALL.slice(0, 4), criterion('work_mode', 'unmatched')]
  assert.match(whySuggestedText([matchOf(sample, weak)]), /Weaker on .*work mode/)
})

test('the why-suggested text never mentions work mode for a real listing', () => {
  assert.equal(/work mode/i.test(whySuggestedText([matchOf(real)])), false)
  const weak = [...ALL.slice(0, 4), criterion('work_mode', 'unmatched')]
  assert.equal(/work mode/i.test(whySuggestedText([matchOf(real, weak)])), false)
})

test('the why-suggested text for a real listing is otherwise unchanged', () => {
  assert.equal(
    whySuggestedText([matchOf(real)]),
    'Closest fit: Data Analyst at Tarkashi Analytics. It matches your role, experience and location. Weaker on skills. Skills to build: excel.',
  )
})

test('a real listing whose only matched criterion was work mode reads as a partial match', () => {
  const onlyWorkMode = [criterion('skills', 'unmatched'), criterion('role', 'unmatched'), criterion('work_mode', 'matched')]
  const text = whySuggestedText([matchOf(real, onlyWorkMode)])
  assert.match(text, /It is the closest partial match we found\./)
  assert.equal(/work mode/i.test(text), false)
})
