"""Schema discovery and LLM assistance for the visual report designer."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from app.core.database import db

# Keep enough room in an 8K context window for the system instructions, the
# user's request, and the model's answer. Schema identifiers tend to tokenize
# less efficiently than prose, so a character limit substantially below 32K is
# intentional.
AI_SCHEMA_MAX_CHARS = 16_000


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
    context = json.dumps(
        _schema_for_prompt(schema, request_text, current_sql), separators=(",", ":")
    )
    # Put the request before the (potentially large) schema. Besides making the
    # recorded request easy to inspect, this guarantees that a provider which
    # defensively truncates a prompt does not discard the actual task.
    user = f"Requested report or refinement:\n{request_text.strip()}"
    if current_sql.strip():
        user += f"\n\nCurrent query to refine:\n{current_sql.strip()}"
    user += f"\n\nDatabase schema:\n{context}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _schema_for_prompt(
    schema: Mapping[str, Any], request_text: str, current_sql: str = ""
) -> dict[str, Any]:
    """Return a relevant, valid schema document bounded for local LLMs.

    Full MyPortal installations can expose hundreds of tables and overflow the
    common 8K context configured by llama.cpp. Tables whose names or columns
    occur in the request/current SQL are placed first, then remaining tables are
    included alphabetically while the serialized document fits the budget.
    """
    raw_tables = schema.get("tables")
    tables = [dict(table) for table in raw_tables or [] if isinstance(table, Mapping)]
    terms = {
        term.lower()
        for term in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", f"{request_text} {current_sql}"
        )
        if len(term) > 1
    }

    def relevance(table: Mapping[str, Any]) -> tuple[int, str]:
        name = str(table.get("name") or "").lower()
        columns = table.get("columns") or []
        identifiers = {
            name,
            *(
                str(column.get("name") or "").lower()
                for column in columns
                if isinstance(column, Mapping)
            ),
        }
        score = sum(4 if term == name else 1 for term in terms if term in identifiers)
        score += sum(2 for term in terms if term and term in name)
        return (-score, name)

    tables.sort(key=relevance)
    relations = [
        dict(relation)
        for relation in schema.get("relations") or []
        if isinstance(relation, Mapping)
    ]
    selected: list[dict[str, Any]] = []
    for table in tables:
        candidate_names = {str(item.get("name") or "") for item in [*selected, table]}
        candidate_relations = [
            relation
            for relation in relations
            if str(relation.get("from_table") or "") in candidate_names
            and str(relation.get("to_table") or "") in candidate_names
        ]
        candidate = {"tables": [*selected, table], "relations": candidate_relations}
        if len(json.dumps(candidate, separators=(",", ":"))) > AI_SCHEMA_MAX_CHARS:
            continue
        selected.append(table)

    selected_names = {str(table.get("name") or "") for table in selected}
    selected_relations = [
        relation
        for relation in relations
        if str(relation.get("from_table") or "") in selected_names
        and str(relation.get("to_table") or "") in selected_names
    ]
    result: dict[str, Any] = {"tables": selected, "relations": selected_relations}
    omitted = len(tables) - len(selected)
    if omitted:
        result["omitted_table_count"] = omitted
    return result


def configured_ai_model() -> str | None:
    """Return an optional model override for reporting query generation."""
    model = str(os.getenv("REPORT_QUERY_BUILDER_MODEL", "")).strip()
    return model or None


def extract_ai_sql(response: Any) -> tuple[str, str]:
    """Extract SQL and summary from common module response shapes."""
    value = response
    # Module providers do not all return the generated text at the same depth.
    # In particular, chat-style responses use ``message.content`` while the
    # Ollama generate endpoint uses ``response``. Unwrap those envelopes before
    # trying to parse the assistant's JSON.
    for _ in range(5):
        if not isinstance(value, Mapping):
            break
        if value.get("sql"):
            return str(value["sql"]).strip(), str(
                value.get("summary") or "Query generated."
            )
        message = value.get("message")
        if isinstance(message, Mapping) and message.get("content") is not None:
            value = message["content"]
            continue
        next_value = (
            value.get("response")
            or value.get("content")
            or value.get("text")
            or value.get("response_body")
        )
        if next_value is None or next_value is value:
            break
        value = next_value
    text = str(value or "").strip()
    fenced = re.sub(r"^```(?:json|sql)?\s*|\s*```$", "", text, flags=re.I)
    try:
        parsed = json.loads(fenced)
        return str(parsed.get("sql") or "").strip(), str(
            parsed.get("summary") or "Query generated."
        )
    except (json.JSONDecodeError, AttributeError):
        return fenced, "Query generated."
