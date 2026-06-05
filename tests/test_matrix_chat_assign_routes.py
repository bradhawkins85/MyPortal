from app.features.matrix_chat_assign import routes

import pytest

def test_normalize_conditions_clears_conditions_for_default_rule():
    conditions = [{"type": "subject", "operator": "contains", "value": "vip"}]
    assert routes._normalize_conditions(is_default=True, conditions=conditions) == []


def test_normalize_conditions_keeps_conditions_for_non_default_rule():
    conditions = [{"type": "subject", "operator": "contains", "value": "vip"}]
    assert routes._normalize_conditions(is_default=False, conditions=conditions) == conditions


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeForm:
    def __init__(self, entries):
        self._entries = list(entries)
        self._map = {}
        for key, value in self._entries:
            self._map[key] = value

    def get(self, key, default=None):
        return self._map.get(key, default)

    def multi_items(self):
        return list(self._entries)


class _FakeRequest:
    def __init__(self, form_entries):
        self._form = _FakeForm(form_entries)

    async def form(self):
        return self._form


class _FakeMain:
    async def _require_super_admin_page(self, _request):
        return {"id": 1, "is_super_admin": True}, None


@pytest.mark.anyio
async def test_create_rule_drops_conditions_when_default_selected(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_create_rule(**kwargs):
        captured.update(kwargs)
        return {"id": 123}

    monkeypatch.setattr(routes, "_main", lambda: _FakeMain())
    monkeypatch.setattr(routes.assign_repo, "create_rule", fake_create_rule)

    request = _FakeRequest(
        [
            ("name", "Fallback"),
            ("priority", "10"),
            ("is_default", "1"),
            ("is_active", "1"),
            ("conditions[0][type]", "subject"),
            ("conditions[0][operator]", "contains"),
            ("conditions[0][value]", "vip"),
        ]
    )

    response = await routes.admin_create_auto_assign_rule(request)

    assert response.status_code == 303
    assert captured["is_default"] is True
    assert captured["conditions"] == []


@pytest.mark.anyio
async def test_update_rule_drops_conditions_when_default_selected(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_rule(_rule_id):
        return {"id": 12, "name": "Existing"}

    async def fake_update_rule(rule_id, **kwargs):
        captured["rule_id"] = rule_id
        captured.update(kwargs)
        return {"id": rule_id}

    monkeypatch.setattr(routes, "_main", lambda: _FakeMain())
    monkeypatch.setattr(routes.assign_repo, "get_rule", fake_get_rule)
    monkeypatch.setattr(routes.assign_repo, "update_rule", fake_update_rule)

    request = _FakeRequest(
        [
            ("name", "Fallback"),
            ("priority", "20"),
            ("is_default", "1"),
            ("is_active", "1"),
            ("conditions[0][type]", "company_name"),
            ("conditions[0][operator]", "equals"),
            ("conditions[0][value]", "Acme"),
        ]
    )

    response = await routes.admin_update_auto_assign_rule(12, request)

    assert response.status_code == 303
    assert captured["rule_id"] == 12
    assert captured["is_default"] is True
    assert captured["conditions"] == []
