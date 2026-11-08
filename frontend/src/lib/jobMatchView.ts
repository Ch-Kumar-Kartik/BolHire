// Pure presentation helpers for job matches. No React, no network. Unknown values fall back to a safe label and never throw.

import type { CriterionResult, JobMatch, RecommendationStatus } from './jobsApi.ts'

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
  const criteria = top.criteria.filter((criterion) => criterion.state !== 'not_evaluated')
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

export function headerText(name: string | null | undefined, count: number): string {
  const safe = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0
  const noun = safe === 1 ? 'here is 1 sample job' : `here are ${safe} sample jobs`
  const trimmed = typeof name === 'string' ? name.trim() : ''
  return trimmed ? `${trimmed}, ${noun}` : `${noun.charAt(0).toUpperCase()}${noun.slice(1)}`
}
