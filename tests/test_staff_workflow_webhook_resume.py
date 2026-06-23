import pytest

from app.services import webhook_monitor


@pytest.mark.anyio
async def test_staff_workflow_webhook_delivery_resumes_paused_execution(monkeypatch):
    calls = []

    async def fake_resume_paused_workflow_execution(*, execution_id, initiated_by_user_id):
        calls.append({"execution_id": execution_id, "initiated_by_user_id": initiated_by_user_id})
        return {"execution_id": execution_id, "state": "completed"}

    from app.services import staff_onboarding_workflows

    monkeypatch.setattr(
        staff_onboarding_workflows,
        "resume_paused_workflow_execution",
        fake_resume_paused_workflow_execution,
    )

    await webhook_monitor._resume_staff_workflow_after_delivery(
        event_id=123,
        event={"metadata": {"resume_source": "staff_workflow_http_post", "execution_id": 456}},
    )

    assert calls == [{"execution_id": 456, "initiated_by_user_id": None}]


@pytest.mark.anyio
async def test_non_staff_workflow_webhook_delivery_does_not_resume(monkeypatch):
    async def fail_resume_paused_workflow_execution(**_kwargs):
        raise AssertionError("resume should not be called")

    from app.services import staff_onboarding_workflows

    monkeypatch.setattr(
        staff_onboarding_workflows,
        "resume_paused_workflow_execution",
        fail_resume_paused_workflow_execution,
    )

    await webhook_monitor._resume_staff_workflow_after_delivery(
        event_id=123,
        event={"metadata": {"resume_source": "other", "execution_id": 456}},
    )
