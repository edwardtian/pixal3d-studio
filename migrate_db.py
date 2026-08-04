#!/usr/bin/env python3
"""
Migrate data from the old database (without rating/presets tables) to a new database
with the current schema. Handles the case where columns/tables are missing.

Usage:
    python migrate_db.py [old_db_path] [new_db_path]

Defaults:
    old_db_path = data/db/pixal3d.db
    new_db_path = data/db/pixal3d_new.db
"""

import os
import sys
import shutil
import sqlite3
from datetime import datetime, timezone

DEFAULT_OLD = "data/db/pixal3d.db"
DEFAULT_NEW = "data/db/pixal3d_new.db"


def table_exists(cursor, table_name):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None


def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def get_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def migrate(old_path, new_path):
    if not os.path.exists(old_path):
        print(f"ERROR: Old database not found: {old_path}")
        sys.exit(1)

    print(f"Migrating: {old_path} -> {new_path}")

    # Copy the old DB to the new path so we preserve all data
    shutil.copy2(old_path, new_path)

    conn = sqlite3.connect(new_path)
    cursor = conn.cursor()

    # 1. Add 'rating' column to tasks table if missing
    if table_exists(cursor, "tasks"):
        if not column_exists(cursor, "tasks", "rating"):
            print("  Adding 'rating' column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN rating INTEGER DEFAULT 0")
            print("  Done.")
        else:
            print("  'rating' column already exists in tasks table.")

        # Ensure all NULL ratings are 0
        cursor.execute("UPDATE tasks SET rating = 0 WHERE rating IS NULL")
    else:
        print("  WARNING: 'tasks' table not found in old database.")

    # 2. Create 'presets' table if missing
    if not table_exists(cursor, "presets"):
        print("  Creating 'presets' table...")
        cursor.execute("""
            CREATE TABLE presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name VARCHAR(128) NOT NULL,
                parameters JSON DEFAULT '{}',
                is_default BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX ix_presets_id ON presets (id)")
        print("  Done.")
    else:
        print("  'presets' table already exists.")

    # 3. Verify users table has all expected columns
    if table_exists(cursor, "users"):
        user_cols = get_columns(cursor, "users")
        print(f"  Users table columns: {user_cols}")
    else:
        print("  WARNING: 'users' table not found.")

    # 3b. Add new sub-task / GPU columns to tasks table
    if table_exists(cursor, "tasks"):
        new_task_cols = {
            "subtask_index": "INTEGER DEFAULT 0",
            "subtask_total": "INTEGER DEFAULT 6",
            "subtask_name": "VARCHAR(128) DEFAULT ''",
            "subtask_step": "INTEGER DEFAULT 0",
            "subtask_total_steps": "INTEGER DEFAULT 0",
            "overall_progress": "INTEGER DEFAULT 0",
            "assigned_gpu": "INTEGER",
        }
        for col, typedef in new_task_cols.items():
            if not column_exists(cursor, "tasks", col):
                print(f"  Adding '{col}' column to tasks table...")
                cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}")
        print("  Task sub-task columns OK.")

    # 3c. Create system_config table if missing
    if not table_exists(cursor, "system_config"):
        print("  Creating 'system_config' table...")
        cursor.execute("""
            CREATE TABLE system_config (
                key VARCHAR(64) PRIMARY KEY,
                value TEXT DEFAULT ''
            )
        """)
        print("  Done.")
    else:
        print("  'system_config' table already exists.")

    # 4. Print summary
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks")
    task_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM presets")
    preset_count = cursor.fetchone()[0]

    conn.commit()
    conn.close()

    print(f"\nMigration complete!")
    print(f"  Users:   {user_count}")
    print(f"  Tasks:   {task_count}")
    print(f"  Presets: {preset_count}")
    print(f"\nNew database: {new_path}")
    print(f"To use it, replace the old database:")
    print(f"  mv {old_path} {old_path}.bak")
    print(f"  mv {new_path} {old_path}")


if __name__ == "__main__":
    old = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OLD
    new = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_NEW
    migrate(old, new)
