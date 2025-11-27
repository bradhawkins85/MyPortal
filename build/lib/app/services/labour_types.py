from __future__ import annotations

from typing import Any, Sequence

from app.repositories import labour_types as labour_types_repo


def _clean_code(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _clean_name(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


async def list_labour_types() -> list[dict[str, Any]]:
    try:
        return await labour_types_repo.list_labour_types()
    except RuntimeError as exc:
        message = str(exc)
        if "Database pool not initialised" in message:
            return []
        raise


async def get_labour_type(labour_type_id: int) -> dict[str, Any] | None:
    if labour_type_id <= 0:
        return None
    return await labour_types_repo.get_labour_type(labour_type_id)


async def get_default_labour_type() -> dict[str, Any] | None:
    """Get the default labour type."""
    return await labour_types_repo.get_default_labour_type()


async def create_labour_type(*, code: str, name: str, rate: float | None = None) -> dict[str, Any]:
    cleaned_code = _clean_code(code)
    cleaned_name = _clean_name(name)
    if not cleaned_code:
        raise ValueError("Labour type code is required.")
    if not cleaned_name:
        raise ValueError("Labour type name is required.")
    existing = await labour_types_repo.get_labour_type_by_code(cleaned_code)
    if existing:
        raise ValueError("A labour type with this code already exists.")
    return await labour_types_repo.create_labour_type(code=cleaned_code, name=cleaned_name, rate=rate)


async def update_labour_type(
    labour_type_id: int,
    *,
    code: str | None = None,
    name: str | None = None,
    rate: float | None = None,
) -> dict[str, Any] | None:
    if labour_type_id <= 0:
        return None
    updates: dict[str, str | float] = {}
    if code is not None:
        cleaned_code = _clean_code(code)
        if not cleaned_code:
            raise ValueError("Labour type code is required.")
        existing = await labour_types_repo.get_labour_type_by_code(cleaned_code)
        if existing and existing.get("id") != labour_type_id:
            raise ValueError("A labour type with this code already exists.")
        updates["code"] = cleaned_code
    if name is not None:
        cleaned_name = _clean_name(name)
        if not cleaned_name:
            raise ValueError("Labour type name is required.")
        updates["name"] = cleaned_name
    if rate is not None:
        updates["rate"] = rate
    if not updates:
        return await labour_types_repo.get_labour_type(labour_type_id)
    return await labour_types_repo.update_labour_type(labour_type_id, **updates)


async def delete_labour_type(labour_type_id: int) -> None:
    if labour_type_id <= 0:
        return
    await labour_types_repo.delete_labour_type(labour_type_id)


async def replace_labour_types(definitions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_definitions: list[dict[str, Any]] = []
    for entry in definitions:
        raw_code = _clean_code(str(entry.get("code")) if entry.get("code") is not None else None)
        raw_name = _clean_name(str(entry.get("name")) if entry.get("name") is not None else None)
        raw_rate = entry.get("rate")
        identifier = entry.get("id")
        labour_type_id: int | None = None
        if identifier is not None:
            try:
                labour_type_id = int(identifier)
            except (TypeError, ValueError):
                labour_type_id = None
        cleaned_definitions.append(
            {
                "id": labour_type_id,
                "code": raw_code,
                "name": raw_name,
                "rate": raw_rate,
            }
        )
    return await labour_types_repo.replace_labour_types(cleaned_definitions)
