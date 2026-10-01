import os
import re
from hashlib import sha256
from pathlib import Path

import chromadb
import dotenv
from anthropic import Anthropic
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


dotenv.load_dotenv()

DEFAULT_COLLECTION_NAME = "karumegam_collection"


def resolve_project_paths() -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[1]
    pdf_path = project_root / "data" / "Moral_Story.pdf"
    db_path = project_root / "collection_db"
    return pdf_path, db_path


def validate_extracted_text(full_text: str, pdf_path: Path | str | None = None) -> str:
    if not full_text or not full_text.strip():
        source = pdf_path if pdf_path is not None else "the selected PDF"
        raise ValueError(
            f"Could not extract any text from '{source}'. "
            "Please use a PDF with selectable text or enable OCR for scanned pages."
        )
    return full_text


def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    text_pages = []

    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            text_pages.append(text)

    return "\n".join(text_pages)


def get_document_hash(full_text: str) -> str:
    return sha256(full_text.encode("utf-8")).hexdigest()


def get_chroma_collection(db_path: Path, collection_name: str = DEFAULT_COLLECTION_NAME):
    chroma_client = chromadb.PersistentClient(path=str(db_path))
    embedding_function = OllamaEmbeddingFunction(
        url="http://localhost:11434/api/embeddings",
        model_name="nomic-embed-text",
        timeout=300,
    )
    return chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_function,
    )


def ensure_document_embedded(pdf_path: Path, db_path: Path, collection_name: str = DEFAULT_COLLECTION_NAME) -> bool:
    full_text = extract_text_from_pdf(pdf_path)
    validate_extracted_text(full_text, pdf_path)

    chunks = split_document(full_text)
    if not chunks:
        raise ValueError(f"No chunks were generated for '{pdf_path}'.")

    doc_hash = get_document_hash(full_text)
    collection = get_chroma_collection(db_path, collection_name)

    existing = collection.get(
        where={
            "$and": [
                {"source": str(pdf_path)},
                {"document_hash": doc_hash},
            ]
        },
        include=["metadatas"],
    )
    if existing and existing.get("ids"):
        print(f"✅ Document already embedded for '{pdf_path}'. Skipping re-ingestion.")
        return True

    existing_by_source = collection.get(
        where={"source": str(pdf_path)},
        include=["metadatas"],
    )
    if existing_by_source and existing_by_source.get("ids"):
        print(f"♻️ Existing version found for '{pdf_path}'. Replacing outdated chunks.")
        collection.delete(where={"source": str(pdf_path)})

    ids = [f"chunk_{i}_{doc_hash[:8]}" for i in range(len(chunks))]
    metadatas = [
        {"source": str(pdf_path), "chunk_index": i, "document_hash": doc_hash}
        for i in range(len(chunks))
    ]

    print("🧠 Embedding and indexing document chunks...")
    collection.add(documents=chunks, ids=ids, metadatas=metadatas)
    print(f"✅ Indexed {len(chunks)} chunks for '{pdf_path}'.")
    return True


def split_document(full_text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return splitter.split_text(full_text)


def lexical_overlap_score(document: str, query: str) -> float:
    query_tokens = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))
    doc_tokens = set(re.findall(r"[a-zA-Z0-9]+", document.lower()))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens)
    return overlap / len(query_tokens)


def hybrid_search(collection, query: str, source_path: Path | str | None = None, n_results: int = 4):
    where_filter = {"source": str(source_path)} if source_path else None
    vector_results = collection.query(
        query_texts=[query],
        n_results=max(5, n_results * 3),
        where=where_filter,
    )

    documents = vector_results.get("documents", [[]])[0]
    ids = vector_results.get("ids", [[]])[0]
    metadatas = vector_results.get("metadatas", [[]])[0]
    distances = vector_results.get("distances", [[]])[0]

    ranked = []
    for idx, document in enumerate(documents):
        if not document or not str(document).strip():
            continue

        distance = distances[idx] if idx < len(distances) else 0.0
        vector_score = 1.0 / (1.0 + float(distance)) if distance is not None else 1.0
        lexical_score = lexical_overlap_score(document, query)
        combined_score = (vector_score * 0.7) + (lexical_score * 0.3)

        ranked.append(
            {
                "id": ids[idx],
                "document": document,
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "vector_score": vector_score,
                "lexical_score": lexical_score,
                "combined_score": combined_score,
            }
        )

    ranked.sort(key=lambda item: item["combined_score"], reverse=True)
    return ranked[:n_results]


def rerank_documents(documents: list[dict]) -> list[dict]:
    return sorted(documents, key=lambda item: item["combined_score"], reverse=True)


def build_grounded_context(retrieved_documents: list[dict]) -> str:
    context_parts = []
    for idx, item in enumerate(retrieved_documents, start=1):
        metadata = item.get("metadata", {})
        source = metadata.get("source", "unknown-source")
        chunk_index = metadata.get("chunk_index", idx)
        context_parts.append(
            f"[Document {idx}] Source: {source} | Chunk: {chunk_index}\n{item['document']}"
        )
    return "\n\n---\n\n".join(context_parts)


def get_anthropic_client() -> Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your environment or .env file before asking questions."
        )
    return Anthropic(api_key=api_key)


def answer_question_with_anthropic(question: str, retrieved_documents: list[dict]) -> str:
    if not retrieved_documents:
        return "I could not find relevant grounded context in the indexed document for that question."

    context = build_grounded_context(retrieved_documents)
    client = get_anthropic_client()
    system_prompt = (
        "You are a careful answering assistant. Use only the provided document context. "
        "If the answer is not supported by the context, say that the information is not available in the document. "
        "Answer briefly and clearly, and cite the document source/chunk when helpful."
    )
    user_prompt = (
        f"Question: {question}\n\nReferenced document context:\n{context}\n\n"
        "Give a grounded answer using only this context."
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def ask_questions_loop(collection, pdf_path: Path | str):
    print("\n💬 Ask questions about the document. Type 'exit' to close.")

    while True:
        user_question = input("\nYour question: ").strip()
        if not user_question:
            continue
        if user_question.lower() in {"exit", "quit", "q"}:
            print("Goodbye!")
            break

        retrieved = hybrid_search(collection, user_question, source_path=pdf_path, n_results=4)
        retrieved = rerank_documents(retrieved)

        if not retrieved:
            print("No relevant document chunks were found for that question.")
            continue

        answer = answer_question_with_anthropic(user_question, retrieved)
        print("\nAnswer:")
        print(answer)
        print("\nRelevant passages:")
        for index, item in enumerate(retrieved, start=1):
            metadata = item.get("metadata", {})
            print(f"[{index}] Source={metadata.get('source')} | chunk={metadata.get('chunk_index')} | score={item['combined_score']:.3f}")


def run_ingestion() -> None:
    pdf_path, db_path = resolve_project_paths()
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Could not find '{pdf_path}'. Please place the PDF in the project data folder."
        )

    print("🚀 Starting local RAG Ingestion Pipeline...\n")
    print(f"📄 Reading raw text from: {pdf_path}...")
    full_text = extract_text_from_pdf(pdf_path)
    validate_extracted_text(full_text, pdf_path)
    print(f"✅ Text extraction complete. Total character count: {len(full_text)}")

    chunks = split_document(full_text)
    if not chunks:
        raise ValueError(f"No chunks were generated for '{pdf_path}'.")

    print(f"✅ Generated {len(chunks)} text chunks.")
    collection = get_chroma_collection(db_path)
    ensure_document_embedded(pdf_path, db_path)

    print("\n--- 🔍 Sample retrieval check ---")
    sample_query = "Why did Arun return to the village and help?"
    sample_results = hybrid_search(collection, sample_query, source_path=pdf_path, n_results=2)
    for index, item in enumerate(sample_results, start=1):
        print(f"\n[Sample #{index}] {item['document'][:400]}...")

    ask_questions_loop(collection, pdf_path)


if __name__ == "__main__":
    try:
        run_ingestion()
    except RuntimeError as exc:
        print(f"\n⚠️ {exc}")
        print("Set ANTHROPIC_API_KEY to use grounded Anthropic answers.")
    except Exception as exc:
        print(f"\n❌ Error: {exc}")
        raise