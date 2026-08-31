from pathlib import Path

from app.api.routes import bcp
from app.repositories import bcp as repository


def test_global_library_routes_are_registered():
    paths = {(route.path, method) for route in bcp.router.routes for method in route.methods}
    assert ("/bcp/library", "GET") in paths
    assert ("/bcp/library/risks", "POST") in paths
    assert ("/bcp/library/bia", "POST") in paths
    assert ("/bcp/available/{kind}/{assessment_id}/add", "POST") in paths


def test_global_library_repository_contract():
    assert callable(repository.list_global_risks)
    assert callable(repository.list_global_bia_assessments)
    assert callable(repository.assign_global_risk)
    assert callable(repository.assign_global_bia)


def test_global_library_migration_tracks_customer_copies():
    sql = Path("migrations/354_bcp_global_assessment_library.sql").read_text()
    assert "bcp_global_risk_assignment" in sql
    assert "bcp_global_bia_assignment" in sql
    assert "PRIMARY KEY (global_risk_id, company_id)" in sql
    assert "PRIMARY KEY (global_bia_id, company_id)" in sql
