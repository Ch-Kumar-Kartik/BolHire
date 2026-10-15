import { useState } from 'react'
import Recorder from './components/Recorder'

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

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
        <Recorder />
      </section>
    </main>
  )
}

export default App
