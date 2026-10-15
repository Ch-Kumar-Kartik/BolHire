import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { decodeVoiceAudio, submitVoice, VoiceApiError } from './voiceApi.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

test('posts WAV with language and session only on explicit call', async () => {
  let calls = 0
  globalThis.fetch = async (_url, options) => {
    calls += 1
    const form = options?.body as FormData
    assert.equal(form.get('language'), 'hi-IN')
    assert.equal(form.get('session_id'), 'session-1')
    assert.equal((form.get('audio') as File).type, 'audio/wav')
    return new Response(JSON.stringify({
      session_id: 'session-1', transcript: 'mock', reply: 'reply', audio: null,
      timings_ms: { stt: 1, llm: 2, tts: 3, total: 6 }, estimated_speech_credits: 0,
    }), { status: 200 })
  }
  assert.equal(calls, 0)
  const result = await submitVoice(new Blob(['RIFF'], { type: 'audio/wav' }), 'hi-IN', 'session-1')
  assert.equal(calls, 1)
  assert.equal(result.reply, 'reply')
})

test('reports session errors and malformed successful responses', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'SESSION_EXPIRED', message: 'Expired' } }), { status: 410 })
  await assert.rejects(submitVoice(new Blob(), 'en-IN', null), (error) => error instanceof VoiceApiError && error.code === 'SESSION_EXPIRED')
  globalThis.fetch = async () => new Response(JSON.stringify({ reply: 'missing fields' }), { status: 200 })
  await assert.rejects(submitVoice(new Blob(), 'en-IN', null), /invalid voice response/)
})

test('decodes WAV audio and rejects malformed audio', () => {
  const header = new Uint8Array(44)
  header.set([82, 73, 70, 70], 0)
  header.set([87, 65, 86, 69], 8)
  assert.equal(decodeVoiceAudio({ mime_type: 'audio/wav', base64: btoa(String.fromCharCode(...header)) })?.type, 'audio/wav')
  assert.throws(() => decodeVoiceAudio({ mime_type: 'audio/wav', base64: btoa('wrong') }), /invalid mock audio/)
})
