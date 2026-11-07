import { useEffect, useRef, useState } from 'react'
import { fetchRecommendations, isJobsUnavailableError, isUnauthorizedError, type JobMatch, type Recommendations } from '../lib/jobsApi'
import {
  coverageText, criterionLabel, describeCriterion, emptyStateText, employmentTypeLabel, formatExperienceRange, scoreTone, workModeLabel,
} from '../lib/jobMatchView'

interface Props {
  signedIn: boolean
  onUnauthorized: () => void
}

type View =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; kind: 'jobs_unavailable' | 'generic' }
  | { phase: 'done'; data: Recommendations }

const BANNER = 'Sample listings only. These jobs are fictional examples for demonstration. Matching is rule-based, not AI-generated, and nothing here is sent to any employer.'

function SkillChips({ match }: { match: JobMatch }) {
  const skills = match.criteria.find((criterion) => criterion.name === 'skills')
  const matched = new Set(skills?.matched_skills ?? [])
  const missing = new Set(skills?.missing_skills ?? [])
  return (
    <ul className="chip-list" aria-label="Required skills">
      {match.job.required_skills.map((skill) => {
        const kind = matched.has(skill) ? 'matched' : missing.has(skill) ? 'missing' : 'plain'
        return (
          <li key={skill} className={`chip chip-${kind}`}>
            {skill}
            {kind === 'matched' && <span className="sr-only"> (in your profile)</span>}
            {kind === 'missing' && <span className="sr-only"> (not in your profile)</span>}
          </li>
        )
      })}
    </ul>
  )
}

function MatchCard({ match }: { match: JobMatch }) {
  const { job } = match
  return (
    <li className="job-card">
      <div className="job-card-head">
        <div>
          <h3>{job.title}</h3>
          <p className="job-company">{job.company}</p>
        </div>
        <span className={`score-badge score-${scoreTone(match.score_percent)}`}>Match {match.score_percent}%</span>
      </div>
      <p className="job-meta">
        <span className="sample-tag">Sample listing</span>
        <span>{job.location_label}</span>
        <span>{workModeLabel(job.work_mode)}</span>
        <span>{employmentTypeLabel(job.employment_type)}</span>
        <span>{formatExperienceRange(job.min_experience_years, job.max_experience_years)}</span>
      </p>
      <SkillChips match={match} />
      <details className="job-details">
        <summary>Why this match</summary>
        <ul className="criteria-list">
          {match.criteria.map((criterion) => (
            <li key={criterion.name}>
              <span>{criterionLabel(criterion.name)}</span>
              <span>{describeCriterion(criterion)}</span>
            </li>
          ))}
        </ul>
      </details>
      <details className="job-details">
        <summary>About this job</summary>
        <p className="job-description">{job.description}</p>
      </details>
    </li>
  )
}

function JobMatches({ signedIn, onUnauthorized }: Props) {
  const [view, setView] = useState<View>({ phase: 'idle' })
  const latestRequest = useRef(0)

  // Signing out (or unmounting) invalidates any request in flight and forgets everything shown.
  useEffect(() => {
    if (!signedIn) {
      latestRequest.current += 1
      setView({ phase: 'idle' })
    }
  }, [signedIn])
  useEffect(() => () => { latestRequest.current += 1 }, [])

  async function load() {
    if (!signedIn || view.phase === 'loading') return
    const id = ++latestRequest.current
    setView({ phase: 'loading' })
    try {
      const data = await fetchRecommendations()
      if (id === latestRequest.current) setView({ phase: 'done', data })
    } catch (caught) {
      if (id !== latestRequest.current) return  // signed out, unmounted, or superseded: ignore the late answer
      if (isUnauthorizedError(caught)) {
        setView({ phase: 'idle' })
        onUnauthorized()
        return
      }
      setView({ phase: 'error', kind: isJobsUnavailableError(caught) ? 'jobs_unavailable' : 'generic' })
    }
  }

  if (!signedIn) return null
  const loading = view.phase === 'loading'

  return (
    <section className="recorder job-matches" aria-labelledby="job-matches-title">
      <h2 id="job-matches-title">Sample job matches</h2>
      <p className="sample-banner" role="note">{BANNER}</p>

      {view.phase === 'idle' && (
        <p className="recorder-description">
          See which sample jobs fit your saved profile. Matching runs only when you ask, and uses your skills, target role, experience, location and work mode.
        </p>
      )}
      {view.phase === 'error' && (
        <p className="recording-error" role="alert">
          {view.kind === 'jobs_unavailable' ? 'Job matching is temporarily unavailable.' : 'We could not load your matches. Please try again.'}
        </p>
      )}
      {view.phase === 'done' && (
        <div aria-live="polite">
          {view.data.matches.length === 0 ? (
            <p className="muted-note">{emptyStateText(view.data.status)}</p>
          ) : (
            <>
              <p className="muted-note">{coverageText(view.data.coverage_percent)}</p>
              <ul className="job-list">
                {view.data.matches.map((match) => <MatchCard key={match.job.id} match={match} />)}
              </ul>
            </>
          )}
          {view.data.status === 'ok' && view.data.matches.length === 0 && <p className="muted-note">{coverageText(view.data.coverage_percent)}</p>}
        </div>
      )}

      <div className="submit-control">
        <button
          type="button"
          className={view.phase === 'done' || view.phase === 'error' ? 'secondary-button' : undefined}
          onClick={() => void load()}
          disabled={loading}
        >
          {loading ? 'Finding matches…' : view.phase === 'error' ? 'Try again' : view.phase === 'done' ? 'Refresh matches' : 'Show my matches'}
        </button>
      </div>
    </section>
  )
}

export default JobMatches
