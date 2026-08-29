import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import m365_mail


def test_graph_dmarc_recipient_uses_tagged_address_only():
    message = {"toRecipients": [
        {"emailAddress": {"address": "DMARC@example.com"}},
        {"emailAddress": {"address": "DMARC+abcdefghijklmnop@example.com"}},
    ]}
    assert m365_mail._graph_dmarc_recipient(message) == "DMARC+abcdefghijklmnop@example.com"


def test_graph_dmarc_import_persists_each_attachment(monkeypatch):
    monkeypatch.setattr(
        m365_mail, "_graph_file_attachments",
        AsyncMock(return_value=[("one.xml", b"<one/>"), ("two.xml.gz", b"gzip")]),
    )
    ingest = AsyncMock(return_value=[])
    monkeypatch.setattr(m365_mail.dmarc_service, "ingest_attachment", ingest)
    message = {"toRecipients": [{"emailAddress": {"address": "DMARC+abcdefghijklmnop@example.com"}}]}
    count = asyncio.run(
        m365_mail._import_graph_dmarc_message(
            access_token="secret", upn="DMARC@example.com", graph_message=message,
            message_id="stable-graph-id", internet_message_id="internet-id",
            received_at=m365_mail.datetime.now(m365_mail.timezone.utc),
            company_id=42,
        )
    )
    assert count == 2
    assert ingest.await_count == 2
    assert all(call.kwargs["company_id"] == 42 for call in ingest.await_args_list)


def test_dmarc_mailbox_requires_company(monkeypatch):
    monkeypatch.setattr(m365_mail.mail_repo, "get_dmarc_account", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="company is required"):
        asyncio.run(m365_mail.create_account({
                "name": "Reports", "user_principal_name": "reports@example.com",
                "import_purpose": "dmarc",
        }))


def test_multiple_dmarc_mailboxes_are_allowed_per_company(monkeypatch):
    create = AsyncMock(return_value={"id": 10, "company_id": 42, "import_purpose": "dmarc"})
    monkeypatch.setattr(m365_mail.mail_repo, "create_account", create)
    monkeypatch.setattr(m365_mail, "_ensure_scheduled_task", AsyncMock(side_effect=lambda account: account))
    monkeypatch.setattr(m365_mail.modules_service, "update_module", AsyncMock())
    account = asyncio.run(m365_mail.create_account({
        "name": "Reports", "company_id": 42,
        "user_principal_name": "dmarc@customer.example", "import_purpose": "dmarc",
    }))
    assert account["id"] == 10
    create.assert_awaited_once()
