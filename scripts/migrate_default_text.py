#!/usr/bin/env python3
"""
Migration: Add default_post_text fields to managed_channels table.

Adds:
- default_post_text (TEXT) - text to insert in every post
- default_post_text_position (VARCHAR(10)) - "start" or "end"

Usage:
    python scripts/migrate_default_text.py [path_to_db]
"""

import sqlite3
import sys
import shutil
from datetime import datetime
from pathlib import Path


def backup_database(db_path: str) -> str:
    """Create backup of database before migration."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return backup_path


def get_existing_columns(cursor, table_name: str) -> set:
    """Get set of existing column names in table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def get_existing_tables(cursor) -> set:
    """Get set of existing table names."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def migrate(db_path: str):
    """Run migration."""
    print(f"\n[*] Starting migration (default_post_text) for: {db_path}\n")

    backup_path = backup_database(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")

    existing_tables = get_existing_tables(cursor)
    migrations_applied = []

    try:
        if "managed_channels" in existing_tables:
            channel_cols = get_existing_columns(cursor, "managed_channels")

            if "default_post_text" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN default_post_text TEXT")
                migrations_applied.append("managed_channels.default_post_text")

            if "default_post_text_position" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN default_post_text_position VARCHAR(10) DEFAULT 'end'")
                migrations_applied.append("managed_channels.default_post_text_position")

        conn.commit()

        print("\n" + "=" * 50)
        if migrations_applied:
            print("[OK] MIGRATIONS APPLIED:")
            for m in migrations_applied:
                print(f"   - {m}")
        else:
            print("[OK] Database is already up to date!")
        print("=" * 50)

        print(f"\n[BACKUP] Saved at: {backup_path}")
        print("[DONE] Migration completed successfully!\n")

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed: {e}")
        print(f"[RESTORE] From backup: {backup_path}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        db_path = "content_zavod.db"
    else:
        db_path = sys.argv[1]

    if not Path(db_path).exists():
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    migrate(db_path)
