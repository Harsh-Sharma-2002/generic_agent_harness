"""Read-only SQLite execution tool for the KernelAI MCP server."""

from __future__ import annotations

import os
import re
from sqlite3 import connect
import asyncpg
from typing import Any
from dotenv import load_dotenv

load_dotenv()

async def sql_executor(query: str) -> dict[str,Any]:
    """
    Execute a read-only SQL query against the configured PostgreSQL database.

    Args:
        query: SQL SELECT or WITH query to execute.

    Returns:
        A dictionary containing the returned rows and row count.
    """
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    normalized_query = query.strip().lower()

    if not (
        normalized_query.startswith("select")
        or normalized_query.startswith("with")
    ):
        raise ValueError(
            "SQL executor is read-only. "
            "Only SELECT and WITH queries are allowed."
        )

    connection = await asyncpg.connect(database_url)

    try:
        rows = await connection.fetch(query)
        return {
            "rows": [dict(row) for row in rows],
            "row_count": len(rows)
        }

    finally:
        await connection.close()