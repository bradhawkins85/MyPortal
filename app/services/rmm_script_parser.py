"""Detect the inputs an RMM script expects: its parameters and environment variables.

Scripts live in Gitea and are loaded into MyPortal as plain text. Before a
technician runs one, MyPortal shows a field for every input the script reads:

* **Parameters** come from a ``param(...)`` block. PowerShell scripts use their
  real param block. Bash and zsh have no param block, so they declare one with
  the same syntax inside a comment header::

      # param(
      #   [Parameter(Mandatory)] [string] $Hostname,  # Server to check
      #   [int] $Retries = 3
      # )

* **Environment variables** are the variables the script reads but never sets
  itself: ``$env:NAME`` in PowerShell, ``$NAME`` / ``${NAME}`` / ``${NAME:-x}``
  in bash and zsh (upper-case names only, so local variables are not offered).

The parser is deliberately forgiving: anything it cannot understand is ignored
rather than raising, so a script with an unusual layout still loads and simply
offers fewer fields.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

LANGUAGES: dict[str, str] = {".ps1": "powershell", ".sh": "bash", ".bash": "bash", ".zsh": "zsh"}
LANGUAGE_LABELS = {"powershell": "PowerShell", "bash": "Bash", "zsh": "Zsh"}

# Reserved for values MyPortal passes to every run.
RESERVED_ENV_PREFIX = "MYPORTAL_"

# Environment variables every platform provides; never offered as inputs.
_WELL_KNOWN_ENV = frozenset(
    name.upper()
    for name in (
        "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMPUTERNAME",
        "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "LOGONSERVER", "NUMBER_OF_PROCESSORS", "OS",
        "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROGRAMDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "PROGRAMW6432", "PSMODULEPATH", "PUBLIC", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP",
        "TMP", "USERDOMAIN", "USERNAME", "USERPROFILE", "WINDIR", "PATH", "HOME", "USER", "LOGNAME", "SHELL",
        "PWD", "OLDPWD", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "HOSTNAME", "HOSTTYPE", "OSTYPE",
        "MACHTYPE", "UID", "EUID", "PPID", "RANDOM", "SECONDS", "LINENO", "BASH", "BASH_VERSION",
        "BASH_SOURCE", "BASH_REMATCH", "BASH_LINENO", "FUNCNAME", "PIPESTATUS", "IFS", "PS1", "PS2", "PS4",
        "OPTARG", "OPTIND", "OPTERR", "REPLY", "ZSH_VERSION", "ZSH_NAME", "EPOCHSECONDS", "SHLVL", "DISPLAY",
        "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "SUDO_USER", "SUDO_UID",
        "EDITOR", "PAGER", "MAIL", "COLUMNS", "LINES", "GROUPS", "HISTFILE", "HISTSIZE", "DEBIAN_FRONTEND",
    )
)

_SENSITIVE_NAME = re.compile(r"pass(word|wd)?|secret|token|api_?key|credential|private_?key", re.IGNORECASE)
_TYPE_MAP = {
    "string": "string", "str": "string", "char": "string", "uri": "string", "guid": "string",
    "int": "integer", "int16": "integer", "int32": "integer", "int64": "integer", "long": "integer",
    "uint32": "integer", "uint64": "integer", "byte": "integer",
    "double": "number", "float": "number", "single": "number", "decimal": "number",
    "bool": "boolean", "boolean": "boolean", "switch": "switch",
    "securestring": "secret", "pscredential": "secret",
    "string[]": "list", "int[]": "list", "array": "list", "object[]": "list",
}

MAX_SCRIPT_BYTES = 1024 * 1024


@dataclass
class ScriptParameter:
    name: str
    type: str = "string"
    mandatory: bool = False
    default: str | None = None
    choices: list[str] = field(default_factory=list)
    help: str = ""
    sensitive: bool = False


@dataclass
class ScriptEnvVar:
    name: str
    default: str | None = None
    help: str = ""
    sensitive: bool = False


@dataclass
class ParsedScript:
    language: str
    description: str = ""
    parameters: list[ScriptParameter] = field(default_factory=list)
    env_vars: list[ScriptEnvVar] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "description": self.description,
            "parameters": [asdict(item) for item in self.parameters],
            "env_vars": [asdict(item) for item in self.env_vars],
        }


def language_for_path(path: str) -> str | None:
    """Return the script language for a file path, or ``None`` when unsupported."""

    lowered = path.lower()
    for extension, language in LANGUAGES.items():
        if lowered.endswith(extension):
            return language
    return None


def parse_script(content: str, language: str) -> ParsedScript:
    """Return the description, parameters and environment variables of a script."""

    content = content.replace("\r\n", "\n").lstrip("﻿")
    if language == "powershell":
        return _parse_powershell(content)
    return _parse_shell(content, language)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _balanced_end(text: str, start: int, *, powershell: bool) -> int | None:
    """Return the index just past the ``)`` matching ``text[start] == '('``."""

    depth = 0
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char in "'\"":
            index = _skip_string(text, index, powershell=powershell)
            continue
        if char == "#" and powershell:
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if powershell and text.startswith("<#", index):
            close = text.find("#>", index + 2)
            index = length if close == -1 else close + 2
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _skip_string(text: str, start: int, *, powershell: bool) -> int:
    quote = text[start]
    index = start + 1
    length = len(text)
    while index < length:
        char = text[index]
        if quote == "'" and char == "'":
            if powershell and index + 1 < length and text[index + 1] == "'":
                index += 2
                continue
            return index + 1
        if quote == '"':
            escape = "`" if powershell else "\\"
            if char == escape:
                index += 2
                continue
            if char == '"':
                return index + 1
        index += 1
    return length


def _split_top_level(text: str) -> list[str]:
    """Split a param block body on commas that are not nested in brackets or strings."""

    parts: list[str] = []
    depth = 0
    current: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in "'\"":
            end = _skip_string(text, index, powershell=True)
            current.append(text[index:end])
            index = end
            continue
        if text.startswith("<#", index):
            close = text.find("#>", index + 2)
            end = length if close == -1 else close + 2
            current.append(text[index:end])
            index = end
            continue
        if char == "#":
            newline = text.find("\n", index)
            end = length if newline == -1 else newline
            current.append(text[index:end])
            index = end
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            # A comment on the same line after the comma describes this parameter.
            lookahead = index + 1
            while lookahead < length and text[lookahead] in " \t":
                lookahead += 1
            if lookahead < length and text[lookahead] == "#" and not text.startswith("<#", lookahead - 1):
                newline = text.find("\n", lookahead)
                end = length if newline == -1 else newline
                current.append(" " + text[lookahead:end])
                index = end
            parts.append("".join(current))
            current = []
            index += 1
            continue
        else:
            current.append(char)
        index += 1
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('`"', '"')
    if value.lower() in {"$true", "$false", "$null"}:
        return {"$true": "true", "$false": "false", "$null": ""}[value.lower()]
    return value


_ATTRIBUTE = re.compile(r"\[\s*([A-Za-z_][\w.]*)\s*(\((.*?)\))?\s*\]", re.DOTALL)
_VARIABLE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _parse_param_chunk(chunk: str, help_lookup: dict[str, str]) -> ScriptParameter | None:
    comments = [line.split("#", 1)[1].strip() for line in chunk.splitlines() if line.strip().startswith("#")]
    code_lines = []
    trailing_help = ""
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if " #" in line:
            code, comment = line.split(" #", 1)
            trailing_help = comment.strip()
            code_lines.append(code)
        else:
            code_lines.append(line)
    code = re.sub(r"<#.*?#>", " ", "\n".join(code_lines), flags=re.DOTALL)
    # Attributes such as [Parameter(Mandatory=$true)] come first; the
    # parameter's own $Name follows them.
    index = 0
    while True:
        while index < len(code) and code[index].isspace():
            index += 1
        if index >= len(code) or code[index] != "[":
            break
        end = _balanced_end(code, index, powershell=True)
        if end is None:
            return None
        index = end
    variable = _VARIABLE.match(code, index)
    if not variable:
        return None
    name = variable.group(1)
    head = code[: variable.start()]
    tail = code[variable.end():]

    parameter = ScriptParameter(name=name)
    for match in _ATTRIBUTE.finditer(head):
        attribute = match.group(1).lower()
        arguments = match.group(3) or ""
        if attribute == "parameter":
            if re.search(r"\bmandatory\b\s*(=\s*\$true)?(?!\s*=\s*\$false)", arguments, re.IGNORECASE):
                parameter.mandatory = True
            help_match = re.search(r"helpmessage\s*=\s*('(?:[^']|'')*'|\"[^\"]*\")", arguments, re.IGNORECASE)
            if help_match:
                parameter.help = _unquote(help_match.group(1))
        elif attribute == "validateset":
            parameter.choices = [_unquote(item) for item in _split_top_level(arguments) if item.strip()]
        elif not match.group(2):
            mapped = _TYPE_MAP.get(attribute) or _TYPE_MAP.get(attribute.rsplit(".", 1)[-1])
            if mapped:
                parameter.type = mapped
        elif attribute.endswith("[]"):
            parameter.type = "list"
    if re.search(r"\[\s*[\w.]+\s*\[\s*\]\s*\]", head):
        parameter.type = "list"

    default_match = re.match(r"\s*=\s*(.+)", tail, re.DOTALL)
    if default_match:
        raw_default = default_match.group(1).strip()
        array = re.fullmatch(r"@\((.*)\)", raw_default, re.DOTALL)
        if array:
            items = [_unquote(item) for item in _split_top_level(array.group(1)) if item.strip()]
            parameter.default = ", ".join(items)
        else:
            parameter.default = _unquote(raw_default)
    if parameter.choices:
        parameter.type = "choice"
    if not parameter.help:
        parameter.help = help_lookup.get(name.lower()) or trailing_help or " ".join(comments)
    parameter.sensitive = parameter.type == "secret" or bool(_SENSITIVE_NAME.search(name))
    return parameter


def _parse_param_block(body: str, help_lookup: dict[str, str]) -> list[ScriptParameter]:
    parameters: list[ScriptParameter] = []
    seen: set[str] = set()
    for chunk in _split_top_level(body):
        parameter = _parse_param_chunk(chunk, help_lookup)
        if parameter and parameter.name.lower() not in seen:
            seen.add(parameter.name.lower())
            parameters.append(parameter)
    return parameters


def _dedupe_env(names: list[tuple[str, str | None]], exclude: set[str]) -> list[ScriptEnvVar]:
    result: list[ScriptEnvVar] = []
    seen: set[str] = set()
    for name, default in names:
        key = name.upper()
        if key in seen or key in exclude or key in _WELL_KNOWN_ENV or key.startswith(RESERVED_ENV_PREFIX):
            continue
        seen.add(key)
        result.append(ScriptEnvVar(name=name, default=default, sensitive=bool(_SENSITIVE_NAME.search(name))))
    return result


# --------------------------------------------------------------------------- #
# PowerShell
# --------------------------------------------------------------------------- #

_PS_HELP_BLOCK = re.compile(r"<#(.*?)#>", re.DOTALL)
_PS_ENV_READ = re.compile(
    r"\$env:([A-Za-z_][A-Za-z0-9_]*)|\$\{env:([^}]+)\}"
    r"|GetEnvironmentVariable\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]",
    re.IGNORECASE,
)


def _powershell_help(content: str) -> tuple[str, dict[str, str]]:
    match = _PS_HELP_BLOCK.search(content)
    if not match:
        return "", {}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in match.group(1).splitlines():
        keyword = re.match(r"\s*\.([A-Za-z]+)\s*(\S*)\s*$", line)
        if keyword:
            current = keyword.group(1).upper()
            if current == "PARAMETER":
                current = "PARAMETER " + keyword.group(2).lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line.strip())
    description = " ".join(part for part in sections.get("SYNOPSIS", []) if part).strip()
    if not description:
        description = " ".join(part for part in sections.get("DESCRIPTION", []) if part).strip()
    params = {
        key.split(" ", 1)[1]: " ".join(part for part in value if part).strip()
        for key, value in sections.items()
        if key.startswith("PARAMETER ")
    }
    return description, params


def _strip_powershell_comments(content: str) -> str:
    without_blocks = _PS_HELP_BLOCK.sub(" ", content)
    return "\n".join(line for line in without_blocks.splitlines() if not line.lstrip().startswith("#"))


def _find_powershell_param_block(content: str) -> str | None:
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if content.startswith("<#", index):
            close = content.find("#>", index + 2)
            index = length if close == -1 else close + 2
            continue
        if char == "#":
            newline = content.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if char in "'\"":
            index = _skip_string(content, index, powershell=True)
            continue
        if char == "[":
            # Script-level attributes such as [CmdletBinding()] precede param().
            end = _balanced_end(content, index, powershell=True)
            index = length if end is None else end
            continue
        if char.isspace():
            index += 1
            continue
        match = re.match(r"param\s*\(", content[index:], re.IGNORECASE)
        if not match:
            # Any other statement means the script has no top-level param block.
            return None
        open_index = index + match.end() - 1
        end = _balanced_end(content, open_index, powershell=True)
        if end is None:
            return None
        return content[open_index + 1 : end - 1]
    return None


def _parse_powershell(content: str) -> ParsedScript:
    description, help_lookup = _powershell_help(content)
    body = _find_powershell_param_block(content)
    parameters = _parse_param_block(body, help_lookup) if body is not None else []

    code = _strip_powershell_comments(content)
    first_read: dict[str, int] = {}
    first_assign: dict[str, int] = {}
    found: list[tuple[str, str | None]] = []
    for match in _PS_ENV_READ.finditer(code):
        name = match.group(1) or match.group(2) or match.group(3)
        key = name.upper()
        following = code[match.end(): match.end() + 4]
        if re.match(r"\s*=(?!=)", following):
            first_assign.setdefault(key, match.start())
            continue
        if key not in first_read:
            first_read[key] = match.start()
            found.append((name, None))
    exclude = {key for key, position in first_assign.items() if position < first_read.get(key, len(code) + 1)}
    return ParsedScript(
        language="powershell",
        description=description,
        parameters=parameters,
        env_vars=_dedupe_env(found, exclude),
    )


# --------------------------------------------------------------------------- #
# Bash / zsh
# --------------------------------------------------------------------------- #

_SHELL_VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?:(:?[-=?+])([^}]*))?\}|\$([A-Z_][A-Z0-9_]*)")
_SHELL_ASSIGN = re.compile(
    r"(?:^|[;&|\s(])(?:export\s+|local\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?"
    r"([A-Z_][A-Z0-9_]*)(?:\[[^\]]*\])?\+?=",
)
_SHELL_LOOP = re.compile(r"\bfor\s+([A-Z_][A-Z0-9_]*)\s+in\b|\bgetopts\s+\S+\s+([A-Z_][A-Z0-9_]*)")
# The rest of a "read" command; its arguments are split in Python rather than
# by a nested pattern, which could backtrack exponentially.
_SHELL_READ = re.compile(r"\bread\b([^\n;&|<>]*)")
_SHELL_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
# read options whose value is the next word (-a's value is an array name).
_READ_OPTIONS_WITH_VALUE = set("adinNptu")


def _read_targets(arguments: str) -> list[str]:
    """Variable names a ``read`` command assigns."""

    try:
        words = shlex.split(arguments)
    except ValueError:
        words = arguments.split()
    names: list[str] = []
    expect_value = ""
    for word in words:
        if expect_value:
            if expect_value == "a" and _SHELL_NAME.fullmatch(word):
                names.append(word)
            expect_value = ""
            continue
        if word.startswith("-") and len(word) > 1:
            if word[-1] in _READ_OPTIONS_WITH_VALUE:
                expect_value = word[-1]
            continue
        if _SHELL_NAME.fullmatch(word):
            names.append(word)
    return names


def _shell_header(content: str) -> tuple[str, str | None]:
    """Return the leading description and the commented ``param(...)`` body, if any."""

    lines = content.split("\n")
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    comment_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            comment_lines.append(stripped[1:])
        elif stripped:
            break
        elif comment_lines:
            comment_lines.append("")
    header = "\n".join(comment_lines)
    block_match = re.search(r"^\s*param\s*\(", header, re.IGNORECASE | re.MULTILINE)
    body = None
    description_source = header
    if block_match:
        open_index = block_match.end() - 1
        end = _balanced_end(header, open_index, powershell=True)
        if end is not None:
            body = header[open_index + 1 : end - 1]
            description_source = header[: block_match.start()]
    description_lines = []
    for line in description_source.split("\n"):
        text = line.strip()
        if not text:
            if description_lines:
                break
            continue
        if re.match(r"(?i)^(description|synopsis)\s*:\s*", text):
            text = re.sub(r"(?i)^(description|synopsis)\s*:\s*", "", text)
        description_lines.append(text)
    return " ".join(description_lines).strip(), body


def _parse_shell(content: str, language: str) -> ParsedScript:
    description, body = _shell_header(content)
    parameters = _parse_param_block(body, {}) if body is not None else []

    code = "\n".join(
        line for line in content.split("\n") if not line.lstrip().startswith("#")
    )
    # Variables inside single quotes are literal text, not reads.
    code = re.sub(r"'[^'\n]*'", "''", code)

    first_assign: dict[str, int] = {}
    for match in _SHELL_ASSIGN.finditer(code):
        first_assign.setdefault(match.group(1), match.start(1))
    for match in _SHELL_LOOP.finditer(code):
        first_assign.setdefault(match.group(1) or match.group(2), match.start())
    for match in _SHELL_READ.finditer(code):
        for name in _read_targets(match.group(1)):
            first_assign.setdefault(name, match.start())

    first_read: dict[str, int] = {}
    found: list[tuple[str, str | None]] = []
    for match in _SHELL_VAR.finditer(code):
        name = match.group(1) or match.group(4)
        default = None
        if match.group(2) in {"-", ":-", "=", ":="}:
            default = match.group(3)
        if name not in first_read:
            first_read[name] = match.start()
            found.append((name, default))
    exclude = {name for name, position in first_assign.items() if position < first_read.get(name, len(code) + 1)}
    exclude.update(parameter.name.upper() for parameter in parameters)
    return ParsedScript(
        language=language,
        description=description,
        parameters=parameters,
        env_vars=_dedupe_env(found, exclude),
    )
