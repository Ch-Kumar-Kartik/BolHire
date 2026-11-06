import type { ProfileView } from '../lib/candidateApi'

const WORK_MODES: Record<string, string> = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site', no_preference: 'No preference' }
const FIELD_LABELS: Record<string, string> = {
  name: 'Name', target_role: 'Target role', experience: 'Experience', technical_skills: 'Skills', preferred_location: 'Preferred location', work_mode: 'Work mode',
}

export function ProfileFields({ profile }: { profile: ProfileView }) {
  const experience = [
    profile.experience_years === null ? null : `${profile.experience_years} years`,
    profile.experience_level,
  ].filter((part): part is string => part !== null).join(', ')
  const rows: [string, string | null][] = [
    ['Name', profile.name],
    ['Target role', profile.target_role],
    ['Experience', experience || null],
    ['Skills', profile.technical_skills === null ? null : profile.technical_skills.join(', ') || 'None listed'],
    ['Preferred location', profile.preferred_location],
    ['Work mode', profile.work_mode === null ? null : WORK_MODES[profile.work_mode] ?? profile.work_mode],
  ]
  return (
    <dl className="profile-fields">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value ?? 'Not provided'}</dd>
        </div>
      ))}
      {profile.declined_fields.length > 0 && (
        <div>
          <dt>Chose not to say</dt>
          <dd>{profile.declined_fields.map((field) => FIELD_LABELS[field] ?? field).join(', ')}</dd>
        </div>
      )}
    </dl>
  )
}

interface CardProps {
  profile: ProfileView
  expiresAt: string
  notice: string | null
  busy: boolean
  onSignOut: () => void
}

export default function SavedProfileCard({ profile, expiresAt, notice, busy, onSignOut }: CardProps) {
  const expires = new Date(expiresAt)
  return (
    <div className="profile-card">
      {notice && <p role="status" className="success-note">{notice}</p>}
      <h3>Your saved profile</h3>
      <ProfileFields profile={profile} />
      <p className="muted-note">
        Access ends on {Number.isNaN(expires.getTime()) ? expiresAt : expires.toLocaleDateString('en-IN', { dateStyle: 'long' })}. Signing in does not extend it.
      </p>
      <button className="secondary-button" type="button" onClick={onSignOut} disabled={busy}>Sign out</button>
    </div>
  )
}
