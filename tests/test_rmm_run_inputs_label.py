"""The run modal's "Values sent" labels for every stored input shape."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _input_label_source() -> str:
    script = (ROOT / "app/static/js/rmm_scripts.js").read_text()
    match = re.search(r"  function inputLabel\(item\) \{\n.*?\n  \}\n", script, re.S)
    assert match, "inputLabel() not found in rmm_scripts.js"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_values_sent_labels_never_show_undefined():
    items = [
        {"kind": "param", "name": "Package", "value": "office365business"},
        {"kind": "env", "name": "TOKEN", "value": "x"},
        {"kind": "command", "name": "Command", "value": "dir"},
        # Built-in jobs (Chocolatey installs, patch policies, Windows updates)
        {"key": "applications", "label": "Applications", "value": "office365business"},
        {"key": "mode", "label": "Action", "value": "Install"},
        {"key": "force", "value": "Yes"},
    ]
    program = _input_label_source() + "\nconsole.log(JSON.stringify(" + json.dumps(items) + ".map(inputLabel)));"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["-Package", "$env:TOKEN", "Command", "Applications", "Action", "force"]
