import { useRef, useState, type ReactNode, type RefObject } from 'react'
import { bannerText, subtitleText } from '../lib/jobMatchView'
import { ChatIcon, InboxIcon, PersonIcon } from './icons'

export type ShellView = 'chat' | 'jobs' | 'profile'

interface Props {
  theme: 'dark' | 'light'
  signedIn: boolean
  jobCount: number | null
  themeToggle: ReactNode
  chat: ReactNode
  jobs: ReactNode
  profile: ReactNode
}

const NAV: { view: ShellView; label: string; short: string; Icon: () => ReactNode }[] = [
  { view: 'chat', label: 'Chat', short: 'Chat', Icon: ChatIcon },
  { view: 'jobs', label: 'Jobs inbox', short: 'Jobs', Icon: () => <InboxIcon /> },
  { view: 'profile', label: 'Profile', short: 'Profile', Icon: PersonIcon },
]

/** Layout only. Every pane stays mounted (hidden with CSS on small screens), so switching never drops a conversation in progress. */
export default function AppShell({ theme, signedIn, jobCount, themeToggle, chat, jobs, profile }: Props) {
  const [activeView, setActiveView] = useState<ShellView>('chat')
  const panes: Record<ShellView, RefObject<HTMLElement | null>> = {
    chat: useRef<HTMLElement>(null), jobs: useRef<HTMLElement>(null), profile: useRef<HTMLElement>(null),
  }

  function select(view: ShellView) {
    setActiveView(view)
    requestAnimationFrame(() => {
      const target = panes[view].current
      if (!target) return
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      target.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' })
      target.focus({ preventScroll: true })
    })
  }

  function navItems(compact: boolean) {
    return NAV.map(({ view, label, short, Icon }) => (
      <li key={view}>
        <button
          type="button"
          className="nav-item"
          aria-current={activeView === view ? 'page' : undefined}
          onClick={() => select(view)}
        >
          <Icon />
          <span>{compact ? short : label}</span>
          {view === 'jobs' && jobCount !== null && (
            <span className="nav-badge">{jobCount}<span className="sr-only"> {jobCount === 1 ? 'match' : 'matches'}</span></span>
          )}
        </button>
      </li>
    ))
  }

  const status = (
    <span className="account-status">
      <span className={`status-dot${signedIn ? ' is-on' : ''}`} aria-hidden="true" />
      {signedIn ? 'Signed in' : 'Not signed in'}
    </span>
  )

  return (
    <div className="app-shell" data-theme={theme} data-view={activeView}>
      <aside className="sidebar">
        <div className="wordmark">
          <span className="wordmark-name">Bol<span>Hire</span></span>
          <span className="wordmark-tag">Voice-first recruitment for India.</span>
        </div>
        <nav aria-label="Main">
          <ul className="nav-list">{navItems(false)}</ul>
        </nav>
        <div className="sidebar-foot">
          <div className="account-chip">
            {status}
            <button type="button" className="text-button" onClick={() => select('profile')}>
              {signedIn ? 'Profile and sign out' : 'Sign in'}
            </button>
          </div>
          {themeToggle}
        </div>
      </aside>

      <header className="topbar">
        <div className="topbar-row">
          <span className="wordmark-name">Bol<span>Hire</span></span>
          {status}
          {themeToggle}
        </div>
        <nav aria-label="Main">
          <ul className="tab-list">{navItems(true)}</ul>
        </nav>
      </header>

      <main className="workspace">
        <div className="pane pane-left">
          <section ref={panes.chat} className="pane-card" data-pane="chat" tabIndex={-1} aria-label="Chat">
            {chat}
          </section>
          <section ref={panes.profile} className="pane-card" data-pane="profile" tabIndex={-1} aria-label="Profile">
            {profile}
          </section>
        </div>
        <section ref={panes.jobs} className="pane pane-right pane-card" data-pane="jobs" tabIndex={-1} aria-label="Jobs inbox">
          {jobs}
          {!signedIn && (
            <div className="jobs-signed-out">
              <h2>Jobs inbox</h2>
              <p className="inbox-subtitle">{subtitleText('none')}</p>
              <p className="sample-banner" role="note">{bannerText('none')}</p>
              <div className="empty-state">
                <span className="empty-icon"><InboxIcon size={28} /></span>
                <p>Save a profile from the chat, or sign in with your recovery key, to see sample job matches.</p>
                <button type="button" className="secondary-button" onClick={() => select('profile')}>Go to profile</button>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
