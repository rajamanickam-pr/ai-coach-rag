from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.models import Conversation, ConversationMessage


class ConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_user(self, user_id: UUID) -> list[Conversation]:
        return list(
            self.db.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
            ).all()
        )

    def create(self, user_id: UUID, title: str, collection_name: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title, collection_name=collection_name)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_for_user(self, conversation_id: UUID, user_id: UUID, *, lock: bool = False) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_messages(self, conversation_id: UUID) -> list[ConversationMessage]:
        return list(
            self.db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.sequence)
            ).all()
        )

    def get_last_sequence(self, conversation_id: UUID) -> int:
        return self.db.scalar(
            select(func.max(ConversationMessage.sequence)).where(
                ConversationMessage.conversation_id == conversation_id
            )
        ) or 0

    def get_recent_messages(self, conversation_id: UUID, limit: int) -> list[ConversationMessage]:
        rows = self.db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence.desc())
            .limit(limit)
        ).all()
        return list(reversed(rows))

    def add_message(self, message: ConversationMessage) -> None:
        self.db.add(message)

    def delete(self, conversation: Conversation) -> None:
        self.db.delete(conversation)
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()