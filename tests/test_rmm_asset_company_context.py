"""Device routes act on the device's company, not the one in the switcher."""

import ast
from pathlib import Path

RMM = Path(__file__).resolve().parents[1] / "app" / "features" / "rmm"


def _asset_routes_without_asset_context():
    missing = []
    for path in sorted(RMM.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            paths = [d.args[0].value for d in node.decorator_list
                     if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)]
            if not any("{asset_id:int}" in str(p) for p in paths):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_context"
                        and not any(k.arg == "asset_id" for k in call.keywords)):
                    missing.append(f"{path.name}:{node.name}")
    return missing


def test_device_routes_resolve_the_devices_company():
    assert _asset_routes_without_asset_context() == []
