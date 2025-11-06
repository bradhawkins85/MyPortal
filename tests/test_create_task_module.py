import pytest

from app.repositories import ticket_tasks as ticket_tasks_repo
from app.services import modules as modules_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_task_module_single_task(monkeypatch):
    created_records: list[dict[str, int | str]] = []
    manual_events: list[dict[str, object]] = []

    async def fake_create_task(*, ticket_id: int, task_name: str, sort_order: int):
        record = {
            "id": len(created_records) + 1,
            "ticket_id": ticket_id,
            "task_name": task_name,
            "sort_order": sort_order,
        }
        created_records.append(record)
        return record

    async def fake_create_manual_event(**kwargs):
        manual_events.append(kwargs)
        return {"id": 51, "status": "pending"}

    async def fake_record_success(event_id, *, attempt_number, response_status, response_body):
        return {
            "id": event_id,
            "status": "succeeded",
            "attempt_count": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        }

    async def fake_record_failure(*args, **kwargs):  # pragma: no cover - defensive in tests
        return {"id": args[0], "status": kwargs.get("status", "error")}

    monkeypatch.setattr(ticket_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(modules_service.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules_service, "_record_success", fake_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", fake_record_failure)

    payload = {"ticket_id": 1001, "task_name": "Follow up", "sort_order": 5}
    result = await modules_service._invoke_create_task(settings={}, payload=payload, event_future=None)

    assert result["status"] == "succeeded"
    assert result["task_id"] == 1
    assert result["ticket_id"] == 1001
    assert len(created_records) == 1
    assert created_records[0]["task_name"] == "Follow up"
    assert manual_events and manual_events[0]["payload"]["task_name"] == "Follow up"
    response = result.get("response")
    assert isinstance(response, dict)
    assert response["task_id"] == 1


@pytest.mark.anyio
async def test_create_task_module_multiple_tasks(monkeypatch):
    created_records: list[dict[str, int | str]] = []
    manual_events: list[dict[str, object]] = []

    async def fake_create_task(*, ticket_id: int, task_name: str, sort_order: int):
        record = {
            "id": len(created_records) + 1,
            "ticket_id": ticket_id,
            "task_name": task_name,
            "sort_order": sort_order,
        }
        created_records.append(record)
        return record

    async def fake_create_manual_event(**kwargs):
        manual_events.append(kwargs)
        return {"id": 87, "status": "pending"}

    async def fake_record_success(event_id, *, attempt_number, response_status, response_body):
        return {
            "id": event_id,
            "status": "succeeded",
            "attempt_count": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        }

    async def fake_record_failure(*args, **kwargs):  # pragma: no cover - defensive in tests
        return {"id": args[0], "status": kwargs.get("status", "error")}

    monkeypatch.setattr(ticket_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(modules_service.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules_service, "_record_success", fake_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", fake_record_failure)

    payload = {
        "context": {"ticket_id": 2002},
        "tasks": [
            {"task_name": "Call requester", "sort_order": 15},
            {"task_name": "Document actions", "ticket_id": 2003},
        ],
    }
    result = await modules_service._invoke_create_task(settings={}, payload=payload, event_future=None)

    assert result["status"] == "succeeded"
    assert result["task_ids"] == [1, 2]
    assert result["ticket_ids"] == [2002, 2003]
    assert len(created_records) == 2
    assert created_records[0]["ticket_id"] == 2002
    assert created_records[1]["ticket_id"] == 2003
    assert manual_events and manual_events[0]["target_url"] == "internal://tickets/tasks"
    event_payload = manual_events[0]["payload"]
    assert len(event_payload["tasks"]) == 2
    assert event_payload["tasks"][0]["ticket_id"] == 2002
    response = result.get("response")
    assert isinstance(response, dict)
    assert response["count"] == 2
    assert response["tasks"][0]["task_name"] == "Call requester"

