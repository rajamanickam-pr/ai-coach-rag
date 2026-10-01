import pytest
from uuid import uuid4

from server.models import Conversation
from server.services.conversations import ConversationService


class FakeConversationRepository:
    def __init__(self):
        self.conversation = Conversation(id=uuid4(), collection_name="research_docs", title="Hello")
        self.messages = []

    def get_for_user(self, conversation_id, user_id, lock=False):
        return self.conversation

    def get_last_sequence(self, conversation_id):
        return len(self.messages)

    def get_recent_messages(self, conversation_id, limit):
        return self.messages[-limit:]

    def add_message(self, message):
        self.messages.append(message)

    def commit(self):
        pass

    def rollback(self):
        self.messages.clear()


def test_auto_mode_uses_direct_llm_when_retrieval_is_empty(monkeypatch):
    repository = FakeConversationRepository()
    monkeypatch.setattr("server.services.conversations.hybrid_search", lambda *args, **kwargs: [])
    direct_calls = []
    monkeypatch.setattr(
        "server.services.conversations.answer_question_directly",
        lambda question, history: direct_calls.append(question) or "Hello! How can I help?",
    )

    result = ConversationService(None, repository).ask(
        repository.conversation.id, uuid4(), "hello", "research_docs"
    )

    assert result["answer"] == "Hello! How can I help?"
    assert result["answer_mode"] == "direct"
    assert result["sources"] == []
    assert direct_calls == ["hello"]
    assert repository.messages[-1].answer_mode == "direct"


def test_auto_mode_uses_relevant_retrieved_passages(monkeypatch):
    repository = FakeConversationRepository()
    retrieved = [{
        "combined_score": 0.7,
        "id": "chunk-1",
        "metadata": {"source": "guide.pdf", "chunk_index": 0},
        "document": "The retention period is seven years.",
    }]
    rag_calls = []
    direct_calls = []
    monkeypatch.setattr("server.services.conversations.hybrid_search", lambda *args, **kwargs: retrieved)
    monkeypatch.setattr(
        "server.services.conversations.answer_question_with_anthropic",
        lambda question, passages, history: rag_calls.append(passages) or "Seven years.",
    )
    monkeypatch.setattr(
        "server.services.conversations.answer_question_directly",
        lambda question, history: direct_calls.append(question) or "A general answer",
    )

    result = ConversationService(None, repository).ask(
        repository.conversation.id, uuid4(), "What is the retention period?", "research_docs"
    )

    assert result["answer"] == "Seven years."
    assert result["answer_mode"] == "rag"
    assert result["sources"][0]["source"] == "guide.pdf"
    assert rag_calls == [retrieved]
    assert direct_calls == []


def test_auto_mode_uses_direct_llm_when_retrieval_score_is_low(monkeypatch):
    repository = FakeConversationRepository()
    retrieved = [{"combined_score": 0.45, "id": "chunk-1", "metadata": {}, "document": "Unrelated passage"}]
    monkeypatch.setattr("server.services.conversations.hybrid_search", lambda *args, **kwargs: retrieved)
    monkeypatch.setattr(
        "server.services.conversations.answer_question_with_anthropic",
        lambda *args, **kwargs: pytest.fail("Low-confidence passages must not be used in auto mode"),
    )
    monkeypatch.setattr(
        "server.services.conversations.answer_question_directly",
        lambda question, history: "A general answer",
    )

    result = ConversationService(None, repository).ask(
        repository.conversation.id, uuid4(), "Hi, how are you?", "research_docs"
    )

    assert result["answer"] == "A general answer"
    assert result["answer_mode"] == "direct"
    assert result["sources"] == []


def test_direct_mode_skips_vector_search(monkeypatch):
    repository = FakeConversationRepository()
    monkeypatch.setattr(
        "server.services.conversations.hybrid_search",
        lambda *args, **kwargs: pytest.fail("Direct mode must not run retrieval"),
    )
    monkeypatch.setattr(
        "server.services.conversations.answer_question_directly",
        lambda question, history: "A general answer",
    )

    result = ConversationService(None, repository).ask(
        repository.conversation.id, uuid4(), "hello", "research_docs", mode="direct"
    )

    assert result["answer_mode"] == "direct"
    assert result["sources"] == []


def test_auto_mode_falls_back_when_passages_do_not_answer_question(monkeypatch):
    repository = FakeConversationRepository()
    retrieved = [{"combined_score": 0.8, "id": "chunk-1", "metadata": {}, "document": "Unrelated passage"}]
    monkeypatch.setattr("server.services.conversations.hybrid_search", lambda *args, **kwargs: retrieved)
    monkeypatch.setattr(
        "server.services.conversations.answer_question_with_anthropic",
        lambda *args, **kwargs: "[[NO_GROUNDED_ANSWER]]",
    )
    monkeypatch.setattr(
        "server.services.conversations.answer_question_directly",
        lambda question, history: "A general answer",
    )

    result = ConversationService(None, repository).ask(
        repository.conversation.id, uuid4(), "hello", "research_docs"
    )

    assert result["answer"] == "A general answer"
    assert result["answer_mode"] == "direct"
    assert result["sources"] == []


def test_conversation_title_is_trimmed_and_validated():
    assert ConversationService._validate_title("  Project notes  ") == "Project notes"

    with pytest.raises(ValueError, match="cannot be empty"):
        ConversationService._validate_title("   ")

    with pytest.raises(ValueError, match="200 characters"):
        ConversationService._validate_title("x" * 201)


def test_conversation_collection_name_is_validated():
    assert ConversationService._validate_collection_name("research_docs-1") == "research_docs-1"

    with pytest.raises(ValueError, match="Collection name"):
        ConversationService._validate_collection_name("bad name")


def test_conversation_rename_uses_patch_route():
    from server.main import app

    rename_operations = app.openapi()["paths"]["/api/conversations/{conversation_id}"]
    assert "patch" in rename_operations