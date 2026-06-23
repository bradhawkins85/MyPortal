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
                "client": {},
                "custom_fields": [{"field": 7, "string_value": "new-token"}],
            },
        },
    ]


def test_update_trmm_client_token_field_put_payload_allows_name_fallback(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if method == "GET":
            return {"id": 42, "name": "Acme", "custom_fields": []}
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    asyncio.run(
        tray.update_trmm_client_token_field(
            trmm_client_id="42", token="new-token", field_name="Portal Token"
        )
    )

    assert calls[-1] == {
        "endpoint": "/clients/42/",
        "method": "PUT",
        "body": {
            "client": {},
            "custom_fields": [{"name": "Portal Token", "string_value": "new-token"}],
        },
    }
