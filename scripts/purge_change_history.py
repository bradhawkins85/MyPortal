#!/usr/bin/env python3
"""
One-time script to purge all change history records from the application.
This removes:
- All JSON change log files from the changes/ directory
- Markdown change log files (changes.md, change.md)
- All records from the change_log database table
- The change log cache file

Usage: python scripts/purge_change_history.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

try:
    import aiomysql
    HAS_MYSQL = True
except ImportError:
    HAS_MYSQL = False


def log_info(msg: str) -> None:
    """Simple logging function."""
    print(f"[INFO] {msg}")


def log_error(msg: str, error: str = "") -> None:
    """Simple error logging function."""
    print(f"[ERROR] {msg}: {error}", file=sys.stderr)


async def purge_database_records(*, dry_run: bool = False) -> int:
    """Purge all records from the change_log database table."""
    if not HAS_MYSQL:
        log_info("MySQL client not available, skipping database purge")
        log_info("To purge database records, run: TRUNCATE TABLE change_log;")
        return 0
    
    # Get database connection info from environment
    db_host = os.environ.get("DATABASE_HOST", "localhost")
    db_port = int(os.environ.get("DATABASE_PORT", "3306"))
    db_user = os.environ.get("DATABASE_USER", "root")
    db_password = os.environ.get("DATABASE_PASSWORD", "")
    db_name = os.environ.get("DATABASE_NAME", "myportal")
    
    try:
        # Connect to database
        conn = await aiomysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            db=db_name,
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Count records before purge
            await cursor.execute("SELECT COUNT(*) as count FROM change_log")
            result = await cursor.fetchone()
            count = result.get("count", 0) if result else 0
            
            log_info(f"Found {count} records in change_log table")
            
            if count > 0 and not dry_run:
                await cursor.execute("TRUNCATE TABLE change_log")
                await conn.commit()
                log_info(f"Truncated change_log table ({count} records removed)")
        
        conn.close()
        return count
        
    except Exception as exc:
        log_error("Failed to connect to database", error=str(exc))
        log_info("To purge database records manually, run: TRUNCATE TABLE change_log;")
        return 0


def purge_change_files(*, root: Path, dry_run: bool = False) -> tuple[int, int]:
    """Purge JSON change log files and markdown files."""
    changes_dir = root / "changes"
    json_count = 0
    md_count = 0
    
    if changes_dir.exists():
        # Remove all JSON files except .gitkeep
        for json_file in changes_dir.glob("*.json"):
            json_count += 1
            if not dry_run:
                json_file.unlink()
        
        log_info(f"{'Would remove' if dry_run else 'Removed'} {json_count} JSON change log files")
        
        # Remove cache file
        cache_file = changes_dir / ".change-log-cache"
        if cache_file.exists():
            if not dry_run:
                cache_file.unlink()
            log_info(f"{'Would remove' if dry_run else 'Removed'} change log cache file")
    
    # Remove markdown files
    for md_file_name in ["changes.md", "change.md"]:
        md_file = root / md_file_name
        if md_file.exists():
            md_count += 1
            if not dry_run:
                md_file.unlink()
            log_info(f"{'Would remove' if dry_run else 'Removed'} {md_file_name}")
    
    return json_count, md_count


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Purge all change history records from the application"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    args = parser.parse_args()
    
    root = Path(__file__).resolve().parent.parent
    
    log_info("=" * 60)
    log_info("CHANGE HISTORY PURGE")
    if args.dry_run:
        log_info("DRY RUN MODE - No changes will be made")
    log_info("=" * 60)
    
    # Purge file system records
    json_count, md_count = purge_change_files(root=root, dry_run=args.dry_run)
    
    # Purge database records
    db_count = await purge_database_records(dry_run=args.dry_run)
    
    # Summary
    log_info("=" * 60)
    log_info("PURGE COMPLETE")
    log_info(f"  JSON files: {json_count}")
    log_info(f"  Markdown files: {md_count}")
    log_info(f"  Database records: {db_count}")
    log_info(f"  Total: {json_count + md_count + db_count}")
    if args.dry_run:
        log_info("  (DRY RUN - no changes were made)")
    log_info("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
