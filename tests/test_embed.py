from pathlib import Path

import pytest


def test_resolve_project_paths_uses_valid_data_pdf():
    from server.rag_pipeline import resolve_project_paths

    pdf_path, db_path = resolve_project_paths()

    assert pdf_path == Path(__file__).resolve().parents[1] / "data" / "Moral_Story.pdf"
    assert db_path == Path(__file__).resolve().parents[1] / "collection_db"


def test_validate_extracted_text_rejects_empty_pdf_text():
    from server.rag_pipeline import validate_extracted_text

    with pytest.raises(ValueError, match="Could not extract any text"):
        validate_extracted_text("")
