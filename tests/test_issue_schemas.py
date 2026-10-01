import pytest
from pydantic import ValidationError

from app.schemas.issues import IssueAssignmentCreate, IssueStatusUpdate, IssueUpdate
from app.services import issues as issues_service


def test_issue_assignment_create_trims_and_normalises_status():
    model = IssueAssignmentCreate(company_name="  Example Co  ", status=" In Progress ")

    assert model.company_name == "Example Co"
    assert model.status == issues_service.normalise_status("In Progress")


def test_issue_update_rejects_blank_new_name():
    with pytest.raises(ValidationError):
        IssueUpdate(new_name="   ")


def test_issue_status_update_rejects_invalid_status():
    with pytest.raises(ValidationError):
        IssueStatusUpdate(
            issue_name="Issue",
            company_name="Example Co",
            status="definitely-not-valid",
        )
