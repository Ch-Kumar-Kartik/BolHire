import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { CriterionResult, JobMatch } from './jobsApi.ts'
import { SAMPLE_BANNER, avatarHue, companyInitial, fitLabel, headerText, whySuggestedText } from './jobMatchView.ts'

function criterion(name: string, state: CriterionResult['state'], extra: Partial<CriterionResult> = {}): CriterionResult {
  return { name, weight: 10, state, score_percent: state === 'not_evaluated' ? null : 50, reason: state === 'not_evaluated' ? 'missing' : null, matched_skills: [], missing_skills: [], ...extra }
}

function match(criteria: CriterionResult[], title = 'Data Analyst', company = 'Tarkashi Analytics'): JobMatch {
  return {
    job: { id: 'j', title, company, location_label: 'New Delhi, Delhi', work_mode: 'onsite', employment_type: 'full_time', min_experience_years: 1, max_experience_years: 3, required_skills: [], description: 'd', is_sample: true },
    score_percent: 65, criteria,
  }
}

test('fit labels switch exactly at 70 and 40', () => {
  assert.deepEqual([100, 70, 69, 40, 39, 0].map(fitLabel), ['Great fit', 'Great fit', 'Good fit', 'Good fit', 'Possible fit', 'Possible fit'])
  assert.equal(fitLabel(Number.NaN), 'Possible fit')
})

test('company initials', () => {
  assert.equal(companyInitial('tarkashi Analytics'), 'T')
  assert.equal(companyInitial('  quillgrid'), 'Q')
  assert.equal(companyInitial('ऑर्बिटवेल'), 'ऑ')
  assert.equal(companyInitial(''), '?')
  assert.equal(companyInitial('   '), '?')
  assert.equal(companyInitial(null), '?')
  assert.equal(companyInitial(42), '?')
})

test('avatar hues are deterministic, in range, and ignore case and padding', () => {
  const names = ['Kestrelbyte Technologies', 'Orbitwell Systems', 'Lumenary Digital', 'Vantaro Labs', 'Brightloom Software', '', 'ऑर्बिटवेल']
  for (const name of names) {
    const hue = avatarHue(name)
    assert.ok(Number.isInteger(hue) && hue >= 0 && hue <= 359, `${name}: ${hue}`)
    assert.equal(avatarHue(name), hue)
  }
  assert.equal(avatarHue(' Vantaro Labs '), avatarHue('vantaro labs'))
  assert.equal(new Set(names.slice(0, 5).map(avatarHue)).size > 1, true)
  assert.equal(avatarHue(undefined), 0)
})

test('why-suggested text names matched and weaker criteria and up to three missing skills', () => {
  const text = whySuggestedText([match([
    criterion('skills', 'partial', { matched_skills: ['sql'], missing_skills: ['excel', 'power bi', 'pandas', 'python'] }),
    criterion('role', 'matched'), criterion('experience', 'matched'), criterion('location', 'unmatched'), criterion('work_mode', 'not_evaluated'),
  ])])
  assert.equal(text, 'Closest fit: Data Analyst at Tarkashi Analytics. It matches your role and experience. Weaker on skills and location. Skills to build: excel, power bi and pandas.')
})

test('why-suggested text with one matched criterion and no weaker ones', () => {
  assert.equal(
    whySuggestedText([match([criterion('role', 'matched'), criterion('skills', 'not_evaluated')])]),
    'Closest fit: Data Analyst at Tarkashi Analytics. It matches your role.',
  )
})

test('why-suggested text when nothing matched outright', () => {
  assert.equal(
    whySuggestedText([match([criterion('skills', 'partial', { missing_skills: ['sql'] }), criterion('role', 'unmatched')])]),
    'Closest fit: Data Analyst at Tarkashi Analytics. It is the closest partial match we found. Weaker on skills and role. Skills to build: sql.',
  )
})

test('why-suggested text never mentions criteria that were not evaluated', () => {
  const text = whySuggestedText([match([criterion('role', 'matched'), criterion('location', 'not_evaluated'), criterion('work_mode', 'not_evaluated'), criterion('experience', 'not_evaluated')])])
  assert.equal(/location|work mode|experience/.test(text), false)
})

test('why-suggested text skips skills to build when skills were not evaluated', () => {
  const text = whySuggestedText([match([criterion('role', 'matched'), criterion('skills', 'not_evaluated', { missing_skills: ['sql'] })])])
  assert.equal(text.includes('Skills to build'), false)
})

test('why-suggested text uses only the top match and is empty without matches', () => {
  const first = match([criterion('role', 'matched')], 'First Role', 'Alpha Co')
  const second = match([criterion('skills', 'matched')], 'Second Role', 'Beta Co')
  assert.equal(whySuggestedText([first, second]).includes('Second Role'), false)
  assert.equal(whySuggestedText([]), '')
})

test('header text with and without a name, singular and plural', () => {
  assert.equal(headerText('Asha', 8), 'Asha, here are 8 sample jobs')
  assert.equal(headerText(undefined, 8), 'Here are 8 sample jobs')
  assert.equal(headerText(null, 1), 'Here is 1 sample job')
  assert.equal(headerText('Asha', 1), 'Asha, here is 1 sample job')
  assert.equal(headerText('  ', 3), 'Here are 3 sample jobs')
  assert.equal(headerText('Asha', 0), 'Asha, here are 0 sample jobs')
  assert.equal(headerText('Asha', Number.NaN), 'Asha, here are 0 sample jobs')
})

test('no new text contains apply, applied or application', () => {
  const outputs = [
    SAMPLE_BANNER, ...[0, 39, 40, 69, 70, 100].map(fitLabel), headerText('Asha', 1), headerText(undefined, 5),
    whySuggestedText([match([criterion('skills', 'partial', { missing_skills: ['sql'] }), criterion('role', 'unmatched')])]),
    whySuggestedText([match([criterion('role', 'matched'), criterion('experience', 'matched')])]),
  ]
  for (const output of outputs) assert.equal(/\bappl(y|ied|ies|ication)/i.test(output), false, output)
})
