from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile, status
from starlette.datastructures import Headers

from app.api.routes import essential8 as essential8_routes


def _make_upload(data: bytes, filename: str, content_type: str = "application/octet-stream") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


class _InterruptedUpload:
    def __init__(self, filename: str, *, first_chunk: bytes, error: Exception):
        self.filename = filename
        self.content_type = "application/pdf"
        self._first_chunk = first_chunk
        self._error = error
        self._reads = 0
        self.closed = False

    async def read(self, _size: int) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return self._first_chunk
        raise self._error

    async def close(self) -> None:
        self.closed = True


def _configure_upload_dependencies(monkeypatch, tmp_path):
    access = AsyncMock()
    add_calls: list[dict] = []
    upload_dir = tmp_path / "compliance" / "essential8"

    async def fake_add_requirement_evidence(**kwargs):
        add_calls.append(kwargs)
        return {
            "id": len(add_calls),
            "version_number": len(add_calls),
            "uploaded_at": None,
            "is_current": True,
            **kwargs,
        }

    monkeypatch.setattr(essential8_routes, "_assert_company_compliance_access", access)
    monkeypatch.setattr(
        essential8_routes.essential8_repo,
        "get_essential8_requirement",
        AsyncMock(return_value={"id": 17, "control_id": 3}),
    )
    monkeypatch.setattr(essential8_routes.essential8_repo, "add_requirement_evidence", fake_add_requirement_evidence)
    monkeypatch.setattr(essential8_routes, "_requirement_upload_dir", lambda: upload_dir)
    return access, add_calls, upload_dir


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    ("filename", "expected_name"),
    [
        ("../evidence.pdf", "evidence.pdf"),
        ("/var/tmp/proof.txt", "proof.txt"),
        ("..\\..\\windows\\report.csv", "report.csv"),
    ],
)
async def test_upload_requirement_evidence_uses_safe_metadata_and_contained_storage(
    monkeypatch,
    tmp_path,
    filename: str,
    expected_name: str,
):
    access, add_calls, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)

    response = await essential8_routes.upload_requirement_evidence(
        company_id=9,
        requirement_id=17,
        title="Quarterly evidence",
        description="Attached for review",
        evidence_file=_make_upload(b"proof-bytes", filename, "application/pdf"),
        user={"id": 41, "company_id": 9},
    )

    access.assert_awaited_once_with({"id": 41, "company_id": 9}, 9, write=True)
    assert response["file_name"] == expected_name
    assert len(add_calls) == 1
    stored_relative_path = add_calls[0]["file_path"]
    assert stored_relative_path.startswith("compliance/essential8/")
    assert stored_relative_path.endswith(Path(expected_name).suffix)
    assert stored_relative_path.rsplit("/", 1)[-1] != expected_name
    stored_path = tmp_path / Path(stored_relative_path)
    assert stored_path.parent == upload_dir
    assert stored_path.read_bytes() == b"proof-bytes"


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("filename", ["", ".", "..", "/", "\\", "   "])
async def test_upload_requirement_evidence_rejects_empty_or_invalid_names(monkeypatch, tmp_path, filename: str):
    _, _, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await essential8_routes.upload_requirement_evidence(
            company_id=9,
            requirement_id=17,
            title="Quarterly evidence",
            description=None,
            evidence_file=_make_upload(b"proof-bytes", filename, "application/pdf"),
            user={"id": 41, "company_id": 9},
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert not upload_dir.exists()


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_truncates_long_metadata_name(monkeypatch, tmp_path):
    _, add_calls, _ = _configure_upload_dependencies(monkeypatch, tmp_path)
    filename = f"{'a' * 400}.pdf"

    response = await essential8_routes.upload_requirement_evidence(
        company_id=9,
        requirement_id=17,
        title="Quarterly evidence",
        description=None,
        evidence_file=_make_upload(b"proof-bytes", filename, "application/pdf"),
        user={"id": 41, "company_id": 9},
    )

    assert len(response["file_name"]) == 255
    assert response["file_name"].endswith(".pdf")
    assert len(add_calls[0]["file_path"].rsplit("/", 1)[-1]) < 255


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_same_name_uploads_store_distinct_files(monkeypatch, tmp_path):
    _, add_calls, _ = _configure_upload_dependencies(monkeypatch, tmp_path)
    uuids = iter([SimpleNamespace(hex="first"), SimpleNamespace(hex="second")])
    monkeypatch.setattr(essential8_routes, "uuid4", lambda: next(uuids))

    first = await essential8_routes.upload_requirement_evidence(
        company_id=9,
        requirement_id=17,
        title="First evidence",
        description=None,
        evidence_file=_make_upload(b"first", "report.pdf", "application/pdf"),
        user={"id": 41, "company_id": 9},
    )
    second = await essential8_routes.upload_requirement_evidence(
        company_id=9,
        requirement_id=17,
        title="Second evidence",
        description=None,
        evidence_file=_make_upload(b"second", "report.pdf", "application/pdf"),
        user={"id": 41, "company_id": 9},
    )

    assert first["file_path"] != second["file_path"]
    first_path = tmp_path / Path(add_calls[0]["file_path"])
    second_path = tmp_path / Path(add_calls[1]["file_path"])
    assert first_path.read_bytes() == b"first"
    assert second_path.read_bytes() == b"second"


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_retries_on_storage_collision_without_removing_existing_file(
    monkeypatch,
    tmp_path,
):
    _, add_calls, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)
    uuids = iter(
        [
            SimpleNamespace(hex="collision"),
            SimpleNamespace(hex="replacement"),
        ]
    )
    monkeypatch.setattr(essential8_routes, "uuid4", lambda: next(uuids))
    existing_path = upload_dir / "company_9_requirement_17_collision.pdf"
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_bytes(b"keep-me")

    response = await essential8_routes.upload_requirement_evidence(
        company_id=9,
        requirement_id=17,
        title="Quarterly evidence",
        description=None,
        evidence_file=_make_upload(b"new-bytes", "report.pdf", "application/pdf"),
        user={"id": 41, "company_id": 9},
    )

    assert existing_path.read_bytes() == b"keep-me"
    assert response["file_path"].endswith("replacement.pdf")
    assert (tmp_path / Path(add_calls[0]["file_path"])).read_bytes() == b"new-bytes"


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_cleans_up_partial_file_on_oversize(monkeypatch, tmp_path):
    _, _, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(essential8_routes, "uuid4", lambda: SimpleNamespace(hex="oversize"))

    with pytest.raises(HTTPException) as exc_info:
        await essential8_routes.upload_requirement_evidence(
            company_id=9,
            requirement_id=17,
            title="Quarterly evidence",
            description=None,
            evidence_file=_make_upload(
                b"x" * (essential8_routes._MAX_REQUIREMENT_EVIDENCE_SIZE_BYTES + 1),
                "report.pdf",
                "application/pdf",
            ),
            user={"id": 41, "company_id": 9},
        )

    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert not (upload_dir / "company_9_requirement_17_oversize.pdf").exists()


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_cleans_up_only_its_partial_file_on_interrupted_upload(
    monkeypatch,
    tmp_path,
):
    _, _, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(essential8_routes, "uuid4", lambda: SimpleNamespace(hex="interrupted"))
    neighbour = upload_dir / "existing.bin"
    neighbour.parent.mkdir(parents=True, exist_ok=True)
    neighbour.write_bytes(b"keep-me")
    upload = _InterruptedUpload("report.pdf", first_chunk=b"partial", error=OSError("socket dropped"))

    with pytest.raises(OSError):
        await essential8_routes.upload_requirement_evidence(
            company_id=9,
            requirement_id=17,
            title="Quarterly evidence",
            description=None,
            evidence_file=upload,
            user={"id": 41, "company_id": 9},
        )

    assert upload.closed is True
    assert neighbour.read_bytes() == b"keep-me"
    assert not (upload_dir / "company_9_requirement_17_interrupted.pdf").exists()


@pytest.mark.anyio("asyncio")
async def test_upload_requirement_evidence_rejects_symlinked_upload_directory(monkeypatch, tmp_path):
    _, _, upload_dir = _configure_upload_dependencies(monkeypatch, tmp_path)
    target_dir = tmp_path / "real-evidence"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        upload_dir.parent.mkdir(parents=True, exist_ok=True)
        upload_dir.symlink_to(target_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on this platform")

    with pytest.raises(HTTPException) as exc_info:
        await essential8_routes.upload_requirement_evidence(
            company_id=9,
            requirement_id=17,
            title="Quarterly evidence",
            description=None,
            evidence_file=_make_upload(b"proof-bytes", "report.pdf", "application/pdf"),
            user={"id": 41, "company_id": 9},
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert list(target_dir.iterdir()) == []
