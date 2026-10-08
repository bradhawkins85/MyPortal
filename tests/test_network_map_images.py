"""Detailed PDF identification: image priority, scoped sources and real pagination."""
import asyncio
import base64
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader
from starlette.datastructures import QueryParams

from app.features.network_map import routes
from app.repositories import rack_item_images as rack_repo
from app.services import asset_photos, network_map as nm, network_map_images as images_service, rack_item_images

ROOT = Path(__file__).resolve().parents[1]


def _graph(detail="detailed"):
    nodes = {
        "asset:1": nm.Node("asset:1", "asset", "Core switch", "switch", site="Office", rack_name="Comms A", facts=[("Serial", "SW001")]),
        "item:2": nm.Node("item:2", "item", "Patch panel", "patch_panel", site="Office", rack_name="Comms A"),
        "asset:3": nm.Node("asset:3", "asset", "Wireless bridge", "wireless_bridge", site="Warehouse"),
        "asset:4": nm.Node("asset:4", "asset", "No image device", "router", site="Office"),
        "internet": nm.Node("internet", "internet", "Internet", "router"),
    }
    return nm.Graph(nodes, [], ["Office", "Warehouse"], {}, nm.MapOptions(detail=detail, hide_unlinked=False))


def _write_image(path, colour="blue"):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    Image.new("RGB", (600, 240), colour).save(buffer, "PNG")
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


@pytest.fixture
def sources(monkeypatch, tmp_path):
    root = tmp_path / "private_uploads"
    monkeypatch.setattr(rack_item_images, "_UPLOADS_ROOT", root)
    monkeypatch.setattr(asset_photos, "ROOT", root / "asset-photos")
    product = _write_image(root / "product.png")
    _write_image(root / "device.png", "red")
    _write_image(root / "asset-photos/7/3/bridge.png", "green")
    rack_images = [
        {"equipment_id": 10, "asset_id": 1, "id": 100, "kind": "product", "storage_name": "product.png", "caption": "Front"},
        {"equipment_id": 11, "asset_id": 1, "id": 100, "kind": "product", "storage_name": "product.png", "caption": "Front"},
        {"equipment_id": 10, "asset_id": 1, "id": 101, "kind": "device", "storage_name": "device.png"},
        {"equipment_id": 2, "asset_id": None, "id": 102, "kind": "device", "storage_name": "device.png"},
        {"equipment_id": 99, "asset_id": 99, "id": 103, "kind": "product", "storage_name": "product.png"},
    ]
    rack_files = AsyncMock(return_value=rack_images)
    monkeypatch.setattr(rack_repo, "equipment_image_files", rack_files)
    photos = AsyncMock(side_effect=lambda company, asset, **kwargs: [
        {"storage_name": "bridge.png", "caption": "Installed bridge"}] if asset == 3 else [])
    monkeypatch.setattr(images_service.asset_photo_repo, "list_for_asset", photos)
    access = AsyncMock(return_value=False)
    return SimpleNamespace(root=root, product=product, rack_images=rack_images, rack_files=rack_files,
                           photos=photos, access=access)


def test_prefers_product_then_device_then_asset_photos_and_omits_missing(sources):
    entries = asyncio.run(images_service.identification_images(7, _graph(), asset_photo_access=sources.access))
    by_name = {entry["name"]: entry for entry in entries}
    assert set(by_name) == {"Core switch", "Patch panel", "Wireless bridge"}
    assert by_name["Core switch"]["source"] == "Product image"
    assert len(by_name["Core switch"]["images"]) == 1  # Shared placements never duplicate a device or image.
    assert by_name["Core switch"]["images"][0]["src"] == "data:image/png;base64," + base64.b64encode(sources.product).decode()
    assert by_name["Patch panel"]["source"] == "Device image"
    assert by_name["Wireless bridge"]["source"] == "Asset photo"
    assert {call.args[0] for call in sources.access.await_args_list} == {3, 4}
    sources.photos.assert_any_await(7, 3, customer_only=False)


@pytest.mark.parametrize("detail", ["overview", "standard"])
def test_other_detail_levels_never_load_identification_images(sources, detail):
    assert asyncio.run(images_service.identification_images(7, _graph(detail), asset_photo_access=sources.access)) == []
    sources.rack_files.assert_not_awaited()
    sources.access.assert_not_awaited()


@pytest.mark.parametrize("missing", ["missing.png", "corrupt.png", "../../outside.png"])
def test_unavailable_product_image_falls_back_to_device(sources, missing):
    (sources.root / "corrupt.png").write_bytes(b"not an image")
    _write_image(sources.root.parent.parent / "outside.png")
    sources.rack_images[0]["storage_name"] = missing
    sources.rack_images[1]["storage_name"] = missing
    entries = asyncio.run(images_service.identification_images(7, _graph(), asset_photo_access=sources.access))
    assert next(entry for entry in entries if entry["name"] == "Core switch")["source"] == "Device image"


def test_filters_follow_visible_nodes_and_asset_photo_access(sources):
    graph = _graph()
    graph.nodes.pop("asset:1")
    sources.access.return_value = None
    entries = asyncio.run(images_service.identification_images(7, graph, asset_photo_access=sources.access))
    assert [entry["name"] for entry in entries] == ["Patch panel"]
    sources.photos.assert_not_awaited()


def test_customer_photo_visibility_and_rack_access_are_preserved(sources):
    sources.access.return_value = True
    entries = asyncio.run(images_service.identification_images(
        7, _graph(), allow_rack_images=False, asset_photo_access=sources.access))
    assert [entry["name"] for entry in entries] == ["Wireless bridge"]
    sources.rack_files.assert_not_awaited()
    sources.photos.assert_any_await(7, 3, customer_only=True)


def test_multiple_product_images_are_kept(sources):
    sources.rack_images.append({"equipment_id": 10, "asset_id": 1, "id": 104, "kind": "product", "storage_name": "device.png"})
    entries = asyncio.run(images_service.identification_images(7, _graph()))
    assert len(next(entry for entry in entries if entry["name"] == "Core switch")["images"]) == 2


def test_file_lookup_scopes_attachments_items_and_images(monkeypatch):
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(rack_repo.db, "fetch_all", fetch)
    assert asyncio.run(rack_repo.equipment_image_files(7)) == []
    query, params = fetch.await_args.args
    assert "a.company_id=%s AND e.company_id=%s AND i.company_id=%s" in query
    assert params == (7, 7, 7)


def _environment():
    return Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True)


@pytest.mark.parametrize("detail, expected", [("overview", 0), ("standard", 0), ("detailed", 1)])
def test_pdf_template_only_lists_identification_in_detailed(sources, detail, expected):
    entries = asyncio.run(images_service.identification_images(7, _graph(), asset_photo_access=sources.access))
    html = _environment().get_template("network_map/pdf.html").render(
        detail=detail, identification=entries, inventory=[], svg="", page_size="A4", map_width_mm=180)
    assert html.count('<section class="identification">') == expected
    assert html.count('<img ') == (3 if expected else 0)
    empty = _environment().get_template("network_map/pdf.html").render(
        detail="detailed", identification=[], inventory=[], svg="", page_size="A4", map_width_mm=180)
    assert '<section class="identification">' not in empty


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", ["overview", "standard", "detailed"])
async def test_pdf_route_gates_image_loading_and_generates_real_pdf(monkeypatch, sources, tmp_path, detail):
    graph = _graph(detail)
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": 1}, {}, {"name": "Acme"}, 7, True)))
    monkeypatch.setattr(routes, "_graph", AsyncMock(return_value=(graph, {}, {})))
    entries = await images_service.identification_images(7, _graph(), asset_photo_access=sources.access)
    load_images = AsyncMock(return_value=entries)
    monkeypatch.setattr(routes, "_identification_images", load_images)
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    main = SimpleNamespace(templates=SimpleNamespace(env=_environment()))
    monkeypatch.setattr(routes, "_routes", lambda: SimpleNamespace(_main=lambda: main))
    request = SimpleNamespace(query_params=QueryParams({"detail": detail}))
    response = await routes.export_pdf(request)
    assert response.status_code == 200
    assert response.body.startswith(b"%PDF-")
    if detail == "detailed":
        load_images.assert_awaited_once_with(request, 7, graph)
    else:
        load_images.assert_not_awaited()
    pdf = tmp_path / "map.pdf"
    pdf.write_bytes(response.body)
    if shutil.which("pdftotext"):
        result = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, check=True).stdout.decode()
        assert ("Device identification" in result) == (detail == "detailed")


@pytest.mark.asyncio
async def test_photo_permission_checks_are_used_by_export(monkeypatch):
    assets_routes = SimpleNamespace(
        _rack_image_context=AsyncMock(side_effect=HTTPException(403, "Denied")),
        _photo_context=AsyncMock(side_effect=[({"id": 1}, 7, False), HTTPException(404, "Hidden")]),
    )
    monkeypatch.setattr(routes, "_routes", lambda: assets_routes)

    async def check(company, graph, **kwargs):
        assert not kwargs["allow_rack_images"]
        assert await kwargs["asset_photo_access"](3) is True
        assert await kwargs["asset_photo_access"](4) is None
        return []

    monkeypatch.setattr(images_service, "identification_images", check)
    assert await routes._identification_images(SimpleNamespace(), 7, _graph()) == []


def test_identification_pages_paginate_and_keep_all_images(sources, tmp_path):
    from markupsafe import Markup
    from weasyprint import HTML

    graph = _graph()
    entries = asyncio.run(images_service.identification_images(7, graph, asset_photo_access=sources.access))
    # Multiple images for a device are grouped into pairs, so a large gallery
    # can continue onto later pages without an oversized, indivisible row.
    repeated = [{**entries[0], "name": "Identification device " + str(index),
                 "images": entries[0]["images"] * 5} for index in range(8)]
    html = _environment().get_template("network_map/pdf.html").render(
        detail="detailed", identification=repeated, inventory=nm.inventory(graph),
        svg=Markup(routes._sanitize_svg(routes._fit_svg(nm.render_svg(graph, title="Network map")))),
        company={"name": "Acme"}, subtitle="Acme — Detailed", page_size="420mm 297mm", map_width_mm=380)
    assert html.count('<img ') == 40
    document = HTML(string=html).render()
    assert len(document.pages) > 3
    assert document.pages[0].width > document.pages[0].height  # Map paper size remains independent.
    assert all(page.height > page.width for page in document.pages[1:])
    pdf = tmp_path / "identification.pdf"
    pdf.write_bytes(document.write_pdf())
    if shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, check=True).stdout.decode()
        for index in range(8):
            assert "Identification device " + str(index) in text
        assert "(continued)" in text
