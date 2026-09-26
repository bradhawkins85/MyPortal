from __future__ import annotations

from typing import Any

from app.core.database import db
from app.security import vault

_METADATA_COLUMNS = (
    "id, company_id, name, username, current_version, created_at, updated_at"
)
_LINK_TABLES = {"asset": "assets", "staff": "staff", "ticket": "tickets"}


async def _validate_links(company_id: int, links: list[tuple[str, int]]) -> None:
    """Prevent links from becoming a cross-company existence oracle."""
    for target_type, target_id in links:
        table = _LINK_TABLES.get(target_type)
        if table is None:
            raise ValueError("Unsupported credential link type")
        # ``table`` comes exclusively from the static allow-list above.
        row = await db.fetch_one(
            "SELECT id FROM " + table + " WHERE id = %s AND company_id = %s",
            (target_id, company_id),
        )
        if row is None:
            raise ValueError("Credential link target is not in the company")


async def list_credentials(company_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT "
        + _METADATA_COLUMNS
        + " FROM credentials WHERE company_id = %s ORDER BY name, id",
        (company_id,),
    )


async def get_metadata(company_id: int, credential_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT "
        + _METADATA_COLUMNS
        + " FROM credentials WHERE company_id = %s AND id = %s",
        (company_id, credential_id),
    )


async def create_credential(
    *,
    company_id: int,
    name: str,
    username: str | None,
    plaintext: str,
    created_by: int | None,
    links: list[tuple[str, int]],
) -> dict[str, Any]:
    await _validate_links(company_id, links)
    credential_id = await db.execute(
        "INSERT INTO credentials (company_id, name, username, current_version, created_by) VALUES (%s, %s, %s, 1, %s)",
        (company_id, name, username, created_by),
    )
    encrypted = vault.encrypt(
        plaintext, company_id=company_id, credential_id=credential_id, version=1
    )
    await db.execute(
        "INSERT INTO credential_secret_versions (credential_id, version, key_id, nonce, ciphertext, created_by) VALUES (%s, 1, %s, %s, %s, %s)",
        (
            credential_id,
            encrypted.key_id,
            encrypted.nonce,
            encrypted.ciphertext,
            created_by,
        ),
    )
    for target_type, target_id in links:
        await db.execute(
            "INSERT INTO credential_links (credential_id, company_id, target_type, target_id) VALUES (%s, %s, %s, %s)",
            (credential_id, company_id, target_type, target_id),
        )
    result = await get_metadata(company_id, credential_id)
    if result is None:
        raise RuntimeError("Credential creation failed")
    return result


async def reveal(
    company_id: int, credential_id: int, version: int | None = None
) -> tuple[int, str] | None:
    metadata = await get_metadata(company_id, credential_id)
    if metadata is None:
        return None
    selected = version or int(metadata["current_version"])
    row = await db.fetch_one(
        "SELECT key_id, nonce, ciphertext FROM credential_secret_versions WHERE credential_id = %s AND version = %s",
        (credential_id, selected),
    )
    if row is None:
        return None
    secret = vault.EncryptedSecret(
        row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"])
    )
    return selected, vault.decrypt(
        secret, company_id=company_id, credential_id=credential_id, version=selected
    )


async def add_version(
    *, company_id: int, credential_id: int, plaintext: str, created_by: int | None
) -> dict[str, Any] | None:
    metadata = await get_metadata(company_id, credential_id)
    if metadata is None:
        return None
    version = int(metadata["current_version"]) + 1
    encrypted = vault.encrypt(
        plaintext, company_id=company_id, credential_id=credential_id, version=version
    )
    await db.execute(
        "INSERT INTO credential_secret_versions (credential_id, version, key_id, nonce, ciphertext, created_by) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            credential_id,
            version,
            encrypted.key_id,
            encrypted.nonce,
            encrypted.ciphertext,
            created_by,
        ),
    )
    await db.execute(
        "UPDATE credentials SET current_version = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND company_id = %s",
        (version, credential_id, company_id),
    )
    return await get_metadata(company_id, credential_id)
