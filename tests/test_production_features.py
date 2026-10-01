from pathlib import Path
import pytest
from pypdf import PdfWriter
from sqlalchemy import UniqueConstraint

from server.models import Conversation, ConversationMessage, DocumentChunk
from server.pgvector_rag import embed_texts, split_document, validate_pdf_file
from server.security import hash_password, hash_session_token, verify_password


def test_passwords_are_hashed_and_verified():
    password = "a-long-test-password"
    encoded = hash_password(password)

    assert encoded != password
    assert verify_password(password, encoded)
    assert not verify_password("incorrect-password", encoded)


def test_session_tokens_are_stored_as_one_way_hashes():
    token = "opaque-session-token"

    assert hash_session_token(token) != token
    assert hash_session_token(token) == hash_session_token(token)


def test_pdf_validation_rejects_non_pdf(tmp_path: Path):
    invalid_pdf = tmp_path / "not-a-pdf.pdf"
    invalid_pdf.write_bytes(b"not a PDF")

    with pytest.raises(ValueError, match="valid PDF"):
        validate_pdf_file(invalid_pdf)


def test_pdf_validation_accepts_a_well_formed_pdf(tmp_path: Path):
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as pdf_file:
        writer.write(pdf_file)

    assert validate_pdf_file(pdf_path) == 1


def test_chunk_validation_and_split():
    with pytest.raises(ValueError, match="Chunk size"):
        split_document("text", chunk_size=100, chunk_overlap=0)
    chunks = split_document("evidence " * 250, chunk_size=400, chunk_overlap=100)
    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_embedding_response_requires_expected_vector_dimension(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1] * 768]}

    monkeypatch.setattr("server.pgvector_rag.requests.post", lambda *args, **kwargs: FakeResponse())
    vectors = embed_texts(["A test passage"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 768


def test_conversation_messages_are_ordered_and_vector_dimensions_are_fixed():
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"conversation_id", "sequence"}
        for constraint in ConversationMessage.__table__.constraints
    )
    assert "user_id" in Conversation.__table__.c
    assert DocumentChunk.__table__.c.embedding.type.dim == 768