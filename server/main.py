import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server.rag_service import ask_document_question, get_document_paths
from server.rag_pipeline import ensure_document_embedded


class AskRequest(BaseModel):
    question: str


app = FastAPI(title="AI Coach RAG API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "AI Coach RAG API is running."}


@app.get("/api/document")
def get_document_info():
    pdf_path, db_path = get_document_paths()
    return {
        "pdf_path": str(pdf_path),
        "db_path": str(db_path),
        "pdf_exists": pdf_path.exists(),
    }


@app.post("/api/index")
def index_document():
    try:
        pdf_path, db_path = get_document_paths()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found at {pdf_path}")
        ensure_document_embedded(pdf_path, db_path)
        return {"status": "ok", "message": f"Document indexed: {pdf_path}"}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/ask")
def answer_question(payload: AskRequest):
    try:
        result = ask_document_question(payload.question)
        return {
            "status": "ok",
            "answer": result["answer"],
            "sources": result["sources"],
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
