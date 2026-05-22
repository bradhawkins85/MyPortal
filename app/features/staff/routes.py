"""Staff routes for the ``staff`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Staff"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(
        path, getattr(_main(), endpoint_name), methods=methods, **kwargs
    )


_add("/staff", "staff_page", ["GET"], response_class=HTMLResponse)
_add(
    "/staff/workflows/onboarding",
    "staff_onboarding_workflow_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/staff/workflows/onboarding/policy", "staff_onboarding_workflow_policy", ["GET"])
_add(
    "/staff/workflows/onboarding/policy",
    "upsert_staff_onboarding_workflow_policy",
    ["POST"],
)
_add(
    "/staff/workflows/offboarding",
    "staff_offboarding_workflow_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/staff/workflows/offboarding/policy", "staff_offboarding_workflow_policy", ["GET"])
_add(
    "/staff/workflows/offboarding/policy",
    "upsert_staff_offboarding_workflow_policy",
    ["POST"],
)
_add("/staff/workflows/{direction}/policies", "list_staff_workflow_policies", ["GET"])
_add("/staff/workflows/{direction}/policies", "create_staff_workflow_policy", ["POST"])
_add(
    "/staff/workflows/{direction}/policies/{policy_id}",
    "update_staff_workflow_policy",
    ["PUT"],
)
_add(
    "/staff/workflows/{direction}/policies/{policy_id}",
    "delete_staff_workflow_policy",
    ["DELETE"],
)
_add(
    "/staff/workflows/history",
    "staff_workflow_history_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/staff/workflows/history/recent", "staff_workflow_history_recent", ["GET"])
_add(
    "/api/staff/workflows/executions/{execution_id}/retry",
    "retry_workflow_execution",
    ["POST"],
)
_add("/staff", "create_staff_member", ["POST"], response_class=HTMLResponse)
_add("/staff/{staff_id}", "update_staff_member", ["PUT"])
_add("/api/staff/{staff_id}/offboarding/request", "request_staff_offboarding", ["POST"])
_add("/staff/{staff_id}", "delete_staff_member", ["DELETE"])
_add("/staff/enabled", "set_staff_enabled", ["POST"], response_class=HTMLResponse)
_add("/staff/{staff_id}/verify", "verify_staff_member", ["POST"])
_add("/staff/{staff_id}/invite", "invite_staff_member", ["POST"])
_add(
    "/api/staff/{staff_id}/m365/reset-password",
    "m365_reset_staff_password",
    ["POST"],
)
_add("/api/staff/{staff_id}/m365/sign-in", "m365_set_staff_sign_in", ["POST"])


__all__ = ["router"]
