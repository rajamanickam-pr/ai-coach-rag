"""Initial relational, RBAC, conversation, and pgvector schema."""

from alembic import op
from server.models import Base

revision = "0001_pgvector_app"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    initial_tables = [
        Base.metadata.tables[name]
        for name in (
            "roles",
            "users",
            "login_sessions",
            "conversations",
            "conversation_messages",
            "documents",
            "document_chunks",
        )
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=initial_tables)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())