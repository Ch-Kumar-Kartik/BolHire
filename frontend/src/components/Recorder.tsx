import { useEffect, useRef, useState } from 'react'
import { useAudioRecorder } from '../hooks/useAudioRecorder'
import { decodeVoiceAudio, submitVoice, VoiceApiError, type VoiceLanguage, type VoiceReply } from '../lib/voiceApi'

function formatDuration(durationSeconds: number): string {
  const wholeSeconds = Math.floor(durationSeconds)
  const minutes = Math.floor(wholeSeconds / 60)
  const seconds = wholeSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function Recorder() {
  const [language, setLanguage] = useState<VoiceLanguage>('en-IN')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [reply, setReply] = useState<VoiceReply | null>(null)
  const [replyAudioUrl, setReplyAudioUrl] = useState<string | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const replyAudioUrlRef = useRef<string | null>(null)
  const submittingRef = useRef(false)
  const {
    clearRecording,
    durationSeconds,
    error,
    recording,
    startRecording,
    status,
    stopRecording,
  } = useAudioRecorder()
  const isRecording = status === 'recording'
  const isRequesting = status === 'requesting'
  const isBusy = isRequesting || status === 'cancelling' || status === 'processing'

  useEffect(() => () => {
    if (replyAudioUrlRef.current) URL.revokeObjectURL(replyAudioUrlRef.current)
  }, [])

  function replaceReturnedAudioUrl(nextUrl: string | null) {
    if (replyAudioUrlRef.current) URL.revokeObjectURL(replyAudioUrlRef.current)
    replyAudioUrlRef.current = nextUrl
    setReplyAudioUrl(nextUrl)
  }

  function handleNewConversation() {
    clearRecording()
    replaceReturnedAudioUrl(null)
    setReply(null)
    setSessionId(null)
    setRequestError(null)
  }

  async function handleSubmit() {
    if (!recording || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setRequestError(null)
    try {
      const result = await submitVoice(recording.blob, language, sessionId)
      let nextUrl: string | null = null
      let audioWarning: string | null = null
      try {
        const audioBlob = decodeVoiceAudio(result.audio)
        if (audioBlob) nextUrl = URL.createObjectURL(audioBlob)
      } catch {
        audioWarning = 'Returned mock audio could not be played.'
      }
      replaceReturnedAudioUrl(nextUrl)
      setReply({ ...result, warning: audioWarning || result.warning })
      setSessionId(result.session_id)
    } catch (error) {
      if (error instanceof VoiceApiError && ['SESSION_NOT_FOUND', 'SESSION_EXPIRED'].includes(error.code)) {
        setSessionId(null)
        setRequestError(`${error.message} Submit the recording again to start a new conversation.`)
      } else {
        setRequestError(error instanceof Error ? error.message : 'Voice request failed.')
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <section className="recorder" aria-labelledby="recorder-title">
      <h2 id="recorder-title">Try the voice recorder</h2>
      <p className="recorder-description">
        Record up to 30 seconds. Submit when ready to send the WAV to the local voice server.
      </p>
      <p className="mock-notice">STT and TTS are mocked. The transcript is a placeholder and the returned audio is a tone, not spoken speech.</p>

      <label className="language-control">
        Language
        <select value={language} onChange={(event) => setLanguage(event.target.value as VoiceLanguage)} disabled={submitting}>
          <option value="en-IN">English (India)</option>
          <option value="hi-IN">Hindi (India)</option>
        </select>
      </label>

      <div className="recorder-controls">
        <button
          type="button"
          onClick={() => void startRecording(performance.now())}
          disabled={isRecording || isBusy || submitting}
        >
          {status === 'requesting' ? 'Requesting microphone...' : 'Start recording'}
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void stopRecording()}
          disabled={!isRecording && !isRequesting}
        >
          {isRequesting ? 'Cancel recording' : 'Stop recording'}
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={handleNewConversation}
          disabled={isRecording || isBusy || submitting}
        >
          New conversation
        </button>
      </div>

      <p className="recording-status" role="status">
        {isRecording
          ? 'Recording'
          : isRequesting
            ? 'Starting microphone...'
            : status === 'cancelling'
              ? 'Cancelling...'
              : status === 'processing'
                ? 'Preparing WAV'
                : 'Ready'}
        {' · '}
        {formatDuration(durationSeconds)} / 0:30
      </p>

      {error && (
        <p className="recording-error" role="alert">
          {error}
        </p>
      )}

      {recording && (
        <div className="playback">
          <h3>Your local recording</h3>
          <audio controls src={recording.url}>
            Your browser cannot play WAV audio.
          </audio>
          <p>
            {formatDuration(recording.durationSeconds)} WAV,{' '}
            {Math.ceil(recording.sizeBytes / 1024)} KB
          </p>
          <button className="text-button" type="button" onClick={clearRecording}>
            Clear recording
          </button>
          <div className="submit-control">
            <button type="button" onClick={() => void handleSubmit()} disabled={submitting || isBusy || isRecording}>
              {submitting ? 'Submitting...' : 'Submit recording'}
            </button>
          </div>
        </div>
      )}
      {requestError && <p className="recording-error" role="alert">{requestError}</p>}
      {reply && (
        <div className="voice-response" aria-live="polite">
          <h3>Mock voice response</h3>
          <p><strong>Transcript:</strong> {reply.transcript}</p>
          <p><strong>Mock LLM reply:</strong> {reply.reply}</p>
          {replyAudioUrl && <><p>Returned mock audio</p><audio controls src={replyAudioUrl}>Your browser cannot play WAV audio.</audio></>}
          {reply.warning && <p role="status">{reply.warning}</p>}
        </div>
      )}
    </section>
  )
}

export default Recorder
