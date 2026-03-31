from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import main


class _DummyRequest:
    def __init__(self, form_data: dict[str, object]):
        self._form_data = form_data

    async def form(self):
        return self._form_data


@pytest.mark.anyio("asyncio")
async def test_create_staff_member_allows_missing_email(monkeypatch):
    request = _DummyRequest({"first_name": "Alex", "last_name": "Rivera"})

    monkeypatch.setattr(
        main,
        "_load_staff_context",
        AsyncMock(
            return_value=(
                {"id": 1, "is_super_admin": True},
                {"role": "admin"},
                {"id": 9, "name": "Acme"},
                3,
                9,
                None,
            )
        ),
    )
    monkeypatch.setattr(
        main.staff_field_config_service,
        "load_effective_company_staff_fields",
        AsyncMock(
            return_value=[
                {"key": "first_name", "required": True},
                {"key": "last_name", "required": True},
                {"key": "email", "required": False},
            ]
        ),
    )
    monkeypatch.setattr(
        main.staff_field_config_service,
        "validate_staff_form_values",
        lambda submitted, field_config: (
            {
                "first_name": "Alex",
                "last_name": "Rivera",
                "email": "",
                "enabled": True,
            },
            [],
        ),
    )

    create_staff_mock = AsyncMock(return_value={"id": 42})
    monkeypatch.setattr(main.staff_repo, "create_staff", create_staff_mock)
    monkeypatch.setattr(
        main.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.staff_custom_fields_repo,
        "set_staff_field_values_by_name",
        AsyncMock(return_value=None),
    )

    response = await main.create_staff_member(request)  # type: ignore[arg-type]

    assert response.status_code == 303
    create_staff_mock.assert_awaited_once()
    assert create_staff_mock.await_args.kwargs["email"] is None
