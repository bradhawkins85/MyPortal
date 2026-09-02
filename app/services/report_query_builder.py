"""Schema discovery and LLM assistance for the visual report designer."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from app.core.database import db


def _derive_table_group(table_name: str) -> str:
    """Best-effort grouping based on the logical feature prefix in a table name."""
    name = str(table_name or "").strip()
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", name) if part]
    if not parts:
        return "Other"
    return parts[0].lower()


async def describe_schema() -> dict[str, Any]:
    """Return user tables, columns, and declared foreign-key relationships."""
    if db.is_sqlite():
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        result: list[dict[str, Any]] = []
        relations: list[dict[str, str]] = []
        for row in tables or []:
            name = str(row["name"])
            # Names originate from sqlite_master, not user input.
            columns = await db.fetch_all(
                f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")'
            )
            foreign_keys = await db.fetch_all(
                f'PRAGMA foreign_key_list("{name.replace(chr(34), chr(34) * 2)}")'
            )
            result.append(
                {
                    "name": name,
                    "feature_group": _derive_table_group(name),
                    "columns": [
                        {"name": str(col["name"]), "type": str(col.get("type") or "")}
                        for col in columns or []
                    ],
                }
            )
            relations.extend(
                {
                    "from_table": name,
                    "from_column": str(fk["from"]),
                    "to_table": str(fk["table"]),
                    "to_column": str(fk["to"]),
                }
                for fk in foreign_keys or []
            )
        return {"tables": result, "relations": relations}

    columns = await db.fetch_all(
        "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    foreign_keys = await db.fetch_all(
        "SELECT TABLE_NAME AS from_table, COLUMN_NAME AS from_column, "
        "REFERENCED_TABLE_NAME AS to_table, REFERENCED_COLUMN_NAME AS to_column "
        "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() "
        "AND REFERENCED_TABLE_NAME IS NOT NULL"
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    for col in columns or []:
        grouped.setdefault(str(col["table_name"]), []).append(
            {"name": str(col["column_name"]), "type": str(col.get("data_type") or "")}
        )
    return {
        "tables": [
            {"name": name, "feature_group": _derive_table_group(name), "columns": cols}
            for name, cols in grouped.items()
        ],
        "relations": [dict(row) for row in foreign_keys or []],
    }


def build_ai_messages(
    schema: Mapping[str, Any], request_text: str, current_sql: str = ""
) -> list[dict[str, str]]:
    """Construct a constrained, schema-grounded SQL authoring conversation."""
    dialect = "SQLite" if db.is_sqlite() else "MySQL"
    system = (
        f"You are MyPortal's read-only report SQL assistant. Generate exactly one {dialect} SELECT query. "
        "Use only the supplied schema. Never invent tables or columns. Never produce INSERT, UPDATE, DELETE, "
        "DDL, comments, or multiple statements. Prefer explicit JOINs using declared relationships and clear aliases. "
        "Return JSON only, with keys sql and summary. The sql value must contain the complete query."
    )
    context = json.dumps(schema, separators=(",", ":"))
    user = f"Database schema:\n{context}\n\nRequested report or refinement:\n{request_text.strip()}"
    if current_sql.strip():
        user += f"\n\nCurrent query to refine:\n{current_sql.strip()}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def configured_ai_model() -> str | None:
    """Return an optional model override for reporting query generation."""
    model = str(os.getenv("REPORT_QUERY_BUILDER_MODEL", "")).strip()
    return model or None


def extract_ai_sql(response: Any) -> tuple[str, str]:
    """Extract SQL and summary from common module response shapes."""
    value = response.get("response") if isinstance(response, Mapping) else response
    if isinstance(value, Mapping):
        value = (
            value.get("response") or value.get("message") or value.get("text") or value
        )
    if isinstance(value, Mapping):
        return str(value.get("sql") or "").strip(), str(
            value.get("summary") or "Query generated."
        )
    text = str(value or "").strip()
    fenced = re.sub(r"^```(?:json|sql)?\s*|\s*```$", "", text, flags=re.I)
    try:
        parsed = json.loads(fenced)
        return str(parsed.get("sql") or "").strip(), str(
            parsed.get("summary") or "Query generated."
        )
    except (json.JSONDecodeError, AttributeError):
        return fenced, "Query generated."
