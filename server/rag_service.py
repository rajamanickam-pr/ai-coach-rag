import os
from pathlib import Path

from fastapi import HTTPException

from server.rag_pipeline import (
    answer_question_with_anthropic,
    ensure_document_embedded,
    get_chroma_collection,
    hybrid_search,
    rerank_documents,
    resolve_project_paths,
)


def get_document_paths() -> tuple[Path, Path]:
    return resolve_project_paths()


def get_indexed_collection():
    pdf_path, db_path = get_document_paths()
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"Document not found at {pdf_path}")
    collection = get_chroma_collection(db_path)
    ensure_document_embedded(pdf_path, db_path)
    return collection, pdf_path


def ask_document_question(question: str):
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set. Add it to your environment before asking a question.",
        )

    collection, pdf_path = get_indexed_collection()
    retrieved = hybrid_search(collection, question, source_path=pdf_path, n_results=4)
    ranked = rerank_documents(retrieved)

    answer = answer_question_with_anthropic(question, ranked)

    return {
        "answer": answer,
        "sources": [
            {
                "id": item["id"],
                "source": item["metadata"].get("source", str(pdf_path)),
                "chunk_index": item["metadata"].get("chunk_index", 0),
                "score": round(float(item["combined_score"]), 4),
                "text": item["document"][:600],
            }
            for item in ranked
        ],
    }
