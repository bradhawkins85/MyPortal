"""Network map pages, exports, and interface/link documentation.

The map is drawn server-side (see ``app.services.network_map``) so browsing,
the SVG/PNG exports and the PDF export all show the same drawing.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup

from app.repositories import infrastructure as infrastructure_repo
from app.repositories import network_map as links_repo
from app.services import asset_types
from app.services import audit as audit_service
from app.services import network_map

router = APIRouter(tags=["Network map"])

PERMISSION = "menu.network_map"
PAPER_SIZES = {"a4": "A4", "a3": "A3", "a2": "A2", "letter": "Letter", "tabloid": "Tabloid (11 × 17 in)"}
# Portrait page sizes in millimetres, for scaling the map to fit one page.
PAPER_MM = {"a4": (210, 297), "a3": (297, 420), "a2": (420, 594), "letter": (216, 279), "tabloid": (279, 432)}
PDF_MARGIN_MM = 10


def _routes():
    from app.features.assets import routes

    return routes


async def _context(request: Request, *, write: bool = False):
    """Return ``(user, membership, company, company_id, can_edit)`` or a redirect."""
    routes = _routes()
    main_module = routes._main()
    if not main_module._feature_pack_available("assets"):
        # The map is drawn from assets; without the assets pack there is nothing to show.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    user, membership, company, company_id, redirect = await routes._load_asset_context(
        request, PERMISSION)
    if redirect:
        return redirect
    can_edit = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, PERMISSION, write=True)
    if write and not can_edit:
        raise HTTPException(status_code=403, detail="Network map write access required")
    return user, membership, company, company_id, can_edit


async def _graph(company_id: int, options: network_map.MapOptions):
    overview, extra = await asyncio.gather(
        infrastructure_repo.overview(company_id), links_repo.load(company_id))
    return network_map.build_graph(overview, extra, options), overview, extra


def _safe_filename(company: dict[str, Any] | None, extension: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "-", str((company or {}).get("name") or "company")).strip("-").lower()
    return f"network-map-{name or 'company'}-{datetime.now(timezone.utc):%Y%m%d}.{extension}"


def _type_filter_groups(all_nodes: dict[str, Any]) -> list[dict[str, Any]]:
    """Catalogue groups for the device type filter, with how many of each exist."""
    counts: dict[str, int] = {}
    for node in all_nodes.values():
        if node.kind in {"asset", "item"}:
            counts[node.type_key] = counts.get(node.type_key, 0) + 1
    groups = []
    for group in asset_types.grouped():
        types = [{"key": item.key, "label": item.label, "icon": item.icon, "count": counts.get(item.key, 0)}
                 for item in group["types"]]
        groups.append({"label": group["label"], "types": types})
    return groups


def _link_rows(links: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for link in links:
        details = [part for part in (
            network_map._frequency(link.get("frequency_mhz")), network_map._distance(link.get("distance_m")),
            f"{link['signal_dbm']} dBm" if link.get("signal_dbm") is not None else None,
            network_map._speed(link.get("speed_mbps"))) if part]
        rows.append({
            "id": link["id"],
            "a": labels.get(f"{link['a_kind']}:{link['a_id']}", "Removed endpoint"),
            "b": labels.get(f"{link['b_kind']}:{link['b_id']}", "Removed endpoint"),
            "medium": links_repo.MEDIA.get(link["medium"], link["medium"]),
            "label": link.get("label"), "details": " · ".join(details),
        })
    return rows


@router.get("/network-map", response_class=HTMLResponse, summary="Browse the company network map")
async def network_map_page(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, company, company_id, can_edit = context
    options = network_map.MapOptions.from_params(request.query_params)
    graph, overview, extra = await _graph(company_id, options)
    everything = network_map.build_graph(overview, extra, network_map.MapOptions(include_unlinked=True))
    title = "Network map"
    svg = network_map.render_svg(
        graph, title=title, subtitle=network_map.subtitle((company or {}).get("name"), options),
        interactive=True)
    endpoint_options = await links_repo.endpoint_options(company_id) if can_edit else []
    labels = {item["value"]: item["label"] for item in endpoint_options}
    query = urlencode(options.query())
    return await _routes()._main()._render_template(
        "network_map/index.html", request, user, extra={
            "title": title, "company": company, "can_edit": can_edit,
            "map_svg": Markup(svg), "map_payload": network_map.graph_payload(graph),
            "options": options, "options_query": query,
            "detail_levels": network_map.DETAIL_LEVELS,
            "type_groups": _type_filter_groups(everything.nodes),
            "sites": network_map.site_names(overview, extra),
            "node_count": sum(1 for node in graph.nodes.values() if node.kind in {"asset", "item"}),
            "link_count": len(graph.edges),
            "endpoint_options": endpoint_options,
            "links": _link_rows(extra.get("links") or [], labels) if can_edit else [],
            "media": links_repo.MEDIA, "paper_sizes": PAPER_SIZES,
        })


@router.get("/network-map/export.svg", summary="Export the network map as SVG")
async def export_svg(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    _user, _membership, company, company_id, _can_edit = context
    options = network_map.MapOptions.from_params(request.query_params)
    graph, _overview, _extra = await _graph(company_id, options)
    svg = network_map.render_svg(
        graph, title="Network map", subtitle=network_map.subtitle((company or {}).get("name"), options))
    headers = {"Cache-Control": "no-store"}
    if request.query_params.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{_safe_filename(company, "svg")}"'
    await audit_service.record(action="network_map.export", request=request, entity_type="network_map",
                               after={"company_id": company_id, "format": "svg", "detail": options.detail})
    return Response(content='<?xml version="1.0" encoding="UTF-8"?>\n' + svg,
                    media_type="image/svg+xml", headers=headers)


@router.get("/network-map/export.pdf", summary="Export the network map as PDF")
async def export_pdf(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, company, company_id, _can_edit = context
    options = network_map.MapOptions.from_params(request.query_params)
    graph, _overview, _extra = await _graph(company_id, options)
    subtitle = network_map.subtitle((company or {}).get("name"), options)
    svg = network_map.render_svg(graph, title="Network map", subtitle=subtitle)
    width, height = _svg_size(svg)
    paper = str(request.query_params.get("paper") or "a3").lower()
    if paper not in PAPER_MM:
        paper = "a3"
    short, long = PAPER_MM[paper]
    page_w, page_h = (long, short) if width >= height else (short, long)
    # Scale the drawing to fit the page's printable area in both directions.
    avail_w, avail_h = page_w - 2 * PDF_MARGIN_MM, page_h - 2 * PDF_MARGIN_MM - 6
    map_width_mm = min(avail_w, avail_h * width / height)
    html = _routes()._main().templates.env.get_template("network_map/pdf.html").render(
        svg=Markup(_fit_svg(svg)), company=company, subtitle=subtitle, options=options,
        page_size=f"{page_w}mm {page_h}mm", map_width_mm=round(map_width_mm, 1),
        inventory=network_map.inventory(graph) if options.detail != "overview" else [],
        detail=options.detail,
    )
    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - depends on system packages
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF export requires WeasyPrint and its native dependencies.",
        ) from exc
    pdf_bytes = await asyncio.to_thread(lambda: HTML(string=html).write_pdf())
    await audit_service.record(action="network_map.export", request=request, entity_type="network_map",
                               user_id=int(user["id"]) if user.get("id") else None,
                               after={"company_id": company_id, "format": "pdf", "detail": options.detail})
    return Response(content=pdf_bytes, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_safe_filename(company, "pdf")}"',
        "Cache-Control": "no-store"})


def _svg_size(svg: str) -> tuple[float, float]:
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    return (float(match.group(1)), float(match.group(2))) if match else (1.0, 1.0)


def _fit_svg(svg: str) -> str:
    """Let the SVG scale to the PDF page instead of using its pixel size."""
    return re.sub(r'^<svg([^>]*?) width="[\d.]+" height="[\d.]+"', r'<svg\1 class="pdf-map"', svg, count=1)


def _next_url(form: Any, fallback: str) -> str:
    """Only same-site paths are followed after a form post."""
    target = str(form.get("next") or "")
    return target if target.startswith("/") and not target.startswith("//") else fallback


@router.post("/api/network-map/links", status_code=201, summary="Link two interfaces or rack ports")
async def create_link(request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    form = await request.form()
    target = _next_url(form, "/network-map#links")
    main_module = _routes()._main()
    try:
        a = links_repo.Endpoint.parse(form.get("a"))
        b = links_repo.Endpoint.parse(form.get("b"))
        link_id = await links_repo.create_link(company_id, a, b, dict(form))
    except ValueError as exc:
        return main_module.flash_redirect(target, str(exc), "error")
    await audit_service.record(action="network_map.link.create", request=request, user_id=int(user["id"]),
                               entity_type="network_link", entity_id=link_id,
                               after={"company_id": company_id, "a": str(a), "b": str(b),
                                      "medium": form.get("medium")})
    return main_module.flash_redirect(target, "Link documented.", "success")


@router.post("/api/network-map/links/{link_id}/delete", summary="Remove a documented link")
async def delete_link(request: Request, link_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    form = await request.form()
    await links_repo.delete_link(company_id, link_id)
    await audit_service.record(action="network_map.link.delete", request=request, user_id=int(user["id"]),
                               entity_type="network_link", entity_id=link_id, before={"company_id": company_id})
    return _routes()._main().flash_redirect(_next_url(form, "/network-map#links"), "Link removed.", "success")


def _ip_address_id(form: Any) -> int | None:
    raw = str(form.get("ip_address_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("Choose a documented IP address") from exc


@router.post("/api/network-map/interfaces", status_code=201, summary="Add a network or radio interface to an asset")
async def create_interface(request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    form = await request.form()
    main_module = _routes()._main()
    try:
        asset_id = int(str(form.get("asset_id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Choose an asset") from exc
    target = _next_url(form, f"/assets/{asset_id}#interfaces")
    try:
        interface_id = await links_repo.create_interface(company_id, asset_id, dict(form), _ip_address_id(form))
    except ValueError as exc:
        return main_module.flash_redirect(target, str(exc), "error")
    await audit_service.record(action="network_map.interface.create", request=request, user_id=int(user["id"]),
                               entity_type="asset_interface", entity_id=interface_id,
                               after={"company_id": company_id, "asset_id": asset_id, "kind": form.get("kind")})
    return main_module.flash_redirect(target, "Interface added.", "success")


@router.post("/api/network-map/interfaces/{interface_id}/edit", summary="Edit an asset interface")
async def update_interface(request: Request, interface_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    form = await request.form()
    main_module = _routes()._main()
    target = _next_url(form, "/network-map")
    try:
        await links_repo.update_interface(company_id, interface_id, dict(form), _ip_address_id(form))
    except ValueError as exc:
        return main_module.flash_redirect(target, str(exc), "error")
    await audit_service.record(action="network_map.interface.update", request=request, user_id=int(user["id"]),
                               entity_type="asset_interface", entity_id=interface_id,
                               after={"company_id": company_id, "kind": form.get("kind")})
    return main_module.flash_redirect(target, "Interface saved.", "success")


@router.post("/api/network-map/interfaces/{interface_id}/delete", summary="Remove an asset interface and its links")
async def delete_interface(request: Request, interface_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    form = await request.form()
    await links_repo.delete_interface(company_id, interface_id)
    await audit_service.record(action="network_map.interface.delete", request=request, user_id=int(user["id"]),
                               entity_type="asset_interface", entity_id=interface_id,
                               before={"company_id": company_id})
    return _routes()._main().flash_redirect(_next_url(form, "/network-map"), "Interface removed.", "success")


async def asset_interfaces_context(company_id: int, asset_id: int) -> dict[str, Any]:
    """Interfaces for the asset page, each with the far ends of its links."""
    interfaces = await links_repo.list_interfaces(company_id, asset_id)
    links = await links_repo.list_links(company_id)
    options = await links_repo.endpoint_options(company_id)
    labels = {item["value"]: item["label"] for item in options}
    for interface in interfaces:
        me = f"interface:{interface['id']}"
        interface["kind_label"] = links_repo.INTERFACE_KINDS.get(interface["kind"], interface["kind"])
        interface["is_radio"] = interface["kind"] in links_repo.RADIO_KINDS
        interface["radio_summary"] = network_map._radio_summary(interface) if interface["is_radio"] else ""
        interface["links"] = []
        for link in links:
            ends = (f"{link['a_kind']}:{link['a_id']}", f"{link['b_kind']}:{link['b_id']}")
            if me in ends:
                far = ends[1] if ends[0] == me else ends[0]
                interface["links"].append({
                    "id": link["id"], "peer": labels.get(far, "Removed endpoint"),
                    "medium": links_repo.MEDIA.get(link["medium"], link["medium"])})
    return {"interfaces": interfaces, "endpoint_options": options,
            "interface_kinds": links_repo.INTERFACE_KINDS, "radio_modes": links_repo.RADIO_MODES,
            "media": links_repo.MEDIA}
