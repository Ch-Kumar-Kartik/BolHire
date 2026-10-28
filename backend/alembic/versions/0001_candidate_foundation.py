"""Candidate database foundation. Explicit PostgreSQL schema snapshot."""
from alembic import op

revision = "0001_candidate_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION bolhire_valid_skills(items text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
        SELECT CASE WHEN items IS NULL THEN true ELSE
         cardinality(items) <= 20
         AND (cardinality(items) = 0 OR (array_ndims(items) = 1 AND array_lower(items, 1) = 1))
         AND NOT EXISTS (SELECT 1 FROM unnest(items) AS item
                         WHERE item IS NULL OR length(btrim(item)) = 0 OR length(item) > 60)
         AND cardinality(items) = (SELECT count(DISTINCT item COLLATE "C") FROM unnest(items) AS item)
         END
        $$
    """)
    op.execute("""
        CREATE FUNCTION bolhire_valid_declines(items text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
        SELECT items IS NOT NULL
         AND cardinality(items) <= 6
         AND (cardinality(items) = 0 OR (array_ndims(items) = 1 AND array_lower(items, 1) = 1))
         AND NOT EXISTS (SELECT 1 FROM unnest(items) AS item WHERE item IS NULL OR item NOT IN
            ('name', 'target_role', 'experience', 'technical_skills', 'preferred_location', 'work_mode'))
         AND cardinality(items) = (SELECT count(DISTINCT item COLLATE "C") FROM unnest(items) AS item)
        $$
    """)
    op.execute("""
        CREATE TABLE candidates (
            id UUID NOT NULL,
            recovery_secret_hash BYTEA NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT candidate_hash_length CHECK (octet_length(recovery_secret_hash) = 32),
            UNIQUE (recovery_secret_hash)
        )
    """)
    op.execute("""
        CREATE TABLE candidate_auth_sessions (
            id UUID NOT NULL,
            candidate_id UUID NOT NULL,
            token_hash BYTEA NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT auth_hash_length CHECK (octet_length(token_hash) = 32),
            CONSTRAINT auth_timestamps CHECK (expires_at > created_at),
            FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE,
            UNIQUE (token_hash)
        )
    """)
    op.execute('CREATE INDEX ix_candidate_auth_sessions_candidate_id ON candidate_auth_sessions (candidate_id)')
    op.execute('CREATE INDEX ix_candidate_auth_sessions_expires_at ON candidate_auth_sessions (expires_at)')
    op.execute("""
        CREATE TABLE candidate_profiles (
            candidate_id UUID NOT NULL,
            name VARCHAR(80),
            target_role VARCHAR(120),
            experience_years DOUBLE PRECISION,
            experience_level VARCHAR(60),
            technical_skills TEXT[],
            preferred_location VARCHAR(120),
            work_mode VARCHAR(20),
            declined_fields TEXT[] DEFAULT '{}'::text[] NOT NULL,
            revision BIGINT DEFAULT 1 NOT NULL,
            consent_version VARCHAR(80) NOT NULL,
            consented_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (candidate_id),
            CONSTRAINT profile_name_nonempty CHECK (name IS NULL OR length(btrim(name)) > 0),
            CONSTRAINT profile_target_role_nonempty CHECK (target_role IS NULL OR length(btrim(target_role)) > 0),
            CONSTRAINT profile_experience_level_nonempty CHECK (experience_level IS NULL OR length(btrim(experience_level)) > 0),
            CONSTRAINT profile_preferred_location_nonempty CHECK (preferred_location IS NULL OR length(btrim(preferred_location)) > 0),
            CONSTRAINT profile_years_range CHECK (experience_years IS NULL OR (experience_years >= 0 AND experience_years <= 80)),
            CONSTRAINT profile_work_mode CHECK (work_mode IS NULL OR work_mode IN ('remote','hybrid','onsite','no_preference')),
            CONSTRAINT profile_skills_structure CHECK (bolhire_valid_skills(technical_skills)),
            CONSTRAINT profile_declines_structure CHECK (bolhire_valid_declines(declined_fields)),
            CONSTRAINT profile_declined_name CHECK (NOT ('name' = ANY(declined_fields)) OR name IS NULL),
            CONSTRAINT profile_declined_target_role CHECK (NOT ('target_role' = ANY(declined_fields)) OR target_role IS NULL),
            CONSTRAINT profile_declined_technical_skills CHECK (NOT ('technical_skills' = ANY(declined_fields)) OR technical_skills IS NULL),
            CONSTRAINT profile_declined_preferred_location CHECK (NOT ('preferred_location' = ANY(declined_fields)) OR preferred_location IS NULL),
            CONSTRAINT profile_declined_work_mode CHECK (NOT ('work_mode' = ANY(declined_fields)) OR work_mode IS NULL),
            CONSTRAINT profile_declined_experience CHECK (NOT ('experience' = ANY(declined_fields)) OR (experience_years IS NULL AND experience_level IS NULL)),
            CONSTRAINT profile_positive_revision CHECK (revision > 0),
            CONSTRAINT profile_consent_version CHECK (length(btrim(consent_version)) > 0),
            CONSTRAINT profile_timestamps CHECK (expires_at > created_at AND updated_at >= created_at),
            FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE
        )
    """)
    op.execute('CREATE INDEX ix_candidate_profiles_expires_at ON candidate_profiles (expires_at)')


def downgrade():
    op.drop_table("candidate_auth_sessions")
    op.drop_table("candidate_profiles")
    op.drop_table("candidates")
    op.execute("DROP FUNCTION bolhire_valid_declines(text[])")
    op.execute("DROP FUNCTION bolhire_valid_skills(text[])")
