"""Automations page routes for the ``automations`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["Automations"])

router.add_api_route(
    "/admin/automations",
    handlers.admin_automations_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/reorder",
    handlers.admin_reorder_automations,
    methods=["POST"],
)
router.add_api_route(
    "/admin/automations/create/scheduled",
    handlers.admin_create_scheduled_automation_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/create/event",
    handlers.admin_create_event_automation_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations",
    handlers.admin_create_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/preview",
    handlers.admin_preview_automation,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/simulate",
    handlers.admin_simulate_automation,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/simulate/process",
    handlers.admin_process_simulated_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/edit",
    handlers.admin_edit_automation_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}",
    handlers.admin_update_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/status",
    handlers.admin_update_automation_status,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/history",
    handlers.admin_automation_history,
    methods=["GET"],
)
router.add_api_route(
    "/admin/automations/{automation_id}/test",
    handlers.admin_test_automation,
    methods=["POST"],
)
router.add_api_route(
    "/admin/automations/{automation_id}/execute",
    handlers.admin_execute_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/clone",
    handlers.admin_clone_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/automations/{automation_id}/delete",
    handlers.admin_delete_automation,
    methods=["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
