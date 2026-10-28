import type { VoiceWorkflow } from './conversationSelection'

export type VoiceLanguage = 'en-IN' | 'hi-IN'

export interface VoiceReply {
  session_id: string
  transcript: string
  reply: string
  audio: { mime_type: 'audio/wav'; base64: string } | null
  timings_ms: { stt: number; llm: number; tts: number; total: number }
  estimated_speech_credits: number | null
  usage?: {
    audio_duration_seconds: number
    tts_characters: number
    language: VoiceLanguage
    speech_provider: 'mock' | 'gnani'
  }
  estimated_speech_cost_inr?: {
    stt: number
    tts: number | null
    total: number | null
  }
  warning?: string | null
}

const apiBaseUrl = (import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export async function submitVoice(
  blob: Blob,
  language: VoiceLanguage,
  sessionId: string | null,
  workflow: VoiceWorkflow = 'general',
): Promise<VoiceReply> {
  const form = new FormData()
  form.append('audio', blob, 'recording.wav')
  form.append('language', language)
  form.append('workflow', workflow)
  if (sessionId) form.append('session_id', sessionId)

  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/api/v1/voice/chat`, { method: 'POST', body: form })
  } catch {
    throw new Error('Could not reach the voice server. Check that the backend is running.')
  }
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error('The server returned an unreadable response.')
  }
  if (!response.ok) {
    const error = isRecord(body) && isRecord(body.error) ? body.error : null
    const message = error && typeof error.message === 'string' ? error.message : 'Voice request failed.'
    const code = error && typeof error.code === 'string' ? error.code : ''
    throw new VoiceApiError(message, code)
  }
  if (!isVoiceReply(body)) throw new Error('The server returned an invalid voice response.')
  return body
}

export class VoiceApiError extends Error {
  readonly code: string

  constructor(message: string, code: string) {
    super(message)
    this.code = code
  }
}

export function decodeVoiceAudio(audio: VoiceReply['audio']): Blob | null {
  if (!audio) return null
  if (audio.mime_type !== 'audio/wav' || !audio.base64) {
    throw new Error('The server returned unsupported audio.')
  }
  try {
    const decoded = atob(audio.base64)
    const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0))
    if (bytes.length < 44 || String.fromCharCode(...bytes.slice(0, 4)) !== 'RIFF' ||
        String.fromCharCode(...bytes.slice(8, 12)) !== 'WAVE') {
      throw new Error('Invalid WAV')
    }
    return new Blob([bytes], { type: audio.mime_type })
  } catch {
    throw new Error('The server returned invalid audio.')
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isVoiceReply(value: unknown): value is VoiceReply {
  if (!isRecord(value) || !isRecord(value.timings_ms)) return false
  const audio = value.audio
  const usage = value.usage
  const cost = value.estimated_speech_cost_inr
  return typeof value.session_id === 'string' && value.session_id.length > 0 &&
    typeof value.transcript === 'string' && typeof value.reply === 'string' &&
    (value.estimated_speech_credits === null ||
      (typeof value.estimated_speech_credits === 'number' && Number.isFinite(value.estimated_speech_credits))) &&
    ['stt', 'llm', 'tts', 'total'].every((key) => typeof (value.timings_ms as Record<string, unknown>)[key] === 'number') &&
    (audio === null || (isRecord(audio) && audio.mime_type === 'audio/wav' && typeof audio.base64 === 'string')) &&
    (usage === undefined || (isRecord(usage) &&
      typeof usage.audio_duration_seconds === 'number' && Number.isFinite(usage.audio_duration_seconds) && usage.audio_duration_seconds > 0 &&
      typeof usage.tts_characters === 'number' && Number.isSafeInteger(usage.tts_characters) && usage.tts_characters >= 0 &&
      (usage.language === 'en-IN' || usage.language === 'hi-IN') &&
      (usage.speech_provider === 'mock' || usage.speech_provider === 'gnani'))) &&
    (cost === undefined || (isRecord(cost) &&
      typeof cost.stt === 'number' && Number.isFinite(cost.stt) && cost.stt >= 0 &&
      (cost.tts === null || (typeof cost.tts === 'number' && Number.isFinite(cost.tts) && cost.tts >= 0)) &&
      (cost.total === null || (typeof cost.total === 'number' && Number.isFinite(cost.total) && cost.total >= 0)) &&
      ((cost.tts === null && cost.total === null) || (cost.tts !== null && cost.total !== null)))) &&
    (value.warning === undefined || value.warning === null || typeof value.warning === 'string')
}
