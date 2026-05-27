"""Staff routes for the ``staff`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers


router = APIRouter(tags=["Staff"])

router.add_api_route("/staff", handlers.staff_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route(
    "/staff/workflows/onboarding",
    handlers.staff_onboarding_workflow_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/staff/workflows/onboarding/policy",
    handlers.staff_onboarding_workflow_policy,
    methods=["GET"],
)
router.add_api_route(
    "/staff/workflows/onboarding/policy",
    handlers.upsert_staff_onboarding_workflow_policy,
    methods=["POST"],
)
router.add_api_route(
    "/staff/workflows/offboarding",
    handlers.staff_offboarding_workflow_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/staff/workflows/offboarding/policy",
    handlers.staff_offboarding_workflow_policy,
    methods=["GET"],
)
router.add_api_route(
    "/staff/workflows/offboarding/policy",
    handlers.upsert_staff_offboarding_workflow_policy,
    methods=["POST"],
)
router.add_api_route(
    "/staff/workflows/{direction}/policies",
    handlers.list_staff_workflow_policies,
    methods=["GET"],
)
router.add_api_route(
    "/staff/workflows/{direction}/policies",
    handlers.create_staff_workflow_policy,
    methods=["POST"],
)
router.add_api_route(
    "/staff/workflows/{direction}/policies/{policy_id}",
    handlers.update_staff_workflow_policy,
    methods=["PUT"],
)
router.add_api_route(
    "/staff/workflows/{direction}/policies/{policy_id}",
    handlers.delete_staff_workflow_policy,
    methods=["DELETE"],
)
router.add_api_route(
    "/staff/workflows/history",
    handlers.staff_workflow_history_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/staff/workflows/history/recent",
    handlers.staff_workflow_history_recent,
    methods=["GET"],
)
router.add_api_route(
    "/api/staff/workflows/executions/{execution_id}/retry",
    handlers.retry_workflow_execution,
    methods=["POST"],
)
router.add_api_route("/staff", handlers.create_staff_member, methods=["POST"], response_class=HTMLResponse)
router.add_api_route("/staff/{staff_id}", handlers.update_staff_member, methods=["PUT"])
router.add_api_route(
    "/api/staff/{staff_id}/offboarding/request",
    handlers.request_staff_offboarding,
    methods=["POST"],
)
router.add_api_route("/staff/{staff_id}", handlers.delete_staff_member, methods=["DELETE"])
router.add_api_route("/staff/enabled", handlers.set_staff_enabled, methods=["POST"], response_class=HTMLResponse)
router.add_api_route("/staff/{staff_id}/verify", handlers.verify_staff_member, methods=["POST"])
router.add_api_route("/staff/{staff_id}/invite", handlers.invite_staff_member, methods=["POST"])
router.add_api_route(
    "/api/staff/{staff_id}/m365/reset-password",
    handlers.m365_reset_staff_password,
    methods=["POST"],
)
router.add_api_route(
    "/api/staff/{staff_id}/m365/sign-in",
    handlers.m365_set_staff_sign_in,
    methods=["POST"],
)


__all__ = ["router"]
