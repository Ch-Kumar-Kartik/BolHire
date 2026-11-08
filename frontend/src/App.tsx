import { useState } from 'react'
import AppShell from './components/AppShell'
import CandidateAccount from './components/CandidateAccount'
import JobMatches from './components/JobMatches'
import Recorder from './components/Recorder'
import type { SavedProfile } from './lib/candidateApi'

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const [account, setAccount] = useState<SavedProfile | null>(null)
  const [accountNotice, setAccountNotice] = useState<string | null>(null)
  const [jobCount, setJobCount] = useState<number | null>(null)

  function handleProfileSaved(profile: SavedProfile | null, created: boolean) {
    if (profile === null) return
    setAccount(profile)
    setAccountNotice(created ? 'Your profile was saved and you are signed in.' : 'This profile was already saved. You are signed in.')
  }

  const themeToggle = (
    <button
      className="secondary-button theme-toggle"
      type="button"
      aria-label="Dark mode"
      aria-pressed={theme === 'dark'}
      onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
    >
      Dark mode: {theme === 'dark' ? 'On' : 'Off'}
    </button>
  )

  return (
    <AppShell
      theme={theme}
      signedIn={account !== null}
      jobCount={jobCount}
      themeToggle={themeToggle}
      chat={<Recorder onProfileSaved={handleProfileSaved} />}
      profile={(
        <CandidateAccount
          account={account}
          notice={accountNotice}
          onAccountChange={(next, notice) => { setAccount(next); setAccountNotice(notice) }}
        />
      )}
      jobs={(
        <JobMatches
          key={account ? `${account.consented_at}|${account.expires_at}` : 'signed-out'}
          signedIn={account !== null}
          onUnauthorized={() => { setAccount(null); setAccountNotice('Your session ended. Sign in again with your recovery key.') }}
          onResultCount={setJobCount}
          candidateName={account?.profile.name ?? null}
        />
      )}
    />
  )
}

export default App
