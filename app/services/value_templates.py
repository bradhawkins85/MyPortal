from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timezone
from typing import Any

from app.services import dynamic_variables, message_templates, system_variables, conditional_expressions

_TOKEN_PATTERN = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")
_UPPER_TOKEN_SANITISER = re.compile(r"[^A-Z0-9]+")


def build_base_token_map(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return system and context tokens available for template interpolation."""

    ticket: Mapping[str, Any] | None = None
    if isinstance(context, Mapping):
        possible_ticket = context.get("ticket")
        if isinstance(possible_ticket, Mapping):
            ticket = possible_ticket
    tokens = dict(system_variables.get_system_variables(ticket=ticket))
    if context:
        tokens.update(system_variables.build_context_variables(context))
    return tokens


def _collect_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, str):
        # Collect tokens from conditional expressions
        conditionals = conditional_expressions.find_conditionals(value)
        for _, condition, then_value, else_value in conditionals:
            # Extract token references from condition, then, and else clauses
            # These can be bare token names (not wrapped in {{}})
            for part in [condition, then_value, else_value]:
                if part:
                    # Look for wrapped tokens first
                    tokens.update(match.group(1) for match in _TOKEN_PATTERN.finditer(part))
                    
                    # Also look for bare token-like strings (e.g., count:asset:bitdefender)
                    # These are identifiers that contain colons and don't start with quotes
                    part = part.strip()
                    # Skip quoted strings
                    if part and not (part.startswith('"') or part.startswith("'")):
                        # Split on comparison operators to get left and right sides
                        import re
                        comparison_parts = re.split(r'\s*(>=|<=|>|<|==|!=)\s*', part)
                        for token_candidate in comparison_parts:
                            token_candidate = token_candidate.strip()
                            # Add if it looks like a token (contains colon or is a known pattern)
                            if ':' in token_candidate or token_candidate.replace('_', '').replace('-', '').replace('.', '').isalnum():
                                # Exclude numeric literals
                                try:
                                    float(token_candidate)
                                except (ValueError, TypeError):
                                    # Not a number, might be a token
                                    if token_candidate and token_candidate not in ['>', '<', '>=', '<=', '==', '!=']:
                                        tokens.add(token_candidate)
        
        # Collect regular tokens (wrapped in {{}})
        tokens.update(match.group(1) for match in _TOKEN_PATTERN.finditer(value))
        return tokens
    if isinstance(value, Mapping):
        for item in value.values():
            tokens.update(_collect_tokens(item))
        return tokens
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            tokens.update(_collect_tokens(item))
        return tokens
    return tokens


async def build_async_base_token_map(
    context: Mapping[str, Any] | None,
    *,
    tokens: Iterable[str] | None = None,
    include_templates: bool = True,
) -> dict[str, Any]:
    base_tokens = build_base_token_map(context)
    required_tokens: set[str] = set(tokens or [])
    if include_templates:
        for template in message_templates.iter_templates():
            required_tokens.update(_collect_tokens(str(template.get("content") or "")))
    if required_tokens:
        dynamic_values = await dynamic_variables.build_dynamic_token_map(
            required_tokens,
            context,
            base_tokens=base_tokens,
        )
        if dynamic_values:
            base_tokens.update(dynamic_values)
    return base_tokens


def _coerce_template_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (int, float, bool, str)):
        return value
    return value


def _stringify_template_value(value: Any) -> str:
    coerced = _coerce_template_value(value)
    if isinstance(coerced, str):
        return coerced
    if isinstance(coerced, (int, float, bool)):
        return str(coerced)
    if coerced is None or coerced == "":
        return ""
    if isinstance(coerced, Mapping):
        try:
            return json.dumps(coerced)
        except TypeError:
            return str(coerced)
    if isinstance(coerced, Sequence) and not isinstance(coerced, (str, bytes, bytearray)):
        try:
            return json.dumps(coerced)
        except TypeError:
            return str(coerced)
    return str(coerced)


def _resolve_context_value(context: Mapping[str, Any] | None, path: str) -> Any:
    if not context or not path:
        return None
    current: Any = context
    for segment in path.split('.'):
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _uppercase_token(slug: str) -> str:
    cleaned = _UPPER_TOKEN_SANITISER.sub("_", slug.upper())
    return cleaned.strip("_")


def build_template_token_map(
    context: Mapping[str, Any] | None,
    *,
    base_tokens: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if base_tokens is None:
        base_tokens = build_base_token_map(context)
    tokens: dict[str, str] = {}
    for template in message_templates.iter_templates():
        slug = str(template.get("slug") or "").strip()
        if not slug:
            continue
        content = str(template.get("content") or "")
        rendered = render_string(
            content,
            context,
            base_tokens=base_tokens,
            include_templates=False,
        )
        upper_name = _uppercase_token(slug)
        if upper_name:
            tokens[f"TEMPLATE_{upper_name}"] = _stringify_template_value(rendered)
        tokens[f"template.{slug}"] = _stringify_template_value(rendered)
    return tokens


def render_string(
    value: str,
    context: Mapping[str, Any] | None,
    *,
    base_tokens: Mapping[str, Any] | None = None,
    include_templates: bool = True,
) -> Any:
    if base_tokens is None:
        base_tokens = build_base_token_map(context)
    token_map = dict(base_tokens)
    if include_templates:
        token_map.update(build_template_token_map(context, base_tokens=base_tokens))
    
    # First, process conditional expressions
    processed_value = conditional_expressions.process_conditionals(value, token_map)
    
    # Then process any token references that may have been returned by conditionals
    # This handles cases where conditionals return token names like "list:asset:bitdefender"
    def _replace(match: re.Match[str]) -> str:
        token_name = match.group(1)
        resolved = _resolve_context_value(context, token_name)
        if resolved is None:
            resolved = token_map.get(token_name, "")
        return _stringify_template_value(resolved)
    
    # Apply token replacement to the processed value
    final_value = _TOKEN_PATTERN.sub(_replace, processed_value)
    
    # Check if the entire result is a single token (for type coercion)
    stripped = final_value.strip()
    single_match = _TOKEN_PATTERN.fullmatch(stripped)
    if single_match:
        token_name = single_match.group(1)
        resolved = _resolve_context_value(context, token_name)
        if resolved is None:
            resolved = token_map.get(token_name)
        return _coerce_template_value(resolved)
    
    return final_value


async def render_string_async(
    value: str,
    context: Mapping[str, Any] | None,
    *,
    include_templates: bool = True,
) -> Any:
    base_tokens = await build_async_base_token_map(
        context,
        tokens=_collect_tokens(value),
        include_templates=include_templates,
    )
    return render_string(
        value,
        context,
        base_tokens=base_tokens,
        include_templates=include_templates,
    )


def render_value(
    value: Any,
    context: Mapping[str, Any] | None,
    *,
    base_tokens: Mapping[str, Any] | None = None,
    include_templates: bool = True,
) -> Any:
    if base_tokens is None:
        base_tokens = build_base_token_map(context)
    if isinstance(value, str):
        return render_string(
            value,
            context,
            base_tokens=base_tokens,
            include_templates=include_templates,
        )
    if isinstance(value, Mapping):
        return {
            key: render_value(item, context, base_tokens=base_tokens, include_templates=include_templates)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            render_value(item, context, base_tokens=base_tokens, include_templates=include_templates)
            for item in value
        ]
    return value


async def render_value_async(
    value: Any,
    context: Mapping[str, Any] | None,
    *,
    include_templates: bool = True,
) -> Any:
    base_tokens = await build_async_base_token_map(
        context,
        tokens=_collect_tokens(value),
        include_templates=include_templates,
    )
    return render_value(
        value,
        context,
        base_tokens=base_tokens,
        include_templates=include_templates,
    )


def render_payload(value: Any, context: Mapping[str, Any] | None) -> Any:
    """Backwards-compatible helper for rendering arbitrary payloads."""

    return render_value(value, context)
