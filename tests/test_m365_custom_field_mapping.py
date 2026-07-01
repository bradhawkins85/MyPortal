from __future__ import annotations

import asyncio

from app.services import m365 as m365_service


def test_sync_staff_custom_fields_matches_pipe_alternative_display_name(monkeypatch):
    """Checkbox M365 mappings can use | alternatives for non-UPN group names."""
    saved: list[dict] = []

    async def fake_definitions(company_id: int) -> list[dict]:
        assert company_id == 42
        return [
            {
                "name": "netsuite",
                "field_type": "checkbox",
                "m365_upn": "Netsuite|Netsuite Users",
            }
        ]

    async def fake_staff(company_id: int) -> list[dict]:
        assert company_id == 42
        return [{"id": 7, "email": "alex@example.com"}]

    async def fake_accessible(company_id: int, member_upns: list[str]) -> list[dict]:
        assert company_id == 42
        assert member_upns == ["alex@example.com"]
        return [
            {
                "mailbox_email": "netsuite-users@example.com",
                "display_name": "Netsuite Users",
            }
        ]

    async def fake_save(**kwargs):
        saved.append(kwargs)

    monkeypatch.setattr(
        m365_service.staff_custom_fields_repo,
        "list_field_definitions",
        fake_definitions,
    )
    monkeypatch.setattr(
        m365_service.staff_repo, "list_all_staff_for_import", fake_staff
    )
    monkeypatch.setattr(
        m365_service.m365_repo,
        "get_mailboxes_accessible_by_member",
        fake_accessible,
    )
    monkeypatch.setattr(
        m365_service.staff_custom_fields_repo,
        "set_staff_field_values_by_name",
        fake_save,
    )

    updated = asyncio.run(m365_service.sync_staff_custom_fields_from_m365_mailboxes(42))

    assert updated == 1
    assert saved == [{"company_id": 42, "staff_id": 7, "values": {"netsuite": True}}]


def test_m365_mapping_identifiers_split_pipe_alternatives():
    assert m365_service._m365_mapping_identifiers("Netsuite|Netsuite Users") == {
        "netsuite",
        "netsuite users",
    }
