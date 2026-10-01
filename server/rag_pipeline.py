from pathlib import Path

from pypdf import PdfReader


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
