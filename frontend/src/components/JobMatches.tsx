import { useEffect, useReducer, useRef, useState, type CSSProperties } from 'react'
import { fetchLiveJobs, fetchRecommendations, isJobsUnavailableError, isUnauthorizedError, type JobMatch, type Recommendations } from '../lib/jobsApi'
import {
  POSTING_LINK_TEXT, avatarHue, bannerText, companyInitial, coverageText, criterionLabel, describeCriterion, emptyStateText,
  employmentTypeLabel, fitLabel, formatExperienceRange, formatRetrievedDate, headerText, jobMetaLine, listingMix, postingUrl, scoreTone, sourceLabel, visibleCriteria,
  sourceTag, subtitleText, whySuggestedText,
} from '../lib/jobMatchView'
import { LIVE_BUTTON_TEXT, initialLive, liveReducer, liveStatusText } from '../lib/liveFetchView'
import { ChevronIcon, InboxIcon } from './icons'

interface Props {
  signedIn: boolean
  onUnauthorized: () => void
  /** Additive, optional: the number of loaded matches, or null when none are loaded. Used only for the navigation badge. */
  onResultCount?: (count: number | null) => void
  /** Additive, optional: the saved profile's name, used only in the list caption. */
  candidateName?: string | null
}

type View =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; kind: 'jobs_unavailable' | 'generic' }
  | { phase: 'done'; data: Recommendations }

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

function MatchRow({ match, open, onToggle }: { match: JobMatch; open: boolean; onToggle: () => void }) {
  const { job } = match
  const panelId = `match-panel-${job.id}`
  const tone = scoreTone(match.score_percent)
  const link = postingUrl(job)
  const retrieved = formatRetrievedDate(job.retrieved_at)
  return (
    <li className={`job-row${open ? ' is-open' : ''}`}>
      <button type="button" className="job-row-button" aria-expanded={open} aria-controls={panelId} onClick={onToggle}>
        <span className="avatar" style={{ '--avatar-hue': avatarHue(job.company) } as CSSProperties} aria-hidden="true">
          {companyInitial(job.company)}
        </span>
        <span className="job-row-main">
          <span className="job-row-title">
            <strong>{job.title}</strong>
            {job.is_sample ? <span className="sample-tag">Sample</span> : <span className="source-tag">{sourceTag(job)}</span>}
          </span>
          <span className="job-row-meta">{jobMetaLine(job)}</span>
        </span>
        <span className="job-row-side">
          <span className={`fit-chip fit-${tone}`}>{fitLabel(match.score_percent)}</span>
          <span className="job-row-score">{match.score_percent}%</span>
        </span>
        <span className="job-row-chevron"><ChevronIcon /></span>
      </button>
      <div id={panelId} className="job-row-panel" hidden={!open}>
        <dl className="job-facts">
          <div><dt>Employment</dt><dd>{employmentTypeLabel(job.employment_type)}</dd></div>
          <div><dt>Experience</dt><dd>{formatExperienceRange(job.min_experience_years, job.max_experience_years)}</dd></div>
          <div><dt>Match</dt><dd>{match.score_percent}%</dd></div>
          {!job.is_sample && <div><dt>Source</dt><dd>{sourceLabel(job.source)}</dd></div>}
          {!job.is_sample && retrieved && <div><dt>Retrieved</dt><dd>{retrieved}</dd></div>}
        </dl>
        <SkillChips match={match} />
        <h4 className="panel-heading">Why this match</h4>
        <ul className="criteria-list">
          {visibleCriteria(match).map((criterion) => (
            <li key={criterion.name}>
              <span>{criterionLabel(criterion.name)}</span>
              <span>{describeCriterion(criterion)}</span>
            </li>
          ))}
        </ul>
        <h4 className="panel-heading">About this job</h4>
        <p className="job-description">{job.description}</p>
        {!job.is_sample && (
          <p className="source-note">
            This is a real listing retrieved on the date shown. It may be out of date.
            {link && (
              <>
                {' '}
                <a className="source-link" href={link} target="_blank" rel="noopener noreferrer" aria-label={`${POSTING_LINK_TEXT} (opens in a new tab)`}>
                  {POSTING_LINK_TEXT}
                </a>
              </>
            )}
          </p>
        )}
      </div>
    </li>
  )
}

function JobMatches({ signedIn, onUnauthorized, onResultCount, candidateName }: Props) {
  const [view, setView] = useState<View>({ phase: 'idle' })
  const [openId, setOpenId] = useState<string | null>(null)
  const latestRequest = useRef(0)
  const [live, dispatchLive] = useReducer(liveReducer, initialLive)
  const latestLive = useRef(0)

  // Signing out (or unmounting) invalidates any request in flight and forgets everything shown.
  useEffect(() => {
    if (!signedIn) {
      latestRequest.current += 1
      latestLive.current += 1
      setView({ phase: 'idle' })
      dispatchLive({ type: 'reset' })
    }
  }, [signedIn])
  useEffect(() => () => { latestRequest.current += 1; latestLive.current += 1 }, [])
  useEffect(() => {
    onResultCount?.(view.phase === 'done' ? view.data.matches.length : null)
    setOpenId(null)
  }, [view, onResultCount])

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

  // Explicit click only: a live search is never started on mount, sign-in or reload.
  async function findLive() {
    if (!signedIn || live.phase === 'running') return
    const id = ++latestLive.current
    dispatchLive({ type: 'start' })
    try {
      const result = await fetchLiveJobs()
      if (id !== latestLive.current) return
      dispatchLive({ type: 'succeeded', result })
      void load()  // show the refreshed matches
    } catch (caught) {
      if (id !== latestLive.current) return  // signed out, unmounted, or superseded: ignore the late answer
      if (isUnauthorizedError(caught)) {
        dispatchLive({ type: 'reset' })
        onUnauthorized()
        return
      }
      dispatchLive({ type: 'failed', error: caught })
    }
  }

  if (!signedIn) return null
  const loading = view.phase === 'loading'
  const liveStatus = liveStatusText(live)
  const mix = view.phase === 'done' ? listingMix(view.data.matches) : 'none'
  const label = loading ? 'Finding matches…' : view.phase === 'error' ? 'Try again' : view.phase === 'done' ? 'Refresh matches' : 'Show my matches'
  const actionButton = (
    <button type="button" className={view.phase === 'idle' ? undefined : 'secondary-button'} onClick={() => void load()} disabled={loading}>
      {label}
    </button>
  )

  return (
    <div className="job-matches">
      <div className="inbox-header">
        <div>
          <h2 id="job-matches-title">Jobs inbox</h2>
          <p className="inbox-subtitle">{subtitleText(mix)}</p>
        </div>
        <div className="inbox-actions">
          <button type="button" className="secondary-button" onClick={() => void findLive()} disabled={live.phase === 'running'}>
            {LIVE_BUTTON_TEXT}
          </button>
          {view.phase !== 'idle' && actionButton}
        </div>
      </div>
      <p className="sample-banner" role="note">{bannerText(mix)}</p>
      <p className={`live-status${live.phase === 'error' ? ' is-error' : ''}`} role="status" aria-live="polite">{liveStatus ?? ''}</p>

      {view.phase === 'idle' && (
        <div className="empty-state">
          <span className="empty-icon"><InboxIcon size={28} /></span>
          <p>
            See which jobs fit your saved profile. Matching runs only when you ask, and uses your skills, target role, experience, location and work mode.
          </p>
          {actionButton}
        </div>
      )}
      {loading && (
        <ul className="job-list is-loading" aria-hidden="true">
          {[0, 1, 2].map((row) => <li key={row} className="skeleton-row"><span /><span /></li>)}
        </ul>
      )}
      {view.phase === 'error' && (
        <p className="recording-error" role="alert">
          {view.kind === 'jobs_unavailable' ? 'Job matching is temporarily unavailable.' : 'We could not load your matches. Please try again.'}
        </p>
      )}
      {view.phase === 'done' && (
        <div aria-live="polite">
          {view.data.matches.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon"><InboxIcon size={28} /></span>
              <p>{emptyStateText(view.data.status)}</p>
              {view.data.status === 'ok' && <p className="muted-note">{coverageText(view.data.coverage_percent)}</p>}
            </div>
          ) : (
            <>
              <p className="list-caption">{headerText(candidateName, view.data.matches.length, mix)}</p>
              <ul className="job-list">
                {view.data.matches.map((match) => (
                  <MatchRow
                    key={match.job.id}
                    match={match}
                    open={openId === match.job.id}
                    onToggle={() => setOpenId((current) => (current === match.job.id ? null : match.job.id))}
                  />
                ))}
              </ul>
              <aside className="why-box" aria-labelledby="why-these-jobs">
                <h3 id="why-these-jobs">Why these jobs</h3>
                <p>{whySuggestedText(view.data.matches)}</p>
                <p className="muted-note">{coverageText(view.data.coverage_percent)}</p>
              </aside>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default JobMatches
