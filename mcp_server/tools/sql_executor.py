"""Read-only SQLite execution tool for the KernelAI MCP server."""

from __future__ import annotations

from locale import normalize
import os
import sqlite3
from typing import Any

def sql_executor(query: str) -> dict[str,Any]:
    database_path = os.environ.get("DATABASE_PATH")

    if not  database_path:
        raise ValueError("DATABASE_PATH environment variable is not set")

    normalized_query = query.strip.lower()

    if not (
        normalized_query.startswith("select")
        or normalized_query.startswith("with")
        or normalized_query.startswith("pragma")
    ):
        raise ValueError(
            "SQL executor is read-only. "
            "Only SELECT, WITH, and PRAGMA queries are allowed."
        )

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row

        cursor = connection.execute(query)
        rows = cursor.fetchall()

    return {
        "columns": (
            [description[0] for description in cursor.description]
            if cursor.description
            else []
        ),
        "rows": [dict(row) for row in rows],
        "row_count": len(rows),
    }

        