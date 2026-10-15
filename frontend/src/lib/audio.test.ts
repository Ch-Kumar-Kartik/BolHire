import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_AUDIO_BYTES,
  MAX_RECORDING_SECONDS,
  TARGET_SAMPLE_RATE,
  calculateDurationSeconds,
  calculateWavByteLength,
  encodeMonoPcmWav,
  isWithinRecordingLimits,
  resampleMono,
} from './audio.ts'

test('resamples mono audio to 16 kHz with linear interpolation', () => {
  const source = new Float32Array([0, 1, 0, -1])

  const result = resampleMono(source, 8_000, TARGET_SAMPLE_RATE)

  assert.equal(result.length, 8)
  assert.deepEqual([...result], [0, 0.5, 1, 0.5, 0, -0.5, -1, -1])
})

test('encodes a mono 16-bit PCM WAV with matching header values', () => {
  const wav = encodeMonoPcmWav(new Float32Array([-1, 0, 1]))
  const view = new DataView(wav)
  const ascii = (offset: number, length: number) =>
    String.fromCharCode(
      ...Array.from({ length }, (_, index) => view.getUint8(offset + index)),
    )

  assert.equal(wav.byteLength, 50)
  assert.equal(ascii(0, 4), 'RIFF')
  assert.equal(view.getUint32(4, true), 42)
  assert.equal(ascii(8, 4), 'WAVE')
  assert.equal(view.getUint16(20, true), 1)
  assert.equal(view.getUint16(22, true), 1)
  assert.equal(view.getUint32(24, true), TARGET_SAMPLE_RATE)
  assert.equal(view.getUint16(32, true), 2)
  assert.equal(view.getUint16(34, true), 16)
  assert.equal(ascii(36, 4), 'data')
  assert.equal(view.getUint32(40, true), 6)
  assert.deepEqual(
    [view.getInt16(44, true), view.getInt16(46, true), view.getInt16(48, true)],
    [-32_768, 0, 32_767],
  )
})

test('calculates duration and file sizes within the recording limits', () => {
  const sampleCount = TARGET_SAMPLE_RATE * MAX_RECORDING_SECONDS

  assert.equal(
    calculateDurationSeconds(sampleCount, TARGET_SAMPLE_RATE),
    MAX_RECORDING_SECONDS,
  )
  assert.equal(calculateWavByteLength(sampleCount), 960_044)
  assert.ok(calculateWavByteLength(sampleCount) < MAX_AUDIO_BYTES)
  assert.equal(isWithinRecordingLimits(sampleCount), true)
  assert.equal(isWithinRecordingLimits(sampleCount + 1), false)
})
