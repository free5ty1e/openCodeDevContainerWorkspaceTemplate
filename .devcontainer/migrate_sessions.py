#!/usr/bin/env python3
"""
Migrate opencode sessions to use the current project ID.
Handles the case where the container is rebuilt and opencode generates a new project ID.
"""
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.environ.get("HOME", "/home/vscode"), ".local/share/opencode/opencode.db")

def migrate_sessions():
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT directory FROM session")
    directories = [row[0] for row in cursor.fetchall()]

    for directory in directories:
        cursor.execute(
            "SELECT DISTINCT project_id FROM session WHERE directory = ?",
            (directory,)
        )
        project_ids = [row[0] for row in cursor.fetchall()]

        if len(project_ids) <= 1:
            continue

        cursor.execute(
            "SELECT project_id FROM session WHERE directory = ? ORDER BY time_updated DESC LIMIT 1",
            (directory,)
        )
        result = cursor.fetchone()
        if result is None:
            continue

        current_project_id = result[0]

        cursor.execute(
            "UPDATE session SET project_id = ? WHERE directory = ? AND project_id != ?",
            (current_project_id, directory, current_project_id)
        )

        if cursor.rowcount > 0:
            print(f"Migrated {cursor.rowcount} sessions for {directory} to project ID {current_project_id}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate_sessions()
