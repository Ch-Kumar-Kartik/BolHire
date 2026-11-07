import { useState } from 'react'
import CandidateAccount from './components/CandidateAccount'
import JobMatches from './components/JobMatches'
import Recorder from './components/Recorder'
import type { SavedProfile } from './lib/candidateApi'

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const [account, setAccount] = useState<SavedProfile | null>(null)
  const [accountNotice, setAccountNotice] = useState<string | null>(null)

  function handleProfileSaved(profile: SavedProfile | null, created: boolean) {
    if (profile === null) return
    setAccount(profile)
    setAccountNotice(created ? 'Your profile was saved and you are signed in.' : 'This profile was already saved. You are signed in.')
  }

  return (
    <main className="app-shell" data-theme={theme}>
      <section className="hero" aria-labelledby="page-title">
        <div className="hero-header">
          <p className="eyebrow">BolHire</p>
          <button
            className="secondary-button theme-toggle"
            type="button"
            aria-label="Dark mode"
            aria-pressed={theme === 'dark'}
            onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
          >
            Dark mode: {theme === 'dark' ? 'On' : 'Off'}
          </button>
        </div>
        <h1 id="page-title">Voice-first recruitment for India.</h1>
        <p className="description">
          A multilingual hiring experience for candidates and recruiters.
        </p>
        <Recorder onProfileSaved={handleProfileSaved} />
        <CandidateAccount
          account={account}
          notice={accountNotice}
          onAccountChange={(next, notice) => { setAccount(next); setAccountNotice(notice) }}
        />
        <JobMatches
          key={account ? `${account.consented_at}|${account.expires_at}` : 'signed-out'}
          signedIn={account !== null}
          onUnauthorized={() => { setAccount(null); setAccountNotice('Your session ended. Sign in again with your recovery key.') }}
        />
      </section>
    </main>
  )
}

export default App
