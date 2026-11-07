import { CandidateApiError, request } from './candidateApi.ts'

export type RecommendationStatus = 'ok' | 'insufficient_profile' | 'no_jobs'
export type CriterionName = 'skills' | 'role' | 'experience' | 'location' | 'work_mode'
export type CriterionState = 'matched' | 'partial' | 'unmatched' | 'not_evaluated'
export type CriterionReason = 'missing' | 'declined' | 'unrecognized'

export interface JobSummary {
  id: string
  title: string
  company: string
  location_label: string
  work_mode: string
  employment_type: string
  min_experience_years: number | null
  max_experience_years: number | null
  required_skills: string[]
  description: string
  is_sample: boolean
}

export interface CriterionResult {
  name: CriterionName | string
  weight: number
  state: CriterionState
  score_percent: number | null
  reason: CriterionReason | null
  matched_skills: string[]
  missing_skills: string[]
}

export interface JobMatch {
  job: JobSummary
  score_percent: number
  criteria: CriterionResult[]
}

export interface Recommendations {
  status: RecommendationStatus
  coverage_percent: number
  sample_data_notice?: string
  matches: JobMatch[]
}

const STATUSES: readonly unknown[] = ['ok', 'insufficient_profile', 'no_jobs']
const STATES: readonly unknown[] = ['matched', 'partial', 'unmatched', 'not_evaluated']
const REASONS: readonly unknown[] = ['missing', 'declined', 'unrecognized']

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isNullableNumber = (value: unknown): boolean => value === null || isNumber(value)
const isStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string')

function isJob(value: unknown): value is JobSummary {
  return isRecord(value)
    && ['id', 'title', 'company', 'location_label', 'work_mode', 'employment_type', 'description'].every((key) => typeof value[key] === 'string')
    && isNullableNumber(value.min_experience_years) && isNullableNumber(value.max_experience_years)
    && isStringArray(value.required_skills) && typeof value.is_sample === 'boolean'
}

function isCriterion(value: unknown): value is CriterionResult {
  return isRecord(value) && typeof value.name === 'string' && isNumber(value.weight) && STATES.includes(value.state)
    && isNullableNumber(value.score_percent) && (value.reason === null || REASONS.includes(value.reason))
    && isStringArray(value.matched_skills) && isStringArray(value.missing_skills)
}

function isMatch(value: unknown): value is JobMatch {
  return isRecord(value) && isJob(value.job) && isNumber(value.score_percent)
    && Array.isArray(value.criteria) && value.criteria.every(isCriterion)
}

function isRecommendations(value: unknown): value is Recommendations {
  return isRecord(value) && STATUSES.includes(value.status) && isNumber(value.coverage_percent)
    && (value.sample_data_notice === undefined || typeof value.sample_data_notice === 'string')
    && Array.isArray(value.matches) && value.matches.every(isMatch)
}

/** A 401 means the session is gone: the caller must clear its account state, exactly as it does on sign-out. */
export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof CandidateApiError && error.status === 401
}

/** The server answered, but could not read the job catalogue. Not a network failure. */
export function isJobsUnavailableError(error: unknown): boolean {
  return error instanceof CandidateApiError && error.status === 503 && error.code === 'JOBS_UNAVAILABLE'
}

// Read-only GET: no body, no extra headers, the shared request helper's default (same-origin) credentials, like every candidate call.
export async function fetchRecommendations(): Promise<Recommendations> {
  const result = await request('GET', '/api/v1/candidate/jobs/recommendations')
  if (!isRecommendations(result)) throw new CandidateApiError('The server returned an invalid response.', 'INVALID_RESPONSE', 200)
  return result
}
