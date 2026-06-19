"""Shop handlers for the ``shop`` feature pack."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

import aiomysql
from fastapi import File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.security.flash import flash_redirect


def _main():
    from app import main as main_module

    return main_module


def _form_bool(form: Any, key: str) -> bool:
    if hasattr(form, "getlist"):
        values = form.getlist(key)
        if values:
            for value in values:
                if isinstance(value, str):
                    if value.strip().lower() not in {"", "0", "false", "off"}:
                        return True
                elif bool(value):
                    return True
            return False
    value = form.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off"}
    return bool(value)


def _parse_freight_conditions(form: Any) -> list[dict[str, Any]]:
    conditions: dict[int, dict[str, str]] = {}
    for key, val in form.multi_items():
        if not key.startswith("conditions["):
            continue
        try:
            idx_end = key.index("]")
            idx = int(key[len("conditions["):idx_end])
            field_start = key.index("[", idx_end) + 1
            field_end = key.index("]", field_start)
            field = key[field_start:field_end]
        except (ValueError, IndexError):
            continue
        conditions.setdefault(idx, {})[field] = str(val)

    result: list[dict[str, Any]] = []
    for _, cond in sorted(conditions.items()):
        condition_type = cond.get("type", "").strip()
        operator = cond.get("operator", "equals").strip()
        value = cond.get("value", "").strip()
        if condition_type:
            result.append(
                {"type": condition_type, "operator": operator, "value": value}
            )
    return result


def _normalise_freight_conditions(
    *, is_default: bool, conditions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if is_default:
        return []
    return conditions


async def _validate_recommendation_product_ids(
    raw_ids: Sequence[int | str] | None,
    *,
    field_label: str,
    disallow_product_id: int | None = None,
) -> list[int]:
    from app.repositories import shop as shop_repo

    values: list[int] = []
    for raw in raw_ids or []:
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field_label.lower()} selection submitted",
            )
        if value <= 0:
            continue
        values.append(value)

    unique_ids = sorted(set(values))
    if not unique_ids:
        return []

    candidates = await shop_repo.list_products_by_ids(unique_ids, include_archived=False)
    found_ids = {int(candidate.get("id") or 0) for candidate in candidates}
    missing = [str(value) for value in unique_ids if value not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_label} selection is no longer available",
        )

    validated: list[int] = []
    for candidate in candidates:
        candidate_id = int(candidate.get("id") or 0)
        if disallow_product_id is not None and candidate_id == disallow_product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_label} products cannot include the item being edited",
            )
        if bool(candidate.get("archived")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_label} selection is archived and cannot be used",
            )
        validated.append(candidate_id)

    return sorted(validated)


async def _resolve_related_product_id_by_sku(sku: str | None) -> int | None:
    """Look up a related product identifier from a SKU value."""

    from app.repositories import shop as shop_repo

    if sku in (None, ""):
        return None

    candidate = str(sku).strip()
    if not candidate:
        return None

    product = await shop_repo.get_product_by_sku(candidate, include_archived=True)
    if not product:
        return None

    try:
        product_id = int(product.get("id") or 0)
    except (TypeError, ValueError):
        return None

    return product_id or None


def _normalise_related_product_inputs(raw: Any) -> list[int | str]:
    """Normalise related product identifiers from mixed FastAPI form inputs."""

    from fastapi.params import Form as FormField

    if isinstance(raw, FormField):
        raw = raw.default

    if raw in (None, ""):
        return []

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return list(raw)

    return [raw]


def _strip_internal_shop_product_fields(products: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove internal-only product fields before sending customer-facing JSON."""
    hidden_fields = {"buy_price", "vendor_sku"}
    return [
        {key: value for key, value in product.items() if key not in hidden_fields}
        for product in products
    ]


async def shop_page(
    request: Request,
    category: str | None = Query(None),
    show_out_of_stock: bool = Query(False, alias="showOutOfStock"),
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, alias="pageSize", ge=1, le=100),
    cart_error: str | None = None,
):
    from app.repositories import shop as shop_repo
    from app.repositories import subscriptions as subscriptions_repo
    from app.services import shop as shop_service
    from app.services import shop_packages as shop_packages_service

    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _main()._load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        return redirect
    search_term = (q or "").strip()
    effective_search = search_term or None
    search_term_lower = effective_search.lower() if effective_search else None

    category_param = category.strip() if isinstance(category, str) and category.strip() else None
    show_packages = False
    category_id: int | None = None
    if category_param:
        if category_param.lower() == "packages":
            show_packages = True
        else:
            try:
                parsed_category = int(category_param)
            except (TypeError, ValueError):
                parsed_category = None
            if parsed_category is not None and parsed_category > 0:
                category_id = parsed_category

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)

    categories_task = asyncio.create_task(shop_repo.list_categories())
    available_category_ids_task = asyncio.create_task(
        shop_repo.get_category_ids_with_available_products(
            company_id=company_id,
            include_out_of_stock=show_out_of_stock,
        )
    )

    products: list[dict[str, Any]]
    total_count = 0
    if show_packages:
        packages = await shop_packages_service.load_company_packages(
            company_id=company_id,
            is_vip=is_vip,
        )

        def _package_matches_search(package: Mapping[str, Any]) -> bool:
            if not search_term_lower:
                return True
            candidate_fields: list[str] = [
                str(package.get("name") or ""),
                str(package.get("sku") or ""),
            ]
            for item in package.get("items") or []:
                candidate_fields.append(str(item.get("product_name") or ""))
                candidate_fields.append(str(item.get("product_sku") or ""))
            return any(search_term_lower in field.lower() for field in candidate_fields if field)

        products = []
        for package in packages:
            if package.get("archived"):
                continue
            if package.get("is_restricted"):
                continue
            items = package.get("items") or []
            if not items:
                continue
            if not _package_matches_search(package):
                continue

            stock_level = int(package.get("stock_level") or 0)
            if not show_out_of_stock and stock_level <= 0:
                continue

            price_total = package.get("price_total")
            try:
                price_value = Decimal(str(price_total))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if price_value <= 0:
                continue
            products.append(
                {
                    "id": package.get("id"),
                    "name": package.get("name"),
                    "sku": package.get("sku"),
                    "price": price_value,
                    "stock": stock_level,
                    "is_package": True,
                    "items": items,
                    "product_count": int(package.get("product_count") or 0),
                }
            )

        products.sort(key=lambda entry: str(entry.get("name") or "").lower())
        total_count = len(products)
        offset = (page - 1) * page_size
        products = products[offset: offset + page_size]
    else:
        # Get category IDs to filter by (including descendants)
        category_ids = None
        if category_id is not None:
            category_ids = await shop_repo.get_category_descendants(category_id)

        filters = shop_repo.ProductFilters(
            include_archived=False,
            company_id=company_id,
            category_ids=category_ids,
            search_term=effective_search,
            in_stock_only=not show_out_of_stock,
            sort="name_asc",
        )

        products = await shop_repo.list_products_summary(filters)

        products = [
            product
            for product in products
            if not shop_service.is_price_below_dbp_threshold(product, is_vip=is_vip)
        ]

        if is_vip:
            for product in products:
                vip_price = product.get("vip_price")
                if vip_price is not None:
                    product["price"] = vip_price

        def _product_has_price(product: Mapping[str, Any]) -> bool:
            raw_price = product.get("price")
            if raw_price is None:
                return False
            try:
                return Decimal(str(raw_price)) > 0
            except (InvalidOperation, TypeError, ValueError):
                return False

        products = [product for product in products if _product_has_price(product)]
        total_count = len(products)
        offset = (page - 1) * page_size
        products = products[offset: offset + page_size]

    products = _strip_internal_shop_product_fields(products)
    products = cast(list[dict[str, Any]], _main()._serialise_for_json(products))

    categories = await categories_task
    available_category_ids = await available_category_ids_task

    def _filter_categories(
        cats: list[dict[str, Any]], available_ids: set[int]
    ) -> list[dict[str, Any]]:
        result = []
        for cat in cats:
            filtered_children = _filter_categories(cat.get("children", []), available_ids)
            if cat["id"] in available_ids or filtered_children:
                result.append({**cat, "children": filtered_children})
        return result

    categories = _filter_categories(categories, available_category_ids)

    # Get active subscription product IDs for the customer
    active_subscription_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)

    extra = {
        "title": "Shop",
        "categories": categories,
        "products": products,
        "current_category": "packages" if show_packages else category_id,
        "show_packages": show_packages,
        "show_out_of_stock": show_out_of_stock,
        "search_term": search_term,
        "cart_error": cart_error,
        "low_stock_threshold": _main().SHOP_LOW_STOCK_THRESHOLD,
        "active_subscription_product_ids": active_subscription_product_ids,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total_count / page_size)) if page_size else 1,
    }
    return await _main()._render_template("shop/index.html", request, user, extra=extra)


async def shop_product_detail_api(request: Request, product_id: int):
    from app.repositories import shop as shop_repo

    (
        _user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _main()._load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, company_id=company_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    product = _public_shop_product_payload(product, is_vip=is_vip)

    return JSONResponse(content=cast(dict[str, Any], _main()._serialise_for_json(product)))


def _public_shop_product_payload(product: Mapping[str, Any], *, is_vip: bool) -> dict[str, Any]:
    payload = {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "description": product.get("description"),
        "image_url": product.get("image_url"),
        "price": product.get("price"),
        "vip_price": product.get("vip_price"),
        "stock": product.get("stock"),
        "stock_nsw": product.get("stock_nsw"),
        "stock_qld": product.get("stock_qld"),
        "stock_vic": product.get("stock_vic"),
        "stock_sa": product.get("stock_sa"),
        "category_id": product.get("category_id"),
        "category_name": product.get("category_name"),
        "features": product.get("features") or [],
        "cross_sell_products": product.get("cross_sell_products") or [],
        "cross_sell_product_ids": product.get("cross_sell_product_ids") or [],
        "upsell_products": product.get("upsell_products") or [],
        "upsell_product_ids": product.get("upsell_product_ids") or [],
    }

    if is_vip and payload.get("vip_price") is not None:
        payload["price"] = payload["vip_price"]
    return payload


async def admin_shop_product_search_api(
    request: Request,
    q: str = Query("", min_length=1),
    limit: int = Query(10, ge=1, le=25),
):
    from app.repositories import shop as shop_repo

    _current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    results = await shop_repo.search_products_for_admin_lookup(q, limit=limit)
    return JSONResponse(content=cast(list[dict[str, Any]], _main()._serialise_for_json(results)))


async def admin_shop_product_restrictions_api(request: Request, product_id: int):
    from app.repositories import shop as shop_repo

    _current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    restrictions = await shop_repo.list_product_restrictions_for_product(product_id)
    return JSONResponse(content=cast(list[dict[str, Any]], _main()._serialise_for_json(restrictions)))


async def admin_shop_product_detail_api(request: Request, product_id: int):
    from app.repositories import shop as shop_repo

    _current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return JSONResponse(content=cast(dict[str, Any], _main()._serialise_for_json(product)))


async def admin_shop_product_price_history_api(request: Request, product_id: int):
    from app.repositories import shop as shop_repo
    from app.repositories import stock_feed as stock_feed_repo

    _current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    vendor_sku = str(product.get("vendor_sku") or "").strip()
    if not vendor_sku:
        return JSONResponse(content=[])

    history = await stock_feed_repo.get_price_history(vendor_sku)
    return JSONResponse(content=cast(list[dict[str, Any]], _main()._serialise_for_json(history)))


async def shop_packages_page(
    request: Request,
    cart_error: str | None = None,
):
    from app.services import shop_packages as shop_packages_service

    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _main()._load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        return redirect

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    packages = await shop_packages_service.load_company_packages(
        company_id=company_id,
        is_vip=is_vip,
    )

    packages_json = cast(list[dict[str, Any]], _main()._serialise_for_json(packages))

    extra = {
        "title": "Shop packages",
        "packages": packages,
        "packages_json": packages_json,
        "cart_error": cart_error,
        "low_stock_threshold": _main().SHOP_LOW_STOCK_THRESHOLD,
    }
    return await _main()._render_template("shop/packages.html", request, user, extra=extra)


async def admin_company_shop_items_api(request: Request, company_id: int):
    """Return all non-archived shop products with their hidden status for a company."""
    from app.repositories import companies as company_repo
    from app.repositories import shop as shop_repo

    _current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    products = await shop_repo.list_products_with_exclusion_status_for_company(company_id)
    return JSONResponse(content=cast(list[dict[str, Any]], _main()._serialise_for_json(products)))


async def admin_update_company_shop_visibility(
    request: Request,
    company_id: int,
    hidden: list[str] = Form(default=[]),
):
    from app.repositories import companies as company_repo
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    hidden_product_ids: set[int] = set()
    for value in hidden:
        if value in (None, ""):
            continue
        try:
            hidden_product_ids.add(int(value))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid product ID: {value!r}",
            )

    await shop_repo.replace_company_exclusions(company_id, hidden_product_ids)

    _main().log_info(
        "Company shop visibility updated",
        company_id=company_id,
        hidden_product_count=len(hidden_product_ids),
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.company.visibility_change",
        request=request,
        entity_type="company",
        entity_id=company_id,
        after={"hidden_product_ids": sorted(hidden_product_ids)},
    )
    return _main()._company_edit_redirect(
        company_id=company_id,
        success="Shop item visibility saved.",
    )


async def admin_shop_packages_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
):
    from app.services import shop_packages as shop_packages_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    packages = await shop_packages_service.load_admin_packages(
        include_archived=show_archived,
    )

    extra = {
        "title": "Package admin",
        "packages": packages,
        "show_archived": show_archived,
    }
    return await _main()._render_template("admin/shop_packages.html", request, current_user, extra=extra)


async def admin_shop_package_detail(request: Request, package_id: int):
    from app.repositories import shop as shop_repo
    from app.services import shop_packages as shop_packages_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    package = await shop_packages_service.get_package_detail(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    products = await shop_repo.list_all_products(include_archived=False)

    extra = {
        "title": f"Manage package: {package['name']}",
        "package": package,
        "products": products,
    }
    return await _main()._render_template("admin/shop_package_detail.html", request, current_user, extra=extra)


async def admin_create_shop_package(
    request: Request,
    name: str = Form(...),
    sku: str = Form(...),
    description: str | None = Form(default=None),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    cleaned_sku = sku.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package name cannot be empty")
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package SKU cannot be empty")

    try:
        package_id = await shop_repo.create_package(
            sku=cleaned_sku,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A package with that SKU already exists."
        else:
            detail = "Unable to create package."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    _main().log_info(
        "Shop package created",
        package_id=package_id,
        sku=cleaned_sku,
        name=cleaned_name,
        created_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_create(
        action="shop.package.create",
        request=request,
        entity_type="shop.package",
        entity_id=int(package_id) if package_id else None,
        after={
            "id": package_id,
            "sku": cleaned_sku,
            "name": cleaned_name,
            "description": cleaned_description,
        },
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


async def admin_update_shop_package(
    request: Request,
    package_id: int,
    name: str = Form(...),
    sku: str = Form(...),
    description: str | None = Form(default=None),
    archived: str | None = Form(default=None),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    existing_package = await shop_repo.get_package(package_id, include_archived=True)
    if not existing_package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    cleaned_name = name.strip()
    cleaned_sku = sku.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package name cannot be empty")
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package SKU cannot be empty")

    try:
        updated = await shop_repo.update_package(
            package_id,
            sku=cleaned_sku,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A package with that SKU already exists."
        else:
            detail = "Unable to update package."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    archived_flag = bool(archived and archived != "0")
    await shop_repo.set_package_archived(package_id, archived=archived_flag)

    _main().log_info(
        "Shop package updated",
        package_id=package_id,
        sku=cleaned_sku,
        archived=archived_flag,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.update",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before={
            "sku": existing_package.get("sku"),
            "name": existing_package.get("name"),
            "description": existing_package.get("description"),
            "archived": bool(existing_package.get("archived")),
        },
        after={
            "sku": cleaned_sku,
            "name": cleaned_name,
            "description": cleaned_description,
            "archived": archived_flag,
        },
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_archive_shop_package(
    request: Request,
    package_id: int,
    archived: str = Form(...),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    archived_flag = bool(archived and archived != "0")
    package = await shop_repo.get_package(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    await shop_repo.set_package_archived(package_id, archived=archived_flag)

    _main().log_info(
        "Shop package archived" if archived_flag else "Shop package restored",
        package_id=package_id,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.archive" if archived_flag else "shop.package.unarchive",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before={"archived": bool(package.get("archived"))},
        after={"archived": archived_flag},
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


async def admin_delete_shop_package(request: Request, package_id: int):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    package = await shop_repo.get_package(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    deleted = await shop_repo.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    _main().log_info(
        "Shop package deleted",
        package_id=package_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_delete(
        action="shop.package.delete",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before=package,
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


async def admin_add_package_item(
    request: Request,
    package_id: int,
    product_id: str = Form(...),
    quantity: str = Form(...),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        product_identifier = int(product_id)
        quantity_value = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid product or quantity")

    if quantity_value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1")

    product = await shop_repo.get_product_by_id(product_identifier, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    await shop_repo.upsert_package_item(
        package_id=package_id,
        product_id=product_identifier,
        quantity=quantity_value,
    )

    _main().log_info(
        "Shop package item added",
        package_id=package_id,
        product_id=product_identifier,
        quantity=quantity_value,
        added_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.item.add",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_identifier, "quantity": quantity_value},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_update_package_item(
    request: Request,
    package_id: int,
    product_id: int,
    quantity: str = Form(...),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        quantity_value = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid quantity")

    if quantity_value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1")

    await shop_repo.upsert_package_item(
        package_id=package_id,
        product_id=product_id,
        quantity=quantity_value,
    )

    _main().log_info(
        "Shop package item updated",
        package_id=package_id,
        product_id=product_id,
        quantity=quantity_value,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.item.update",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_id, "quantity": quantity_value},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_add_package_item_alternate(
    request: Request,
    package_id: int,
    product_id: int,
    alternate_product_id: str = Form(...),
    priority: str = Form("0"),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        alternate_id = int(alternate_product_id)
        primary_id = int(product_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid product selection")

    if primary_id == alternate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alternate product must differ from the primary product",
        )

    try:
        priority_value = int(priority) if priority is not None else 0
    except (TypeError, ValueError):
        priority_value = 0

    alternate_product = await shop_repo.get_product_by_id(
        alternate_id,
        include_archived=True,
    )
    if not alternate_product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alternate product not found")

    success = await shop_repo.upsert_package_item_alternate(
        package_id=package_id,
        product_id=primary_id,
        alternate_product_id=alternate_id,
        priority=priority_value,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package item not found")

    _main().log_info(
        "Shop package alternate assigned",
        package_id=package_id,
        product_id=primary_id,
        alternate_product_id=alternate_id,
        priority=priority_value,
        added_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.item.alternate.add",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={
            "product_id": primary_id,
            "alternate_product_id": alternate_id,
            "priority": priority_value,
        },
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_remove_package_item_alternate(
    request: Request,
    package_id: int,
    product_id: int,
    alternate_product_id: int,
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    removed = await shop_repo.remove_package_item_alternate(
        package_id,
        product_id,
        alternate_product_id,
    )
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alternate product not found")

    _main().log_info(
        "Shop package alternate removed",
        package_id=package_id,
        product_id=product_id,
        alternate_product_id=alternate_product_id,
        removed_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.item.alternate.remove",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={
            "product_id": product_id,
            "alternate_product_id": alternate_product_id,
        },
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_remove_package_item(
    request: Request,
    package_id: int,
    product_id: int,
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.remove_package_item(package_id, product_id)

    _main().log_info(
        "Shop package item removed",
        package_id=package_id,
        product_id=product_id,
        removed_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.package.item.remove",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_id},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_shop_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
):
    from app.repositories import companies as company_repo
    from app.repositories import shop as shop_repo
    from app.repositories import stock_feed as stock_feed_repo
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import shop as shop_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    filter_categories_task = asyncio.create_task(shop_repo.list_categories_with_products())
    offset = (page - 1) * page_size
    filters = shop_repo.ProductFilters(
        include_archived=show_archived,
        limit=page_size,
        offset=offset,
        sort="name_asc",
    )
    products_task = asyncio.create_task(
        shop_repo.list_products_summary(filters)
    )
    total_count_task = asyncio.create_task(shop_repo.count_products(filters))
    companies_task = asyncio.create_task(company_repo.list_companies())
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, filter_categories, products, total_count, companies, subscription_categories = await asyncio.gather(
        categories_task,
        filter_categories_task,
        products_task,
        total_count_task,
        companies_task,
        subscription_categories_task,
    )

    for product in products:
        product["price_below_threshold"] = shop_service.is_price_below_dbp_threshold(
            product, is_vip=False
        )
        product["vip_price_below_threshold"] = product.get(
            "vip_price"
        ) is not None and shop_service.is_price_below_dbp_threshold(product, is_vip=True)
        _profit = shop_service.calculate_profit(product, is_vip=False)
        product["profit"] = float(_profit) if _profit is not None else None
        _vip_profit = shop_service.calculate_profit(product, is_vip=True)
        product["vip_profit"] = float(_vip_profit) if _vip_profit is not None else None

    # Collect the SKU used for price-history look-ups (vendor_sku preferred).
    history_skus = [
        product["vendor_sku"] or product["sku"]
        for product in products
        if product.get("vendor_sku") or product.get("sku")
    ]
    dbp_trends: dict[str, str | None] = {}
    if history_skus:
        dbp_trends = await stock_feed_repo.get_recent_dbp_trends(history_skus)
    for product in products:
        lookup_sku = product.get("vendor_sku") or product.get("sku") or ""
        product["dbp_trend"] = dbp_trends.get(lookup_sku)

    extra = {
        "title": "Shop admin",
        "categories": categories,
        "filter_categories": filter_categories,
        "products": products,
        "all_companies": companies,
        "show_archived": show_archived,
        "subscription_categories": subscription_categories,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total_count / page_size)) if page_size else 1,
    }
    return await _main()._render_template("admin/shop.html", request, current_user, extra=extra)


async def admin_shop_freight_rules_page(
    request: Request,
    rule_id: int | None = Query(default=None, alias="ruleId"),
):
    from app.repositories import freight_rules as freight_rules_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    rules = await freight_rules_repo.list_rules()
    editing_rule = None
    if rule_id is not None:
        editing_rule = await freight_rules_repo.get_rule(rule_id)

    extra = {
        "title": "Freight rules",
        "rules": rules,
        "editing_rule": editing_rule,
    }
    return await _main()._render_template(
        "admin/shop_freight_rules.html",
        request,
        current_user,
        extra=extra,
    )


async def admin_create_shop_freight_rule(request: Request):
    from app.repositories import freight_rules as freight_rules_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return flash_redirect(
            "/admin/shop/freight-rules",
            "Rule name is required.",
            "error",
        )

    try:
        priority = int(form.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    try:
        freight_amount = Decimal(str(form.get("freight_amount") or "0")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError):
        return flash_redirect(
            "/admin/shop/freight-rules",
            "Freight amount must be a valid number.",
            "error",
        )
    is_default = _form_bool(form, "is_default")
    stop_processing = _form_bool(form, "stop_processing")
    is_active = _form_bool(form, "is_active")
    conditions = _normalise_freight_conditions(
        is_default=is_default,
        conditions=_parse_freight_conditions(form),
    )

    await freight_rules_repo.create_rule(
        name=name,
        priority=priority,
        is_default=is_default,
        stop_processing=stop_processing,
        freight_amount=freight_amount,
        conditions=conditions,
        is_active=is_active,
    )
    return flash_redirect(
        "/admin/shop/freight-rules",
        "Freight rule created.",
        "success",
    )


async def admin_update_shop_freight_rule(request: Request, rule_id: int):
    from app.repositories import freight_rules as freight_rules_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    existing = await freight_rules_repo.get_rule(rule_id)
    if not existing:
        return flash_redirect(
            "/admin/shop/freight-rules",
            "Rule not found.",
            "error",
        )

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return flash_redirect(
            f"/admin/shop/freight-rules?ruleId={rule_id}",
            "Rule name is required.",
            "error",
        )

    try:
        priority = int(form.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    try:
        freight_amount = Decimal(str(form.get("freight_amount") or "0")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError):
        return flash_redirect(
            f"/admin/shop/freight-rules?ruleId={rule_id}",
            "Freight amount must be a valid number.",
            "error",
        )
    is_default = _form_bool(form, "is_default")
    stop_processing = _form_bool(form, "stop_processing")
    is_active = _form_bool(form, "is_active")
    conditions = _normalise_freight_conditions(
        is_default=is_default,
        conditions=_parse_freight_conditions(form),
    )

    await freight_rules_repo.update_rule(
        rule_id,
        name=name,
        priority=priority,
        is_default=is_default,
        stop_processing=stop_processing,
        freight_amount=freight_amount,
        conditions=conditions,
        is_active=is_active,
    )
    return flash_redirect(
        "/admin/shop/freight-rules",
        "Freight rule updated.",
        "success",
    )


async def admin_delete_shop_freight_rule(request: Request, rule_id: int):
    from app.repositories import freight_rules as freight_rules_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await freight_rules_repo.delete_rule(rule_id)
    return flash_redirect(
        "/admin/shop/freight-rules",
        "Freight rule deleted.",
        "success",
    )


async def admin_shop_optional_accessories_page(
    request: Request, show: str = "pending"
):
    from app.repositories import shop as shop_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    if show not in ("pending", "dismissed"):
        show = "pending"

    show_dismissed = show == "dismissed"
    if show_dismissed:
        accessories = await shop_repo.list_dismissed_optional_accessories()
    else:
        accessories = await shop_repo.list_pending_optional_accessories()

    extra = {
        "title": "Optional accessories",
        "accessories": accessories,
        "show_dismissed": show_dismissed,
    }
    return await _main()._render_template(
        "admin/shop_optional_accessories.html", request, current_user, extra=extra
    )


async def admin_sync_optional_accessories(request: Request):
    """Re-scan the stock feed and refresh the pending optional accessories table."""
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.sync_pending_optional_accessories()
    await audit_service.record(
        action="shop.optional_accessory.sync",
        request=request,
        entity_type="shop.optional_accessory",
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_import_optional_accessory(
    request: Request, accessory_id: int
):
    """Import a pending optional accessory from the staging table into shop_products."""
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service
    from app.services import products as products_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    accessory = await shop_repo.get_pending_optional_accessory(accessory_id)
    if not accessory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending optional accessory not found",
        )

    imported = await products_service.import_product_by_vendor_sku(accessory["sku"])
    if imported:
        await shop_repo.dismiss_pending_optional_accessory(accessory_id)

    await audit_service.record(
        action="shop.optional_accessory.import",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
        metadata={"vendor_sku": accessory["sku"], "imported": bool(imported)},
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_dismiss_optional_accessory(
    request: Request, accessory_id: int
):
    """Soft-dismiss a pending optional accessory without importing."""
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.dismiss_pending_optional_accessory(accessory_id)
    await audit_service.record(
        action="shop.optional_accessory.dismiss",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_bulk_dismiss_optional_accessories(request: Request):
    """Soft-dismiss multiple pending optional accessories at once."""
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("accessory_ids")
    ids: list[int] = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (ValueError, TypeError):
            pass

    if ids:
        await shop_repo.bulk_dismiss_pending_optional_accessories(ids)
    await audit_service.record(
        action="shop.optional_accessory.bulk_dismiss",
        request=request,
        entity_type="shop.optional_accessory",
        metadata={"accessory_ids": ids, "count": len(ids)},
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_restore_optional_accessory(
    request: Request, accessory_id: int
):
    """Restore a dismissed optional accessory back to pending."""
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.restore_dismissed_optional_accessory(accessory_id)
    await audit_service.record(
        action="shop.optional_accessory.restore",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories?show=dismissed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_shop_categories_page(request: Request):
    from app.repositories import shop as shop_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    categories = await shop_repo.list_all_categories_flat()

    extra = {
        "title": "Product categories",
        "categories": categories,
    }
    return await _main()._render_template("admin/shop_categories.html", request, current_user, extra=extra)


async def admin_shop_product_create_page(request: Request):
    from app.repositories import shop as shop_repo
    from app.repositories import subscription_categories as subscription_categories_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    products_task = asyncio.create_task(
        shop_repo.list_products_summary(shop_repo.ProductFilters(include_archived=False))
    )
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, products, subscription_categories = await asyncio.gather(
        categories_task, products_task, subscription_categories_task
    )

    extra = {
        "title": "Add product",
        "categories": categories,
        "products": products,
        "subscription_categories": subscription_categories,
        "product_restrictions": [],
    }
    return await _main()._render_template(
        "admin/shop_product_create.html", request, current_user, extra=extra
    )


async def admin_create_shop_category(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    # Convert empty string to None for parent_id
    parsed_parent_id: int | None = None
    if parent_id and parent_id.strip():
        try:
            parsed_parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parent category")

    try:
        category_id = await shop_repo.create_category(cleaned_name, parent_id=parsed_parent_id)
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A category with that name already exists."
        else:
            detail = "Unable to create category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    _main().log_info(
        "Shop category created",
        category_id=category_id,
        name=cleaned_name,
        parent_id=parsed_parent_id,
        created_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_create(
        action="shop.category.create",
        request=request,
        entity_type="shop.category",
        entity_id=int(category_id) if category_id else None,
        after={"id": category_id, "name": cleaned_name, "parent_id": parsed_parent_id},
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_delete_shop_category(request: Request, category_id: int):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    category = await shop_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    deleted = await shop_repo.delete_category(category_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    _main().log_info(
        "Shop category deleted",
        category_id=category_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_delete(
        action="shop.category.delete",
        request=request,
        entity_type="shop.category",
        entity_id=category_id,
        before=category,
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_update_shop_category(
    request: Request,
    category_id: int,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    category = await shop_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    # Convert empty string to None for parent_id
    parsed_parent_id: int | None = None
    if parent_id and parent_id.strip():
        try:
            parsed_parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parent category")

    # Prevent setting itself as parent or creating circular reference
    if parsed_parent_id == category_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A category cannot be its own parent")

    # Check if the new parent is a descendant of the category (which would create a circular reference)
    if parsed_parent_id is not None:
        all_categories = await shop_repo.list_all_categories_flat()

        # Build a map of category children
        children_map: dict[int, list[int]] = {}
        for cat in all_categories:
            parent = cat.get("parent_id")
            if parent is not None:
                children_map.setdefault(parent, []).append(cat["id"])

        def get_all_descendants(cat_id: int, visited: set[int]) -> set[int]:
            """Get all descendants of a category."""
            # Prevent infinite loops by skipping already-visited nodes
            if cat_id in visited:
                return set()

            visited.add(cat_id)
            descendants = set()

            for child_id in children_map.get(cat_id, []):
                descendants.add(child_id)
                descendants.update(get_all_descendants(child_id, visited))

            return descendants

        # Check if the new parent is in the descendants of the current category
        descendants = get_all_descendants(category_id, set())
        if parsed_parent_id in descendants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot move a category into one of its own descendants"
            )

    try:
        updated = await shop_repo.update_category(
            category_id,
            cleaned_name,
            parent_id=parsed_parent_id,
            display_order=category.get("display_order", 0),
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A category with that name already exists."
        else:
            detail = "Unable to update category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    _main().log_info(
        "Shop category updated",
        category_id=category_id,
        name=cleaned_name,
        parent_id=parsed_parent_id,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.category.update",
        request=request,
        entity_type="shop.category",
        entity_id=category_id,
        before={"name": category.get("name"), "parent_id": category.get("parent_id")},
        after={"name": cleaned_name, "parent_id": parsed_parent_id},
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_shop_subscription_categories_page(request: Request):
    from app.repositories import subscription_categories as subscription_categories_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    categories = await subscription_categories_repo.list_categories()

    extra = {
        "title": "Subscription categories",
        "categories": categories,
    }
    return await _main()._render_template("admin/shop_subscription_categories.html", request, current_user, extra=extra)


async def admin_create_subscription_category(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
):
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    cleaned_description = description.strip() if description else None

    try:
        await subscription_categories_repo.create_category(cleaned_name, description=cleaned_description)
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A subscription category with that name already exists."
        else:
            detail = "Unable to create subscription category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    _main().log_info(
        "Subscription category created",
        name=cleaned_name,
        description=cleaned_description,
        created_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_create(
        action="shop.subscription_category.create",
        request=request,
        entity_type="shop.subscription_category",
        after={"name": cleaned_name, "description": cleaned_description},
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_delete_subscription_category(request: Request, category_id: int):
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    category = await subscription_categories_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription category not found")

    await subscription_categories_repo.delete_category(category_id)

    _main().log_info(
        "Subscription category deleted",
        category_id=category_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_delete(
        action="shop.subscription_category.delete",
        request=request,
        entity_type="shop.subscription_category",
        entity_id=category_id,
        before=category,
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_update_subscription_category(
    request: Request,
    category_id: int,
    name: str = Form(...),
    description: str = Form(""),
):
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    category = await subscription_categories_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription category not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    cleaned_description = description.strip() if description else None

    try:
        await subscription_categories_repo.update_category(
            category_id,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A subscription category with that name already exists."
        else:
            detail = "Unable to update subscription category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    _main().log_info(
        "Subscription category updated",
        category_id=category_id,
        name=cleaned_name,
        description=cleaned_description,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.subscription_category.update",
        request=request,
        entity_type="shop.subscription_category",
        entity_id=category_id,
        before={"name": category.get("name"), "description": category.get("description")},
        after={"name": cleaned_name, "description": cleaned_description},
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


async def admin_import_shop_product(
    request: Request,
    vendor_sku: str = Form(...),
):
    """Import a single product by vendor SKU using the stock feed."""
    from app.services import audit as audit_service
    from app.services import products as products_service


    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vendor SKU cannot be empty",
        )

    await products_service.import_product_by_vendor_sku(cleaned_vendor_sku)

    await audit_service.record(
        action="shop.product.import",
        request=request,
        entity_type="shop.product",
        metadata={"vendor_sku": cleaned_vendor_sku},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def admin_create_shop_product(
    request: Request,
    name: str = Form(...),
    sku: str = Form(...),
    vendor_sku: str = Form(...),
    description: str | None = Form(default=None),
    price: str = Form(...),
    stock: str = Form(...),
    vip_price: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    cross_sell_product_ids: list[int] | None = Form(default=None),
    upsell_product_ids: list[int] | None = Form(default=None),
    subscription_category_id: str | None = Form(default=None),
    commitment_type: str | None = Form(default=None),
    payment_frequency: str | None = Form(default=None),
    price_monthly_commitment: str | None = Form(default=None),
    price_annual_monthly_payment: str | None = Form(default=None),
    price_annual_annual_payment: str | None = Form(default=None),
):
    from app.repositories import shop as shop_repo
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product name cannot be empty")

    cleaned_sku = sku.strip()
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU cannot be empty")

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vendor SKU cannot be empty")

    description_value = description.strip() if description and description.strip() else None

    try:
        price_decimal = Decimal(price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be a valid number")
    if price_decimal < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be at least zero")

    try:
        stock_int = int(stock)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be a whole number")
    if stock_int < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be at least zero")

    vip_decimal: Decimal | None = None
    if vip_price not in (None, ""):
        try:
            vip_decimal = Decimal(vip_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be a valid number")
        if vip_decimal < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be at least zero")

    category_value: int | None = None
    if category_id:
        try:
            category_value = int(category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid category selection")
        category = await shop_repo.get_category(category_value)
        if not category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected category does not exist")

    subscription_category_value: int | None = None
    if subscription_category_id:
        try:
            subscription_category_value = int(subscription_category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription category selection")
        sub_category = await subscription_categories_repo.get_category(subscription_category_value)
        if not sub_category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected subscription category does not exist")

    # Validate commitment type and payment frequency for subscriptions
    commitment_value, payment_freq_value = _main()._validate_subscription_commitment_and_payment(
        subscription_category_value,
        commitment_type,
        payment_frequency,
    )

    # Parse pricing fields
    price_monthly_comm: Decimal | None = None
    if price_monthly_commitment not in (None, ""):
        try:
            price_monthly_comm = Decimal(price_monthly_commitment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_monthly_comm < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be a valid number")

    price_annual_monthly: Decimal | None = None
    if price_annual_monthly_payment not in (None, ""):
        try:
            price_annual_monthly = Decimal(price_annual_monthly_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_monthly < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be a valid number")

    price_annual_annual: Decimal | None = None
    if price_annual_annual_payment not in (None, ""):
        try:
            price_annual_annual = Decimal(price_annual_annual_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_annual < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be a valid number")

    cross_sell_ids = await _validate_recommendation_product_ids(
        cross_sell_product_ids,
        field_label="Cross-sell",
    )
    upsell_ids = await _validate_recommendation_product_ids(
        upsell_product_ids,
        field_label="Up-sell",
    )

    image_url: str | None = None
    stored_path: Path | None = None
    if image is not None:
        if image.filename:
            image_url, stored_path = await _main().store_product_image(
                upload=image,
                uploads_root=_main()._private_uploads_path,
                max_size=5 * 1024 * 1024,
            )
        else:
            await image.close()

    try:
        product = await shop_repo.create_product(
            name=cleaned_name,
            sku=cleaned_sku,
            vendor_sku=cleaned_vendor_sku,
            description=description_value,
            price=price_decimal,
            stock=stock_int,
            vip_price=vip_decimal,
            category_id=category_value,
            image_url=image_url,
            cross_sell_product_ids=cross_sell_ids,
            upsell_product_ids=upsell_ids,
            subscription_category_id=subscription_category_value,
            commitment_type=commitment_value,
            payment_frequency=payment_freq_value,
            price_monthly_commitment=price_monthly_comm,
            price_annual_monthly_payment=price_annual_monthly,
            price_annual_annual_payment=price_annual_annual,
        )
    except aiomysql.IntegrityError as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        if exc.args and exc.args[0] == 1062:
            detail = "A product with that SKU or vendor SKU already exists."
        else:
            detail = "Unable to create product."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        raise

    _main().log_info(
        "Shop product created",
        product_id=product["id"],
        sku=product["sku"],
        vendor_sku=product["vendor_sku"],
        created_by=current_user["id"] if current_user else None,
    )
    await audit_service.record_create(
        action="shop.product.create",
        request=request,
        entity_type="shop.product",
        entity_id=int(product["id"]),
        after=product,
        sensitive_extra_keys=("buy_price",),
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def admin_update_shop_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    sku: str = Form(...),
    vendor_sku: str = Form(...),
    description: str | None = Form(default=None),
    price: str = Form(...),
    stock: str = Form(...),
    vip_price: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    features: str | None = Form(default=None),
    cross_sell_product_ids: list[int] | None = Form(default=None),
    upsell_product_ids: list[int] | None = Form(default=None),
    cross_sell_sku: str | None = Form(default=None),
    upsell_sku: str | None = Form(default=None),
    subscription_category_id: str | None = Form(default=None),
    commitment_type: str | None = Form(default=None),
    payment_frequency: str | None = Form(default=None),
    price_monthly_commitment: str | None = Form(default=None),
    price_annual_monthly_payment: str | None = Form(default=None),
    price_annual_annual_payment: str | None = Form(default=None),
    scheduled_price: str | None = Form(default=None),
    scheduled_vip_price: str | None = Form(default=None),
    scheduled_buy_price: str | None = Form(default=None),
    price_change_date: str | None = Form(default=None),
):
    from app.repositories import shop as shop_repo
    from app.repositories import subscription_categories as subscription_categories_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product name cannot be empty")

    cleaned_sku = sku.strip()
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU cannot be empty")

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vendor SKU cannot be empty")

    description_value = description.strip() if description and description.strip() else None

    feature_payload: list[dict[str, Any]] | None = None
    if features not in (None, ""):
        try:
            raw_features = json.loads(features)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid feature payload",
            ) from exc
        if not isinstance(raw_features, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid feature payload",
            )
        parsed_features: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_features):
            if not isinstance(entry, Mapping):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid feature payload",
                )
            name_value = str(entry.get("name") or "").strip()
            value_value = str(entry.get("value") or "").strip()
            if not name_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Feature name cannot be empty",
                )
            parsed_features.append(
                {"name": name_value, "value": value_value, "position": index}
            )
        feature_payload = parsed_features

    try:
        price_decimal = Decimal(price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be a valid number")
    if price_decimal < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be at least zero")

    try:
        stock_int = int(stock)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be a whole number")
    if stock_int < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be at least zero")

    vip_decimal: Decimal | None = None
    if vip_price not in (None, ""):
        try:
            vip_decimal = Decimal(vip_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be a valid number")
        if vip_decimal < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be at least zero")

    category_value: int | None = None
    if category_id:
        try:
            category_value = int(category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid category selection")
        category = await shop_repo.get_category(category_value)
        if not category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected category does not exist")

    subscription_category_value: int | None = None
    if subscription_category_id:
        try:
            subscription_category_value = int(subscription_category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription category selection")
        sub_category = await subscription_categories_repo.get_category(subscription_category_value)
        if not sub_category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected subscription category does not exist")

    # Validate commitment type and payment frequency for subscriptions
    commitment_value, payment_freq_value = _main()._validate_subscription_commitment_and_payment(
        subscription_category_value,
        commitment_type,
        payment_frequency,
    )

    # Parse pricing fields
    price_monthly_comm: Decimal | None = None
    if price_monthly_commitment not in (None, ""):
        try:
            price_monthly_comm = Decimal(price_monthly_commitment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_monthly_comm < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be a valid number")

    price_annual_monthly: Decimal | None = None
    if price_annual_monthly_payment not in (None, ""):
        try:
            price_annual_monthly = Decimal(price_annual_monthly_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_monthly < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be a valid number")

    price_annual_annual: Decimal | None = None
    if price_annual_annual_payment not in (None, ""):
        try:
            price_annual_annual = Decimal(price_annual_annual_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_annual < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be a valid number")

    # Parse scheduled price change fields
    scheduled_price_decimal: Decimal | None = None
    if scheduled_price not in (None, ""):
        try:
            scheduled_price_decimal = Decimal(scheduled_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled price must be a valid number")

    scheduled_vip_price_decimal: Decimal | None = None
    if scheduled_vip_price not in (None, ""):
        try:
            scheduled_vip_price_decimal = Decimal(scheduled_vip_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_vip_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled VIP price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled VIP price must be a valid number")

    scheduled_buy_price_decimal: Decimal | None = None
    if scheduled_buy_price not in (None, ""):
        try:
            scheduled_buy_price_decimal = Decimal(scheduled_buy_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_buy_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled buy price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled buy price must be a valid number")

    # Parse price change date
    from datetime import datetime as dt
    price_change_date_value: Any | None = None
    if price_change_date and price_change_date.strip():
        try:
            price_change_date_value = dt.strptime(price_change_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price change date must be in YYYY-MM-DD format")

    previous_image_url = product.get("image_url")
    image_url = previous_image_url
    stored_path: Path | None = None
    if image is not None:
        if image.filename:
            image_url, stored_path = await _main().store_product_image(
                upload=image,
                uploads_root=_main()._private_uploads_path,
                max_size=5 * 1024 * 1024,
            )
        else:
            await image.close()

    cross_sell_candidates = _normalise_related_product_inputs(cross_sell_product_ids)
    resolved_cross_id = await _resolve_related_product_id_by_sku(cross_sell_sku)
    if resolved_cross_id:
        cross_sell_candidates.append(resolved_cross_id)

    cross_sell_ids = await _validate_recommendation_product_ids(
        cross_sell_candidates,
        field_label="Cross-sell",
        disallow_product_id=product_id,
    )
    upsell_candidates = _normalise_related_product_inputs(upsell_product_ids)
    resolved_upsell_id = await _resolve_related_product_id_by_sku(upsell_sku)
    if resolved_upsell_id:
        upsell_candidates.append(resolved_upsell_id)

    upsell_ids = await _validate_recommendation_product_ids(
        upsell_candidates,
        field_label="Up-sell",
        disallow_product_id=product_id,
    )

    try:
        updated = await shop_repo.update_product(
            product_id,
            name=cleaned_name,
            sku=cleaned_sku,
            vendor_sku=cleaned_vendor_sku,
            description=description_value,
            price=price_decimal,
            stock=stock_int,
            vip_price=vip_decimal,
            category_id=category_value,
            image_url=image_url,
            cross_sell_product_ids=cross_sell_ids,
            upsell_product_ids=upsell_ids,
            subscription_category_id=subscription_category_value,
            commitment_type=commitment_value,
            payment_frequency=payment_freq_value,
            price_monthly_commitment=price_monthly_comm,
            price_annual_monthly_payment=price_annual_monthly,
            price_annual_annual_payment=price_annual_annual,
            scheduled_price=scheduled_price_decimal,
            scheduled_vip_price=scheduled_vip_price_decimal,
            scheduled_buy_price=scheduled_buy_price_decimal,
            price_change_date=price_change_date_value,
        )
    except aiomysql.IntegrityError as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        if exc.args and exc.args[0] == 1062:
            detail = "A product with that SKU or vendor SKU already exists."
        else:
            detail = "Unable to update product."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        _main().log_error(
            "Failed to update product",
            product_id=product_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update product",
        ) from exc

    if not updated:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if feature_payload is not None:
        try:
            await shop_repo.replace_product_features(product_id, feature_payload)
        except Exception as exc:  # pragma: no cover - safety
            if stored_path:
                stored_path.unlink(missing_ok=True)
            _main().log_error(
                "Failed to update product features",
                product_id=product_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to update product features",
            ) from exc

    if stored_path and previous_image_url and previous_image_url != updated.get("image_url"):
        try:
            _main().delete_stored_file(previous_image_url, _main()._private_uploads_path)
        except HTTPException as exc:
            _main().log_error(
                "Failed to remove replaced product image",
                product_id=product_id,
                error=str(exc),
            )
        except OSError as exc:
            _main().log_error(
                "Failed to remove replaced product image",
                product_id=product_id,
                error=str(exc),
            )

    _main().log_info(
        "Shop product updated",
        product_id=product_id,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.product.update",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before=product,
        after=updated,
        sensitive_extra_keys=("buy_price",),
    )
    redirect_params: dict[str, str] = {}
    try:
        # request.query_params accesses scope["query_string"] which may be absent
        # in synthetic test requests; guard with KeyError to stay safe in production
        qp = request.query_params
        if qp.get("showArchived"):
            redirect_params["showArchived"] = "1"
        page_str = qp.get("page", "")
        if page_str.isdigit() and int(page_str) > 1:
            redirect_params["page"] = page_str
        page_size_str = qp.get("pageSize", "")
        if page_size_str.isdigit() and int(page_size_str) > 0:
            redirect_params["pageSize"] = page_size_str
    except KeyError:
        pass
    redirect_url = f"/admin/shop?{urlencode(redirect_params)}" if redirect_params else "/admin/shop"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


async def _handle_shop_product_archive(
    request: Request,
    product_id: int,
    *,
    archived: bool,
):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if bool(product.get("archived")) == archived:
        return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)

    updated = await shop_repo.set_product_archived(product_id, archived=archived)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    _main().log_info(
        "Shop product archived" if archived else "Shop product unarchived",
        product_id=product_id,
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.product.archive" if archived else "shop.product.unarchive",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before={"archived": bool(product.get("archived"))},
        after={"archived": archived},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def admin_archive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=True)


async def admin_unarchive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=False)


async def admin_update_shop_product_visibility(
    request: Request,
    product_id: int,
    excluded: list[str] = Form(default=[]),
):
    from app.repositories import companies as company_repo
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    excluded_ids: set[int] = set()
    for value in excluded:
        if value in (None, ""):
            continue
        try:
            company_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company selection")
        excluded_ids.add(company_id)

    for company_id in excluded_ids:
        company = await company_repo.get_company_by_id(company_id)
        if not company:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected company does not exist")

    await shop_repo.replace_product_exclusions(product_id, excluded_ids)

    _main().log_info(
        "Shop product visibility updated",
        product_id=product_id,
        excluded_companies=sorted(excluded_ids),
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.product.visibility_change",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before={"excluded_company_ids": sorted(int(cid) for cid in (product.get("excluded_company_ids") or []))},
        after={"excluded_company_ids": sorted(excluded_ids)},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def admin_delete_shop_product(request: Request, product_id: int):
    from app.repositories import shop as shop_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    delete_result = await shop_repo.delete_product(product_id)
    if delete_result in (False, "missing"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if delete_result == "archived":
        _main().log_info(
            "Shop product archived because it has existing orders",
            product_id=product_id,
            updated_by=current_user.get("id") if current_user else None,
        )
        await audit_service.record(
            action="shop.product.archive",
            request=request,
            entity_type="shop.product",
            entity_id=product_id,
            before={"archived": bool(product.get("archived"))},
            after={"archived": True, "reason": "referenced_by_orders"},
        )
        return flash_redirect(
            "/admin/shop",
            "Product has existing orders, so it was archived instead of permanently deleted.",
            "warning",
        )

    image_url = product.get("image_url")
    if image_url:
        try:
            _main().delete_stored_file(image_url, _main()._private_uploads_path)
        except HTTPException as exc:
            _main().log_error(
                "Failed to remove deleted product image",
                product_id=product_id,
                error=str(exc),
            )
        except OSError as exc:
            _main().log_error(
                "Failed to remove deleted product image",
                product_id=product_id,
                error=str(exc),
            )

    _main().log_info(
        "Shop product deleted",
        product_id=product_id,
        deleted_by=current_user.get("id") if current_user else None,
    )
    await audit_service.record_delete(
        action="shop.product.delete",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before=product,
        sensitive_extra_keys=("buy_price",),
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)
