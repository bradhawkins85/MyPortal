from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.services import (
    dynamic_variables,
    message_templates,
    system_variables,
    conditional_expressions,
)

_TOKEN_PATTERN = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")
_VAR_METHOD_PATTERN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\(([^()]*)\)")
_UPPER_TOKEN_SANITISER = re.compile(r"[^A-Z0-9]+")
_MAX_VAR_TOKEN_LENGTH = 2048
_MAX_VAR_FORMAT_LENGTH = 512


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


def _collect_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str | None) -> None:
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)

    def _walk(current: Any) -> None:
        if isinstance(current, str):
            # Collect tokens from conditional expressions
            conditionals = conditional_expressions.find_conditionals(current)
            for _, condition, then_value, else_value in conditionals:
                # Extract token references from condition, then, and else clauses
                # These can be bare token names (not wrapped in {{}})
                for part in [condition, then_value, else_value]:
                    if part:
                        # Look for wrapped tokens first
                        for match in _TOKEN_PATTERN.finditer(part):
                            _add(match.group(1))

                        # Also look for bare token-like strings (e.g., count:asset:bitdefender)
                        # These are identifiers that contain colons and don't start with quotes
                        trimmed = part.strip()
                        # Skip quoted strings
                        if trimmed and not (
                            trimmed.startswith('"') or trimmed.startswith("'")
                        ):
                            # Split on comparison operators to get left and right sides
                            comparison_parts = re.split(
                                r"\s*(>=|<=|>|<|==|!=)\s*", trimmed
                            )
                            for token_candidate in comparison_parts:
                                candidate = token_candidate.strip()
                                # Add if it looks like a token (contains colon or is a known pattern)
                                if (
                                    ":" in candidate
                                    or candidate.replace("_", "")
                                    .replace("-", "")
                                    .replace(".", "")
                                    .isalnum()
                                ):
                                    # Exclude numeric literals
                                    try:
                                        float(candidate)
                                    except (ValueError, TypeError):
                                        # Not a number, might be a token
                                        if candidate and candidate not in [
                                            ">",
                                            "<",
                                            ">=",
                                            "<=",
                                            "==",
                                            "!=",
                                        ]:
                                            _add(candidate)

            # Collect regular tokens (wrapped in {{}})
            for match in _TOKEN_PATTERN.finditer(current):
                _add(match.group(1))
            return
        if isinstance(current, Mapping):
            for item in current.values():
                _walk(item)
            return
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            for item in current:
                _walk(item)
            return

    _walk(value)
    return tokens


async def build_async_base_token_map(
    context: Mapping[str, Any] | None,
    *,
    tokens: Iterable[str] | None = None,
    include_templates: bool = True,
) -> dict[str, Any]:
    base_tokens = build_base_token_map(context)
    required_tokens: list[str] = []
    seen: set[str] = set()

    def _add(iterable: Iterable[str] | None) -> None:
        if not iterable:
            return
        for token in iterable:
            if token and token not in seen:
                seen.add(token)
                required_tokens.append(token)

    _add(tokens)
    if include_templates:
        for template in message_templates.iter_templates():
            _add(_collect_tokens(str(template.get("content") or "")))
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
    if isinstance(coerced, Sequence) and not isinstance(
        coerced, (str, bytes, bytearray)
    ):
        if all(isinstance(item, str) for item in coerced):
            return ", ".join(item for item in coerced if item)
        try:
            return json.dumps(coerced)
        except TypeError:
            return str(coerced)
    return str(coerced)


# Field aliases: when a key is not found in a Mapping context, try the aliased key instead.
# This allows templates to use short names (e.g. ``ticket.number``) even when the underlying
# data only carries the longer canonical name (e.g. ``ticket_number``).
_FIELD_ALIASES: dict[str, str] = {
    "number": "ticket_number",
}


def _project_sequence_field(value: Sequence[Any], field: str) -> list[str] | None:
    projected: list[str] = []
    for item in value:
        item_value: Any = None
        if isinstance(item, Mapping):
            item_value = item.get(field)
        elif not isinstance(item, (str, bytes, bytearray)):
            item_value = getattr(item, field, None)
        if item_value is None:
            continue
        rendered = str(item_value).strip()
        if rendered:
            projected.append(rendered)
    if not projected:
        return None
    return projected


def _resolve_context_value(context: Mapping[str, Any] | None, path: str) -> Any:
    if not context or not path:
        return None
    current: Any = context
    for segment in path.split("."):
        if isinstance(current, Mapping):
            value = current.get(segment)
            if value is None and segment not in current and segment in _FIELD_ALIASES:
                value = current.get(_FIELD_ALIASES[segment])
            current = value
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                current = _project_sequence_field(current, segment)
                if current is None:
                    return None
                continue
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


def _add_months(value: date | datetime, months: int) -> date | datetime:
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _parse_var_method_kwargs(raw: str) -> dict[str, int]:
    kwargs: dict[str, int] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError("variable date adjustment arguments must use name=value")
        key, value = part.split("=", 1)
        key = key.strip()
        if key not in {"days", "weeks", "months", "years"}:
            raise ValueError(f"unsupported variable date adjustment: {key}")
        kwargs[key] = int(value.strip())
    return kwargs


def _apply_var_date_adjustment(value: Any, name: str, raw_args: str = "") -> Any:
    if not isinstance(value, (date, datetime)):
        return value
    if name in {"previous", "last"}:
        kwargs = _parse_var_method_kwargs(raw_args)
        kwargs = {key: -amount for key, amount in kwargs.items()}
    elif name in {"next", "add"}:
        kwargs = _parse_var_method_kwargs(raw_args)
    else:
        return value
    years = kwargs.pop("years", 0)
    months = kwargs.pop("months", 0) + (years * 12)
    adjusted = _add_months(value, months) if months else value
    delta_kwargs = {key: amount for key, amount in kwargs.items() if amount}
    if delta_kwargs:
        adjusted = adjusted + timedelta(**delta_kwargs)
    return adjusted


def _resolve_vars_value(context: Mapping[str, Any] | None, expression: str) -> Any:
    expr = expression.strip()
    methods: list[tuple[str, str]] = []
    while True:
        match = _VAR_METHOD_PATTERN.search(expr)
        if not match:
            break
        methods.append((match.group(1), match.group(2)))
        expr = expr[: match.start()] + expr[match.end() :]
    parts = [part for part in expr.split(".") if part]
    value: Any = None
    if parts[:3] == ["vars", "now", "utc"]:
        value = datetime.now(timezone.utc)
        parts = parts[3:]
    elif parts[:3] == ["vars", "now", "local"]:
        value = datetime.now().astimezone()
        parts = parts[3:]
    elif parts[:2] == ["vars", "now"]:
        value = datetime.now().astimezone()
        parts = parts[2:]
    elif parts and parts[0] == "vars":
        value = _resolve_context_value(context, ".".join(parts[1:]))
        parts = []
    for part in parts:
        if part == "date" and isinstance(value, datetime):
            value = value.date()
        elif part == "time" and isinstance(value, datetime):
            value = value.timetz()
        elif part == "year" and isinstance(value, (date, datetime)):
            value = value.year
        elif part == "month" and isinstance(value, (date, datetime)):
            value = value.month
        elif part == "day" and isinstance(value, (date, datetime)):
            value = value.day
        elif part in {"last_month", "previous_month"}:
            value = (
                _add_months(value, -1) if isinstance(value, (date, datetime)) else value
            )
        elif part == "next_month":
            value = (
                _add_months(value, 1) if isinstance(value, (date, datetime)) else value
            )
        else:
            value = _resolve_context_value({"value": value}, f"value.{part}")
    for name, args in methods:
        value = _apply_var_date_adjustment(value, name, args)
    return value


def _python_date_format(pattern: str) -> str:
    replacements = (
        ("yyyy", "%Y"),
        ("MMMM", "%B"),
        ("MMM", "%b"),
        ("MM", "%m"),
        ("dd", "%d"),
        ("EEEE", "%A"),
        ("EEE", "%a"),
        ("HH", "%H"),
        ("mm", "%M"),
        ("ss", "%S"),
    )
    rendered = pattern
    for source, target in replacements:
        rendered = rendered.replace(source, target)
    return rendered


def _try_parse_var_format(text: str, start: int) -> tuple[int, str | None]:
    if not text.startswith(".format(", start):
        return start, None

    cursor = start + len(".format(")
    text_length = len(text)
    format_limit = min(text_length, start + _MAX_VAR_FORMAT_LENGTH)
    while cursor < format_limit and text[cursor].isspace():
        cursor += 1
    if cursor >= format_limit or text[cursor] not in {'"', "'"}:
        return start, None

    quote = text[cursor]
    pattern_start = cursor + 1
    pattern_end = text.find(quote, pattern_start, format_limit)
    if pattern_end < 0:
        return start, None

    cursor = pattern_end + 1
    while cursor < format_limit and text[cursor].isspace():
        cursor += 1
    if cursor >= format_limit or text[cursor] != ")":
        return start, None

    return cursor + 1, text[pattern_start:pattern_end]


def _iter_var_token_matches(text: str) -> list[tuple[int, int, str, str | None]]:
    matches: list[tuple[int, int, str, str | None]] = []
    search_from = 0
    text_length = len(text)

    while search_from < text_length:
        start = text.find("${", search_from)
        if start < 0:
            break

        close_limit = min(
            text_length,
            start + _MAX_VAR_TOKEN_LENGTH + 2,  # +2 accounts for the `${` opener
        )
        close_index = text.find("}", start + 2, close_limit)
        if close_index < 0:
            search_from = start + 2
            continue

        expression = text[start + 2:close_index].strip()
        if not expression or "{" in expression or "}" in expression:
            search_from = start + 2
            continue

        end, format_pattern = _try_parse_var_format(text, close_index + 1)
        matches.append((start, end, expression, format_pattern))
        search_from = end

    return matches


def _replace_var_tokens(value: str, context: Mapping[str, Any] | None) -> str:
    """Replace supported ${...} tokens.

    Malformed or oversized expressions are left unchanged.
    """
    matches = _iter_var_token_matches(value)
    if not matches:
        return value

    parts: list[str] = []
    last_index = 0

    for start, end, expression, format_pattern in matches:
        parts.append(value[last_index:start])
        resolved = _resolve_vars_value(context, expression)
        parts.append(str(_format_vars_value(resolved, format_pattern)))
        last_index = end

    parts.append(value[last_index:])
    return "".join(parts)


def _format_vars_value(value: Any, pattern: str | None) -> Any:
    if pattern and isinstance(value, (date, datetime, time)):
        return value.strftime(_python_date_format(pattern))
    return _stringify_template_value(value)


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

    processed_value = _replace_var_tokens(processed_value, context)

    # Check if the entire processed result is a single token (for type coercion)
    # This must be done BEFORE token replacement to preserve types
    stripped = processed_value.strip()
    single_match = _TOKEN_PATTERN.fullmatch(stripped)
    if single_match:
        token_name = single_match.group(1)
        resolved = _resolve_context_value(context, token_name)
        if resolved is None:
            resolved = token_map.get(token_name)
        return _coerce_template_value(resolved)

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
            key: render_value(
                item,
                context,
                base_tokens=base_tokens,
                include_templates=include_templates,
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[Any] = []
        for item in value:
            rendered = render_value(
                item,
                context,
                base_tokens=base_tokens,
                include_templates=include_templates,
            )
            if isinstance(rendered, list):
                result.extend(rendered)
            else:
                result.append(rendered)
        return result
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
