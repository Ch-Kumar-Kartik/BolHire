export const TARGET_SAMPLE_RATE = 16_000
export const MAX_RECORDING_SECONDS = 30
export const MAX_AUDIO_BYTES = 2 * 1024 * 1024
export const WAV_HEADER_BYTES = 44

export function calculateDurationSeconds(
  sampleCount: number,
  sampleRate: number,
): number {
  assertValidSampleRate(sampleRate)
  return sampleCount / sampleRate
}

export function calculateWavByteLength(sampleCount: number): number {
  return WAV_HEADER_BYTES + sampleCount * Int16Array.BYTES_PER_ELEMENT
}

export function resampleMono(
  samples: Float32Array,
  inputSampleRate: number,
  outputSampleRate = TARGET_SAMPLE_RATE,
): Float32Array {
  assertValidSampleRate(inputSampleRate)
  assertValidSampleRate(outputSampleRate)

  if (inputSampleRate === outputSampleRate) {
    return new Float32Array(samples)
  }

  const outputLength = Math.round(
    (samples.length * outputSampleRate) / inputSampleRate,
  )
  const output = new Float32Array(outputLength)
  const ratio = inputSampleRate / outputSampleRate

  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = index * ratio
    const lowerIndex = Math.floor(sourcePosition)
    const upperIndex = Math.min(lowerIndex + 1, samples.length - 1)
    const fraction = sourcePosition - lowerIndex
    const lowerSample = samples[Math.min(lowerIndex, samples.length - 1)] ?? 0
    const upperSample = samples[upperIndex] ?? 0
    output[index] = lowerSample + (upperSample - lowerSample) * fraction
  }

  return output
}

export function encodeMonoPcmWav(
  samples: Float32Array,
  sampleRate = TARGET_SAMPLE_RATE,
): ArrayBuffer {
  assertValidSampleRate(sampleRate)

  const buffer = new ArrayBuffer(calculateWavByteLength(samples.length))
  const view = new DataView(buffer)
  const dataByteLength = samples.length * Int16Array.BYTES_PER_ELEMENT

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataByteLength, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * Int16Array.BYTES_PER_ELEMENT, true)
  view.setUint16(32, Int16Array.BYTES_PER_ELEMENT, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, dataByteLength, true)

  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0))
    const integerSample = sample < 0 ? sample * 0x8000 : sample * 0x7fff
    view.setInt16(WAV_HEADER_BYTES + index * 2, integerSample, true)
  }

  return buffer
}

export function mergeAudioChunks(chunks: Float32Array[]): Float32Array {
  const totalLength = chunks.reduce((total, chunk) => total + chunk.length, 0)
  const merged = new Float32Array(totalLength)
  let offset = 0

  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.length
  }

  return merged
}

export function isWithinRecordingLimits(
  sampleCount: number,
  sampleRate = TARGET_SAMPLE_RATE,
): boolean {
  return (
    calculateDurationSeconds(sampleCount, sampleRate) <= MAX_RECORDING_SECONDS &&
    calculateWavByteLength(sampleCount) <= MAX_AUDIO_BYTES
  )
}

function assertValidSampleRate(sampleRate: number): void {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    throw new Error('Sample rate must be a positive finite number.')
  }
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index))
  }
}
