"""Tests for ticket split and merge functionality."""
from datetime import datetime, timezone

import pytest

from app.repositories import tickets as tickets_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_split_ticket_moves_replies_to_new_ticket(monkeypatch):
    """Test that splitting a ticket moves selected replies to a new ticket."""
    # Mock data
    original_ticket_data = {
        "id": 1,
        "subject": "Original Ticket",
        "description": "Original description",
        "company_id": 10,
        "requester_id": 20,
        "assigned_user_id": 30,
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "external_reference": None,
        "ticket_number": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "closed_at": None,
        "merged_into_ticket_id": None,
        "split_from_ticket_id": None,
    }
    
    new_ticket_data = {
        **original_ticket_data,
        "id": 2,
        "subject": "Split Ticket",
        "description": "Split from ticket #1",
        "split_from_ticket_id": 1,
    }
    
    replies_data = [
        {"id": 100, "ticket_id": 1, "body": "Reply 1", "author_id": 20},
        {"id": 101, "ticket_id": 1, "body": "Reply 2", "author_id": 30},
        {"id": 102, "ticket_id": 1, "body": "Reply 3", "author_id": 20},
    ]
    
    # Track database operations
    executed_queries = []
    
    async def mock_get_ticket(ticket_id: int):
        if ticket_id == 1:
            return original_ticket_data.copy()
        elif ticket_id == 2:
            return new_ticket_data.copy()
        return None
    
    async def mock_create_ticket(**kwargs):
        executed_queries.append(("create_ticket", kwargs))
        return new_ticket_data.copy()
    
    async def mock_execute(query, params=None):
        executed_queries.append(("execute", query, params))
        return 2  # Number of affected rows
    
    async def mock_move_replies(reply_ids, target_ticket_id):
        executed_queries.append(("move_replies", reply_ids, target_ticket_id))
        return len(reply_ids)
    
    # Apply mocks
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "create_ticket", mock_create_ticket)
    monkeypatch.setattr(tickets_repo.db, "execute", mock_execute)
    monkeypatch.setattr(tickets_repo, "move_replies_to_ticket", mock_move_replies)
    
    # Execute split
    original, new_ticket, moved_count = await tickets_repo.split_ticket(
        original_ticket_id=1,
        reply_ids=[100, 101],
        new_ticket_subject="Split Ticket",
        new_ticket_id=None,
    )
    
    # Assertions
    assert original is not None
    assert new_ticket is not None
    assert moved_count == 2
    assert new_ticket["id"] == 2
    assert new_ticket["split_from_ticket_id"] == 1
    assert new_ticket["company_id"] == original_ticket_data["company_id"]
    assert new_ticket["requester_id"] == original_ticket_data["requester_id"]
    
    # Verify create_ticket was called with correct parameters
    create_call = next((q for q in executed_queries if q[0] == "create_ticket"), None)
    assert create_call is not None
    assert create_call[1]["subject"] == "Split Ticket"
    assert create_call[1]["company_id"] == 10
    assert create_call[1]["requester_id"] == 20
    
    # Verify replies were moved
    move_call = next((q for q in executed_queries if q[0] == "move_replies"), None)
    assert move_call is not None
    assert move_call[1] == [100, 101]
    assert move_call[2] == 2


@pytest.mark.anyio
async def test_merge_tickets_moves_all_replies_to_target(monkeypatch):
    """Test that merging tickets moves all replies to the target ticket."""
    # Mock data
    tickets_data = {
        1: {
            "id": 1,
            "subject": "Ticket 1",
            "status": "open",
            "merged_into_ticket_id": None,
        },
        2: {
            "id": 2,
            "subject": "Ticket 2",
            "status": "open",
            "merged_into_ticket_id": None,
        },
        3: {
            "id": 3,
            "subject": "Ticket 3",
            "status": "open",
            "merged_into_ticket_id": None,
        },
    }
    
    replies_by_ticket = {
        1: [],  # Target ticket
        2: [{"id": 200, "ticket_id": 2}, {"id": 201, "ticket_id": 2}],
        3: [{"id": 300, "ticket_id": 3}],
    }
    
    executed_queries = []
    
    async def mock_get_ticket(ticket_id: int):
        return tickets_data.get(ticket_id)
    
    async def mock_list_replies(ticket_id: int, include_internal: bool = True):
        return replies_by_ticket.get(ticket_id, [])
    
    async def mock_move_replies(reply_ids, target_ticket_id):
        executed_queries.append(("move_replies", list(reply_ids), target_ticket_id))
        return len(reply_ids)
    
    async def mock_execute(query, params=None):
        executed_queries.append(("execute", query, params))
        return len(params) - 1 if params else 0
    
    # Apply mocks
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(tickets_repo, "move_replies_to_ticket", mock_move_replies)
    monkeypatch.setattr(tickets_repo.db, "execute", mock_execute)
    
    # Execute merge
    merged_ticket, merged_ids, moved_count = await tickets_repo.merge_tickets(
        ticket_ids=[1, 2, 3],
        target_ticket_id=1,
    )
    
    # Assertions
    assert merged_ticket is not None
    assert merged_ticket["id"] == 1
    assert set(merged_ids) == {2, 3}
    assert moved_count == 3  # 2 from ticket 2 + 1 from ticket 3
    
    # Verify replies were moved
    move_calls = [q for q in executed_queries if q[0] == "move_replies"]
    assert len(move_calls) == 2
    
    # Verify update query to mark tickets as merged
    update_calls = [q for q in executed_queries if q[0] == "execute" and "UPDATE tickets" in q[1]]
    assert len(update_calls) == 1


@pytest.mark.anyio
async def test_merge_tickets_requires_target_in_list(monkeypatch):
    """Test that merge validation requires target ticket to be in the list."""
    async def mock_get_ticket(ticket_id: int):
        return {"id": ticket_id}
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    
    # Should raise ValueError because target (1) is not in the list
    with pytest.raises(ValueError, match="Target ticket ID must be in the list"):
        await tickets_repo.merge_tickets(
            ticket_ids=[2, 3, 4],
            target_ticket_id=1,
        )


@pytest.mark.anyio
async def test_get_merged_target_ticket_id_follows_chain(monkeypatch):
    """Test that getting merged target follows the chain of merged tickets."""
    # Mock data: ticket 1 -> 2 -> 3 (final target)
    tickets_data = {
        1: {"merged_into_ticket_id": 2},
        2: {"merged_into_ticket_id": 3},
        3: {"merged_into_ticket_id": None},  # Final target
    }
    
    async def mock_fetch_one(query, params=None):
        if params and len(params) > 0:
            ticket_id = params[0]
            return tickets_data.get(ticket_id)
        return None
    
    monkeypatch.setattr(tickets_repo.db, "fetch_one", mock_fetch_one)
    
    # Test following the chain
    target = await tickets_repo.get_merged_target_ticket_id(1)
    assert target == 3
    
    # Test starting from middle of chain
    target = await tickets_repo.get_merged_target_ticket_id(2)
    assert target == 3
    
    # Test when ticket is not merged
    target = await tickets_repo.get_merged_target_ticket_id(3)
    assert target is None


@pytest.mark.anyio
async def test_get_merged_target_handles_circular_reference(monkeypatch):
    """Test that circular merge references are handled safely."""
    # Mock data: circular reference 1 -> 2 -> 1
    tickets_data = {
        1: {"merged_into_ticket_id": 2},
        2: {"merged_into_ticket_id": 1},
    }
    
    async def mock_fetch_one(query, params=None):
        if params and len(params) > 0:
            ticket_id = params[0]
            return tickets_data.get(ticket_id)
        return None
    
    monkeypatch.setattr(tickets_repo.db, "fetch_one", mock_fetch_one)
    
    # Should return None when circular reference is detected
    target = await tickets_repo.get_merged_target_ticket_id(1)
    assert target is None


@pytest.mark.anyio
async def test_move_replies_to_ticket(monkeypatch):
    """Test that move_replies_to_ticket updates the ticket_id for replies."""
    executed_queries = []
    
    async def mock_execute(query, params=None):
        executed_queries.append((query, params))
        return 3  # Number of affected rows
    
    monkeypatch.setattr(tickets_repo.db, "execute", mock_execute)
    
    # Execute move
    moved_count = await tickets_repo.move_replies_to_ticket(
        reply_ids=[100, 101, 102],
        target_ticket_id=5,
    )
    
    assert moved_count == 3
    assert len(executed_queries) == 1
    query, params = executed_queries[0]
    assert "UPDATE ticket_replies" in query
    assert "SET ticket_id = %s" in query
    assert params[0] == 5
    assert params[1:] == (100, 101, 102)
