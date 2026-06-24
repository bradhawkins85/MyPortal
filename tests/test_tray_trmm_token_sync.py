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
                    {"id": 11, "field": 7, "name": "MyPortalToken", "value": "old"}
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
                "custom_fields": [{"field": 7, "value": "new-token", "id": 11}],
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
                "custom_fields": [{"field": 9, "value": "new-token"}],
            },
        },
    ]


def test_update_trmm_client_token_field_preserves_id_only_custom_fields(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if endpoint == "/clients/86/" and method == "GET":
            return {
                "id": 86,
                "name": "Forgate Contracting",
                "custom_fields": [
                    {"id": 12, "field": 12, "client": 86, "value": "forgate"},
                    {"id": 13, "field": 4, "client": 86, "value": None},
                    {"id": 14, "field": 5, "client": 86, "value": None},
                ],
            }
        if endpoint == "/core/customfields/" and method == "GET":
            return {
                "results": [
                    {"id": 12, "model": "client", "name": "Company Slug"},
                    {"id": 4, "model": "client", "name": "MyPortalToken"},
                    {"id": 5, "model": "client", "name": "Other"},
                ]
            }
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    asyncio.run(
        tray.update_trmm_client_token_field(trmm_client_id=86, token="new-token")
    )

    assert calls[-1] == {
        "endpoint": "/clients/86/",
        "method": "PUT",
        "body": {
            "custom_fields": [
                {"field": 12, "value": "forgate", "id": 12},
                {"field": 4, "value": "new-token", "id": 13},
                {"field": 5, "value": None, "id": 14},
            ]
        },
    }
