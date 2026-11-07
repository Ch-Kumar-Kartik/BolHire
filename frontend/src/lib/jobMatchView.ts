// Pure presentation helpers for job matches. No React, no network. Unknown values fall back to a safe label and never throw.

import type { CriterionResult, RecommendationStatus } from './jobsApi.ts'

export type ScoreTone = 'high' | 'medium' | 'low'

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
