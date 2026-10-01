from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

_MODULE_PREFIXES = (
    "app.core",
    "app.services",
    "app.repositories",
    "app.api.routes",
    "app.main",
    "app.__init__",
    "app.api.__init__",
)


def _build_module_index() -> tuple[dict[str, Path], set[str]]:
    modules: dict[str, Path] = {}
    package_names: set[str] = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        module_name = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        modules[module_name] = path
        if path.name == "__init__.py":
            package_names.add(".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1]))
    return modules, package_names


def _resolve_from_import(
    current_module: str, target: str | None, level: int, modules: dict[str, Path], package_names: set[str]
) -> list[str]:
    current_parts = current_module.split(".")
    if level > 0:
        base = current_parts[:-level]
        parts = base + (target.split(".") if target else [])
    else:
        parts = target.split(".") if target else []
    if not parts:
        return []
    candidate = ".".join(parts)
    if candidate in modules or candidate in package_names:
        return [candidate]
    return []


def _build_import_graph() -> dict[str, set[str]]:
    modules, package_names = _build_module_index()
    graph: dict[str, set[str]] = {
        module: set() for module in modules if module.startswith(_MODULE_PREFIXES)
    }
    for module_name, path in modules.items():
        if module_name not in graph:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name
                    if not imported.startswith("app."):
                        continue
                    candidate = imported
                    while candidate and candidate not in modules and candidate not in package_names:
                        candidate = ".".join(candidate.split(".")[:-1])
                    if candidate in graph:
                        graph[module_name].add(candidate)
            elif isinstance(node, ast.ImportFrom):
                for candidate in _resolve_from_import(
                    module_name, node.module, node.level, modules, package_names
                ):
                    if candidate in graph:
                        graph[module_name].add(candidate)
                if node.module:
                    if node.level > 0:
                        base_parts = module_name.split(".")[:-node.level] + node.module.split(".")
                        base = ".".join(base_parts)
                    else:
                        base = node.module
                    for alias in node.names:
                        candidate = f"{base}.{alias.name}" if base else alias.name
                        if candidate in graph:
                            graph[module_name].add(candidate)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[list[str]] = []

    def dfs(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in graph[node]:
            if neighbour not in indices:
                dfs(neighbour)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbour])
            elif neighbour in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbour])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for node in graph:
        if node not in indices:
            dfs(node)
    return sorted(components, key=lambda component: (-len(component), component))


def test_core_service_repository_route_import_graph_has_no_cycles() -> None:
    components = _strongly_connected_components(_build_import_graph())
    assert components == [], f"Import cycles detected: {components}"
