import Recorder from './components/Recorder'

function App() {
  return (
    <main className="app-shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">BolHire</p>
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
