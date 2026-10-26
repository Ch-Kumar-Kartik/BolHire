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
  assert.equal(result.usage, undefined)
  assert.equal(result.estimated_speech_cost_inr, undefined)
})

test('accepts usage and estimated cost for Gnani and mock responses', async () => {
  const body = {
    session_id: 'session-1', transcript: 'नमस्ते', reply: 'नमस्ते 🌼', audio: null,
    timings_ms: { stt: 15, llm: 25, tts: 35, total: 75 }, estimated_speech_credits: null,
    usage: { audio_duration_seconds: 1, tts_characters: 8, language: 'hi-IN', speech_provider: 'gnani' },
    estimated_speech_cost_inr: { stt: 0.0075, tts: 0.0216, total: 0.0291 },
  }
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 200 })
  const result = await submitVoice(new Blob(), 'hi-IN', null)
  assert.deepEqual(result.usage, body.usage)
  assert.deepEqual(result.estimated_speech_cost_inr, body.estimated_speech_cost_inr)

  body.usage.speech_provider = 'mock'
  body.estimated_speech_credits = 0
  body.estimated_speech_cost_inr = { stt: 0, tts: 0, total: 0 }
  const mock = await submitVoice(new Blob(), 'hi-IN', null)
  assert.deepEqual(mock.estimated_speech_cost_inr, { stt: 0, tts: 0, total: 0 })
})

test('accepts unavailable TTS cost after failure and rejects malformed estimates', async () => {
  const body = {
    session_id: 'session-1', transcript: 'Hello', reply: 'Reply', audio: null,
    timings_ms: { stt: 1, llm: 2, tts: 3, total: 6 }, estimated_speech_credits: null,
    usage: { audio_duration_seconds: 0.1, tts_characters: 5, language: 'en-IN', speech_provider: 'gnani' },
    estimated_speech_cost_inr: { stt: 0.00075, tts: null, total: null },
  }
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 200 })
  assert.equal((await submitVoice(new Blob(), 'en-IN', null)).estimated_speech_cost_inr?.total, null)
  globalThis.fetch = async () => new Response(JSON.stringify({
    ...body, estimated_speech_cost_inr: { stt: -1, tts: 0, total: 0 },
  }), { status: 200 })
  await assert.rejects(submitVoice(new Blob(), 'en-IN', null), /invalid voice response/)
})

test('reports session errors and malformed successful responses', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'SESSION_EXPIRED', message: 'Expired' } }), { status: 410 })
  await assert.rejects(submitVoice(new Blob(), 'en-IN', null), (error) => error instanceof VoiceApiError && error.code === 'SESSION_EXPIRED')
  globalThis.fetch = async () => new Response(JSON.stringify({ reply: 'missing fields' }), { status: 200 })
  await assert.rejects(submitVoice(new Blob(), 'en-IN', null), /invalid voice response/)
})

test('accepts unknown credit estimates from a real speech provider', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    session_id: 'session-1', transcript: 'Hello', reply: 'Mock reply', audio: null,
    timings_ms: { stt: 1, llm: 2, tts: 3, total: 6 }, estimated_speech_credits: null,
  }), { status: 200 })
  const result = await submitVoice(new Blob(), 'en-IN', null)
  assert.equal(result.estimated_speech_credits, null)
})

test('decodes WAV audio and rejects malformed audio', () => {
  const header = new Uint8Array(44)
  header.set([82, 73, 70, 70], 0)
  header.set([87, 65, 86, 69], 8)
  assert.equal(decodeVoiceAudio({ mime_type: 'audio/wav', base64: btoa(String.fromCharCode(...header)) })?.type, 'audio/wav')
  assert.throws(() => decodeVoiceAudio({ mime_type: 'audio/wav', base64: btoa('wrong') }), /invalid audio/)
})

test('keeps normalized TTS WAV bytes and MIME type for browser playback', async () => {
  const wav = new Uint8Array(46)
  wav.set([82, 73, 70, 70, 38, 0, 0, 0, 87, 65, 86, 69], 0)
  wav.set([100, 97, 116, 97, 2, 0, 0, 0], 36)
  const blob = decodeVoiceAudio({ mime_type: 'audio/wav', base64: btoa(String.fromCharCode(...wav)) })
  assert.equal(blob?.type, 'audio/wav')
  assert.deepEqual(new Uint8Array(await blob!.arrayBuffer()), wav)
})
