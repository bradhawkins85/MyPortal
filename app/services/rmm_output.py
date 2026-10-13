"""Turn PowerShell's redirected CLIXML streams into readable console text."""
from __future__ import annotations

import re

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

_ESCAPE = re.compile(r"_x([0-9a-fA-F]{4})_")


def _decode(text: str) -> str:
    # Decode once: _x005F_x0041_ represents the literal string _x0041_.
    text = _ESCAPE.sub(lambda match: chr(int(match[1], 16)), text)
    return text.encode("utf-16", errors="surrogatepass").decode("utf-16", errors="replace")


def readable_output(value: object) -> str:
    text = str(value or "")
    if "#< CLIXML" not in text:
        return text
    plain, _, xml = text.partition("#< CLIXML")
    try:
        root = ElementTree.fromstring(xml.strip())
    except (ElementTree.ParseError, ValueError, DefusedXmlException):
        return text
    if root.tag.rsplit("}", 1)[-1] != "Objs":
        return text
    lines = []
    for record in root:
        tag = record.tag.rsplit("}", 1)[-1]
        if tag == "S":
            lines.append(_decode(record.text or ""))
        elif tag == "Obj":
            children = {child.tag.rsplit("}", 1)[-1]: child for child in record}
            if "ToString" in children:
                lines.append(_decode(children["ToString"].text or ""))
            elif "PR" in children:
                fields = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in children["PR"]}
                lines.append(_decode(": ".join(fields[key] for key in ("AV", "SD") if fields.get(key))))
    return plain + "".join(line if line.endswith("\n") else line + "\n" for line in lines if line)
