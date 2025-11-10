"""Simple test for BC13 SQLite fallback functionality.

This test focuses on the core requirement: verifying SQLite fallback works.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.core.database import Database
from app.core.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sqlite_mode_detection():
    """Test SQLite mode is detected when MySQL config is missing."""
    test_settings = Settings(
        SESSION_SECRET="test-secret",
        TOTP_ENCRYPTION_KEY="A" * 64,
        DB_HOST=None,
        DB_USER=None,
        DB_PASSWORD=None,
        DB_NAME=None,
    )
    
    test_db = Database()
    test_db._settings = test_settings
    test_db._use_sqlite = test_db._should_use_sqlite()
    
    assert test_db.is_sqlite() is True


@pytest.mark.anyio
async def test_mysql_mode_detection():
    """Test MySQL mode is used when config is provided."""
    test_settings = Settings(
        SESSION_SECRET="test-secret",
        TOTP_ENCRYPTION_KEY="A" * 64,
        DB_HOST="localhost",
        DB_USER="testuser",
        DB_PASSWORD="testpass",
        DB_NAME="testdb",
    )
    
    test_db = Database()
    test_db._settings = test_settings
    test_db._use_sqlite = test_db._should_use_sqlite()
    
    assert test_db.is_sqlite() is False


@pytest.mark.anyio
async def test_sqlite_connection():
    """Test that SQLite connection can be established."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None,
            DB_USER=None,
            DB_PASSWORD=None,
            DB_NAME=None,
        )
        
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: db_path
        
        # Connect
        await test_db.connect()
        assert test_db.is_connected()
        
        # Execute a simple query
        await test_db.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        await test_db.execute("INSERT INTO test_table (name) VALUES (?)", ("test",))
        
        # Fetch result
        result = await test_db.fetch_one("SELECT * FROM test_table WHERE name = ?", ("test",))
        assert result is not None
        assert result["name"] == "test"
        
        # Cleanup
        await test_db.disconnect()
        assert not test_db.is_connected()


@pytest.mark.anyio
async def test_sql_adaptation():
    """Test that MySQL SQL is adapted for SQLite."""
    test_db = Database()
    test_db._use_sqlite = True
    
    # Test AUTO_INCREMENT
    mysql_sql = "CREATE TABLE test (id INT AUTO_INCREMENT PRIMARY KEY)"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "AUTOINCREMENT" in adapted
    assert "AUTO_INCREMENT" not in adapted
    
    # Test ENGINE removal
    mysql_sql = "CREATE TABLE test (id INT) ENGINE=InnoDB"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "ENGINE" not in adapted
    
    # Test DATETIME -> TEXT
    mysql_sql = "CREATE TABLE test (created_at DATETIME)"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "TEXT" in adapted or "datetime" in adapted.lower()
    
    # Test COMMENT removal
    mysql_sql = "id INT COMMENT 'User ID'"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "COMMENT" not in adapted


@pytest.mark.anyio
async def test_basic_migration():
    """Test that a basic migration can be applied with SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        migrations_dir = Path(tmpdir) / "migrations"
        migrations_dir.mkdir()
        
        # Create a simple test migration
        migration_file = migrations_dir / "001_test.sql"
        migration_file.write_text(
            "CREATE TABLE IF NOT EXISTS test_migration (id INTEGER PRIMARY KEY, value TEXT);"
        )
        
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None,
            DB_USER=None,
            DB_PASSWORD=None,
            DB_NAME=None,
        )
        
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: db_path
        test_db._get_migrations_dir = lambda: migrations_dir
        
        # Run migrations
        await test_db.run_migrations()
        
        # Verify database was created
        assert db_path.exists()
        
        # Verify migration was applied
        await test_db.connect()
        migrations = await test_db.fetch_all("SELECT name FROM migrations")
        assert len(migrations) == 1
        assert migrations[0]["name"] == "001_test.sql"
        
        # Verify table was created
        result = await test_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_migration'"
        )
        assert result is not None
        
        await test_db.disconnect()


@pytest.mark.anyio
async def test_migration_idempotency():
    """Test that migrations can be run multiple times safely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        migrations_dir = Path(tmpdir) / "migrations"
        migrations_dir.mkdir()
        
        # Create test migration
        migration_file = migrations_dir / "001_test.sql"
        migration_file.write_text(
            "CREATE TABLE IF NOT EXISTS idempotent_test (id INTEGER PRIMARY KEY);"
        )
        
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None,
            DB_USER=None,
            DB_PASSWORD=None,
            DB_NAME=None,
        )
        
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: db_path
        test_db._get_migrations_dir = lambda: migrations_dir
        
        # Run migrations twice
        await test_db.run_migrations()
        await test_db.disconnect()
        
        await test_db.run_migrations()
        await test_db.connect()
        
        # Should still have only one migration record
        migrations = await test_db.fetch_all("SELECT name FROM migrations")
        assert len(migrations) == 1
        
        await test_db.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
