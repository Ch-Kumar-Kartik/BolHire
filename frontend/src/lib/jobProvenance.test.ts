import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { JobMatch, JobSummary } from './jobsApi.ts'
import {
  POSTING_LINK_TEXT, SAMPLE_BANNER, bannerText, emptyStateText, formatRetrievedDate, headerText, listingMix, postingUrl, sourceLabel,
  sourceTag, subtitleText,
} from './jobMatchView.ts'

const base: JobSummary = {
  id: 'j', title: 'Backend Developer', company: 'Real Co', location_label: 'Pune', work_mode: 'hybrid', employment_type: 'full_time',
  min_experience_years: null, max_experience_years: null, required_skills: ['django'], description: 'd', is_sample: true,
  source: null, source_url: null, retrieved_at: null,
}
const real: JobSummary = { ...base, is_sample: false, source: 'linkedin', source_url: 'https://www.linkedin.com/jobs/view/1', retrieved_at: '2026-11-08T09:30:00Z' }
const matchOf = (job: JobSummary): JobMatch => ({ job, score_percent: 50, criteria: [] })
const FORBIDDEN = /\bappl(y|ied|ies|ying|ication|ications)\b/i

// ---- the mix of what is shown ---------------------------------------------------------------------------------------------------
test('the listing mix is decided by is_sample on the shown matches', () => {
  assert.equal(listingMix([]), 'none')
  assert.equal(listingMix([matchOf(base), matchOf(base)]), 'sample')
  assert.equal(listingMix([matchOf(real), matchOf(real)]), 'real')
  assert.equal(listingMix([matchOf(base), matchOf(real)]), 'mixed')
  assert.equal(listingMix([matchOf(real), matchOf(base), matchOf(real)]), 'mixed')
})

test('a sample-only mix keeps the original banner text exactly', () => {
  assert.equal(bannerText('sample'), SAMPLE_BANNER)
  assert.equal(SAMPLE_BANNER, 'Sample listings only. These jobs are fictional examples for demonstration. Matching is rule-based, not AI-generated, and nothing here is sent to any employer.')
})

test('each mix has its own banner and none says the real listings are fictional', () => {
  const mixes = ['none', 'sample', 'real', 'mixed'] as const
  assert.equal(new Set(mixes.map(bannerText)).size, 4)
  assert.equal(/fictional/i.test(bannerText('real')), false)
  assert.match(bannerText('real'), /real listings/i)
  assert.match(bannerText('real'), /may be out of date/)
  assert.match(bannerText('mixed'), /Sample are fictional/)
  assert.match(bannerText('mixed'), /may be out of date/)
  assert.equal(/fictional examples for demonstration/.test(bannerText('none')), false)
})

test('every banner keeps the rule-based and nothing-is-sent statements', () => {
  for (const mix of ['none', 'sample', 'real', 'mixed'] as const) {
    assert.match(bannerText(mix), /rule-based, not AI-generated/)
    assert.match(bannerText(mix), /nothing here is sent to any employer/)
  }
})

test('subtitles follow the mix', () => {
  assert.equal(subtitleText('sample'), 'Sample listings matched to your saved profile')
  assert.equal(subtitleText('real'), 'Real listings matched to your saved profile')
  assert.equal(subtitleText('mixed'), 'Sample and real listings matched to your saved profile')
  assert.equal(subtitleText('none'), 'Listings matched to your saved profile')
})

test('header text follows the mix and keeps its old output by default', () => {
  assert.equal(headerText('Asha', 8), 'Asha, here are 8 sample jobs')           // the pre-existing behaviour
  assert.equal(headerText('Asha', 8, 'sample'), 'Asha, here are 8 sample jobs')
  assert.equal(headerText('Asha', 8, 'real'), 'Asha, here are 8 jobs')
  assert.equal(headerText('Asha', 8, 'mixed'), 'Asha, here are 8 jobs (sample and real)')
  assert.equal(headerText(undefined, 8, 'real'), 'Here are 8 jobs')
  assert.equal(headerText(null, 8, 'mixed'), 'Here are 8 jobs (sample and real)')
  assert.equal(headerText('Asha', 1, 'real'), 'Asha, here is 1 job')
  assert.equal(headerText(null, 1, 'real'), 'Here is 1 job')
  assert.equal(headerText(null, 1, 'sample'), 'Here is 1 sample job')
})

test('a real-only header never says sample', () => {
  for (const count of [0, 1, 2, 10]) assert.equal(/sample/i.test(headerText('Asha', count, 'real')), false)
})

// ---- source label, date, tag ----------------------------------------------------------------------------------------------------
test('source labels', () => {
  assert.equal(sourceLabel('linkedin'), 'LinkedIn')
  assert.equal(sourceLabel(' LinkedIn '), 'LinkedIn')
  assert.equal(sourceLabel('job-board-x'), 'Job board x')
  assert.deepEqual([null, undefined, '', '   ', 5, {}, '---'].map(sourceLabel), Array(7).fill('Unknown source'))
  assert.equal(sourceLabel('constructor'), 'Constructor')
})

test('retrieval dates are shown as the UTC calendar date', () => {
  assert.equal(formatRetrievedDate('2026-11-08T09:30:00Z'), '8 Nov 2026')
  assert.equal(formatRetrievedDate('2026-11-08T23:59:59Z'), '8 Nov 2026')
  assert.equal(formatRetrievedDate('2026-11-09T00:00:00Z'), '9 Nov 2026')
  assert.equal(formatRetrievedDate('2026-11-08T23:30:00-05:00'), '9 Nov 2026')
  assert.equal(formatRetrievedDate('2026-01-02T00:00:00+00:00'), '2 Jan 2026')
  assert.deepEqual([null, undefined, '', 'yesterday', 20261108, {}].map(formatRetrievedDate), Array(6).fill(null))
})

test('the source tag names the source and the retrieval date', () => {
  assert.equal(sourceTag(real), 'LinkedIn · Retrieved 8 Nov 2026')
  assert.equal(sourceTag({ source: 'linkedin', retrieved_at: null }), 'LinkedIn')
  assert.equal(sourceTag({ source: 'linkedin', retrieved_at: 'not a date' }), 'LinkedIn')
  assert.equal(sourceTag({ source: null, retrieved_at: null }), 'Unknown source')
})

// ---- the posting link -----------------------------------------------------------------------------------------------------------
test('the link text is exactly "View original posting"', () => {
  assert.equal(POSTING_LINK_TEXT, 'View original posting')
})

test('a real listing with an https source_url gets a link', () => {
  assert.equal(postingUrl(real), 'https://www.linkedin.com/jobs/view/1')
  assert.equal(postingUrl({ is_sample: false, source_url: 'HTTPS://WWW.LinkedIn.com/jobs/view/1' }), 'https://www.linkedin.com/jobs/view/1')
})

test('a sample never gets a link, even if it somehow carries one', () => {
  assert.equal(postingUrl({ is_sample: true, source_url: 'https://www.linkedin.com/jobs/view/1' }), null)
  assert.equal(postingUrl(base), null)
})

test('only plain https URLs become links', () => {
  const bad = [
    'http://www.linkedin.com/jobs/view/1', 'javascript:alert(1)', 'data:text/html,<script>1</script>', 'ftp://x.example/1', 'file:///etc/passwd',
    '//www.linkedin.com/jobs/view/1', 'www.linkedin.com/jobs/view/1', 'https://', 'https:///nohost', 'https://user:pw@evil.example/1',
    'https://user@evil.example/1', '', '   ', 'not a url', null,
  ]
  for (const source_url of bad) assert.equal(postingUrl({ is_sample: false, source_url }), null, String(source_url))
})

// ---- wording rule -----------------------------------------------------------------------------------------------------------------
test('no new text contains apply, applied or application', () => {
  const outputs: string[] = [POSTING_LINK_TEXT, SAMPLE_BANNER]
  for (const mix of ['none', 'sample', 'real', 'mixed'] as const) {
    outputs.push(bannerText(mix), subtitleText(mix), headerText('Asha', 1, mix), headerText(null, 7, mix))
  }
  outputs.push(sourceTag(real), sourceTag(base), sourceLabel('linkedin'), sourceLabel('apply-board'), sourceLabel(null))
  outputs.push(emptyStateText('ok'), emptyStateText('no_jobs'), emptyStateText('insufficient_profile'))
  outputs.push(formatRetrievedDate('2026-11-08T09:30:00Z') ?? '')
  assert.ok(outputs.length > 20)
  for (const output of outputs.filter((text) => !/apply-board|Apply board/.test(text))) assert.equal(FORBIDDEN.test(output), false, output)
})

test('the forbidden-word check really detects the words', () => {
  for (const sample of ['Apply now', 'You applied', 'Application received', 'applying soon']) assert.equal(FORBIDDEN.test(sample), true, sample)
  assert.equal(FORBIDDEN.test('This does not apply'), true)
  assert.equal(FORBIDDEN.test('Sample listings'), false)
})
