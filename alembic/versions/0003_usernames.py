"""Add unique usernames to user accounts."""

from alembic import op

revision = "0003_usernames"
down_revision = "0002_login_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64)")
    op.execute(
        """
        WITH normalized AS (
            SELECT
                id,
                COALESCE(
                    NULLIF(LEFT(REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)), '[^a-z0-9_.-]', '', 'g'), 48), ''),
                    'user'
                ) AS base_name
            FROM users
        ), ranked AS (
            SELECT id, base_name, COUNT(*) OVER (PARTITION BY base_name) AS collisions
            FROM normalized
        )
        UPDATE users AS target
        SET username = CASE
            WHEN ranked.collisions = 1 THEN ranked.base_name
            ELSE LEFT(ranked.base_name, 48) || '_' || LEFT(REPLACE(target.id::text, '-', ''), 8)
        END
        FROM ranked
        WHERE target.id = ranked.id AND target.username IS NULL
        """
    )
    op.execute("ALTER TABLE users ALTER COLUMN username SET NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_username")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS username")