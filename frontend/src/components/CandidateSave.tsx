import { useEffect, useReducer, useRef, useState } from 'react'
import {
  CandidateApiError, fetchSavedProfile, issueRecoveryKey, previewProfile, saveProfile, signIn, type SavedProfile,
} from '../lib/candidateApi'
import { canSave, CONSENT_LABEL, discardsKey, flowReducer, initialFlow, messageFor, needsFreshPreview } from '../lib/candidateFlow'
import type { VoiceLanguage } from '../lib/voiceApi'
import { ProfileFields } from './SavedProfileCard'

interface Props {
  sessionId: string | null
  language: VoiceLanguage
  onSaved: (profile: SavedProfile | null, created: boolean) => void
}

// The explicit, optional save flow for one Candidate Intake conversation: review -> recovery key -> consent -> save -> sign in.
// The recovery key lives only in this component's state until the save succeeds; it is never written to browser storage.
function CandidateSave({ sessionId, language, onSaved }: Props) {
  const [flow, dispatch] = useReducer(flowReducer, initialFlow)
  const [copied, setCopied] = useState(false)
  const busyRef = useRef(false)
  const generationRef = useRef(0)
  const flowRef = useRef(flow)
  flowRef.current = flow

  // A different conversation or language is a different review: forget the key, the preview and the confirmations.
  useEffect(() => {
    if (sessionId === null && flowRef.current.done !== null) return  // the conversation just ended because the save succeeded
    generationRef.current += 1
    busyRef.current = false
    setCopied(false)
    dispatch({ type: 'reset' })
  }, [sessionId, language])

  async function run(action: (generation: number) => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    const generation = generationRef.current
    dispatch({ type: 'started' })
    try {
      await action(generation)
    } catch (error) {
      if (generation !== generationRef.current) return
      const code = error instanceof CandidateApiError ? error.code : ''
      const message = messageFor(code, error instanceof Error ? error.message : 'Something went wrong.')
      if (needsFreshPreview(code) && sessionId) {
        try {
          const fresh = await previewProfile(sessionId, language)
          if (generation === generationRef.current) dispatch({ type: 'previewLoaded', preview: fresh })
        } catch {
          // keep the original message below; the candidate can press "Review profile" again
        }
      }
      if (generation === generationRef.current) dispatch({ type: 'failed', message, discardKey: discardsKey(code) })
    } finally {
      if (generation === generationRef.current) busyRef.current = false
    }
  }

  function handleReview() {
    if (!sessionId) return
    void run(async (generation) => {
      const preview = await previewProfile(sessionId, language)
      if (generation === generationRef.current) dispatch({ type: 'previewLoaded', preview })
    })
  }

  function handleIssueKey() {
    if (!sessionId) return
    void run(async (generation) => {
      const recoveryKey = await issueRecoveryKey(sessionId)
      if (generation === generationRef.current) {
        setCopied(false)
        dispatch({ type: 'keyIssued', recoveryKey })
      }
    })
  }

  function handleSave() {
    const { preview, recoveryKey } = flow
    if (!sessionId || !preview || !recoveryKey || !canSave(flow)) return
    void run(async (generation) => {
      const result = await saveProfile({ sessionId, recoveryKey, profileDigest: preview.profile_digest, consentVersion: preview.consent.version })
      if (generation !== generationRef.current) return
      dispatch({ type: 'saved', created: result.created })  // clears the key from state
      try {
        await signIn(recoveryKey)
        onSaved(await fetchSavedProfile(), result.created)
      } catch {
        dispatch({ type: 'failed', message: 'Your profile was saved, but signing in failed. Use your recovery key under "Candidate sign in" below.' })
        onSaved(null, result.created)
      }
    })
  }

  async function handleCopy() {
    if (!flow.recoveryKey) return
    try {
      await navigator.clipboard.writeText(flow.recoveryKey)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  const preview = flow.preview
  return (
    <section className="candidate-panel" aria-labelledby="candidate-save-title">
      <h3 id="candidate-save-title">Save your profile (optional)</h3>
      <p className="muted-note">Nothing is saved to a candidate account unless you complete every step below.</p>

      {flow.done ? (
        <p role="status" className="success-note">
          {flow.done.created ? 'Your profile was saved.' : 'This profile was already saved.'}
        </p>
      ) : (
        <>
          <div className="recorder-controls">
            <button type="button" onClick={handleReview} disabled={!sessionId || flow.busy}>
              {flow.busy && !preview ? 'Loading...' : preview ? 'Review again' : 'Review profile'}
            </button>
          </div>
          {!sessionId && !preview && <p className="muted-note">Record and submit a message first, so there is something to review.</p>}

          {preview && (
            <div className="profile-preview">
              <h4>What would be saved</h4>
              <ProfileFields profile={preview.profile} />
              {!preview.can_save && <p className="muted-note">Nothing can be saved yet. Tell the assistant at least one detail about yourself.</p>}
            </div>
          )}

          {preview?.can_save && (
            <div className="recorder-controls">
              <button type="button" onClick={handleIssueKey} disabled={flow.busy}>
                {flow.recoveryKey ? 'Create a new recovery key' : 'Create my recovery key'}
              </button>
            </div>
          )}

          {preview && flow.recoveryKey && (
            <div className="key-section">
              <p><strong>Your recovery key</strong> (shown only now)</p>
              <code className="recovery-key" aria-label="Recovery key">{flow.recoveryKey}</code>
              <p className="muted-note">
                This is the only way to get back to your profile. It cannot be recovered if you lose it. Do not say it aloud or type it into the chat.
                Creating a new key makes this one stop working.
              </p>
              <button className="secondary-button" type="button" onClick={() => void handleCopy()}>{copied ? 'Copied' : 'Copy key'}</button>
              <label className="check-row">
                <input type="checkbox" checked={flow.keyRecorded} onChange={(event) => dispatch({ type: 'keyRecordedChanged', value: event.target.checked })} />
                I have saved this recovery key somewhere safe.
              </label>

              <blockquote className="consent-text" lang={preview.consent.language === 'hi-IN' ? 'hi' : 'en'}>{preview.consent.text}</blockquote>
              <label className="check-row">
                <input type="checkbox" checked={flow.consentAccepted} onChange={(event) => dispatch({ type: 'consentChanged', value: event.target.checked })} />
                {CONSENT_LABEL[preview.consent.language]}
              </label>

              <div className="submit-control">
                <button type="button" onClick={handleSave} disabled={!canSave(flow)}>{flow.busy ? 'Saving...' : 'Save my profile'}</button>
              </div>
            </div>
          )}
        </>
      )}
      {flow.error && <p className="recording-error" role="alert">{flow.error}</p>}
    </section>
  )
}

export default CandidateSave
