"""Persisted login rate limiting."""

from alembic import op
import sqlalchemy as sa

revision = "0002_login_throttle"
down_revision = "0001_pgvector_app"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash"),
    )


def downgrade() -> None:
    op.drop_table("login_throttles")