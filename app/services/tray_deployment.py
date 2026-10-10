"""Tray deployment URLs.

A deployment link is a per-company magic link, created from the admin page,
that hands out tray installers already carrying the portal address and a
company install token: a signed EXE and MSI built for that company by the
Windows build agent (see :mod:`app.services.tray_deployment_builds`), a macOS
zip with the cached pkg and a launcher, and PowerShell and shell scripts for
RMM tools.  The installers are only ever served by MyPortal through the link,
never published to GitHub.

Whoever holds the link can enrol devices into that company, so the slug is a
long random value, stored hashed for lookup and encrypted so administrators
can copy it again.  Each link owns one install token and both expire
together, so a forwarded package stops working when the link lapses.
Revoking the link revokes the token.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_error
from app.repositories import companies as companies_repo
from app.repositories import tray as tray_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import tray as tray_service
from app.services import tray_installer

EXPIRY_CHOICES_DAYS = (1, 7, 30, 90)
DEFAULT_EXPIRY_DAYS = 7

_INSTALLER_NAMES = {
    "macos": "myportal-tray.pkg",
}

# Values are written into cmd, PowerShell and shell scripts, so only allow
# characters that are inert in all three.  Install tokens come from
# ``secrets.token_urlsafe`` and always match ``_TOKEN_RE``.
_PORTAL_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~\-/]*)?$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")


class DeploymentLinkUnavailable(Exception):
    """Raised when a deployment slug is unknown, revoked or expired."""


class InstallerNotAvailable(Exception):
    """Raised when the server has no cached installer for a platform."""


def generate_slug() -> str:
    return tray_service.generate_install_token()


def link_path(slug: str) -> str:
    return "/deploy/" + slug


def resolve_portal_url(request: Any) -> str:
    """Return the public portal address, preferring the ``PORTAL_URL`` setting."""

    settings = get_settings()
    if settings.portal_url:
        return str(settings.portal_url).rstrip("/")
    return str(request.base_url.replace(scheme="https")).rstrip("/")


async def serialise_link(record: dict[str, Any], portal_url: str) -> dict[str, Any]:
    slug = reveal_slug(record)
    return {
        "id": int(record["id"]),
        "company_id": int(record["company_id"]),
        "company_name": record.get("company_name"),
        "label": record.get("label") or "",
        "url": portal_url + link_path(slug) if slug else None,
        "status": await link_status(record),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "revoked_at": record.get("revoked_at"),
        "download_count": int(record.get("download_count") or 0),
        "last_downloaded_at": record.get("last_downloaded_at"),
    }


def _check_script_values(portal_url: str, token: str) -> None:
    if not _PORTAL_URL_RE.match(portal_url or ""):
        raise ValueError("Portal URL contains characters that cannot be used in an installer")
    if not _TOKEN_RE.match(token or ""):
        raise ValueError("Install token has an unexpected format")


async def create_deployment_link(
    *,
    company_id: int,
    label: str | None,
    created_by_user_id: int | None,
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
) -> tuple[dict[str, Any], str]:
    """Create a deployment link and its install token. Returns ``(record, slug)``.

    The link and its token expire together, so a forwarded or leaked package
    stops enrolling devices once the link lapses.
    """

    if expires_in_days not in EXPIRY_CHOICES_DAYS:
        raise ValueError("Choose how long the link should stay valid.")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).replace(
        tzinfo=None
    )
    company = await companies_repo.get_company_by_id(int(company_id))
    if not company:
        raise ValueError("Company not found")
    clean_label = (label or "").strip() or f"{company.get('name') or 'Company'} deployment"
    clean_label = clean_label[:150]

    raw_token = tray_service.generate_install_token()
    token_record = await tray_repo.create_install_token(
        label=("Deployment URL: " + clean_label)[:150],
        company_id=int(company_id),
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
    )
    slug = generate_slug()
    record = await tray_repo.create_deployment_link(
        company_id=int(company_id),
        label=clean_label,
        slug_hash=tray_service.hash_token(slug),
        slug_prefix=tray_service.token_prefix(slug),
        slug_encrypted=encrypt_secret(slug),
        install_token_id=token_record.get("id"),
        install_token_encrypted=encrypt_secret(raw_token),
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
    )
    if record.get("id"):
        await tray_repo.create_deployment_build(int(record["id"]))
    return record, slug


def reveal_slug(record: dict[str, Any]) -> str | None:
    try:
        return decrypt_secret(str(record.get("slug_encrypted") or ""), allow_plaintext=False)
    except Exception:  # pragma: no cover - corrupt row or rotated key
        log_error("Unable to decrypt tray deployment link", link_id=record.get("id"))
        return None


def _is_past(value: Any) -> bool:
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    return value.replace(tzinfo=value.tzinfo or timezone.utc) < datetime.now(timezone.utc)


def _token_is_usable(token: dict[str, Any] | None) -> bool:
    if not token or token.get("revoked_at"):
        return False
    return not _is_past(token.get("expires_at"))


async def link_status(record: dict[str, Any]) -> str:
    """Return ``active``, ``expired`` or ``revoked`` for an admin listing."""

    if record.get("revoked_at"):
        return "revoked"
    if _is_past(record.get("expires_at")):
        return "expired"
    token_id = record.get("install_token_id")
    token = await tray_repo.get_install_token_by_id(int(token_id)) if token_id else None
    return "active" if _token_is_usable(token) else "revoked"


async def resolve_active_link(slug: str) -> tuple[dict[str, Any], str]:
    """Return ``(link, raw_install_token)`` for a usable deployment slug."""

    if not slug or not _TOKEN_RE.match(slug):
        raise DeploymentLinkUnavailable("This deployment link is not valid.")
    link = await tray_repo.get_deployment_link_by_slug_hash(tray_service.hash_token(slug))
    if not link or link.get("revoked_at"):
        raise DeploymentLinkUnavailable("This deployment link is not valid.")
    if _is_past(link.get("expires_at")):
        raise DeploymentLinkUnavailable("This deployment link has expired.")
    token_id = link.get("install_token_id")
    token = await tray_repo.get_install_token_by_id(int(token_id)) if token_id else None
    if not _token_is_usable(token):
        raise DeploymentLinkUnavailable("This deployment link has been revoked.")
    try:
        raw_token = decrypt_secret(
            str(link.get("install_token_encrypted") or ""), allow_plaintext=False
        )
    except Exception as exc:
        log_error("Unable to decrypt tray deployment token", link_id=link.get("id"))
        raise DeploymentLinkUnavailable("This deployment link is not valid.") from exc
    return link, raw_token


async def revoke_deployment_link(link_id: int) -> bool:
    link = await tray_repo.get_deployment_link(int(link_id))
    if not link:
        return False
    await tray_repo.revoke_deployment_link(int(link_id))
    if link.get("install_token_id"):
        await tray_repo.revoke_install_token(int(link["install_token_id"]))
    return True


# ---------------------------------------------------------------------------
# Bundles and scripts
# ---------------------------------------------------------------------------


def installer_path(platform: str) -> Path | None:
    name = _INSTALLER_NAMES.get(platform)
    if not name:
        return None
    path = tray_installer._TRAY_STATIC_DIR / name
    return path if path.is_file() else None


def download_filename(company_name: str | None, kind: str) -> str:
    """Return the file name a browser saves a deployment download as."""

    safe = re.sub(r"[^A-Za-z0-9]+", "-", str(company_name or "")).strip("-")[:60]
    stem = "MyPortal-Tray-" + (safe + "-" if safe else "")
    if kind == "exe":
        return stem + "Setup.exe"
    if kind == "msi":
        return stem + "Setup.msi"
    return stem + "macOS.zip"


def macos_env_file(portal_url: str, token: str) -> str:
    _check_script_values(portal_url, token)
    return "MYPORTAL_URL=" + portal_url + "\nENROL_TOKEN=" + token + "\nAUTO_UPDATE=true\n"


def macos_launcher() -> str:
    return "\n".join(
        [
            "#!/bin/bash",
            "# MyPortal Tray installer. Generated by MyPortal for one company.",
            "# Keep this file next to myportal-tray.pkg and myportal-tray.env.",
            "set -euo pipefail",
            "cd \"$(dirname \"$0\")\"",
            "echo \"Installing MyPortal Tray. Enter your password if asked.\"",
            "sudo install -m 600 -o root -g wheel myportal-tray.env"
            + " /Library/Preferences/io.myportal.tray.env",
            "sudo installer -pkg myportal-tray.pkg -target /",
            "echo \"MyPortal Tray installed.\"",
            "",
        ]
    )


def windows_script(portal_url: str, token: str) -> str:
    _check_script_values(portal_url, token)
    return "\n".join(
        [
            "# MyPortal Tray deployment script. Generated by MyPortal for one company.",
            "# Run as administrator, for example from an RMM:",
            "#   irm '" + portal_url + "/deploy/<link>/install.ps1' | iex",
            "$ErrorActionPreference = 'Stop'",
            "$PortalURL = '" + portal_url + "'",
            "$EnrolToken = '" + token + "'",
            "$msiPath = Join-Path $env:TEMP 'myportal-tray.msi'",
            "Invoke-WebRequest -Uri \"$PortalURL/static/tray/myportal-tray.msi\""
            + " -OutFile $msiPath -UseBasicParsing",
            "$msiArgs = @('/i', $msiPath, \"MYPORTAL_URL=$PortalURL\", \"ENROL_TOKEN=$EnrolToken\","
            + " 'AUTO_UPDATE=true', '/qn', '/norestart', '/l*v',"
            + " (Join-Path $env:TEMP 'myportal-tray-install.log'))",
            "$proc = Start-Process msiexec.exe -ArgumentList $msiArgs -Wait -PassThru",
            "if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {",
            "    throw \"MSI install exited with code $($proc.ExitCode)\"",
            "}",
            "Write-Host 'MyPortal Tray installed successfully.'",
            "",
        ]
    )


def macos_script(portal_url: str, token: str) -> str:
    _check_script_values(portal_url, token)
    return "\n".join(
        [
            "#!/bin/bash",
            "# MyPortal Tray deployment script. Generated by MyPortal for one company.",
            "# Run as root, for example from an RMM:",
            "#   curl -fsSL '" + portal_url + "/deploy/<link>/install.sh' | sudo bash",
            "set -euo pipefail",
            "PORTAL_URL='" + portal_url + "'",
            "ENROL_TOKEN='" + token + "'",
            "PKG_PATH=\"$(mktemp -d)/myportal-tray.pkg\"",
            "curl -fsSL \"$PORTAL_URL/static/tray/myportal-tray.pkg\" -o \"$PKG_PATH\"",
            "umask 077",
            "printf 'MYPORTAL_URL=%s\\nENROL_TOKEN=%s\\nAUTO_UPDATE=true\\n'"
            + " \"$PORTAL_URL\" \"$ENROL_TOKEN\" > /Library/Preferences/io.myportal.tray.env",
            "chmod 600 /Library/Preferences/io.myportal.tray.env",
            "installer -pkg \"$PKG_PATH\" -target /",
            "echo 'MyPortal Tray installed successfully.'",
            "",
        ]
    )


_README = (
    "MyPortal Tray\n"
    "=============\n\n"
    "This package installs the MyPortal Tray app and connects it to your\n"
    "organisation automatically.\n\n"
    "{steps}\n\n"
    "Keep this package private: anyone with it can register a device with\n"
    "your organisation.\n"
)

_MACOS_STEPS = (
    "1. Open the zip file so it extracts into a folder.\n"
    "2. Right-click \"Install MyPortal Tray.command\", choose Open, then Open\n"
    "   again, and enter your Mac password when asked."
)


def _write_text(archive: zipfile.ZipFile, name: str, content: str, *, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=datetime.now(timezone.utc).timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    archive.writestr(info, content)


def build_macos_bundle(portal_url: str, token: str) -> Path:
    """Write the macOS deployment zip to a temporary file and return its path.

    The pkg is stored uncompressed since it is already compressed.  The caller
    deletes the file once it has been sent.
    """

    source = installer_path("macos")
    if source is None:
        raise InstallerNotAvailable("macos")
    _check_script_values(portal_url, token)

    handle = tempfile.NamedTemporaryFile(prefix="myportal-tray-", suffix=".zip", delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(source, source.name, compress_type=zipfile.ZIP_STORED)
            _write_text(archive, "myportal-tray.env", macos_env_file(portal_url, token))
            _write_text(
                archive, "Install MyPortal Tray.command", macos_launcher(), executable=True
            )
            _write_text(archive, "README.txt", _README.format(steps=_MACOS_STEPS))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path
