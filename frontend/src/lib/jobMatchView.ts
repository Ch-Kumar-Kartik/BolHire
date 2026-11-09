// Pure presentation helpers for job matches. No React, no network. Unknown values fall back to a safe label and never throw.

import type { CriterionResult, JobMatch, JobSummary, RecommendationStatus } from './jobsApi.ts'

export type ScoreTone = 'high' | 'medium' | 'low'

export const SAMPLE_BANNER = 'Sample listings only. These jobs are fictional examples for demonstration. Matching is rule-based, not AI-generated, and nothing here is sent to any employer.'

const WORK_MODES: Record<string, string> = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site' }
const EMPLOYMENT_TYPES: Record<string, string> = {
  full_time: 'Full-time', part_time: 'Part-time', contract: 'Contract', internship: 'Internship',
}
const CRITERIA: Record<string, string> = {
  skills: 'Skills', role: 'Role', experience: 'Experience', location: 'Location', work_mode: 'Work mode',
}
const REASONS: Record<string, string> = {
  missing: 'not provided', declined: 'you chose not to share this', unrecognized: 'we could not recognise this',
}

function lookup(table: Record<string, string>, key: unknown, fallback: string): string {
  return typeof key === 'string' && Object.hasOwn(table, key) ? table[key] : fallback
}

function years(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function plural(count: number): string {
  return `${count} ${count === 1 ? 'year' : 'years'}`
}

export function formatExperienceRange(min: number | null | undefined, max: number | null | undefined): string {
  const low = years(min)
  const high = years(max)
  if (low === null && high === null) return 'Any experience'
  if (low === null) return `Up to ${plural(high as number)}`
  if (high === null) return low === 0 ? 'Any experience' : `${low}+ years`
  return low === high ? plural(low) : `${low}–${high} years`
}

export function workModeLabel(value: unknown): string {
  return lookup(WORK_MODES, value, 'Not specified')
}

/** The work-mode label to show, or null. A real listing's work mode is a default the import filled in (the export has no such
 * field), so it is never displayed; samples keep showing theirs. */
export function shownWorkMode(job: Pick<JobSummary, 'is_sample' | 'work_mode'>): string | null {
  return job.is_sample ? workModeLabel(job.work_mode) : null
}

/** 'Company · Location' plus the work mode for samples. Real listings never include a work mode. */
export function jobMetaLine(job: Pick<JobSummary, 'is_sample' | 'work_mode' | 'company' | 'location_label'>): string {
  return [job.company, job.location_label, shownWorkMode(job)].filter((part): part is string => typeof part === 'string' && part !== '').join(' · ')
}

/** The criteria to display. The work-mode criterion of a real listing was scored against a defaulted value, so it is not shown. */
export function visibleCriteria(match: Pick<JobMatch, 'job' | 'criteria'>): CriterionResult[] {
  return match.job.is_sample ? match.criteria : match.criteria.filter((criterion) => criterion.name !== 'work_mode')
}

export function employmentTypeLabel(value: unknown): string {
  return lookup(EMPLOYMENT_TYPES, value, 'Not specified')
}

export function criterionLabel(name: unknown): string {
  return lookup(CRITERIA, name, 'Other')
}

export function describeCriterion(criterion: Pick<CriterionResult, 'state' | 'score_percent' | 'reason'>): string {
  switch (criterion.state) {
    case 'matched':
      return 'Match'
    case 'partial':
      return typeof criterion.score_percent === 'number' ? `Partial match (${criterion.score_percent}%)` : 'Partial match'
    case 'unmatched':
      return 'No match'
    case 'not_evaluated':
      return `Not evaluated: ${lookup(REASONS, criterion.reason, 'not available')}`  // never a percent
    default:
      return 'Not available'
  }
}

export function coverageText(coverage: number): string {
  const percent = Number.isFinite(coverage) ? Math.min(100, Math.max(0, Math.round(coverage))) : 0
  return `Based on ${percent}% of the match criteria we could evaluate from your profile.`
}

export function emptyStateText(status: RecommendationStatus | string): string {
  if (status === 'insufficient_profile') return 'Add a target role or technical skills to your profile to see matches.'
  if (status === 'no_jobs') return 'No sample jobs are available right now.'
  return 'No sample jobs matched your profile.'
}

export function scoreTone(percent: number): ScoreTone {
  if (!Number.isFinite(percent)) return 'low'
  return percent >= 70 ? 'high' : percent >= 40 ? 'medium' : 'low'
}

export function fitLabel(percent: number): string {
  const tone = scoreTone(percent)
  return tone === 'high' ? 'Great fit' : tone === 'medium' ? 'Good fit' : 'Possible fit'
}

export function companyInitial(company: unknown): string {
  if (typeof company !== 'string') return '?'
  const first = Array.from(company.trim())[0]
  return first === undefined ? '?' : first.toLocaleUpperCase('en-IN')
}

/** Deterministic 0–359 hue for a company name (FNV-1a over code points, case- and space-insensitive). */
export function avatarHue(company: unknown): number {
  if (typeof company !== 'string') return 0
  let hash = 0x811c9dc5
  for (const char of company.trim().toLocaleLowerCase('en-IN')) {
    hash ^= char.codePointAt(0) ?? 0
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash % 360
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** A fixed template over the top match's own criteria. No model, no invented facts; not-evaluated criteria are never mentioned. */
export function whySuggestedText(matches: readonly JobMatch[]): string {
  const top = matches[0]
  if (top === undefined) return ''
  const criteria = visibleCriteria(top).filter((criterion) => criterion.state !== 'not_evaluated')
  const name = (criterion: CriterionResult) => criterionLabel(criterion.name).toLocaleLowerCase('en-IN')
  const matched = criteria.filter((criterion) => criterion.state === 'matched').map(name)
  const weaker = criteria.filter((criterion) => criterion.state === 'partial' || criterion.state === 'unmatched').map(name)
  const parts = [`Closest fit: ${top.job.title} at ${top.job.company}.`]
  parts.push(matched.length > 0 ? `It matches your ${joinNames(matched)}.` : 'It is the closest partial match we found.')
  if (weaker.length > 0) parts.push(`Weaker on ${joinNames(weaker)}.`)
  const skills = criteria.find((criterion) => criterion.name === 'skills')
  if (skills !== undefined && skills.missing_skills.length > 0) parts.push(`Skills to build: ${joinNames(skills.missing_skills.slice(0, 3))}.`)
  return parts.join(' ')
}

export type ListingMix = 'none' | 'sample' | 'real' | 'mixed'

const MIX_COMMON = 'Matching is rule-based, not AI-generated, and nothing here is sent to any employer.'
const BANNERS: Record<ListingMix, string> = {
  none: `Listings here are labelled as fictional samples or as real listings retrieved earlier. ${MIX_COMMON}`,
  sample: SAMPLE_BANNER,
  real: `Real listings retrieved from a public source for a local demo. They may be out of date, so check the original posting. ${MIX_COMMON}`,
  mixed: `Listings tagged Sample are fictional examples. The others are real listings retrieved earlier and may be out of date, so check the original posting. ${MIX_COMMON}`,
}
const SUBTITLES: Record<ListingMix, string> = {
  none: 'Listings matched to your saved profile',
  sample: 'Sample listings matched to your saved profile',
  real: 'Real listings matched to your saved profile',
  mixed: 'Sample and real listings matched to your saved profile',
}

/** What the shown matches are made of. Decided by `is_sample` on the listings, never by a guess from their text. */
export function listingMix(matches: readonly Pick<JobMatch, 'job'>[]): ListingMix {
  const samples = matches.filter((match) => match.job.is_sample).length
  if (matches.length === 0) return 'none'
  return samples === matches.length ? 'sample' : samples === 0 ? 'real' : 'mixed'
}

export function bannerText(mix: ListingMix): string {
  return BANNERS[mix] ?? BANNERS.none
}

export function subtitleText(mix: ListingMix): string {
  return SUBTITLES[mix] ?? SUBTITLES.none
}

export function headerText(name: string | null | undefined, count: number, mix: ListingMix = 'sample'): string {
  const safe = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0
  const kind = mix === 'sample' ? ' sample' : ''
  const noun = safe === 1 ? `here is 1${kind} job` : `here are ${safe}${kind} jobs`
  const tail = mix === 'mixed' ? ' (sample and real)' : ''
  const trimmed = typeof name === 'string' ? name.trim() : ''
  return trimmed ? `${trimmed}, ${noun}${tail}` : `${noun.charAt(0).toUpperCase()}${noun.slice(1)}${tail}`
}

const SOURCE_NAMES: Record<string, string> = { linkedin: 'LinkedIn' }
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Display name for a source key. Unknown keys are shown as readable text; blank or missing is 'Unknown source'. */
export function sourceLabel(source: unknown): string {
  if (typeof source !== 'string' || !source.trim()) return 'Unknown source'
  const key = source.trim().toLowerCase()
  if (Object.hasOwn(SOURCE_NAMES, key)) return SOURCE_NAMES[key]
  const words = key.replace(/[^a-z0-9]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Unknown source'
}

/** '2026-11-08T09:30:00Z' -> '8 Nov 2026' (UTC date, so it never depends on the viewer's locale or time zone). Invalid -> null. */
export function formatRetrievedDate(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const moment = new Date(value)
  if (Number.isNaN(moment.getTime())) return null
  return `${moment.getUTCDate()} ${MONTHS[moment.getUTCMonth()]} ${moment.getUTCFullYear()}`
}

/** The posting link, only for a real listing and only when it is a plain https URL. Anything else shows no link at all. */
export function postingUrl(job: Pick<JobSummary, 'is_sample' | 'source_url'>): string | null {
  if (job.is_sample || typeof job.source_url !== 'string') return null
  // The URL parser repairs some malformed input (https:///host becomes https://host/), so require the literal shape first.
  if (!/^https:\/\/[^/\s@]/i.test(job.source_url.trim()) || /\s/.test(job.source_url.trim())) return null
  try {
    const url = new URL(job.source_url.trim())
    return url.protocol === 'https:' && url.hostname && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

export const POSTING_LINK_TEXT = 'View original posting'

/** The tag text for a real listing's row: 'LinkedIn · Retrieved 8 Nov 2026' (the date part is omitted if it cannot be read). */
export function sourceTag(job: Pick<JobSummary, 'source' | 'retrieved_at'>): string {
  const date = formatRetrievedDate(job.retrieved_at)
  return date ? `${sourceLabel(job.source)} · Retrieved ${date}` : sourceLabel(job.source)
}
