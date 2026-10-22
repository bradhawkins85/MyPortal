from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.api_keys import require_api_key
from app.api.routes import tray_deployment as deploy_routes
from app.core.database import db
from app.repositories import tray as tray_repo
from app.schemas.tray import TrayDeploymentLinkCreate, TrayProvisioningSettings
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import tray_deployment
from app.services import tray_deployment_builds as builds

PORTAL = "https://portal.example.com"
TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789-abcde"
SLUG = "SlugSlugSlugSlugSlugSlugSlugSlugSlugSlug_-1"
ADMIN_PASSWORD = "OnlyForTests!42"
WIFI_PASSWORD = "WifiForTests!42"
PACKAGE_PASSWORD = "GeneratedPackagePassword!42"
ENTRA_BULK_TOKEN = "BulkEnrollmentTokenForTests.0123456789"
AD_PASSWORD = "DirectoryJoinPasswordForTests!42"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(**overrides) -> TrayProvisioningSettings:
    return TrayProvisioningSettings(admin_password=ADMIN_PASSWORD, **overrides)


def _plain_settings() -> dict:
    return {
        "admin_password": ADMIN_PASSWORD,
        "wifi_ssid": "Office & Guests",
        "wifi_password": WIFI_PASSWORD,
        "device_name_template": "MP-%RAND:8%",
    }


def _entra_join(**overrides) -> dict:
    return {
        "mode": "entra",
        "bulk_token": ENTRA_BULK_TOKEN,
        "authority": "https://login.microsoftonline.com/common",
        **overrides,
    }


def _ad_join(**overrides) -> dict:
    return {
        "mode": "active_directory",
        "domain_name": "corp.example.com",
        "username": "EXAMPLE\\provisioning",
        "password": AD_PASSWORD,
        "organizational_unit": "OU=New Computers,DC=corp,DC=example,DC=com",
        **overrides,
    }


def _link(**overrides) -> dict:
    row = {
        "id": 7,
        "company_id": 3,
        "company_name": "Acme",
        "label": "Acme provisioning",
        "slug_encrypted": encrypt_secret(SLUG),
        "install_token_id": 11,
        "install_token_encrypted": encrypt_secret(TOKEN),
        "provisioning_encrypted": encrypt_secret(json.dumps(_plain_settings())),
        "created_at": datetime(2026, 10, 1),
        "revoked_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "password",
    [
        "Short!42",
        "onlylowercaseletters",
        "ONLYUPPERCASE42",
        "Aa1!" * 32,
        "ProvisioningAdmin!42",
        "provisioningadmin!42",
        "ValidPassword!42\n",
        "ValidPassword!42\x00",
    ],
)
def test_provisioning_rejects_unsafe_local_admin_password(password):
    with pytest.raises(ValidationError):
        TrayProvisioningSettings(admin_password=password)


def test_provisioning_secrets_are_masked_in_model_output():
    settings = _settings(wifi_ssid="Office", wifi_password=WIFI_PASSWORD)
    assert settings.admin_password.get_secret_value() == ADMIN_PASSWORD
    for output in (repr(settings), settings.model_dump_json()):
        assert ADMIN_PASSWORD not in output
        assert WIFI_PASSWORD not in output
    assert TrayDeploymentLinkCreate(company_id=3).provisioning is None


def test_invalid_form_settings_do_not_reflect_passwords():
    submitted_password = "TooShort!42"
    with pytest.raises(ValueError) as exc:
        tray_deployment.provisioning_from_form({
            "provisioning_enabled": "on",
            "provisioning_admin_password": submitted_password,
        })
    assert submitted_password not in str(exc.value)
    assert tray_deployment.provisioning_from_form({
        "provisioning_admin_password": submitted_password,
    }) is None


@pytest.mark.parametrize(
    "values",
    [
        {"wifi_ssid": "é" * 17},
        {"wifi_ssid": "Office\x00Guest"},
        {"wifi_password": WIFI_PASSWORD},
        {"wifi_ssid": "Office", "wifi_password": "short"},
        {"wifi_ssid": "Office", "wifi_password": "x" * 64},
        {"wifi_ssid": "Office", "wifi_password": "pässword42"},
        {"wifi_ssid": "Office", "wifi_password": "password\n42"},
    ],
)
def test_provisioning_rejects_invalid_wifi_configuration(values):
    with pytest.raises(ValidationError):
        _settings(**values)


def test_wifi_allows_a_32_byte_ssid_and_open_network():
    settings = _settings(wifi_ssid="é" * 16)
    assert settings.wifi_ssid == "é" * 16
    assert not settings.wifi_password or not settings.wifi_password.get_secret_value()


@pytest.mark.parametrize(
    "template",
    [
        "MP-%RAND:0%",
        "MP-%RAND:08%",
        "MP-%RAND:16%",
        "MP-%RAND:13%",
        "MP-%RAND:2%-%RAND:2%",
        "MP-%SERIAL%",
        "MP-$env:COMPUTERNAME",
        "MP-$(whoami)",
        "PC.NAME",
        "-DESKTOP",
        "DESKTOP-",
        "123456",
        "DESKTOP1234567890",
    ],
)
def test_provisioning_rejects_invalid_computer_name_templates(template):
    with pytest.raises(ValidationError):
        _settings(device_name_template=template)


@pytest.mark.parametrize("template", ["MP-%RAND:8%", "MP-%RAND:12%", "FRONTDESK"])
def test_provisioning_accepts_valid_computer_name_templates(template):
    assert _settings(device_name_template=template).device_name_template == template


@pytest.mark.anyio
@pytest.mark.parametrize("directory_join", [None, _entra_join(), _ad_join()])
async def test_provisioning_settings_are_encrypted_and_never_listed(monkeypatch, directory_join):
    monkeypatch.setattr(
        tray_deployment.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 3, "name": "Acme"}),
    )
    monkeypatch.setattr(tray_repo, "create_install_token", AsyncMock(return_value={"id": 11}))
    create_link = AsyncMock(side_effect=lambda **kwargs: {**_link(), **kwargs})
    monkeypatch.setattr(tray_repo, "create_deployment_link", create_link)
    monkeypatch.setattr(tray_repo, "create_deployment_build", AsyncMock(return_value=1))
    monkeypatch.setattr(tray_repo, "get_install_token_by_id", AsyncMock(return_value={"id": 11}))

    raw_settings = _plain_settings()
    if directory_join:
        raw_settings["directory_join"] = directory_join
    settings = TrayProvisioningSettings(**raw_settings)
    record, _slug = await tray_deployment.create_deployment_link(
        company_id=3, label=None, created_by_user_id=1, provisioning=settings
    )

    ciphertext = create_link.await_args.kwargs["provisioning_encrypted"]
    assert ADMIN_PASSWORD not in ciphertext
    assert WIFI_PASSWORD not in ciphertext
    assert json.loads(decrypt_secret(ciphertext, allow_plaintext=False)) == raw_settings
    serialised = await tray_deployment.serialise_link(record, PORTAL)
    assert serialised["provisioning_enabled"] is True
    for value in (ADMIN_PASSWORD, WIFI_PASSWORD, ENTRA_BULK_TOKEN, AD_PASSWORD, ciphertext):
        assert value not in str(serialised)
    assert "provisioning_encrypted" not in serialised
    assert "directory_join" not in serialised


@pytest.mark.parametrize("directory_join", [_entra_join(), _ad_join()])
def test_directory_join_secrets_are_masked_but_available_to_builds(directory_join):
    settings = _settings(
        device_name_template="MP-%RAND:8%", directory_join=directory_join
    )
    assert settings.build_settings()["directory_join"] == directory_join
    for output in (repr(settings), settings.model_dump_json()):
        assert ADMIN_PASSWORD not in output
        assert ENTRA_BULK_TOKEN not in output
        assert AD_PASSWORD not in output


def test_directory_join_is_optional_for_existing_provisioning_settings():
    settings = _settings()
    assert settings.directory_join is None
    assert "directory_join" not in settings.build_settings()
    legacy_record = {"provisioning_encrypted": encrypt_secret(json.dumps(_plain_settings()))}
    assert tray_deployment.provisioning_settings(legacy_record) == _plain_settings()


@pytest.mark.parametrize(
    "authority",
    [
        "https://login.microsoftonline.com/common",
        "https://login.microsoftonline.com/organizations",
        "https://login.microsoftonline.com/94f3cd80-c2b2-499d-9401-fbd5a387c179",
        "https://login.microsoftonline.com/contoso.onmicrosoft.com",
        "https://login.microsoftonline.us/common",
        "https://login.chinacloudapi.cn/organizations",
    ],
)
def test_entra_join_accepts_microsoft_authorities(authority):
    settings = _settings(directory_join=_entra_join(authority=authority))
    assert settings.build_settings()["directory_join"]["authority"] == authority


def test_entra_authority_defaults_to_microsoft_common_tenant():
    join = _entra_join()
    join.pop("authority")
    settings = _settings(directory_join=join)
    assert settings.build_settings()["directory_join"]["authority"] == (
        "https://login.microsoftonline.com/common"
    )


def test_entra_authority_removes_optional_trailing_slash():
    settings = _settings(directory_join=_entra_join(
        authority="https://login.microsoftonline.com/contoso.onmicrosoft.com/"
    ))
    assert settings.build_settings()["directory_join"]["authority"] == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com"
    )


@pytest.mark.parametrize(
    "authority",
    [
        "http://login.microsoftonline.com/common",
        "https://example.com/common",
        "https://login.microsoftonline.com.example.com/common",
        "https://username:password@login.microsoftonline.com/common",
        "https://login.microsoftonline.com/",
        "https://login.microsoftonline.com/common/extra",
        "https://login.microsoftonline.com/common?token=value",
        "https://login.microsoftonline.com/common#fragment",
        "https://login.microsoftonline.com/common?",
        "https://login.microsoftonline.com/common#",
        " https://login.microsoftonline.com/common",
        "https://login.microsoftonline.com/com\tmon",
        "https://login.microsoftonline.com:443/common",
        "https://login.microsoftonline.com/../common",
        "https://login.microsoftonline.com/common%2fextra",
        "https://login.microsoftonline.com/unrecognizedtenant",
    ],
)
def test_entra_join_rejects_untrusted_or_ambiguous_authorities(authority):
    with pytest.raises(ValidationError):
        _settings(directory_join=_entra_join(authority=authority))


@pytest.mark.parametrize("bulk_token", ["", "x" * 16385, "token\nvalue", "token\x00value", "token value"])
def test_entra_join_requires_a_bounded_nonempty_bulk_token(bulk_token):
    with pytest.raises(ValidationError):
        _settings(directory_join=_entra_join(bulk_token=bulk_token))


@pytest.mark.parametrize("username", ["EXAMPLE\\provisioning", "joiner@corp.example.com"])
def test_active_directory_join_accepts_domain_or_upn_credentials(username):
    settings = _settings(
        device_name_template="MP-%RAND:8%", directory_join=_ad_join(username=username)
    )
    assert settings.build_settings()["directory_join"]["username"] == username


@pytest.mark.parametrize(
    "domain_name",
    [
        "localhost",
        "-corp.example",
        "corp-.example",
        "corp..example",
        "corp example.com",
        "corp_example.com",
        "192.0.2.1",
        "123.456",
        "a" * 64 + ".example",
        "a." * 127 + "com",
    ],
)
def test_active_directory_join_rejects_invalid_dns_domains(domain_name):
    with pytest.raises(ValidationError):
        _settings(
            device_name_template="MP-%RAND:8%", directory_join=_ad_join(domain_name=domain_name)
        )


@pytest.mark.parametrize(
    "values",
    [
        {"username": "joiner"},
        {"username": "EXAMPLE\\"},
        {"username": "\\joiner"},
        {"username": "EXAMPLE\\one\\two"},
        {"username": "joiner@"},
        {"username": "@corp.example"},
        {"username": "a" * 250 + "@corp.example"},
        {"username": "joiner\n@corp.example"},
        {"password": ""},
        {"password": "x" * 128},
        {"password": "Password\n42"},
        {"organizational_unit": "x" * 1025},
        {"organizational_unit": "OU=New\x00,DC=corp,DC=example"},
    ],
)
def test_active_directory_join_rejects_invalid_credentials_and_ou(values):
    with pytest.raises(ValidationError):
        _settings(device_name_template="MP-%RAND:8%", directory_join=_ad_join(**values))


def test_active_directory_join_defaults_to_a_randomized_computer_name():
    settings = _settings(directory_join=_ad_join())
    assert settings.device_name_template == "MP-%RAND:8%"


def test_active_directory_join_rejects_a_static_computer_name():
    with pytest.raises(ValidationError):
        _settings(device_name_template="FRONTDESK", directory_join=_ad_join())


@pytest.mark.parametrize(
    "join",
    [
        {**_entra_join(), "password": AD_PASSWORD},
        {**_ad_join(), "bulk_token": ENTRA_BULK_TOKEN},
        {**_entra_join(), "domain_name": "corp.example.com"},
        {**_ad_join(), "authority": "https://login.microsoftonline.com/common"},
        {"mode": "both", "bulk_token": ENTRA_BULK_TOKEN},
        {"bulk_token": ENTRA_BULK_TOKEN},
    ],
)
def test_directory_join_rejects_extra_or_cross_mode_settings(join):
    with pytest.raises(ValidationError):
        _settings(device_name_template="MP-%RAND:8%", directory_join=join)


@pytest.mark.parametrize("mode", ["none", "entra", "active_directory"])
def test_directory_join_form_reads_only_the_selected_mode(mode):
    form = {
        "provisioning_enabled": "on",
        "provisioning_admin_password": ADMIN_PASSWORD,
        "provisioning_device_name_template": "MP-%RAND:8%",
        "provisioning_join_mode": mode,
        "provisioning_entra_bulk_token": ENTRA_BULK_TOKEN,
        "provisioning_entra_authority": "https://login.microsoftonline.com/common",
        "provisioning_ad_domain": "corp.example.com",
        "provisioning_ad_username": "EXAMPLE\\provisioning",
        "provisioning_ad_password": AD_PASSWORD,
        "provisioning_ad_ou": "OU=New Computers,DC=corp,DC=example,DC=com",
    }
    settings = tray_deployment.provisioning_from_form(form)
    result = settings.build_settings()
    if mode == "none":
        assert settings.directory_join is None
        assert "directory_join" not in result
    else:
        assert result["directory_join"] == (_entra_join() if mode == "entra" else _ad_join())
    for output in (repr(settings), settings.model_dump_json()):
        assert ENTRA_BULK_TOKEN not in output
        assert AD_PASSWORD not in output


@pytest.mark.parametrize(
    "mode, extra_fields",
    [
        ("entra", {"provisioning_entra_bulk_token": ENTRA_BULK_TOKEN + "\n"}),
        ("active_directory", {
            "provisioning_ad_domain": "corp.example.com",
            "provisioning_ad_username": "EXAMPLE\\provisioning",
            "provisioning_ad_password": AD_PASSWORD + "\n",
        }),
        ("unknown", {}),
    ],
)
def test_directory_join_form_errors_do_not_reflect_enrollment_secrets(mode, extra_fields):
    form = {
        "provisioning_enabled": "on",
        "provisioning_admin_password": ADMIN_PASSWORD,
        "provisioning_device_name_template": "MP-%RAND:8%",
        "provisioning_join_mode": mode,
        **extra_fields,
    }
    with pytest.raises(ValueError) as exc:
        tray_deployment.provisioning_from_form(form)
    assert ENTRA_BULK_TOKEN not in str(exc.value)
    assert AD_PASSWORD not in str(exc.value)


@pytest.fixture
async def provisioning_db(tmp_path, monkeypatch):
    conn = await aiosqlite.connect(str(tmp_path / "provisioning.db"))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE tray_install_tokens (
          id INTEGER PRIMARY KEY, company_id INT, label TEXT, token_hash TEXT,
          token_prefix TEXT, created_by_user_id INT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at DATETIME,
          revoked_at DATETIME, last_used_at DATETIME, use_count INT DEFAULT 0);
        CREATE TABLE tray_deployment_links (
          id INTEGER PRIMARY KEY, company_id INT NOT NULL, label TEXT NOT NULL,
          slug_hash TEXT NOT NULL, slug_prefix TEXT NOT NULL, slug_encrypted TEXT NOT NULL,
          install_token_id INT, install_token_encrypted TEXT NOT NULL,
          provisioning_encrypted TEXT, created_by_user_id INT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at DATETIME,
          revoked_at DATETIME, download_count INT DEFAULT 0, last_downloaded_at DATETIME);
        CREATE TABLE tray_deployment_builds (
          id INTEGER PRIMARY KEY AUTOINCREMENT, deployment_link_id INT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', release_tag TEXT, error TEXT,
          msi_sha256 TEXT, exe_sha256 TEXT, ppkg_sha256 TEXT, ppkg_password_encrypted TEXT,
          requested_at DATETIME DEFAULT CURRENT_TIMESTAMP, claimed_at DATETIME,
          completed_at DATETIME);
        INSERT INTO tray_install_tokens (id, company_id, label, token_hash, token_prefix)
          VALUES (11, 3, 'Acme', 'h', 'p');
        """
    )
    for link_id, config in ((7, json.dumps(_plain_settings())), (8, None)):
        await conn.execute(
            "INSERT INTO tray_deployment_links "
            "(id, company_id, label, slug_hash, slug_prefix, slug_encrypted, install_token_id, "
            "install_token_encrypted, provisioning_encrypted) VALUES (?, 3, 'Acme', ?, 's', ?, 11, ?, ?)",
            (
                link_id,
                str(link_id),
                encrypt_secret(SLUG),
                encrypt_secret(TOKEN),
                encrypt_secret(config) if config else None,
            ),
        )
    await conn.commit()
    monkeypatch.setattr(db, "_use_sqlite", True)
    monkeypatch.setattr(db, "_sqlite_conn", conn)
    monkeypatch.setattr(builds, "_STORAGE_DIR", tmp_path / "tray-builds")
    monkeypatch.setattr(builds, "current_release_tag", lambda: "v1.4.2")
    monkeypatch.setattr(
        tray_deployment.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 3, "name": "Acme"}),
    )
    yield conn
    await conn.close()


async def _chunks(body: bytes):
    yield body


async def _upload(build_id: int, *kinds: str):
    for kind in kinds:
        await builds.save_artifact(build_id, kind, _chunks(kind.encode()))


@pytest.mark.anyio
async def test_legacy_agent_skips_provisioning_jobs_without_blocking_other_links(provisioning_db):
    provisioning_build = await builds.queue_build(7)
    legacy_build = await builds.queue_build(8)

    legacy_job = await builds.claim_next_build(PORTAL)
    assert legacy_job["id"] == legacy_build
    assert not legacy_job.get("provisioning")
    assert (await tray_repo.get_deployment_build(provisioning_build))["status"] == "queued"

    provisioning_job = await builds.claim_next_build(PORTAL, supports_ppkg=True)
    assert provisioning_job["id"] == provisioning_build
    assert provisioning_job["provisioning"] == _plain_settings()


@pytest.mark.anyio
@pytest.mark.parametrize("directory_join", [_entra_join(), _ad_join()])
async def test_build_agent_claim_receives_decrypted_directory_join_settings(
    provisioning_db, directory_join
):
    configured = {**_plain_settings(), "directory_join": directory_join}
    await provisioning_db.execute(
        "UPDATE tray_deployment_links SET provisioning_encrypted = ? WHERE id = 7",
        (encrypt_secret(json.dumps(configured)),),
    )
    await provisioning_db.commit()
    build_id = await builds.queue_build(7)

    job = await builds.claim_next_build(PORTAL, supports_ppkg=True)

    assert job["id"] == build_id
    assert job["provisioning"] == configured


@pytest.mark.anyio
async def test_provisioning_build_requires_package_and_password(provisioning_db):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL, supports_ppkg=True)
    await _upload(build_id, "msi", "exe")
    with pytest.raises(builds.BuildStateError):
        await builds.complete_build(build_id, provisioning_password=PACKAGE_PASSWORD)
    await _upload(build_id, "ppkg")
    with pytest.raises(builds.BuildStateError):
        await builds.complete_build(build_id)
    assert (await tray_repo.get_deployment_build(build_id))["status"] == "building"


@pytest.mark.anyio
async def test_completed_provisioning_build_keeps_password_encrypted(provisioning_db):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL, supports_ppkg=True)
    await _upload(build_id, "msi", "exe", "ppkg")

    await builds.complete_build(build_id, provisioning_password=PACKAGE_PASSWORD)

    record = await tray_repo.get_deployment_build(build_id)
    assert record["status"] == "ready"
    assert record["ppkg_sha256"] == hashlib.sha256(b"ppkg").hexdigest()
    assert PACKAGE_PASSWORD not in record["ppkg_password_encrypted"]
    assert decrypt_secret(record["ppkg_password_encrypted"], allow_plaintext=False) == PACKAGE_PASSWORD
    assert (await builds.ready_artifact(7, "ppkg")).read_bytes() == b"ppkg"


@pytest.mark.anyio
async def test_legacy_build_still_completes_with_only_existing_installers(provisioning_db):
    build_id = await builds.queue_build(8)
    await builds.claim_next_build(PORTAL)
    await _upload(build_id, "msi", "exe")
    await builds.complete_build(build_id)
    record = await tray_repo.get_deployment_build(build_id)
    assert record["status"] == "ready"
    assert record.get("ppkg_password_encrypted") is None
    assert await builds.ready_artifact(8, "ppkg") is None


@pytest.mark.anyio
async def test_public_package_download_is_private_and_counted(monkeypatch, tmp_path):
    package = tmp_path / "myportal-tray.ppkg"
    package.write_bytes(b"encrypted package")
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    ready = AsyncMock(return_value=package)
    monkeypatch.setattr(builds, "ready_artifact", ready)
    mark = AsyncMock()
    monkeypatch.setattr(tray_repo, "mark_deployment_link_downloaded", mark)

    response = await deploy_routes.deployment_windows_ppkg(SLUG)

    assert Path(response.path) == package
    assert ".ppkg" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    ready.assert_awaited_once_with(7, "ppkg")
    mark.assert_awaited_once_with(7)


@pytest.mark.anyio
@pytest.mark.parametrize("provisioning_enabled, expected_status", [(False, 404), (True, 503)])
async def test_unconfigured_or_pending_packages_are_unavailable(
    monkeypatch, provisioning_enabled, expected_status
):
    link = _link(provisioning_encrypted="configured" if provisioning_enabled else None)
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(link, TOKEN))
    )
    monkeypatch.setattr(builds, "ready_artifact", AsyncMock(return_value=None))
    mark = AsyncMock()
    monkeypatch.setattr(tray_repo, "mark_deployment_link_downloaded", mark)
    with pytest.raises(HTTPException) as exc:
        await deploy_routes.deployment_windows_ppkg(SLUG)
    assert exc.value.status_code == expected_status
    mark.assert_not_awaited()


@pytest.mark.anyio
async def test_revoked_link_cannot_download_package(monkeypatch):
    monkeypatch.setattr(
        tray_deployment,
        "resolve_active_link",
        AsyncMock(side_effect=tray_deployment.DeploymentLinkUnavailable("revoked")),
    )
    ready = AsyncMock()
    monkeypatch.setattr(builds, "ready_artifact", ready)
    with pytest.raises(HTTPException) as exc:
        await deploy_routes.deployment_windows_ppkg(SLUG)
    assert exc.value.status_code == 404
    ready.assert_not_awaited()


@pytest.mark.anyio
async def test_generated_package_password_requires_super_admin(monkeypatch):
    app = FastAPI()
    app.include_router(deploy_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 3, "is_super_admin": False}
    lookup = AsyncMock()
    monkeypatch.setattr(tray_repo, "get_latest_ready_deployment_build", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://portal.example.com"
    ) as client:
        response = await client.get("/api/tray/admin/deployment-links/7/provisioning-password")
    assert response.status_code == 403
    assert PACKAGE_PASSWORD not in response.text
    lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_generated_package_password_revealed_only_to_admin(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(deploy_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(tray_repo, "get_deployment_link", AsyncMock(return_value=_link()))
    monkeypatch.setattr(tray_repo, "get_install_token_by_id", AsyncMock(return_value={"id": 11}))
    monkeypatch.setattr(builds, "_STORAGE_DIR", tmp_path / "tray-builds")
    package = builds.artifact_path(42, "ppkg")
    package.parent.mkdir(parents=True)
    package.write_bytes(b"encrypted package")
    monkeypatch.setattr(
        tray_repo,
        "get_latest_ready_deployment_build",
        AsyncMock(return_value={
            "id": 42,
            "status": "ready",
            "ppkg_sha256": "a" * 64,
            "ppkg_password_encrypted": encrypt_secret(PACKAGE_PASSWORD),
        }),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://portal.example.com"
    ) as client:
        response = await client.get("/api/tray/admin/deployment-links/7/provisioning-password")
    assert response.status_code == 200
    assert response.json() == {"password": PACKAGE_PASSWORD}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path, payload, secrets",
    [
        (
            "/api/tray/admin/deployment-links",
            {"company_id": 3, "provisioning": {
                "admin_password": ADMIN_PASSWORD,
                "wifi_password": WIFI_PASSWORD,
            }},
            (ADMIN_PASSWORD, WIFI_PASSWORD),
        ),
        (
            "/api/tray/build-agent/jobs/42/complete",
            {"provisioning_password": PACKAGE_PASSWORD * 12},
            (PACKAGE_PASSWORD,),
        ),
        (
            "/api/tray/admin/deployment-links",
            {"company_id": 3, "provisioning": {
                "admin_password": ADMIN_PASSWORD,
                "directory_join": {**_entra_join(), "password": AD_PASSWORD},
            }},
            (ADMIN_PASSWORD, ENTRA_BULK_TOKEN, AD_PASSWORD),
        ),
        (
            "/api/tray/admin/deployment-links",
            {"company_id": 3, "provisioning": {
                "admin_password": ADMIN_PASSWORD,
                "device_name_template": "STATIC-PC",
                "directory_join": _ad_join(),
            }},
            (ADMIN_PASSWORD, AD_PASSWORD),
        ),
    ],
)
async def test_api_validation_errors_never_reflect_bootstrap_secrets(path, payload, secrets):
    from app import main

    app = FastAPI()
    app.include_router(deploy_routes.router)
    app.add_exception_handler(RequestValidationError, main.handle_request_validation_error)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "is_super_admin": True}
    app.dependency_overrides[require_api_key] = lambda: {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=PORTAL
    ) as client:
        response = await client.post(path, json=payload)
    assert response.status_code == 422
    assert all(secret not in response.text for secret in secrets)
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
