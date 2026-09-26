"""Browser-facing process workflow regression coverage."""

from pathlib import Path

from app.features.processes import PACK


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_process_pack_exposes_api_and_web_routes():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert "/api/processes/templates" in paths
    assert "/api/processes/templates/{template_id}" in paths
    assert "/api/processes/runs/{run_id}/steps/{step_id}" in paths
    assert "/processes" in paths
    assert "/processes/start" in paths
    assert "/processes/{run_id}" in paths


def test_creation_execution_reassignment_and_empty_states_are_wired():
    editor = read("app/templates/processes/template_editor.html")
    start = read("app/templates/processes/start.html")
    detail = read("app/templates/processes/detail.html")
    listing = read("app/templates/processes/index.html")
    script = read("app/static/js/processes.js")

    assert "data-template-form" in editor and "data-add-step" in editor
    assert "data-run-start-form" in start and "ticket_id" in start
    assert "data-run-assignee" in detail and "data-step-action" in detail
    assert 'data-run-action="paused"' in detail
    assert 'data-run-action="failed"' in detail
    assert "immutable" in detail.lower()
    assert "No runs found" in listing and "data-status-filter" in listing
    assert "X-CSRF-Token" in script
    assert "window.location.reload()" in script


def test_asset_ticket_and_navigation_entry_points_are_present():
    assert "/processes/start?asset_id=" in read("app/templates/assets/detail.html")
    assert "/processes/start?ticket_id=" in read("app/templates/admin/ticket_detail.html")
    assert '<span class="menu__label">Processes</span>' in read("app/templates/base.html")
