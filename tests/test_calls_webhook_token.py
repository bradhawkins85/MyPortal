import asyncio

from app.repositories import calls


class ModuleRepoStub:
    def __init__(self, module):
        self.module = module
        self.updated = None
        self.upserted = None

    async def get_module(self, slug):
        assert slug == "calls"
        return self.module

    async def update_module(self, slug, *, settings):
        assert slug == "calls"
        self.updated = settings
        if self.module is not None:
            self.module = {**self.module, "settings": settings}
        return self.module

    async def upsert_module(self, **kwargs):
        self.upserted = kwargs
        self.module = {"settings": kwargs["settings"]}
        return self.module


def test_get_or_create_webhook_token_replaces_placeholder(monkeypatch):
    async def run():
        stub = ModuleRepoStub(
            {
                "settings": {
                    "webhook_token": "obscure_static_id_for_security",
                    "webhook_path": "/phonewebhook/{obscure_static_id_for_security}/",
                    "supported_variables": ["remote"],
                }
            }
        )
        monkeypatch.setattr(calls, "module_repo", stub)
        monkeypatch.setattr(calls, "_ensure_connection", _noop)
        monkeypatch.setattr(calls, "_generate_webhook_token", lambda: "generated-token")

        token = await calls.get_or_create_webhook_token()

        assert token == "generated-token"
        assert stub.updated["webhook_token"] == "generated-token"
        assert stub.updated["webhook_path"] == "/phonewebhook/generated-token/"
        assert stub.updated["supported_variables"] == ["remote"]

    asyncio.run(run())


def test_get_or_create_webhook_token_keeps_existing_token(monkeypatch):
    async def run():
        stub = ModuleRepoStub(
            {
                "settings": {
                    "webhook_token": "existing-token",
                    "webhook_path": "/phonewebhook/existing-token/",
                }
            }
        )
        monkeypatch.setattr(calls, "module_repo", stub)
        monkeypatch.setattr(calls, "_ensure_connection", _noop)

        token = await calls.get_or_create_webhook_token()

        assert token == "existing-token"
        assert stub.updated is None

    asyncio.run(run())


async def _noop():
    return None
