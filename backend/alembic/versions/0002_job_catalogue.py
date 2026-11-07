"""Task 10: curated sample job catalogue and private candidate job interest. Explicit PostgreSQL SQL.

Adds two tables only. Existing tables and the bolhire_valid_skills() function from 0001 are reused, not changed;
the downgrade therefore drops only what this revision created.
"""
from alembic import op

revision = "0002_job_catalogue"
down_revision = "0001_candidate_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE jobs (
            id UUID NOT NULL,
            slug VARCHAR(80) NOT NULL,
            title VARCHAR(120) NOT NULL,
            company VARCHAR(120) NOT NULL,
            location_label VARCHAR(120) NOT NULL,
            description TEXT NOT NULL,
            employment_type VARCHAR(20) NOT NULL,
            role_key VARCHAR(60) NOT NULL,
            city_key VARCHAR(60),
            work_mode VARCHAR(20) NOT NULL,
            min_experience_years DOUBLE PRECISION,
            max_experience_years DOUBLE PRECISION,
            required_skills TEXT[] NOT NULL,
            is_sample BOOLEAN DEFAULT true NOT NULL,
            is_active BOOLEAN DEFAULT true NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT job_title_nonempty CHECK (btrim(title) <> ''),
            CONSTRAINT job_company_nonempty CHECK (btrim(company) <> ''),
            CONSTRAINT job_location_label_nonempty CHECK (btrim(location_label) <> ''),
            CONSTRAINT job_description_nonempty CHECK (btrim(description) <> ''),
            CONSTRAINT job_description_length CHECK (char_length(description) <= 4000),
            CONSTRAINT job_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            CONSTRAINT job_role_key_format CHECK (role_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            CONSTRAINT job_city_key_format CHECK (city_key IS NULL OR city_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            CONSTRAINT job_work_mode CHECK (work_mode IN ('remote','hybrid','onsite')),
            CONSTRAINT job_employment_type CHECK (employment_type IN ('full_time','part_time','contract','internship')),
            CONSTRAINT job_min_years_range CHECK (min_experience_years IS NULL OR (min_experience_years >= 0 AND min_experience_years <= 80)),
            CONSTRAINT job_max_years_range CHECK (max_experience_years IS NULL OR (max_experience_years >= 0 AND max_experience_years <= 80)),
            CONSTRAINT job_years_order CHECK (min_experience_years IS NULL OR max_experience_years IS NULL OR min_experience_years <= max_experience_years),
            CONSTRAINT job_skills_structure CHECK (bolhire_valid_skills(required_skills)),
            CONSTRAINT job_skills_required CHECK (cardinality(required_skills) >= 1),
            CONSTRAINT job_city_required CHECK (work_mode = 'remote' OR city_key IS NOT NULL),
            CONSTRAINT job_is_sample CHECK (is_sample),
            CONSTRAINT job_timestamps CHECK (updated_at >= created_at),
            UNIQUE (slug)
        )
    """)
    op.execute("""
        CREATE TABLE job_interests (
            candidate_id UUID NOT NULL,
            job_id UUID NOT NULL,
            consent_version VARCHAR(80) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT statement_timestamp() NOT NULL,
            PRIMARY KEY (candidate_id, job_id),
            CONSTRAINT job_interest_consent_version CHECK (btrim(consent_version) <> ''),
            FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE,
            FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
        )
    """)
    op.execute('CREATE INDEX ix_job_interests_job_id ON job_interests (job_id)')


def downgrade():
    op.drop_table("job_interests")
    op.drop_table("jobs")
