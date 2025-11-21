"""Test to verify email signature saving functionality."""
import pytest
from app.repositories import users as user_repo
from app.core.database import db


@pytest.mark.asyncio
async def test_update_user_email_signature():
    """Test that email_signature field can be updated in the database."""
    # Create a test user
    created = await user_repo.create_user(
        email="test_sig@example.com",
        password="TestPassword123!",
        first_name="Test",
        last_name="User",
        is_super_admin=False,
    )
    user_id = created["id"]
    
    try:
        # Update with email signature
        signature_html = "<p>Best regards,<br>Test User</p>"
        updated = await user_repo.update_user(user_id, email_signature=signature_html)
        
        assert updated is not None
        assert updated["email_signature"] == signature_html
        
        # Retrieve user again to verify persistence
        retrieved = await user_repo.get_user_by_id(user_id)
        assert retrieved is not None
        assert retrieved["email_signature"] == signature_html
        
        # Update with None (clear signature)
        updated_null = await user_repo.update_user(user_id, email_signature=None)
        assert updated_null["email_signature"] is None
        
        # Verify null persisted
        retrieved_null = await user_repo.get_user_by_id(user_id)
        assert retrieved_null["email_signature"] is None
        
    finally:
        # Clean up
        await user_repo.delete_user(user_id)


@pytest.mark.asyncio
async def test_email_signature_column_exists():
    """Test that email_signature column exists in users table."""
    result = await db.fetch_one(
        """
        SELECT COLUMN_NAME 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_NAME = 'users' 
        AND COLUMN_NAME = 'email_signature'
        """
    )
    assert result is not None, "email_signature column does not exist in users table"
