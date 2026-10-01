"""Essential 8 evidence is downloaded through a per-company endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app import main as app_main
from app.api.routes import essential8 as essential8_routes


@pytest.fixture
def evidence_store(tmp_path: Path, monkeypatch):
    root = tmp_path / "private_uploads"
    folder = root / "compliance" / "essential8"
    folder.mkdir(parents=True)
    (folder / "abc.evidence").write_bytes(b"<script>alert(1)</script>")
    (root / "secret.txt").write_bytes(b"secret")
    monkeypatch.setattr(app_main, "_private_uploads_path", root)
    records = {
        (10, 1): {"id": 1, "company_id": 10, "file_name": "policy.html", "file_path": "compliance/essential8/abc.evidence"},
        (10, 2): {"id": 2, "company_id": 10, "file_name": "x.txt", "file_path": "compliance/essential8/../../secret.txt"},
    }

    async def fake_get(company_id, evidence_id):
        return records.get((company_id, evidence_id))

    monkeypatch.setattr(essential8_routes.essential8_repo, "get_requirement_evidence", fake_get)
    return records


def _membership(monkeypatch, membership):
    async def fake_membership(user_id, company_id):
        return membership

    monkeypatch.setattr(essential8_routes.user_company_repo, "get_user_company", fake_membership)


@pytest.mark.asyncio
async def test_member_with_compliance_access_downloads_as_attachment(monkeypatch, evidence_store):
    _membership(monkeypatch, {"can_view_compliance": 1})
    response = await essential8_routes.download_requirement_evidence(
        company_id=10, evidence_id=1, _=None, user={"id": 5}
    )
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_other_company_user_is_rejected(monkeypatch, evidence_store):
    _membership(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await essential8_routes.download_requirement_evidence(
            company_id=10, evidence_id=1, _=None, user={"id": 6}
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_member_without_compliance_permission_is_rejected(monkeypatch, evidence_store):
    _membership(monkeypatch, {"can_view_compliance": 0})
    with pytest.raises(HTTPException) as exc:
        await essential8_routes.download_requirement_evidence(
            company_id=10, evidence_id=1, _=None, user={"id": 7}
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_id", [2, 99])
async def test_missing_or_escaping_paths_are_not_found(monkeypatch, evidence_store, evidence_id):
    _membership(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await essential8_routes.download_requirement_evidence(
            company_id=10, evidence_id=evidence_id, _=None, user={"id": 1, "is_super_admin": True}
        )
    assert exc.value.status_code == 404


def test_template_links_to_download_endpoint():
    template = Path("app/templates/compliance/control_requirements.html").read_text()
    assert "/uploads/{{ evidence.file_path }}" not in template
    assert "/api/essential8/companies/{{ evidence.company_id }}/evidence/{{ evidence.id }}/download" in template
