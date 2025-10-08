import pytest
from fastapi import HTTPException

from app import main


@pytest.fixture(autouse=True)
def _reset_private_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_private_uploads_path", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path.chmod(0o700)
    except OSError:
        pass
    yield


def test_resolve_private_upload_returns_file(tmp_path):
    target = tmp_path / "example.png"
    target.write_bytes(b"data")
    resolved = main._resolve_private_upload("../example.png")
    assert resolved == target


def test_resolve_private_upload_missing_file(tmp_path):
    with pytest.raises(HTTPException) as exc:
        main._resolve_private_upload("missing.png")
    assert exc.value.status_code == 404


def test_resolve_private_upload_rejects_directory_escape(tmp_path):
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"data")
    with pytest.raises(HTTPException):
        main._resolve_private_upload("../secret.png")
