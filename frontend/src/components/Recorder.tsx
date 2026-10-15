import { useAudioRecorder } from '../hooks/useAudioRecorder'

function formatDuration(durationSeconds: number): string {
  const wholeSeconds = Math.floor(durationSeconds)
  const minutes = Math.floor(wholeSeconds / 60)
  const seconds = wholeSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function Recorder() {
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

  return (
    <section className="recorder" aria-labelledby="recorder-title">
      <h2 id="recorder-title">Try the voice recorder</h2>
      <p className="recorder-description">
        Record up to 30 seconds. Your audio stays in this browser and is not uploaded.
      </p>

      <div className="recorder-controls">
        <button
          type="button"
          onClick={() => void startRecording(performance.now())}
          disabled={isRecording || isBusy}
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
        </div>
      )}
    </section>
  )
}

export default Recorder
