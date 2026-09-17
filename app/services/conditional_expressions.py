"""Conditional expression parsing and evaluation for template variables.

Supports syntax like:
{{if count:asset:bitdefender > 0 then list:asset:bitdefender}}
{{if count:asset:bitdefender > 0 then list:asset:bitdefender else "No assets"}}
"""
from __future__ import annotations

import re
from typing import Any


_CONDITIONAL_OPENER_LENGTH = 2
_CONDITIONAL_CLOSER_LENGTH = 2
_MAX_CONDITIONAL_LENGTH = 4096

# Pattern to match comparison operators
_COMPARISON_PATTERN = re.compile(
    r"^(.+?)\s*(>=|<=|>|<|==|!=)\s*(.+?)$"
)


def _skip_whitespace(text: str, start: int, end: int) -> int:
    while start < end and text[start].isspace():
        start += 1
    return start


def _find_clause_keyword(text: str, keyword: str) -> int:
    keyword_lower = keyword.lower()
    keyword_length = len(keyword)
    in_single_quote = False
    in_double_quote = False
    index = 0
    text_length = len(text)

    while index <= text_length - keyword_length:
        char = text[index]

        if char == "\\" and (in_single_quote or in_double_quote):
            index += 2
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            index += 1
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            index += 1
            continue

        if not in_single_quote and not in_double_quote:
            if (
                text[index:index + keyword_length].lower() == keyword_lower
                and (index == 0 or text[index - 1].isspace())
                and (
                    index + keyword_length == text_length
                    or text[index + keyword_length].isspace()
                )
            ):
                return index
        index += 1

    return -1


def _parse_conditional_body(body: str) -> tuple[str, str, str | None] | None:
    then_keyword = "then"
    else_keyword = "else"
    then_index = _find_clause_keyword(body, then_keyword)
    if then_index < 0:
        return None

    condition = body[:then_index].strip()
    remainder = body[then_index + len(then_keyword):].strip()
    if not condition or not remainder:
        return None

    else_index = _find_clause_keyword(remainder, else_keyword)
    if else_index < 0:
        return condition, remainder, None

    then_value = remainder[:else_index].strip()
    else_value = remainder[else_index + len(else_keyword):].strip()
    if not then_value or not else_value:
        return None

    return condition, then_value, else_value


def _iter_conditional_matches(
    text: str,
) -> list[tuple[int, int, str, str, str | None]]:
    matches: list[tuple[int, int, str, str, str | None]] = []
    search_from = 0
    text_length = len(text)

    while search_from < text_length:
        start = text.find("{{", search_from)
        if start < 0:
            break

        parse_limit = min(
            text_length,
            start
            + _CONDITIONAL_OPENER_LENGTH
            + _MAX_CONDITIONAL_LENGTH
            + _CONDITIONAL_CLOSER_LENGTH,
        )
        cursor = _skip_whitespace(text, start + 2, parse_limit)
        if cursor >= parse_limit:
            search_from = start + 2
            continue
        if text[cursor:cursor + 2].lower() != "if":
            search_from = start + 2
            continue

        after_if = cursor + 2
        if after_if >= parse_limit or not text[after_if].isspace():
            search_from = start + 2
            continue

        close_index = text.find("}}", after_if, parse_limit)
        if close_index < 0:
            search_from = start + 2
            continue

        parsed = _parse_conditional_body(text[after_if:close_index].strip())
        if parsed is None:
            search_from = close_index + 2
            continue

        condition, then_value, else_value = parsed
        matches.append((start, close_index + 2, condition, then_value, else_value))
        search_from = close_index + 2

    return matches


def _parse_value(value_str: str) -> str | int | float:
    """Parse a string value to its appropriate type.
    
    Handles:
    - Quoted strings (single or double quotes)
    - Numbers (int or float)
    - Unquoted strings (treated as variable references)
    """
    value_str = value_str.strip()
    
    # Handle quoted strings
    if (value_str.startswith('"') and value_str.endswith('"')) or \
       (value_str.startswith("'") and value_str.endswith("'")):
        return value_str[1:-1]
    
    # Try to parse as number
    try:
        if '.' in value_str:
            return float(value_str)
        return int(value_str)
    except (ValueError, TypeError):
        return value_str
    
    # Return as-is (will be treated as variable reference)
    return value_str


def _resolve_value(value: str | int | float, token_map: dict[str, Any]) -> Any:
    """Resolve a value from the token map if it's a string (variable reference)."""
    if not isinstance(value, str):
        return value
    
    # Try to resolve as token
    resolved = token_map.get(value)
    if resolved is not None:
        # Try to convert to numeric if possible
        try:
            if isinstance(resolved, str):
                if '.' in resolved:
                    return float(resolved)
                return int(resolved)
        except (ValueError, TypeError):
            return resolved
        return resolved
    
    # Not a token, return the string as-is
    return value


def _evaluate_comparison(left: Any, operator: str, right: Any) -> bool:
    """Evaluate a comparison expression."""
    # Convert to comparable types
    try:
        # Try numeric comparison first
        if isinstance(left, str):
            left_num = float(left) if '.' in left else int(left)
        else:
            left_num = float(left) if not isinstance(left, int) else left
            
        if isinstance(right, str):
            right_num = float(right) if '.' in right else int(right)
        else:
            right_num = float(right) if not isinstance(right, int) else right
        
        # Use numeric comparison
        if operator == ">":
            return left_num > right_num
        elif operator == "<":
            return left_num < right_num
        elif operator == ">=":
            return left_num >= right_num
        elif operator == "<=":
            return left_num <= right_num
        elif operator == "==":
            return left_num == right_num
        elif operator == "!=":
            return left_num != right_num
    except (ValueError, TypeError):
        # Fall back to string comparison
        left_str = str(left)
        right_str = str(right)
        
        if operator == ">":
            return left_str > right_str
        elif operator == "<":
            return left_str < right_str
        elif operator == ">=":
            return left_str >= right_str
        elif operator == "<=":
            return left_str <= right_str
        elif operator == "==":
            return left_str == right_str
        elif operator == "!=":
            return left_str != right_str
    
    return False


def _evaluate_condition(condition: str, token_map: dict[str, Any]) -> bool:
    """Evaluate a conditional expression.
    
    Supports comparison operators: >, <, >=, <=, ==, !=
    """
    # Try to parse as comparison
    match = _COMPARISON_PATTERN.match(condition.strip())
    if match:
        left_str = match.group(1)
        operator = match.group(2)
        right_str = match.group(3)
        
        # Parse and resolve values
        left_val = _parse_value(left_str)
        right_val = _parse_value(right_str)
        
        left_resolved = _resolve_value(left_val, token_map)
        right_resolved = _resolve_value(right_val, token_map)
        
        return _evaluate_comparison(left_resolved, operator, right_resolved)
    
    # Simple boolean check (non-zero, non-empty)
    value = _resolve_value(condition.strip(), token_map)
    if isinstance(value, str):
        # Try to parse as number
        try:
            value = float(value) if '.' in value else int(value)
        except (ValueError, TypeError):
            # String is truthy if non-empty
            return bool(value)
    
    return bool(value)


def find_conditionals(text: str) -> list[tuple[str, str, str, str | None]]:
    """Find all conditional expressions in the text.
    
    Returns a list of tuples: (full_match, condition, then_value, else_value).

    Malformed or oversized conditional expressions are ignored and left
    unchanged by callers.
    """
    if not text or not isinstance(text, str):
        return []

    return [
        (text[start:end], condition, then_value, else_value)
        for start, end, condition, then_value, else_value
        in _iter_conditional_matches(text)
    ]


def evaluate_conditional(
    condition: str,
    then_value: str,
    else_value: str | None,
    token_map: dict[str, Any],
) -> str:
    """Evaluate a conditional expression and return the appropriate value.
    
    Args:
        condition: The condition to evaluate (e.g., "count:asset:bitdefender > 0")
        then_value: The value to return if condition is true
        else_value: The value to return if condition is false (optional)
        token_map: Dictionary of available tokens for resolution
    
    Returns:
        The evaluated value as a string
    """
    is_true = _evaluate_condition(condition, token_map)
    
    if is_true:
        result = _parse_value(then_value)
        resolved = _resolve_value(result, token_map)
        return str(resolved) if resolved is not None else ""
    else:
        if else_value is not None:
            result = _parse_value(else_value)
            resolved = _resolve_value(result, token_map)
            return str(resolved) if resolved is not None else ""
        return ""


def process_conditionals(text: str, token_map: dict[str, Any]) -> str:
    """Process all conditional expressions in the text.
    
    Args:
        text: The text containing conditional expressions
        token_map: Dictionary of available tokens for resolution
    
    Returns:
        The text with all conditionals replaced by their evaluated values
    """
    if not text or not isinstance(text, str):
        return text

    matches = _iter_conditional_matches(text)
    if not matches:
        return text

    parts: list[str] = []
    last_index = 0
    for start, end, condition, then_value, else_value in matches:
        parts.append(text[last_index:start])
        parts.append(
            evaluate_conditional(condition, then_value, else_value, token_map)
        )
        last_index = end
    parts.append(text[last_index:])
    return "".join(parts)
