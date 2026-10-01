import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from server.models import Conversation, ConversationMessage
from server.pgvector_rag import (
    NO_GROUNDED_ANSWER,
    answer_question_directly,
    answer_question_with_anthropic,
    hybrid_search,
)
from server.repositories.conversations import ConversationRepository

logger = logging.getLogger(__name__)
COLLECTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,62}$")
AUTO_RETRIEVAL_SCORE_THRESHOLD = 0.5


class ConversationNotFoundError(Exception):
    pass


class AnswerGenerationError(Exception):
    pass


class ConversationService:
    def __init__(self, db: Session, repository: ConversationRepository | None = None):
        self.repository = repository or ConversationRepository(db)
        self.db = db

    def list_for_user(self, user_id: UUID) -> list[Conversation]:
        return self.repository.list_for_user(user_id)

    def create(self, user_id: UUID, title: str, collection_name: str) -> Conversation:
        return self.repository.create(
            user_id,
            self._validate_title(title),
            self._validate_collection_name(collection_name),
        )

    def get(self, conversation_id: UUID, user_id: UUID) -> tuple[Conversation, list[ConversationMessage]]:
        conversation = self._get_owned(conversation_id, user_id)
        return conversation, self.repository.list_messages(conversation.id)

    def rename(self, conversation_id: UUID, user_id: UUID, title: str) -> Conversation:
        conversation = self._get_owned(conversation_id, user_id)
        conversation.title = self._validate_title(title)
        conversation.updated_at = datetime.now(UTC)
        self.repository.commit()
        return conversation

    def delete(self, conversation_id: UUID, user_id: UUID) -> None:
        self.repository.delete(self._get_owned(conversation_id, user_id))

    def ask(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        collection_name: str,
        mode: str = "auto",
    ) -> dict:
        if mode not in {"auto", "rag", "direct"}:
            raise ValueError("Answer mode must be auto, rag, or direct.")
        collection_name = self._validate_collection_name(collection_name)
        conversation = self._get_owned(conversation_id, user_id, lock=True)
        if collection_name != conversation.collection_name:
            conversation.collection_name = collection_name

        last_sequence = self.repository.get_last_sequence(conversation.id)
        history = [
            {"role": message.role, "content": message.content}
            for message in self.repository.get_recent_messages(conversation.id, 12)
        ]
        self.repository.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=last_sequence + 1,
                role="user",
                content=question,
            )
        )
        try:
            if mode == "direct":
                retrieved = []
                answer_mode = "direct"
                answer = answer_question_directly(question, history)
            else:
                retrieved = hybrid_search(self.db, question, collection_name, n_results=4)
                answer_mode = "rag"
                if mode == "auto" and (
                    not retrieved
                    or max(float(item["combined_score"]) for item in retrieved)
                    < AUTO_RETRIEVAL_SCORE_THRESHOLD
                ):
                    answer_mode = "direct"
                    answer = answer_question_directly(question, history)
                    retrieved = []
                else:
                    answer = answer_question_with_anthropic(question, retrieved, history)
                    if answer.strip() == NO_GROUNDED_ANSWER:
                        if mode == "auto":
                            answer_mode = "direct"
                            answer = answer_question_directly(question, history)
                            retrieved = []
                        else:
                            answer = "I could not find relevant grounded context in the selected collection for that question."
            sources = [
                {
                    "id": item["id"],
                    "source": item["metadata"].get("source", "unknown"),
                    "chunk_index": item["metadata"].get("chunk_index", 0),
                    "score": round(float(item["combined_score"]), 4),
                    "text": item["document"][:600],
                }
                for item in retrieved
            ]
            self.repository.add_message(
                ConversationMessage(
                    conversation_id=conversation.id,
                    sequence=last_sequence + 2,
                    role="assistant",
                    content=answer,
                    sources=sources,
                    answer_mode=answer_mode,
                )
            )
            if last_sequence == 0:
                conversation.title = question[:77] + ("..." if len(question) > 80 else "")
            conversation.updated_at = datetime.now(UTC)
            self.repository.commit()
            return {"status": "ok", "answer": answer, "sources": sources, "answer_mode": answer_mode}
        except Exception as exc:
            self.repository.rollback()
            logger.exception(
                "Question answering failed",
                extra={"user_id": str(user_id), "conversation_id": str(conversation_id)},
            )
            raise AnswerGenerationError from exc

    def _get_owned(self, conversation_id: UUID, user_id: UUID, *, lock: bool = False) -> Conversation:
        conversation = self.repository.get_for_user(conversation_id, user_id, lock=lock)
        if conversation is None:
            raise ConversationNotFoundError
        return conversation

    @staticmethod
    def _validate_title(title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise ValueError("Conversation title cannot be empty.")
        if len(normalized) > 200:
            raise ValueError("Conversation title must be 200 characters or fewer.")
        return normalized

    @staticmethod
    def _validate_collection_name(collection_name: str) -> str:
        if not COLLECTION_NAME_PATTERN.fullmatch(collection_name):
            raise ValueError(
                "Collection name must be 3-63 characters using letters, numbers, underscores, or hyphens."
            )
        return collection_name