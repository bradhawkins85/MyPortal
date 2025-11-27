"""Conditional expression parsing and evaluation for template variables.

Supports syntax like:
{{if count:asset:bitdefender > 0 then list:asset:bitdefender}}
{{if count:asset:bitdefender > 0 then list:asset:bitdefender else "No assets"}}
"""
from __future__ import annotations

import re
from typing import Any


# Pattern to match conditional expressions
# Format: {{if condition then value_if_true [else value_if_false]}}
_CONDITIONAL_PATTERN = re.compile(
    r"\{\{\s*if\s+(.+?)\s+then\s+(.+?)(?:\s+else\s+(.+?))?\s*\}\}",
    re.IGNORECASE
)

# Pattern to match comparison operators
_COMPARISON_PATTERN = re.compile(
    r"^(.+?)\s*(>=|<=|>|<|==|!=)\s*(.+?)$"
)


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
        pass
    
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
            pass
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
    
    Returns a list of tuples: (full_match, condition, then_value, else_value)
    """
    results = []
    for match in _CONDITIONAL_PATTERN.finditer(text):
        full_match = match.group(0)
        condition = match.group(1)
        then_value = match.group(2)
        else_value = match.group(3) if match.lastindex >= 3 else None
        results.append((full_match, condition, then_value, else_value))
    return results


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
    
    result = text
    conditionals = find_conditionals(result)
    
    # Process from longest to shortest to avoid conflicts
    conditionals.sort(key=lambda x: len(x[0]), reverse=True)
    
    for full_match, condition, then_value, else_value in conditionals:
        evaluated = evaluate_conditional(condition, then_value, else_value, token_map)
        result = result.replace(full_match, evaluated)
    
    return result
