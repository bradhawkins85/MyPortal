# BC4 Migration Compatibility Notes

## Overview

The BC3/BC4 data model migrations (migration 124_bc3_bcp_data_model.sql) create 12 tables for the Business Continuity Planning system. This document outlines the MySQL-specific features used and considerations for SQLite compatibility.

## Current Implementation

The migration is designed for **MySQL/MariaDB** and uses the existing file-driven migration runner in `app/core/database.py`. The migration runs automatically on application startup.

## MySQL-Specific Features Used

The following MySQL-specific features are used in the migration:

### 1. Storage Engine and Character Set
```sql
ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```
- **MySQL**: Specifies InnoDB storage engine with UTF-8 support
- **SQLite**: These clauses are ignored (SQLite uses a single storage engine)

### 2. AUTO_INCREMENT
```sql
id INT AUTO_INCREMENT PRIMARY KEY
```
- **MySQL**: Automatically generates sequential IDs
- **SQLite**: Use `INTEGER PRIMARY KEY AUTOINCREMENT` instead
- **Note**: SQLite's `INTEGER PRIMARY KEY` automatically acts as autoincrement

### 3. ENUM Types
```sql
status ENUM('draft', 'in_review', 'approved', 'archived')
```
- **MySQL**: Native ENUM type
- **SQLite**: No native ENUM support; use VARCHAR with CHECK constraint:
  ```sql
  status VARCHAR(20) NOT NULL DEFAULT 'draft' 
  CHECK (status IN ('draft', 'in_review', 'approved', 'archived'))
  ```

### 4. ON UPDATE CURRENT_TIMESTAMP
```sql
updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
```
- **MySQL**: Automatically updates timestamp on row modification
- **SQLite**: Not supported; requires application-level handling or triggers

### 5. JSON Column Type
```sql
schema_json JSON COMMENT 'Section and field definitions as JSON'
```
- **MySQL**: Native JSON type with validation and indexing
- **SQLite**: Use TEXT type and validate JSON in application code

### 6. COMMENT Clauses
```sql
org_id INT COMMENT 'For multi-tenant support'
```
- **MySQL**: Inline column comments for documentation
- **SQLite**: Comments not supported; use SQL comments (`--`) instead

### 7. Index Creation Syntax
```sql
INDEX idx_bc_template_default (is_default)
```
- **MySQL**: Inline index definitions in CREATE TABLE
- **SQLite**: Supports inline indexes but CREATE INDEX is more portable

### 8. Foreign Key Actions
```sql
FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE
```
- **MySQL**: Fully supported
- **SQLite**: Supported but must enable: `PRAGMA foreign_keys = ON`

## Idempotency Guarantees

The migration is **idempotent** and safe to run multiple times:

✅ **CREATE TABLE IF NOT EXISTS** - All tables use this clause
✅ **No DROP or TRUNCATE** - Existing data is preserved
✅ **No destructive ALTER** - Only adds constraints after table creation

## Migration Testing

Comprehensive tests verify:
- ✅ First boot execution (creates all 12 BC tables)
- ✅ No-op on subsequent runs (skips already-applied migrations)
- ✅ Data preservation (no data loss on re-runs)
- ✅ Idempotency (CREATE IF NOT EXISTS pattern)
- ✅ Lock acquisition for concurrent safety
- ✅ Proper migration ordering
- ✅ UTC timestamp usage
- ✅ Check constraints for data integrity
- ✅ Index creation for query optimization

See `tests/test_bc4_migrations.py` for full test suite.

## SQLite Compatibility Path

### Option 1: Maintain MySQL-Only (Current)
The application currently uses `aiomysql` exclusively. SQLite is not supported in production.

**Advantages:**
- No changes needed
- Full feature set available
- Simpler maintenance

**Disadvantages:**
- Cannot use SQLite for development/testing
- Less portable across environments

### Option 2: Create SQLite-Compatible Version
Create a parallel migration or conditional logic to support SQLite.

**Required Changes:**
1. Replace `AUTO_INCREMENT` with `AUTOINCREMENT`
2. Replace `ENUM` with `VARCHAR` + `CHECK` constraints
3. Remove `ON UPDATE CURRENT_TIMESTAMP` (handle in application)
4. Replace `JSON` type with `TEXT`
5. Remove `ENGINE`, `CHARSET`, `COLLATE` clauses
6. Remove inline `COMMENT` clauses
7. Enable `PRAGMA foreign_keys = ON` for SQLite

**Example SQLite-Compatible Syntax:**
```sql
-- BC Plan (SQLite version)
CREATE TABLE IF NOT EXISTS bc_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER,
  title VARCHAR(255) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  template_id INTEGER,
  current_version_id INTEGER,
  owner_user_id INTEGER NOT NULL,
  approved_at_utc TEXT, -- ISO8601 format
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (template_id) REFERENCES bc_template(id) ON DELETE SET NULL,
  FOREIGN KEY (current_version_id) REFERENCES bc_plan_version(id) ON DELETE SET NULL,
  CHECK (status IN ('draft', 'in_review', 'approved', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_bc_plan_org_status ON bc_plan(org_id, status);
CREATE INDEX IF NOT EXISTS idx_bc_plan_status_updated ON bc_plan(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_bc_plan_template ON bc_plan(template_id);
CREATE INDEX IF NOT EXISTS idx_bc_plan_owner ON bc_plan(owner_user_id);
```

### Option 3: Database Abstraction Layer
Implement runtime detection and conditional SQL generation.

**Required Changes:**
1. Detect database type at runtime
2. Generate appropriate SQL for each database
3. Maintain separate migration versions
4. Add SQLite support to `app/core/database.py`

## Recommendations

### For Current Implementation (MySQL-Only)
1. ✅ Document MySQL requirement in README
2. ✅ Maintain comprehensive test coverage
3. ✅ Use idempotent migrations (already done)
4. ✅ Preserve existing data (already done)

### For Future SQLite Support
1. Create database abstraction layer in `app/core/database.py`
2. Add database type detection
3. Create SQLite-specific migration variants
4. Update tests to run against both databases
5. Add `aiosqlite` to dependencies

## Migration Runner Behavior

The file-driven migration runner (`app/core/database.py`):

1. **Acquires database lock** - Prevents concurrent migrations
2. **Creates migration tracking table** - Tracks applied migrations
3. **Reads migration files** - From `migrations/` directory in order
4. **Splits SQL statements** - Handles semicolons in strings correctly
5. **Executes each statement** - One at a time in transaction
6. **Records migration** - Marks migration as applied
7. **Releases lock** - Allows other instances to proceed

## Data Model Summary

The BC3/BC4 tables created:

1. **bc_template** - Template definitions
2. **bc_section_definition** - Optional section definitions
3. **bc_plan_version** - Version history
4. **bc_plan** - Main plans table (circular FK with bc_plan_version)
5. **bc_contact** - Emergency contacts
6. **bc_process** - Critical processes with RTO/RPO/MTPD
7. **bc_risk** - Risk assessments
8. **bc_attachment** - File metadata
9. **bc_review** - Review/approval workflow
10. **bc_ack** - User acknowledgments
11. **bc_audit** - Audit trail
12. **bc_change_log_map** - Change log integration

## Performance Considerations

### Indexes Created
- **23 indexes** across all tables for query optimization
- Focus on foreign keys, status columns, and common filters
- UTC timestamp columns for time-based queries

### Constraints
- **Foreign keys** with CASCADE deletes for data integrity
- **Check constraints** for positive numbers (RTO, RPO, MTPD, file sizes)
- **UNIQUE constraints** in permission tables

## Troubleshooting

### Common Issues

**Migration fails with lock timeout:**
```
Could not obtain database migration lock
```
**Solution:** Another instance is running migrations. Wait or increase `MIGRATION_LOCK_TIMEOUT`.

**Migration creates duplicate tables:**
```
Table 'bc_plan' already exists
```
**Solution:** This shouldn't happen due to `CREATE TABLE IF NOT EXISTS`. Check migration tracking table.

**Circular foreign key error:**
```
Cannot add foreign key constraint
```
**Solution:** The migration creates bc_plan_version first, then bc_plan, then adds the circular FK via ALTER TABLE.

## Related Documentation

- **BC3 Data Model**: `docs/bc3_data_model.md`
- **BCP Template API**: `docs/bcp-template-api.md`
- **Migration Tests**: `tests/test_bc4_migrations.py`
- **BC3 Implementation**: `BC3_IMPLEMENTATION_SUMMARY.md`
- **Models**: `app/models/bc_models.py`
- **Schemas**: `app/schemas/bc3_models.py`

## Version History

- **v1.0** - Initial BC3 data model (migration 124)
- **v1.1** - BC4 migration testing and documentation (this document)
