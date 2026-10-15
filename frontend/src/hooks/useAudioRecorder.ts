import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import {
  MAX_RECORDING_SECONDS,
  TARGET_SAMPLE_RATE,
  calculateDurationSeconds,
  encodeMonoPcmWav,
  isWithinRecordingLimits,
  mergeAudioChunks,
  resampleMono,
} from '../lib/audio'

export type RecorderStatus = 'idle' | 'requesting' | 'cancelling' | 'recording' | 'processing' | 'error'

type StartStage =
  | 'click'
  | 'getUserMedia begin'
  | 'getUserMedia resolved'
  | 'AudioContext constructed'
  | 'AudioContext.resume resolved'
  | 'audio graph connected'
  | 'first audio callback'
  | 'Recording UI committed'

interface StartTiming {
  lastAt: number
  startedAt: number
  stages: Partial<Record<StartStage, number>>
}

export interface LocalRecording {
  blob: Blob
  durationSeconds: number
  sizeBytes: number
  url: string
}

interface RecorderResources {
  audioContext: AudioContext
  chunks: Float32Array[]
  inputSampleRate: number
  mediaStream: MediaStream
  processor: ScriptProcessorNode
  silentGain: GainNode
  source: MediaStreamAudioSourceNode
  stopped: boolean
  totalFrames: number
  firstSampleTimeoutId?: number
}

interface UseAudioRecorderResult {
  clearRecording: () => void
  durationSeconds: number
  error: string | null
  recording: LocalRecording | null
  startRecording: (clickedAt?: number) => Promise<void>
  status: RecorderStatus
  stopRecording: () => Promise<void>
}

export function useAudioRecorder(): UseAudioRecorderResult {
  const [durationSeconds, setDurationSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [recording, setRecording] = useState<LocalRecording | null>(null)
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const cancelRequestedRef = useRef(false)
  const isMountedRef = useRef(true)
  const isStartingRef = useRef(false)
  const pendingContextRef = useRef<AudioContext | null>(null)
  const pendingStreamRef = useRef<MediaStream | null>(null)
  const startTimingRef = useRef<StartTiming | null>(null)
  const objectUrlRef = useRef<string | null>(null)
  const resourcesRef = useRef<RecorderResources | null>(null)

  const recordStartStage = useCallback((stage: StartStage, details: Record<string, unknown> = {}) => {
    const timing = startTimingRef.current
    if (!timing) return
    const at = performance.now()
    timing.stages[stage] = at
    recorderDiagnostic(stage, {
      ...details,
      fromClickMs: roundMilliseconds(at - timing.startedAt),
      sincePreviousMs: roundMilliseconds(at - timing.lastAt),
    })
    timing.lastAt = at
    if (stage === 'Recording UI committed') {
      const stages = timing.stages
      recorderDiagnostic('start latency breakdown', {
        microphoneAcquisitionMs: difference(stages['getUserMedia begin'], stages['getUserMedia resolved']),
        audioInitializationMs: difference(stages['getUserMedia resolved'], stages['audio graph connected']),
        firstSampleWaitMs: difference(stages['audio graph connected'], stages['first audio callback']),
        reactCommitMs: difference(stages['first audio callback'], stages['Recording UI committed']),
        totalMs: difference(stages.click, stages['Recording UI committed']),
      })
    }
  }, [])

  useLayoutEffect(() => {
    if (status === 'recording') {
      recordStartStage('Recording UI committed')
    }
  }, [recordStartStage, status])

  const revokeRecordingUrl = useCallback(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
  }, [])

  const clearRecording = useCallback(() => {
    revokeRecordingUrl()
    if (isMountedRef.current) {
      setRecording(null)
      setDurationSeconds(0)
    }
  }, [revokeRecordingUrl])

  const finishRecording = useCallback(
    async (resources: RecorderResources, reachedDurationLimit: boolean) => {
      if (resources.stopped) {
        return
      }

      recorderDiagnostic('finishing recording', {
        contextState: resources.audioContext.state,
        reachedDurationLimit,
        totalFrames: resources.totalFrames,
      })
      resources.stopped = true
      if (resourcesRef.current === resources) {
        resourcesRef.current = null
      }
      if (isMountedRef.current) {
        setStatus('processing')
      }

      releaseRecorderResources(resources)

      if (resources.totalFrames === 0) {
        if (isMountedRef.current) {
          setError('No audio was captured. Try recording again.')
          setStatus('error')
        }
        return
      }

      const sourceSamples = mergeAudioChunks(resources.chunks)
      const resampledSamples = resampleMono(
        sourceSamples,
        resources.inputSampleRate,
        TARGET_SAMPLE_RATE,
      )

      if (!isWithinRecordingLimits(resampledSamples.length)) {
        if (isMountedRef.current) {
          setError('The recording exceeded the 30-second or 2 MB limit.')
          setStatus('error')
        }
        return
      }

      const wavBuffer = encodeMonoPcmWav(resampledSamples)
      const blob = new Blob([wavBuffer], { type: 'audio/wav' })
      const url = URL.createObjectURL(blob)
      revokeRecordingUrl()
      objectUrlRef.current = url

      if (!isMountedRef.current) {
        URL.revokeObjectURL(url)
        objectUrlRef.current = null
        return
      }

      const duration = calculateDurationSeconds(
        resampledSamples.length,
        TARGET_SAMPLE_RATE,
      )
      setRecording({ blob, durationSeconds: duration, sizeBytes: blob.size, url })
      setDurationSeconds(duration)
      setError(
        reachedDurationLimit
          ? 'Recording stopped at the 30-second limit.'
          : null,
      )
      setStatus('idle')
      recorderDiagnostic('recording ready for local playback', {
        durationSeconds: duration,
        sizeBytes: blob.size,
      })
    },
    [revokeRecordingUrl],
  )

  const startRecording = useCallback(async (clickedAt = performance.now()) => {
    if (isStartingRef.current || resourcesRef.current) {
      recorderDiagnostic('ignored duplicate start request')
      return
    }

    if (!navigator.mediaDevices?.getUserMedia || !getAudioContextConstructor()) {
      setError('Microphone recording is not supported in this browser.')
      setStatus('error')
      return
    }

    isStartingRef.current = true
    cancelRequestedRef.current = false
    startTimingRef.current = { startedAt: clickedAt, lastAt: clickedAt, stages: { click: clickedAt } }
    recorderDiagnostic('Start Recording clicked', { atMs: roundMilliseconds(clickedAt) })
    clearRecording()
    setError(null)
    setStatus('requesting')
    recorderDiagnostic('recording initialization started')

    let mediaStream: MediaStream | null = null
    let audioContext: AudioContext | null = null
    let initializedResources: RecorderResources | null = null

    try {
      recordStartStage('getUserMedia begin')
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      pendingStreamRef.current = mediaStream
      recordStartStage('getUserMedia resolved')
      recorderDiagnostic('microphone stream resolved', {
        audioTracks: mediaStream.getAudioTracks().map((track) => ({
          enabled: track.enabled,
          readyState: track.readyState,
        })),
      })
      if (!isMountedRef.current || cancelRequestedRef.current) {
        recorderDiagnostic('microphone stream released before initialization completed', {
          cancelled: cancelRequestedRef.current,
          mounted: isMountedRef.current,
        })
        stopMediaStream(mediaStream)
        if (isMountedRef.current) {
          setStatus('idle')
        }
        return
      }

      const AudioContextConstructor = getAudioContextConstructor()
      if (!AudioContextConstructor) {
        throw new Error('Microphone recording is not supported in this browser.')
      }

      recorderDiagnostic('constructing AudioContext')
      audioContext = new AudioContextConstructor()
      pendingContextRef.current = audioContext
      recordStartStage('AudioContext constructed', {
        sampleRate: audioContext.sampleRate,
        state: audioContext.state,
      })
      const source = audioContext.createMediaStreamSource(mediaStream)
      const processor = audioContext.createScriptProcessor(2048, 1, 1)
      const silentGain = audioContext.createGain()
      recorderDiagnostic('audio graph nodes created', {
        sampleRate: audioContext.sampleRate,
        state: audioContext.state,
      })
      silentGain.gain.value = 0
      const resources: RecorderResources = {
        audioContext,
        chunks: [],
        inputSampleRate: audioContext.sampleRate,
        mediaStream,
        processor,
        silentGain,
        source,
        stopped: false,
        totalFrames: 0,
      }
      initializedResources = resources

      processor.onaudioprocess = (event) => {
        if (resources.stopped || resourcesRef.current !== resources) {
          return
        }

        const channel = event.inputBuffer.getChannelData(0)
        const maxFrames = Math.floor(resources.inputSampleRate * MAX_RECORDING_SECONDS)
        const availableFrames = maxFrames - resources.totalFrames
        const capturedFrames = Math.min(channel.length, availableFrames)

        if (capturedFrames > 0) {
          resources.chunks.push(channel.slice(0, capturedFrames))
          resources.totalFrames += capturedFrames
          if (resources.totalFrames === capturedFrames) {
            if (resources.firstSampleTimeoutId !== undefined) {
              window.clearTimeout(resources.firstSampleTimeoutId)
              resources.firstSampleTimeoutId = undefined
            }
            recordStartStage('first audio callback', { capturedFrames })
            if (isMountedRef.current) setStatus('recording')
          }
          if (isMountedRef.current) {
            setDurationSeconds(
              calculateDurationSeconds(
                resources.totalFrames,
                resources.inputSampleRate,
              ),
            )
          }
        }

        if (resources.totalFrames >= maxFrames) {
          void finishRecording(resources, true)
        }
      }

      source.connect(processor)
      processor.connect(silentGain)
      silentGain.connect(audioContext.destination)
      resourcesRef.current = resources
      pendingStreamRef.current = null
      pendingContextRef.current = null
      recordStartStage('audio graph connected', { state: audioContext.state })
      resources.firstSampleTimeoutId = window.setTimeout(() => {
        if (resources.stopped || resources.totalFrames > 0) return
        recorderDiagnostic('first audio callback timed out', {
          contextState: resources.audioContext.state,
        })
        resources.stopped = true
        if (resourcesRef.current === resources) resourcesRef.current = null
        releaseRecorderResources(resources)
        if (isMountedRef.current && !cancelRequestedRef.current) {
          setError('The microphone opened, but no audio arrived. Try again.')
          setStatus('error')
        }
      }, 5_000)
      recorderDiagnostic('audio graph connected; awaiting first samples', {
        sampleRate: audioContext.sampleRate,
        state: audioContext.state,
      })
      if (audioContext.state !== 'running') {
        recorderDiagnostic('resuming suspended AudioContext', { state: audioContext.state })
        await resumeAudioContext(audioContext)
        recordStartStage('AudioContext.resume resolved', { state: audioContext.state })
      } else {
        recordStartStage('AudioContext.resume resolved', { skipped: true, state: audioContext.state })
      }
    } catch (caughtError) {
      recorderDiagnostic('recording initialization failed', {
        contextState: audioContext?.state ?? 'not-created',
        errorName: getErrorName(caughtError),
      })
      if (mediaStream) {
        stopMediaStream(mediaStream)
      }
      const resources = resourcesRef.current
      if (resources && resources.audioContext === audioContext) {
        resources.stopped = true
        resourcesRef.current = null
        releaseRecorderResources(resources)
      }
      if (audioContext && audioContext.state !== 'closed') {
        void closeAudioContext(audioContext)
      }
      if (
        isMountedRef.current &&
        !cancelRequestedRef.current &&
        !initializedResources?.stopped
      ) {
        setError(toRecorderErrorMessage(caughtError))
        setStatus('error')
      }
    } finally {
      pendingStreamRef.current = null
      pendingContextRef.current = null
      isStartingRef.current = false
      if (cancelRequestedRef.current && isMountedRef.current) setStatus('idle')
      recorderDiagnostic('recording initialization settled', {
        cancelled: cancelRequestedRef.current,
        mounted: isMountedRef.current,
      })
    }
  }, [clearRecording, finishRecording, recordStartStage])

  const stopRecording = useCallback(async () => {
    const resources = resourcesRef.current
    if (!resources) {
      if (isStartingRef.current) {
        cancelRequestedRef.current = true
        recorderDiagnostic('recording initialization cancellation requested')
        if (pendingStreamRef.current) stopMediaStream(pendingStreamRef.current)
        if (pendingContextRef.current) void closeAudioContext(pendingContextRef.current)
        if (isMountedRef.current) {
          setStatus('cancelling')
        }
      }
      return
    }
    await finishRecording(resources, false)
  }, [finishRecording])

  useEffect(() => {
    isMountedRef.current = true
    recorderDiagnostic('recorder effect initialized')
    return () => {
      recorderDiagnostic('recorder cleanup started')
      isMountedRef.current = false
      cancelRequestedRef.current = true
      if (pendingStreamRef.current) stopMediaStream(pendingStreamRef.current)
      if (pendingContextRef.current) void closeAudioContext(pendingContextRef.current)
      const resources = resourcesRef.current
      if (resources) {
        resources.stopped = true
        resourcesRef.current = null
        releaseRecorderResources(resources)
      }
      revokeRecordingUrl()
    }
  }, [revokeRecordingUrl])

  return {
    clearRecording,
    durationSeconds,
    error,
    recording,
    startRecording,
    status,
    stopRecording,
  }
}

function getAudioContextConstructor(): typeof AudioContext | null {
  const browserWindow = window as Window & {
    webkitAudioContext?: typeof AudioContext
  }
  return (
    (typeof AudioContext === 'undefined' ? undefined : AudioContext) ??
    browserWindow.webkitAudioContext ??
    null
  )
}

function releaseRecorderResources(resources: RecorderResources): void {
  if (resources.firstSampleTimeoutId !== undefined) {
    window.clearTimeout(resources.firstSampleTimeoutId)
    resources.firstSampleTimeoutId = undefined
  }
  resources.processor.onaudioprocess = null
  resources.source.disconnect()
  resources.processor.disconnect()
  resources.silentGain.disconnect()
  stopMediaStream(resources.mediaStream)
  if (resources.audioContext.state !== 'closed') {
    void closeAudioContext(resources.audioContext)
  }
}

async function resumeAudioContext(audioContext: AudioContext): Promise<void> {
  await withTimeout(audioContext.resume(), 5_000, 'AudioContext did not resume in time.')
}

async function closeAudioContext(audioContext: AudioContext): Promise<void> {
  try {
    await audioContext.close()
  } catch {
    recorderDiagnostic('AudioContext close failed', { state: audioContext.state })
  }
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMilliseconds: number,
  message: string,
): Promise<T> {
  let timeoutId: number | undefined
  const timeout = new Promise<never>((_resolve, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMilliseconds)
  })

  try {
    return await Promise.race([promise, timeout])
  } finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId)
    }
  }
}

function stopMediaStream(stream: MediaStream): void {
  const tracks = stream.getTracks()
  tracks.forEach((track) => track.stop())
  recorderDiagnostic('microphone tracks released', {
    trackStates: tracks.map((track) => track.readyState),
  })
}

function toRecorderErrorMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
      return 'Microphone access was denied. Allow microphone access and try again.'
    }
    if (error.name === 'NotFoundError') {
      return 'No microphone was found. Connect one and try again.'
    }
  }

  if (error instanceof Error && error.message === 'AudioContext did not resume in time.') {
    return 'Audio processing did not start. Close other microphone apps and try again.'
  }

  return 'Unable to start recording. Check your browser microphone settings.'
}

function getErrorName(error: unknown): string {
  if (error instanceof DOMException || error instanceof Error) {
    return error.name
  }
  return 'unknown'
}

function recorderDiagnostic(
  stage: string,
  details: Record<string, unknown> = {},
): void {
  if (import.meta.env.DEV) {
    console.info('[BolHire recorder]', new Date().toISOString(), stage, JSON.stringify(details))
  }
}

function roundMilliseconds(value: number): number {
  return Math.round(value * 100) / 100
}

function difference(start: number | undefined, end: number | undefined): number | null {
  return start === undefined || end === undefined ? null : roundMilliseconds(end - start)
}
