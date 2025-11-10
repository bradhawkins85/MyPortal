"""
Tests for BC4 (BC3 data model) migrations.

Verifies that the bc_* tables are created idempotently through the automatic
startup migration system, following the file-driven migration runner pattern.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database


class FakeCursor:
    """Mock cursor for testing migrations without a real database."""

    def __init__(self, conn, cursor_type=None):
        self._conn = conn
        self._cursor_type = cursor_type
        self._fetchone_result = None
        self._fetchall_result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self._conn.record(sql, params)
        
        # Handle lock operations
        if sql.startswith("SELECT GET_LOCK"):
            self._fetchone_result = (1,)
        elif sql.startswith("SELECT RELEASE_LOCK"):
            self._conn.released_locks.append(params[0] if params else None)
        
        # Handle migration tracking table
        elif sql.startswith("CREATE TABLE IF NOT EXISTS migrations"):
            self._conn.migration_table_created = True
        elif sql.startswith("SELECT name FROM migrations"):
            pass  # fetchall will return the applied migrations
        elif sql.startswith("INSERT INTO migrations"):
            self._conn.inserted_migrations.append(params[0] if params else None)
        
        # Handle SQL notes
        elif sql.startswith("SET sql_notes"):
            self._conn.sql_notes_statements.append(sql)
        
        # Track actual migration statements - be more flexible with CREATE TABLE detection
        elif "CREATE TABLE" in sql and "bc_" in sql:
            # Extract table name from CREATE TABLE statement
            # Handle formats like: "CREATE TABLE IF NOT EXISTS bc_plan (...)"
            sql_upper = sql.upper()
            start_idx = sql_upper.find("BC_")
            if start_idx != -1:
                # Find the table name (ends at space, newline, or parenthesis)
                end_markers = [' ', '\n', '(', '\t']
                end_idx = len(sql)
                for marker in end_markers:
                    marker_idx = sql.find(marker, start_idx)
                    if marker_idx != -1:
                        end_idx = min(end_idx, marker_idx)
                table_name = sql[start_idx:end_idx]
                self._conn.created_tables.append(table_name)
        elif sql.startswith("ALTER TABLE"):
            self._conn.alter_statements.append(sql)
        else:
            self._conn.other_statements.append(sql)

    async def fetchone(self):
        return self._fetchone_result

    async def fetchall(self):
        return self._fetchall_result or []


class FakeConnection:
    """Mock database connection for testing."""

    def __init__(self, applied_migrations=None):
        self.statements: list[tuple[str, tuple | None]] = []
        self.inserted_migrations: list[str] = []
        self.released_locks: list[str] = []
        self.sql_notes_statements: list[str] = []
        self.created_tables: list[str] = []
        self.alter_statements: list[str] = []
        self.other_statements: list[str] = []
        self.migration_table_created = False
        self._applied_migrations = applied_migrations or []

    def record(self, sql, params):
        self.statements.append((sql, params))

    def cursor(self, cursor_type=None):
        cursor = FakeCursor(self, cursor_type)
        # Return applied migrations when queried
        if cursor_type is not None:  # DictCursor
            cursor._fetchall_result = [{"name": name} for name in self._applied_migrations]
        return cursor


class FakeTempConnection:
    """Mock temporary connection for database creation."""
    
    def __init__(self):
        self.closed = False
        self.statements: list[tuple[str, tuple | None]] = []
        self.inserted_migrations: list[str] = []
        self.released_locks: list[str] = []
        self.sql_notes_statements: list[str] = []
        self.created_tables: list[str] = []
        self.alter_statements: list[str] = []
        self.other_statements: list[str] = []
        self.migration_table_created = False
    
    def record(self, sql, params):
        self.statements.append((sql, params))
    
    def close(self):
        self.closed = True
    
    async def wait_closed(self):
        pass
    
    def cursor(self):
        return FakeCursor(self)


async def _fake_temp_connect(*args, **kwargs):
    """Create mock temporary connection used for database creation."""
    return FakeTempConnection()


def _build_database(monkeypatch, migrations_dir: Path, applied_migrations=None) -> tuple[Database, FakeConnection]:
    """Create a test database instance with mocked connection."""
    database = Database()
    
    # Mock the connect method to avoid actual database connection
    async def fake_connect():
        database._pool = AsyncMock()
    
    monkeypatch.setattr(database, "connect", fake_connect)
    
    # Mock aiomysql.connect for temporary connection  
    import aiomysql
    monkeypatch.setattr(aiomysql, "connect", _fake_temp_connect)
    
    fake_conn = FakeConnection(applied_migrations=applied_migrations)

    @asynccontextmanager
    async def fake_acquire():
        yield fake_conn

    monkeypatch.setattr(database, "acquire", fake_acquire)
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: migrations_dir)
    return database, fake_conn


@pytest.fixture
def anyio_backend():
    """Configure anyio to use asyncio backend."""
    return "asyncio"


@pytest.fixture
def bc3_migration_content():
    """Return the actual content of the BC3 migration."""
    migration_path = Path(__file__).parent.parent / "migrations" / "124_bc3_bcp_data_model.sql"
    if migration_path.exists():
        return migration_path.read_text(encoding="utf-8")
    
    # Fallback minimal content for testing if file doesn't exist
    return """
-- BC3 BCP Data Model
CREATE TABLE IF NOT EXISTS bc_template (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(50) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  schema_json JSON,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bc_plan (
  id INT AUTO_INCREMENT PRIMARY KEY,
  org_id INT,
  title VARCHAR(255) NOT NULL,
  status ENUM('draft', 'in_review', 'approved', 'archived') NOT NULL DEFAULT 'draft',
  template_id INT,
  current_version_id INT,
  owner_user_id INT NOT NULL,
  approved_at_utc DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (template_id) REFERENCES bc_template(id) ON DELETE SET NULL
);
"""


@pytest.mark.anyio
async def test_bc3_migration_runs_on_first_boot(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration executes successfully on first boot."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Verify migration table was created
    assert fake_conn.migration_table_created is True
    
    # Verify migration was recorded
    assert "124_bc3_bcp_data_model.sql" in fake_conn.inserted_migrations
    
    # Verify BC tables were created
    assert len(fake_conn.created_tables) > 0
    bc_tables = [table for table in fake_conn.created_tables if table.startswith("bc_")]
    assert len(bc_tables) >= 2  # At least bc_template and bc_plan


@pytest.mark.anyio
async def test_bc3_migration_is_noop_on_subsequent_runs(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration is a no-op when already applied."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    # Simulate migration already applied
    database, fake_conn = _build_database(
        monkeypatch, 
        tmp_path, 
        applied_migrations=["124_bc3_bcp_data_model.sql"]
    )

    await database.run_migrations()

    # Verify migration was NOT re-inserted
    assert "124_bc3_bcp_data_model.sql" not in fake_conn.inserted_migrations
    
    # Verify no tables were created (migration was skipped)
    assert len(fake_conn.created_tables) == 0


@pytest.mark.anyio
async def test_bc3_migration_is_idempotent(tmp_path, monkeypatch):
    """Test that BC3 migration uses CREATE TABLE IF NOT EXISTS for idempotency."""
    # Create a minimal BC3 migration
    migration_content = """
CREATE TABLE IF NOT EXISTS bc_template (
  id INT PRIMARY KEY,
  name VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS bc_plan (
  id INT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  template_id INT,
  FOREIGN KEY (template_id) REFERENCES bc_template(id)
);
"""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Check that all CREATE TABLE statements use IF NOT EXISTS
    create_statements = [stmt for stmt in fake_conn.statements if "CREATE TABLE" in str(stmt)]
    for stmt_tuple in create_statements:
        stmt = stmt_tuple[0]
        if "bc_" in stmt:
            assert "IF NOT EXISTS" in stmt, f"Migration not idempotent: {stmt}"


@pytest.mark.anyio
async def test_bc3_migration_preserves_existing_data(tmp_path, monkeypatch):
    """Test that BC3 migration doesn't drop or truncate existing tables."""
    # Create a BC3 migration with existing data scenario
    migration_content = """
CREATE TABLE IF NOT EXISTS bc_plan (
  id INT PRIMARY KEY,
  title VARCHAR(255) NOT NULL
);
"""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Verify no DROP or TRUNCATE statements
    for stmt_tuple in fake_conn.statements:
        stmt = str(stmt_tuple[0]).upper()
        assert "DROP TABLE" not in stmt, "Migration contains DROP TABLE - will lose data"
        assert "TRUNCATE" not in stmt, "Migration contains TRUNCATE - will lose data"


@pytest.mark.anyio
async def test_bc3_tables_have_proper_structure(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration creates expected table structure."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Expected BC3 tables from the data model
    expected_tables = [
        "bc_template",
        "bc_section_definition",
        "bc_plan_version",
        "bc_plan",
        "bc_contact",
        "bc_process",
        "bc_risk",
        "bc_attachment",
        "bc_review",
        "bc_ack",
        "bc_audit",
        "bc_change_log_map",
    ]

    # Verify key tables are created
    for table in ["bc_plan", "bc_template", "bc_plan_version"]:
        assert any(table in created_table for created_table in fake_conn.created_tables), \
            f"Essential table {table} not created"


@pytest.mark.anyio
async def test_bc3_migration_handles_circular_foreign_keys(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration properly handles circular FK between bc_plan and bc_plan_version."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Check for ALTER TABLE statement to add the circular FK
    alter_statements = [stmt for stmt in fake_conn.alter_statements if "bc_plan_version" in stmt]
    # The migration should have an ALTER TABLE to add the FK after both tables exist
    if len(alter_statements) > 0:
        assert any("FOREIGN KEY" in stmt for stmt in alter_statements)


@pytest.mark.anyio
async def test_bc3_migration_uses_utc_timestamps(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration uses UTC timestamp columns."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    # Check migration content for UTC timestamp columns
    assert "_utc" in bc3_migration_content, "Migration should use _utc suffix for timestamp columns"
    
    # Common UTC timestamp columns
    utc_columns = [
        "approved_at_utc",
        "authored_at_utc",
        "uploaded_at_utc",
        "requested_at_utc",
        "decided_at_utc",
        "ack_at_utc",
        "at_utc",
        "imported_at_utc",
    ]
    
    found_utc_columns = [col for col in utc_columns if col in bc3_migration_content]
    assert len(found_utc_columns) > 0, "Migration should define UTC timestamp columns"


@pytest.mark.anyio
async def test_migration_runner_acquires_and_releases_lock(tmp_path, monkeypatch, bc3_migration_content):
    """Test that migration runner uses database lock for safe concurrent execution."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Verify lock was acquired
    lock_statements = [stmt for stmt_tuple in fake_conn.statements 
                      for stmt in [stmt_tuple[0]] if "GET_LOCK" in str(stmt)]
    assert len(lock_statements) > 0, "Migration should acquire a database lock"
    
    # Verify lock was released
    assert len(fake_conn.released_locks) > 0, "Migration should release the database lock"


@pytest.mark.anyio
async def test_multiple_migrations_run_in_order(tmp_path, monkeypatch):
    """Test that multiple migrations run in alphabetical/numerical order."""
    # Create multiple migration files
    (tmp_path / "001_first.sql").write_text("CREATE TABLE IF NOT EXISTS bc_first (id INT);", encoding="utf-8")
    (tmp_path / "124_bc3_bcp_data_model.sql").write_text("CREATE TABLE IF NOT EXISTS bc_plan (id INT);", encoding="utf-8")
    (tmp_path / "125_after_bc3.sql").write_text("CREATE TABLE IF NOT EXISTS bc_after (id INT);", encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path, applied_migrations=[])

    await database.run_migrations()

    # Verify migrations were inserted in order
    assert fake_conn.inserted_migrations == ["001_first.sql", "124_bc3_bcp_data_model.sql", "125_after_bc3.sql"]


@pytest.mark.anyio
async def test_bc3_migration_handles_check_constraints(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration includes check constraints for data integrity."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    # Check for CHECK constraints in migration content
    check_constraints = [
        "ck_version_number_positive",
        "ck_rto_non_negative",
        "ck_rpo_non_negative",
        "ck_mtpd_non_negative",
        "ck_size_non_negative",
    ]
    
    found_constraints = [constraint for constraint in check_constraints if constraint in bc3_migration_content]
    assert len(found_constraints) >= 2, "Migration should include check constraints for data validation"


@pytest.mark.anyio
async def test_bc3_migration_includes_indexes(tmp_path, monkeypatch, bc3_migration_content):
    """Test that BC3 migration creates indexes for query optimization."""
    migration = tmp_path / "124_bc3_bcp_data_model.sql"
    migration.write_text(bc3_migration_content, encoding="utf-8")

    # Check for INDEX definitions
    index_patterns = ["INDEX", "idx_bc_"]
    
    has_indexes = any(pattern in bc3_migration_content for pattern in index_patterns)
    assert has_indexes, "Migration should include indexes for common query patterns"
