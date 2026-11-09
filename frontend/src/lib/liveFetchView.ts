// Pure state and wording for the "Find live jobs" control. No React, no network. No technical detail is ever shown.

import { CandidateApiError } from './candidateApi.ts'
import type { LiveFetchResult } from './jobsApi.ts'

export const LIVE_BUTTON_TEXT = 'Find live jobs'
export const LIVE_RUNNING_TEXT = 'Searching live listings… this can take up to a minute'

const ERROR_TEXT: Record<string, string> = {
  LIVE_FETCH_NOT_CONFIGURED: 'Live search is not set up on this server.',
  PROFILE_INCOMPLETE: 'Add a target role and a preferred location to your profile to search live listings.',
  LIVE_FETCH_RATE_LIMITED: 'You searched recently. Please try again later.',
  LIVE_FETCH_IN_PROGRESS: 'A live search is already running. Please wait for it to finish.',
  LIVE_FETCH_LIMIT_REACHED: 'The live search limit for this demo has been reached.',
}
const FALLBACK_ERROR = 'Live search is not available right now. Please try again later.'

function count(value: number, singular: string, plural: string): string {
  return `${value} ${value === 1 ? singular : plural}`
}

export function liveResultText(result: LiveFetchResult): string {
  if (result.status === 'nothing_found') return 'No live listings matched your profile this time.'
  const parts = [`Found ${count(result.fetched, 'live listing', 'live listings')}: ${result.imported_new} new, ${result.already_present} already in your inbox.`]
  if (result.dropped > 0) parts.push(`${count(result.dropped, 'listing', 'listings')} could not be used.`)
  return parts.join(' ')
}

export function liveErrorText(error: unknown): string {
  const code = error instanceof CandidateApiError ? error.code : ''
  return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : FALLBACK_ERROR
}

export type LiveState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'done'; text: string }
  | { phase: 'error'; text: string }

export type LiveAction =
  | { type: 'start' }
  | { type: 'succeeded'; result: LiveFetchResult }
  | { type: 'failed'; error: unknown }
  | { type: 'reset' }

export const initialLive: LiveState = { phase: 'idle' }

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case 'start':
      return state.phase === 'running' ? state : { phase: 'running' }
    case 'succeeded':
      return state.phase === 'running' ? { phase: 'done', text: liveResultText(action.result) } : state
    case 'failed':
      return state.phase === 'running' ? { phase: 'error', text: liveErrorText(action.error) } : state
    case 'reset':
      return initialLive
  }
}

export function liveStatusText(state: LiveState): string | null {
  if (state.phase === 'running') return LIVE_RUNNING_TEXT
  if (state.phase === 'done' || state.phase === 'error') return state.text
  return null
}
