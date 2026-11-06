import { useEffect, useState, type FormEvent } from 'react'
import { CandidateApiError, fetchSavedProfile, signIn, signOut, type SavedProfile } from '../lib/candidateApi'
import { messageFor } from '../lib/candidateFlow'
import SavedProfileCard from './SavedProfileCard'

interface Props {
  account: SavedProfile | null
  notice: string | null
  onAccountChange: (account: SavedProfile | null, notice: string | null) => void
}

function CandidateAccount({ account, notice, onAccountChange }: Props) {
  const [recoveryKey, setRecoveryKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A refreshed page keeps the cookie: quietly restore the signed-in view. Failures simply mean "not signed in".
  useEffect(() => {
    let cancelled = false
    fetchSavedProfile().then((profile) => { if (!cancelled) onAccountChange(profile, null) }).catch(() => undefined)
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSignIn(event: FormEvent) {
    event.preventDefault()
    if (busy || !recoveryKey.trim()) return
    setBusy(true)
    setError(null)
    try {
      await signIn(recoveryKey)
      const profile = await fetchSavedProfile()
      setRecoveryKey('')
      onAccountChange(profile, null)
    } catch (caught) {
      const code = caught instanceof CandidateApiError ? caught.code : ''
      setError(messageFor(code, caught instanceof Error ? caught.message : 'Sign in failed.'))
    } finally {
      setBusy(false)
    }
  }

  async function handleSignOut() {
    setBusy(true)
    try {
      await signOut()
    } catch {
      // The server clears the cookie even when it cannot reach its database; the browser view is cleared either way.
    } finally {
      setBusy(false)
      onAccountChange(null, null)
    }
  }

  return (
    <section className="recorder candidate-account" aria-labelledby="account-title">
      <h2 id="account-title">Candidate sign in</h2>
      {account ? (
        <SavedProfileCard profile={account.profile} expiresAt={account.expires_at} notice={notice} busy={busy} onSignOut={() => void handleSignOut()} />
      ) : (
        <form onSubmit={(event) => void handleSignIn(event)}>
          <p className="recorder-description">Saved a profile before? Enter your recovery key to see it. Recovery keys cannot be recovered if lost.</p>
          <label className="language-control">
            Recovery key
            <input
              className="key-input"
              value={recoveryKey}
              onChange={(event) => setRecoveryKey(event.target.value)}
              autoComplete="off" autoCapitalize="characters" spellCheck={false}
              placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX-X"
              disabled={busy}
            />
          </label>
          <div className="submit-control">
            <button type="submit" disabled={busy || !recoveryKey.trim()}>{busy ? 'Signing in...' : 'Sign in'}</button>
          </div>
          {error && <p className="recording-error" role="alert">{error}</p>}
        </form>
      )}
    </section>
  )
}

export default CandidateAccount
