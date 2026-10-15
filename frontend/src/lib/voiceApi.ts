export type VoiceLanguage = 'en-IN' | 'hi-IN'

export interface VoiceReply {
  session_id: string
  transcript: string
  reply: string
  audio: { mime_type: 'audio/wav'; base64: string } | null
  timings_ms: { stt: number; llm: number; tts: number; total: number }
  estimated_speech_credits: 0
  warning?: string | null
}

const apiBaseUrl = (import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export async function submitVoice(
  blob: Blob,
  language: VoiceLanguage,
  sessionId: string | null,
): Promise<VoiceReply> {
  const form = new FormData()
  form.append('audio', blob, 'recording.wav')
  form.append('language', language)
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
    throw new Error('The server returned invalid mock audio.')
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isVoiceReply(value: unknown): value is VoiceReply {
  if (!isRecord(value) || !isRecord(value.timings_ms)) return false
  const audio = value.audio
  return typeof value.session_id === 'string' && value.session_id.length > 0 &&
    typeof value.transcript === 'string' && typeof value.reply === 'string' &&
    value.estimated_speech_credits === 0 &&
    ['stt', 'llm', 'tts', 'total'].every((key) => typeof (value.timings_ms as Record<string, unknown>)[key] === 'number') &&
    (audio === null || (isRecord(audio) && audio.mime_type === 'audio/wav' && typeof audio.base64 === 'string')) &&
    (value.warning === undefined || value.warning === null || typeof value.warning === 'string')
}
