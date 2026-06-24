import asyncio

from app.services import tray


def test_update_trmm_client_token_field_uses_trmm_put_payload(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if method == "GET":
            return {
                "id": 42,
                "name": "Acme",
                "custom_fields": [
                    {"field": 7, "name": "MyPortalToken", "string_value": "old"}
                ],
            }
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    result = asyncio.run(
        tray.update_trmm_client_token_field(trmm_client_id=42, token="new-token")
    )

    assert result == {"status": "ok"}
    assert calls == [
        {"endpoint": "/clients/42/", "method": "GET", "body": None},
        {
            "endpoint": "/clients/42/",
            "method": "PUT",
            "body": {
                "custom_fields": [{"field": 7, "string_value": "new-token"}],
            },
        },
    ]


def test_update_trmm_client_token_field_resolves_client_field_definition(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if endpoint == "/clients/42/" and method == "GET":
            return {"id": 42, "name": "Acme", "custom_fields": []}
        if endpoint == "/core/customfields/" and method == "GET":
            return [
                {"id": 3, "model": "agent", "name": "Portal Token"},
                {"id": 9, "model": "client", "name": "Portal Token"},
            ]
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    asyncio.run(
        tray.update_trmm_client_token_field(
            trmm_client_id="42", token="new-token", field_name="Portal Token"
        )
    )

    assert calls == [
        {"endpoint": "/clients/42/", "method": "GET", "body": None},
        {"endpoint": "/core/customfields/", "method": "GET", "body": None},
        {
            "endpoint": "/clients/42/",
            "method": "PUT",
            "body": {
                "custom_fields": [{"field": 9, "string_value": "new-token"}],
            },
        },
    ]
