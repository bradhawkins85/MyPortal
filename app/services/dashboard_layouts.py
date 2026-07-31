"""Validation and data resolution for configurable dashboard panels."""

from __future__ import annotations

from numbers import Number
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from app.repositories import reporting as reporting_repo
from app.services import reporting as reporting_service
from app.services.system_variables import get_system_variables

MAX_PANELS = 80
PANEL_TYPES = {"link", "stat", "variable", "graph"}
GRAPH_TYPES = {"bar", "line", "area", "doughnut"}
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
GRAPH_Y_RE = re.compile(r"^Y(?:[1-9][0-9]*)?$")

DEFAULT_LAYOUT = {
    "version": 2,
    "title": "My dashboard",
    "panels": [
        {
            "id": "new-ticket",
            "type": "link",
            "title": "Need help?",
            "label": "Create a ticket",
            "url": "/tickets/new",
            "x": 0,
            "y": 0,
            "w": 4,
            "h": 2,
        },
        {
            "id": "open-tickets",
            "type": "stat",
            "title": "Open tickets",
            "report": "dashboard-open-tickets",
            "function": "count",
            "x": 4,
            "y": 0,
            "w": 4,
            "h": 2,
        },
        {
            "id": "assets",
            "type": "stat",
            "title": "Managed assets",
            "report": "dashboard-assets",
            "function": "count",
            "x": 8,
            "y": 0,
            "w": 4,
            "h": 2,
        },
    ],
}


class InvalidDashboardLayout(ValueError):
    pass


def validate_layout(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("panels"), list):
        raise InvalidDashboardLayout(
            "Layout must be an object containing a panels array."
        )
    if len(value["panels"]) > MAX_PANELS:
        raise InvalidDashboardLayout(
            f"A dashboard may contain at most {MAX_PANELS} panels."
        )
    clean = {
        "version": 2,
        "title": str(value.get("title") or "My dashboard")[:100],
        "panels": [],
    }
    ids: set[str] = set()
    for index, raw in enumerate(value["panels"]):
        if not isinstance(raw, Mapping):
            raise InvalidDashboardLayout(f"Panel {index + 1} must be an object.")
        panel_type = str(raw.get("type") or "")
        panel_id = str(raw.get("id") or f"panel-{index + 1}")[:80]
        if panel_type not in PANEL_TYPES or panel_id in ids:
            raise InvalidDashboardLayout(
                f"Panel {index + 1} has an invalid type or duplicate id."
            )
        ids.add(panel_id)
        panel = {
            "id": panel_id,
            "type": panel_type,
            "title": str(raw.get("title") or "Panel")[:120],
        }
        for key, default, low, high in (
            ("x", 0, 0, 11),
            ("y", index, 0, 500),
            ("w", 4, 1, 12),
            ("h", 2, 1, 12),
        ):
            try:
                panel[key] = max(low, min(high, int(raw.get(key, default))))
            except (TypeError, ValueError):
                panel[key] = default
        if panel_type == "link":
            url = str(raw.get("url") or "")[:500]
            parsed = urlparse(url)
            if parsed.scheme not in {"", "http", "https", "mailto"} or (
                not url.startswith("/") and not parsed.scheme
            ):
                raise InvalidDashboardLayout(
                    f"Panel {index + 1} has an invalid link URL."
                )
            panel.update(url=url, label=str(raw.get("label") or "Open")[:80])
        elif panel_type == "variable":
            panel["variable"] = str(raw.get("variable") or "")[:120].upper()
        else:
            panel["report"] = str(raw.get("report") or "")[:120]
            if panel_type == "stat":
                panel["function"] = "list" if raw.get("function") == "list" else "count"
                if panel["function"] == "count":
                    try:
                        panel["compare_value"] = float(raw.get("compare_value", 0))
                    except (TypeError, ValueError):
                        panel["compare_value"] = 0
                    for key, fallback in (
                        ("less_colour", "#7c2d12"),
                        ("equal_colour", "#1e3a8a"),
                        ("greater_colour", "#14532d"),
                    ):
                        colour = str(raw.get(key) or fallback)
                        panel[key] = colour if COLOUR_RE.fullmatch(colour) else fallback
            else:
                panel["chart"] = (
                    raw.get("chart") if raw.get("chart") in GRAPH_TYPES else "bar"
                )
        clean["panels"].append(panel)
    return clean


async def resolve_layout(
    layout: dict[str, Any], *, company_id: int | None, can_run_all: bool, user_id: int
) -> dict[str, Any]:
    variables = get_system_variables()
    output = {**layout, "panels": []}
    for panel in layout["panels"]:
        item = dict(panel)
        try:
            if panel["type"] == "variable":
                item["value"] = variables.get(
                    panel.get("variable", ""), "Not available"
                )
            elif panel["type"] in {"stat", "graph"}:
                report = await reporting_repo.get_query_by_slug(panel.get("report", ""))
                permitted = report and (
                    can_run_all
                    or await reporting_repo.user_has_permission(report["id"], user_id)
                )
                # An inherited layout may contain panels its viewer cannot run.
                # Omit those panels completely rather than leaking their title,
                # report slug, or the fact that data exists.
                if not permitted:
                    continue
                result = await reporting_service.run_query_with_context(
                    report["sql_query"], company_id=company_id
                )
                rows, columns = result["rows"], result["columns"]
                if panel["type"] == "stat":
                    item["value"] = (
                        len(rows)
                        if panel.get("function") == "count"
                        else (
                            [row.get(columns[0]) for row in rows[:8]] if columns else []
                        )
                    )
                else:
                    # X supplies labels and Y/Y1/Y2/... supply series. Other
                    # query columns are deliberately ignored.
                    x_column = next((c for c in columns if c == "X"), None)
                    y_columns = [c for c in columns if GRAPH_Y_RE.fullmatch(c)]
                    item["chart_data"] = {
                        "labels": (
                            [str(r.get(x_column, "")) for r in rows[:50]]
                            if x_column
                            else []
                        ),
                        "series": [
                            {
                                "name": c,
                                "values": [
                                    r.get(c) if isinstance(r.get(c), Number) else None
                                    for r in rows[:50]
                                ],
                            }
                            for c in y_columns
                        ],
                    }
                    if not x_column or not y_columns:
                        item["error"] = (
                            "Graph queries must return columns named X and Y "
                            "(additional series may be named Y1, Y2, ...)."
                        )
        except Exception as exc:
            item["error"] = str(exc)
        output["panels"].append(item)
    return output
