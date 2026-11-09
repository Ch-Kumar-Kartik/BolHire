"""Task 11b: provenance for real (imported) job listings. Explicit PostgreSQL SQL.

Adds nullable provenance columns to `jobs` and replaces the body of `job_is_sample` (same name) so a row is EITHER a sample
(no provenance at all) OR a real listing (source, external_id, an https source_url and retrieved_at all set). The 24 seeded
sample rows stay valid unchanged. A plain UNIQUE (source, external_id) only binds real rows, because PostgreSQL treats NULLs as
distinct and samples can never carry an external_id.

The downgrade never deletes data: it refuses while any real row exists.
"""
from alembic import op

revision = "0003_real_job_sources"
down_revision = "0002_job_catalogue"
branch_labels = None
depends_on = None

PROVENANCE = """
    (source IS NULL AND is_sample AND external_id IS NULL AND source_url IS NULL
       AND retrieved_at IS NULL AND posted_at IS NULL)
    OR (source IS NOT NULL AND NOT is_sample AND external_id IS NOT NULL AND btrim(external_id) <> ''
       AND source_url IS NOT NULL AND source_url ~ '^https://' AND retrieved_at IS NOT NULL)
"""


def upgrade():
    op.execute("""
        ALTER TABLE jobs
            ADD COLUMN source VARCHAR(40),
            ADD COLUMN external_id VARCHAR(100),
            ADD COLUMN source_url VARCHAR(2048),
            ADD COLUMN retrieved_at TIMESTAMP WITH TIME ZONE,
            ADD COLUMN posted_at TIMESTAMP WITH TIME ZONE
    """)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT job_is_sample")
    op.execute(f"ALTER TABLE jobs ADD CONSTRAINT job_is_sample CHECK ({PROVENANCE})")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT job_source_format CHECK (source IS NULL OR source ~ '^[a-z0-9]+(-[a-z0-9]+)*$')")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT uq_jobs_source_external_id UNIQUE (source, external_id)")


def downgrade():
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM jobs WHERE source IS NOT NULL) THEN
                RAISE EXCEPTION 'Downgrade refused: imported (non-sample) jobs exist. Remove them deliberately first; nothing was changed.';
            END IF;
        END
        $$
    """)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT uq_jobs_source_external_id")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT job_source_format")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT job_is_sample")
    op.execute("""
        ALTER TABLE jobs
            DROP COLUMN posted_at,
            DROP COLUMN retrieved_at,
            DROP COLUMN source_url,
            DROP COLUMN external_id,
            DROP COLUMN source
    """)
    op.execute("ALTER TABLE jobs ADD CONSTRAINT job_is_sample CHECK (is_sample)")
