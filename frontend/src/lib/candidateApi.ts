import { apiBaseUrl } from './apiBase.ts'
import type { VoiceLanguage } from './voiceApi'

export interface ProfileView {
  name: string | null
  target_role: string | null
  experience_years: number | null
  experience_level: string | null
  technical_skills: string[] | null
  preferred_location: string | null
  work_mode: string | null
  declined_fields: string[]
}

export interface PreviewResult {
  profile: ProfileView
  profile_digest: string
  can_save: boolean
  consent: { version: string; language: VoiceLanguage; retention_days: number; text: string }
}

export interface SavedProfile {
  profile: ProfileView
  expires_at: string
  consent_version: string
  consented_at: string
}

export class CandidateApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullable(value: unknown, type: 'string' | 'number'): boolean {
  return value === null || typeof value === type
}

function isProfileView(value: unknown): value is ProfileView {
  return isRecord(value)
    && isNullable(value.name, 'string') && isNullable(value.target_role, 'string')
    && isNullable(value.experience_years, 'number') && isNullable(value.experience_level, 'string')
    && isNullable(value.preferred_location, 'string') && isNullable(value.work_mode, 'string')
    && (value.technical_skills === null || (Array.isArray(value.technical_skills) && value.technical_skills.every((s) => typeof s === 'string')))
    && Array.isArray(value.declined_fields) && value.declined_fields.every((s) => typeof s === 'string')
}

function isPreview(value: unknown): value is PreviewResult {
  return isRecord(value) && isProfileView(value.profile) && typeof value.profile_digest === 'string' && typeof value.can_save === 'boolean'
    && isRecord(value.consent) && typeof value.consent.version === 'string' && typeof value.consent.text === 'string'
    && typeof value.consent.retention_days === 'number' && (value.consent.language === 'en-IN' || value.consent.language === 'hi-IN')
}

function isSavedProfile(value: unknown): value is SavedProfile {
  return isRecord(value) && isProfileView(value.profile) && typeof value.expires_at === 'string'
    && typeof value.consent_version === 'string' && typeof value.consented_at === 'string'
}

// Requests are same-origin (default fetch credentials): the session cookie is sent without any cross-origin cookie handling. The
// recovery key is only ever placed in a JSON body, and no error message below includes request data.
async function request(method: 'GET' | 'POST', path: string, body?: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new CandidateApiError('Could not reach the server. Check that the backend is running.', 'NETWORK_ERROR', 0)
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new CandidateApiError('The server returned an unreadable response.', 'UNREADABLE_RESPONSE', response.status)
  }
  if (!response.ok) {
    const error = isRecord(payload) && isRecord(payload.error) ? payload.error : null
    throw new CandidateApiError(
      error && typeof error.message === 'string' ? error.message : 'The request failed.',
      error && typeof error.code === 'string' ? error.code : 'REQUEST_FAILED',
      response.status,
    )
  }
  return payload
}

function invalid(): never {
  throw new CandidateApiError('The server returned an invalid response.', 'INVALID_RESPONSE', 200)
}

export async function previewProfile(sessionId: string, language: VoiceLanguage): Promise<PreviewResult> {
  const result = await request('POST', '/api/v1/candidate/intake/preview', { session_id: sessionId, language })
  return isPreview(result) ? result : invalid()
}

export async function issueRecoveryKey(sessionId: string): Promise<string> {
  const result = await request('POST', '/api/v1/candidate/recovery-key', { session_id: sessionId })
  return isRecord(result) && typeof result.recovery_key === 'string' ? result.recovery_key : invalid()
}

export interface SaveInput {
  sessionId: string
  recoveryKey: string
  profileDigest: string
  consentVersion: string
}

// Only call this after the candidate ticked the consent box: consent is never inferred or defaulted.
export async function saveProfile(input: SaveInput): Promise<{ created: boolean }> {
  const result = await request('POST', '/api/v1/candidate/profile', {
    session_id: input.sessionId, recovery_key: input.recoveryKey, profile_digest: input.profileDigest,
    consent_version: input.consentVersion, consent_accepted: true,
  })
  return isRecord(result) && typeof result.created === 'boolean' ? { created: result.created } : invalid()
}

export async function signIn(recoveryKey: string): Promise<void> {
  await request('POST', '/api/v1/auth/login', { recovery_key: recoveryKey })
}

export async function signOut(): Promise<void> {
  await request('POST', '/api/v1/auth/logout', {})
}

export async function fetchSavedProfile(): Promise<SavedProfile> {
  const result = await request('GET', '/api/v1/candidate/profile')
  return isSavedProfile(result) ? result : invalid()
}
