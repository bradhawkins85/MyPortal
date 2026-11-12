"""Text functions for staff workflow ``${vars.*}`` tokens.

A token may pipe its value through one or more functions, e.g.
``${vars.staff.first_name}${vars.staff.last_name | left(1)}`` renders
``JohnS`` for John Smith. Functions run left to right.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_MAX_FUNCTIONS = 10
_MAX_ARG_LENGTH = 200
_CALL_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?$", re.DOTALL)


class VariableFunctionError(ValueError):
    """Raised when a pipe expression cannot be parsed or applied."""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _int_arg(args: list[str], index: int, name: str, default: int | None = None) -> int:
    if index >= len(args):
        if default is None:
            raise VariableFunctionError(f"{name}() needs a number")
        return default
    try:
        return int(args[index])
    except ValueError as exc:
        raise VariableFunctionError(f"{name}() expects a whole number") from exc


def _left(value: Any, args: list[str]) -> str:
    return _text(value)[: max(_int_arg(args, 0, "left"), 0)]


def _right(value: Any, args: list[str]) -> str:
    count = max(_int_arg(args, 0, "right"), 0)
    text = _text(value)
    return text[-count:] if count else ""


def _mid(value: Any, args: list[str]) -> str:
    # 1-based start like Excel MID(text, start, length).
    start = max(_int_arg(args, 0, "mid"), 1) - 1
    text = _text(value)
    if len(args) > 1:
        return text[start : start + max(_int_arg(args, 1, "mid"), 0)]
    return text[start:]


def _replace(value: Any, args: list[str]) -> str:
    if not args:
        raise VariableFunctionError("replace() needs the text to find")
    return _text(value).replace(args[0], args[1] if len(args) > 1 else "")


def _word(value: Any, args: list[str]) -> str:
    words = _text(value).split()
    index = _int_arg(args, 0, "word", 1)
    if index == 0 or abs(index) > len(words):
        return ""
    return words[index - 1] if index > 0 else words[index]


def _initials(value: Any, args: list[str]) -> str:
    return "".join(word[0] for word in _text(value).split())


def _default(value: Any, args: list[str]) -> Any:
    if value is None or _text(value).strip() == "":
        return args[0] if args else ""
    return value


def _alnum(value: Any, args: list[str]) -> str:
    return "".join(ch for ch in _text(value) if ch.isalnum())


def _nospace(value: Any, args: list[str]) -> str:
    return "".join(_text(value).split())


def _pad_left(value: Any, args: list[str]) -> str:
    width = min(max(_int_arg(args, 0, "pad_left"), 0), _MAX_ARG_LENGTH)
    fill = (args[1] if len(args) > 1 and args[1] else "0")[0]
    return _text(value).rjust(width, fill)


FUNCTIONS: dict[str, Callable[[Any, list[str]], Any]] = {
    "left": _left,
    "right": _right,
    "mid": _mid,
    "trim": lambda value, args: _text(value).strip(),
    "ltrim": lambda value, args: _text(value).lstrip(),
    "rtrim": lambda value, args: _text(value).rstrip(),
    "len": lambda value, args: len(_text(value)),
    "upper": lambda value, args: _text(value).upper(),
    "lower": lambda value, args: _text(value).lower(),
    "proper": lambda value, args: _text(value).title(),
    "replace": _replace,
    "word": _word,
    "first_word": lambda value, args: _word(value, ["1"]),
    "last_word": lambda value, args: _word(value, ["-1"]),
    "initials": _initials,
    "nospace": _nospace,
    "alnum": _alnum,
    "pad_left": _pad_left,
    "default": _default,
}


def _split_args(raw: str) -> list[str]:
    """Split ``a, "b, c", 'd'`` into ``["a", "b, c", "d"]``."""
    if not raw.strip():
        return []
    pieces: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
        elif ch in {'"', "'"}:
            quote = ch
        elif ch == ",":
            pieces.append("".join(current))
            current = []
            continue
        current.append(ch)
    if quote:
        raise VariableFunctionError("unterminated quote in function arguments")
    pieces.append("".join(current))
    args: list[str] = []
    for piece in pieces:
        text = piece.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1]
        if len(text) > _MAX_ARG_LENGTH:
            raise VariableFunctionError("function argument is too long")
        args.append(text)
    return args


def _split_pipes(expression: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in expression:
        if quote:
            if ch == quote:
                quote = None
        elif ch in {'"', "'"}:
            quote = ch
        elif ch == "|":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def parse_pipeline(expression: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Return ``(variable_path, [(function, args), ...])`` for a token body."""
    segments = _split_pipes(expression)
    path = segments[0].strip()
    calls = [segment.strip() for segment in segments[1:]]
    if len(calls) > _MAX_FUNCTIONS:
        raise VariableFunctionError("too many functions in one variable")
    parsed: list[tuple[str, list[str]]] = []
    for call in calls:
        match = _CALL_PATTERN.match(call)
        if not match:
            raise VariableFunctionError(f"cannot read function '{call}'")
        name = match.group(1).lower()
        if name not in FUNCTIONS:
            raise VariableFunctionError(f"unknown function '{name}'")
        parsed.append((name, _split_args(match.group(2) or "")))
    return path, parsed


def apply_pipeline(value: Any, calls: list[tuple[str, list[str]]]) -> Any:
    for name, args in calls:
        value = FUNCTIONS[name](value, args)
    return value
