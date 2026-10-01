"""Persist assistant answer mode."""

from alembic import op

revision = "0004_message_answer_mode"
down_revision = "0003_usernames"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS answer_mode VARCHAR(16)")


def downgrade() -> None:
    op.execute("ALTER TABLE conversation_messages DROP COLUMN IF EXISTS answer_mode")