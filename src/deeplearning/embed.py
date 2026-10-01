from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


def resolve_project_paths() -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[2]
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


def run_ingestion() -> None:
    pdf_path, db_path = resolve_project_paths()
    collection_name = "karumegam_collection"

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Could not find '{pdf_path}'. Please place the PDF in the project data folder."
        )

    print("🚀 Starting local RAG Ingestion Pipeline...\n")

    # ==========================================
    # STEP 1: Extract text from PDF using PyPDF
    # ==========================================
    print(f"📄 Reading raw text from: {pdf_path}...")
    full_text = extract_text_from_pdf(pdf_path)
    validate_extracted_text(full_text, pdf_path)
    print(f"✅ Text extraction complete. Total character count: {len(full_text)}")

    # ==========================================
    # STEP 2: Intelligently chunk the text
    # ==========================================
    print("✂️ Splitting text into meaningful chunks...")
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )

    chunks = text_splitter.split_text(full_text)
    if not chunks:
        raise ValueError(f"No chunks were generated for '{pdf_path}'.")

    print(f"✅ Generated {len(chunks)} text chunks.")

    ids = [f"chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"source": str(pdf_path), "chunk_index": i} for i in range(len(chunks))]

    # ==========================================
    # STEP 3: Setup Local Persistent Chroma DB
    # ==========================================
    print(f"🗄️ Initializing Chroma DB at: {db_path}")
    chroma_client = chromadb.PersistentClient(path=str(db_path))

    ollama_embedding = OllamaEmbeddingFunction(
        url="http://localhost:11434/api/embeddings",
        model_name="nomic-embed-text",
        timeout=300,
    )

    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=ollama_embedding,
    )

    # ==========================================
    # STEP 4: Embed and Store Chunks into Chroma
    # ==========================================
    print("🧠 Generating Nomic embeddings & saving chunks into the DB...")
    collection.add(
        documents=chunks,
        ids=ids,
        metadatas=metadatas,
    )
    print("✅ Vector database successfully built!\n")

    # ==========================================
    # STEP 5: Verification Search (Test Query)
    # ==========================================
    test_query = "What are the core topics discussed in this document?"
    print(f"🔍 Testing pipeline with a search query: '{test_query}'")

    search_results = collection.query(
        query_texts=[test_query],
        n_results=2,
    )

    print("\n--- 🔍 Top Retrieved Matches ---")
    for i, doc in enumerate(search_results["documents"][0]):
        chunk_id = search_results["ids"][0][i]
        print(f"\n[Result #{i + 1} | ID: {chunk_id}]")
        print(doc)
        print("-" * 40)


if __name__ == "__main__":
    run_ingestion()