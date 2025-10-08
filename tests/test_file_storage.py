import pytest
from fastapi import HTTPException

from app.services.file_storage import delete_stored_file, sanitize_filename
from pathlib import Path


def test_sanitize_filename_replaces_unsafe_characters():
    assert sanitize_filename("../dangerous/../file?.pdf") == "file_.pdf"


def test_sanitize_filename_handles_empty_values():
    assert sanitize_filename("") == "upload"


def test_delete_stored_file_rejects_outside_paths(tmp_path: Path):
    uploads_root = tmp_path / "static" / "uploads"
    uploads_root.mkdir(parents=True)
    with pytest.raises(HTTPException):
        delete_stored_file("../etc/passwd", uploads_root)
