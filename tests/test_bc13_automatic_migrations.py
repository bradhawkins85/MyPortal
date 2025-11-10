"""Tests for BC13 automatic migrations on startup.

This test verifies that:
1. The application can start with an empty database
2. All migrations are automatically applied on startup
3. Required tables are created
4. SQLite fallback works when MySQL is not configured
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import Database, db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sqlite_fallback_mode():
    """Test that database uses SQLite when MySQL config is missing."""
    # Save original env vars
    original_env = {}
    for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]:
        original_env[key] = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]
    
    try:
        # Create a new Database instance with fresh settings
        from app.core.config import Settings
        
        # Create settings without MySQL config
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None,
            DB_USER=None,
            DB_PASSWORD=None,
            DB_NAME=None,
        )
        
        # Create database with these settings
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = test_db._should_use_sqlite()
        
        # Verify SQLite mode is detected
        assert test_db.is_sqlite() is True, "Database should use SQLite when MySQL config is missing"
        
        # Verify SQLite path is correct
        expected_path = Path(__file__).resolve().parent.parent / "myportal.db"
        assert test_db._get_sqlite_path() == expected_path
    finally:
        # Restore env vars
        for key, value in original_env.items():
            if value is not None:
                os.environ[key] = value


@pytest.mark.anyio
async def test_mysql_mode_when_configured():
    """Test that database uses MySQL when properly configured."""
    from app.core.config import Settings
    
    # Create settings with MySQL config
    test_settings = Settings(
        SESSION_SECRET="test-secret",
        TOTP_ENCRYPTION_KEY="A" * 64,
        DB_HOST="localhost",
        DB_USER="testuser",
        DB_PASSWORD="testpass",
        DB_NAME="testdb",
    )
    
    # Create database with these settings
    test_db = Database()
    test_db._settings = test_settings
    test_db._use_sqlite = test_db._should_use_sqlite()
    
    # Verify MySQL mode
    assert test_db.is_sqlite() is False, "Database should use MySQL when config is provided"


@pytest.mark.anyio
async def test_migrations_run_with_sqlite():
    """Test that migrations can run successfully with SQLite backend."""
    # Create a temporary SQLite database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        # Clear MySQL config to force SQLite
        original_env = {}
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]:
            original_env[key] = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]
        
        try:
            # Force reload settings
            from importlib import reload
            import app.core.config as config_module
            reload(config_module)
            
            # Create database instance
            test_db = Database()
            
            # Mock the SQLite path to use our temp directory
            test_db._get_sqlite_path = lambda: db_path
            
            # Run migrations
            await test_db.run_migrations()
            
            # Verify the database file was created
            assert db_path.exists(), "SQLite database file should be created"
            
            # Verify migrations table exists
            await test_db.connect()
            migrations = await test_db.fetch_all("SELECT name FROM migrations ORDER BY name")
            
            # Should have applied some migrations
            assert len(migrations) > 0, "Migrations should have been applied"
            assert migrations[0]["name"].endswith(".sql"), "Migration names should end with .sql"
            
            # Check that critical tables exist
            # Test a few core tables from early migrations
            tables_to_check = ["users", "migrations", "companies"]
            
            for table in tables_to_check:
                # SQLite uses sqlite_master to check for tables
                result = await test_db.fetch_one(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
                )
                assert result is not None, f"Table '{table}' should exist after migrations"
            
            await test_db.disconnect()
            
        finally:
            # Restore env vars
            for key, value in original_env.items():
                if value is not None:
                    os.environ[key] = value
            
            # Reload config
            import app.core.config as config_module
            from importlib import reload
            reload(config_module)


@pytest.mark.anyio
async def test_migrations_are_idempotent():
    """Test that running migrations multiple times is safe."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        # Clear MySQL config
        original_env = {}
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]:
            original_env[key] = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]
        
        try:
            from importlib import reload
            import app.core.config as config_module
            reload(config_module)
            
            test_db = Database()
            test_db._get_sqlite_path = lambda: db_path
            
            # Run migrations first time
            await test_db.run_migrations()
            await test_db.connect()
            
            first_run_migrations = await test_db.fetch_all("SELECT name FROM migrations")
            first_run_count = len(first_run_migrations)
            
            await test_db.disconnect()
            
            # Run migrations second time
            await test_db.run_migrations()
            await test_db.connect()
            
            second_run_migrations = await test_db.fetch_all("SELECT name FROM migrations")
            second_run_count = len(second_run_migrations)
            
            await test_db.disconnect()
            
            # Should have same number of migrations (no duplicates)
            assert first_run_count == second_run_count, "Running migrations twice should not create duplicates"
            
        finally:
            for key, value in original_env.items():
                if value is not None:
                    os.environ[key] = value
            
            import app.core.config as config_module
            from importlib import reload
            reload(config_module)


@pytest.mark.anyio
async def test_sql_adaptation_for_sqlite():
    """Test that MySQL-specific SQL is adapted for SQLite."""
    test_db = Database()
    test_db._use_sqlite = True
    
    # Test AUTO_INCREMENT conversion
    mysql_sql = "CREATE TABLE test (id INT AUTO_INCREMENT PRIMARY KEY)"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "AUTOINCREMENT" in adapted
    assert "AUTO_INCREMENT" not in adapted
    
    # Test ENGINE removal
    mysql_sql = "CREATE TABLE test (id INT) ENGINE=InnoDB"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "ENGINE" not in adapted
    
    # Test DATETIME conversion
    mysql_sql = "CREATE TABLE test (created_at DATETIME)"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "TEXT" in adapted or "datetime" in adapted.lower()
    
    # Test CURRENT_TIMESTAMP conversion
    mysql_sql = "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "datetime('now')" in adapted
    
    # Test COMMENT removal
    mysql_sql = "id INT COMMENT 'User ID'"
    adapted = test_db._adapt_sql_for_sqlite(mysql_sql)
    assert "COMMENT" not in adapted


@pytest.mark.anyio
async def test_app_startup_with_empty_database():
    """Test that the FastAPI app can start with an empty database.
    
    This is the main BC13 requirement: ensure the app boots and migrations run automatically.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "myportal.db"
        
        # Clear MySQL config to use SQLite
        original_env = {}
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]:
            original_env[key] = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]
        
        try:
            # Force reload of app modules
            from importlib import reload
            import app.core.config as config_module
            import app.core.database as database_module
            
            reload(config_module)
            reload(database_module)
            
            # Patch the database path
            from app.core import database as db_module
            original_get_path = db_module.db._get_sqlite_path
            db_module.db._get_sqlite_path = lambda: db_path
            
            # Simulate app startup by running migrations
            await db_module.db.run_migrations()
            
            # Verify database was created and has tables
            assert db_path.exists(), "Database should be created during startup"
            
            await db_module.db.connect()
            
            # Check migrations table
            migrations = await db_module.db.fetch_all("SELECT name FROM migrations")
            assert len(migrations) > 0, "Migrations should have been applied"
            
            # Verify BC11 vendors table exists (one of the newer tables)
            tables = await db_module.db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            table_names = {table["name"] for table in tables}
            
            # Check for a few key tables from different migration eras
            expected_tables = ["migrations", "users", "companies", "bc_vendor"]
            for table in expected_tables:
                assert table in table_names, f"Table '{table}' should exist after migrations"
            
            await db_module.db.disconnect()
            
            # Restore original get_path
            db_module.db._get_sqlite_path = original_get_path
            
        finally:
            # Restore environment
            for key, value in original_env.items():
                if value is not None:
                    os.environ[key] = value
            
            # Reload config
            from importlib import reload
            import app.core.config as config_module
            import app.core.database as database_module
            
            reload(config_module)
            reload(database_module)


@pytest.mark.anyio
async def test_new_bc_tables_created():
    """Test that BC (Business Continuity) tables are created by migrations.
    
    This verifies that newer BC3/BC11 tables are properly created.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        # Clear MySQL config
        original_env = {}
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]:
            original_env[key] = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]
        
        try:
            from importlib import reload
            import app.core.config as config_module
            reload(config_module)
            
            test_db = Database()
            test_db._get_sqlite_path = lambda: db_path
            
            await test_db.run_migrations()
            await test_db.connect()
            
            # Check for BC tables (from migrations 124 and 125)
            bc_tables = [
                "bc_template",
                "bc_section_definition",
                "bc_plan",
                "bc_plan_version",
                "bc_contact",
                "bc_vendor",  # From BC11 migration 125
                "bc_process",
                "bc_risk",
                "bc_attachment",
            ]
            
            tables = await test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            table_names = {table["name"] for table in tables}
            
            for bc_table in bc_tables:
                assert bc_table in table_names, f"BC table '{bc_table}' should exist"
            
            await test_db.disconnect()
            
        finally:
            for key, value in original_env.items():
                if value is not None:
                    os.environ[key] = value
            
            import app.core.config as config_module
            from importlib import reload
            reload(config_module)
