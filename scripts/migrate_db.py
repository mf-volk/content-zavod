#!/usr/bin/env python3
"""
Database Migration Script for Content Zavod Bot

Safely migrates old database schema to new version.
Run this BEFORE deploying new code.

Usage:
    python scripts/migrate_db.py [path_to_db]

Example:
    python scripts/migrate_db.py content_zavod.db
    python scripts/migrate_db.py /var/bot/content_zavod.db
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
    """Run all migrations."""
    print(f"\n[*] Starting migration for: {db_path}\n")

    # Create backup first
    backup_path = backup_database(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Enable WAL mode for better concurrency
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")

    existing_tables = get_existing_tables(cursor)
    migrations_applied = []

    try:
        # ============================================================
        # USERS TABLE MIGRATIONS
        # ============================================================
        if "users" in existing_tables:
            user_cols = get_existing_columns(cursor, "users")

            if "subscription_type" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN subscription_type VARCHAR DEFAULT 'trial'")
                migrations_applied.append("users.subscription_type")

            if "subscription_end_at" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN subscription_end_at TIMESTAMP")
                migrations_applied.append("users.subscription_end_at")

            if "custom_openai_key" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN custom_openai_key VARCHAR")
                migrations_applied.append("users.custom_openai_key")

            if "daily_usage_count" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN daily_usage_count INTEGER DEFAULT 0")
                migrations_applied.append("users.daily_usage_count")

            if "last_usage_date" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN last_usage_date TIMESTAMP")
                migrations_applied.append("users.last_usage_date")

            if "daily_image_count" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN daily_image_count INTEGER DEFAULT 0")
                migrations_applied.append("users.daily_image_count")

            if "last_subscription_notified_at" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN last_subscription_notified_at TIMESTAMP")
                migrations_applied.append("users.last_subscription_notified_at")

            if "subscription_tier" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN subscription_tier VARCHAR(10) DEFAULT 'regular'")
                migrations_applied.append("users.subscription_tier")

            if "paused_regular_end_at" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN paused_regular_end_at TIMESTAMP")
                migrations_applied.append("users.paused_regular_end_at")

        # ============================================================
        # MANAGED_CHANNELS TABLE MIGRATIONS
        # ============================================================
        if "managed_channels" in existing_tables:
            channel_cols = get_existing_columns(cursor, "managed_channels")

            if "idea_topic" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN idea_topic VARCHAR(255)")
                migrations_applied.append("managed_channels.idea_topic")

            if "idea_source_type" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN idea_source_type VARCHAR(50) DEFAULT 'recent'")
                migrations_applied.append("managed_channels.idea_source_type")

            if "next_top_rank" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN next_top_rank INTEGER DEFAULT 0")
                migrations_applied.append("managed_channels.next_top_rank")

            if "used_random_donor_ids" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN used_random_donor_ids TEXT DEFAULT ''")
                migrations_applied.append("managed_channels.used_random_donor_ids")

            if "default_post_text" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN default_post_text TEXT")
                migrations_applied.append("managed_channels.default_post_text")

            if "default_post_text_position" not in channel_cols:
                cursor.execute("ALTER TABLE managed_channels ADD COLUMN default_post_text_position VARCHAR(10) DEFAULT 'end'")
                migrations_applied.append("managed_channels.default_post_text_position")

        # ============================================================
        # DRAFTS TABLE MIGRATIONS
        # ============================================================
        if "drafts" in existing_tables:
            draft_cols = get_existing_columns(cursor, "drafts")

            if "media_position" not in draft_cols:
                cursor.execute("ALTER TABLE drafts ADD COLUMN media_position VARCHAR(20) DEFAULT 'top'")
                migrations_applied.append("drafts.media_position")

            if "last_image_prompt" not in draft_cols:
                cursor.execute("ALTER TABLE drafts ADD COLUMN last_image_prompt TEXT")
                migrations_applied.append("drafts.last_image_prompt")

            if "temp_image_id" not in draft_cols:
                cursor.execute("ALTER TABLE drafts ADD COLUMN temp_image_id VARCHAR(255)")
                migrations_applied.append("drafts.temp_image_id")

        # ============================================================
        # SPACE_MATERIALS TABLE MIGRATIONS
        # ============================================================
        if "space_materials" in existing_tables:
            material_cols = get_existing_columns(cursor, "space_materials")

            if "is_selected" not in material_cols:
                cursor.execute("ALTER TABLE space_materials ADD COLUMN is_selected BOOLEAN DEFAULT 1")
                migrations_applied.append("space_materials.is_selected")

        # ============================================================
        # NEW TABLES (create if not exist)
        # ============================================================

        # Access Keys table
        if "access_keys" not in existing_tables:
            cursor.execute("""
                CREATE TABLE access_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code VARCHAR(50) NOT NULL UNIQUE,
                    duration_days INTEGER NOT NULL,
                    is_used BOOLEAN DEFAULT 0,
                    activated_at TIMESTAMP,
                    activated_by_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (activated_by_id) REFERENCES users(id)
                )
            """)
            migrations_applied.append("CREATE TABLE access_keys")

        # Channel Stats table
        if "channel_stats" not in existing_tables:
            cursor.execute("""
                CREATE TABLE channel_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    date DATETIME NOT NULL,
                    subscribers_count INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES managed_channels(id)
                )
            """)
            migrations_applied.append("CREATE TABLE channel_stats")

        # Content Plans table
        if "content_plans" not in existing_tables:
            cursor.execute("""
                CREATE TABLE content_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    week_start DATETIME NOT NULL,
                    status VARCHAR(9) NOT NULL DEFAULT 'draft',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES managed_channels(id)
                )
            """)
            migrations_applied.append("CREATE TABLE content_plans")

        # Content Plan Slots table
        if "content_plan_slots" not in existing_tables:
            cursor.execute("""
                CREATE TABLE content_plan_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    day_of_week INTEGER NOT NULL,
                    time VARCHAR(5) NOT NULL,
                    topic VARCHAR(500) NOT NULL,
                    description TEXT,
                    draft_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (plan_id) REFERENCES content_plans(id),
                    FOREIGN KEY (draft_id) REFERENCES drafts(id)
                )
            """)
            migrations_applied.append("CREATE TABLE content_plan_slots")

        # Spaces table
        if "spaces" not in existing_tables:
            cursor.execute("""
                CREATE TABLE spaces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    status VARCHAR(10) NOT NULL DEFAULT 'collecting',
                    summary TEXT,
                    generated_ideas TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (channel_id) REFERENCES managed_channels(id)
                )
            """)
            migrations_applied.append("CREATE TABLE spaces")

        # Space Materials table
        if "space_materials" not in existing_tables:
            cursor.execute("""
                CREATE TABLE space_materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    space_id INTEGER NOT NULL,
                    material_type VARCHAR(14) NOT NULL,
                    file_id VARCHAR(255),
                    file_name VARCHAR(255),
                    content TEXT,
                    processed_text TEXT,
                    source_url VARCHAR(1024),
                    language VARCHAR(10) NOT NULL DEFAULT 'ru',
                    is_processed BOOLEAN NOT NULL DEFAULT 0,
                    is_selected BOOLEAN DEFAULT 1,
                    error_message TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (space_id) REFERENCES spaces(id)
                )
            """)
            migrations_applied.append("CREATE TABLE space_materials")

        # ============================================================
        # DATA MIGRATIONS (fix existing data)
        # ============================================================

        # Set default subscription for users without one
        cursor.execute("""
            UPDATE users
            SET subscription_type = 'trial'
            WHERE subscription_type IS NULL
        """)
        if cursor.rowcount > 0:
            migrations_applied.append(f"Updated {cursor.rowcount} users with default subscription")

        # Commit all changes
        conn.commit()

        print("\n" + "="*50)
        if migrations_applied:
            print("[OK] MIGRATIONS APPLIED:")
            for m in migrations_applied:
                print(f"   - {m}")
        else:
            print("[OK] Database is already up to date!")
        print("="*50)

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
        # Default path
        db_path = "content_zavod.db"
    else:
        db_path = sys.argv[1]

    if not Path(db_path).exists():
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    migrate(db_path)
