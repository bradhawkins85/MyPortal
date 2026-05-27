"""Cart routes for the ``cart`` feature pack."""

from __future__ import annotations

import secrets
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, status
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import URL


router = APIRouter(tags=["Cart"])


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


@router.post("/cart/add", response_class=RedirectResponse, include_in_schema=False)
async def add_to_cart(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    product_id_raw = form.get("productId")
    quantity_raw = form.get("quantity")
    upgrade_from_values: Sequence[str] = []
    if isinstance(form, FormData):
        upgrade_from_values = form.getlist("upgradeFrom")
    else:
        upgrade_raw = form.get("upgradeFrom")
        if upgrade_raw is not None:
            upgrade_from_values = [upgrade_raw]
    upgrade_source_candidates: list[int] = []
    seen_upgrade_sources: set[int] = set()
    for raw_value in upgrade_from_values:
        try:
            resolved = int(str(raw_value))
        except (TypeError, ValueError):
            continue
        if resolved <= 0 or resolved in seen_upgrade_sources:
            continue
        upgrade_source_candidates.append(resolved)
        seen_upgrade_sources.add(resolved)

    try:
        product_id = int(product_id_raw)
    except (TypeError, ValueError):
        message = quote("Invalid product selection.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        requested_quantity = int(quantity_raw) if quantity_raw is not None else 1
    except (TypeError, ValueError):
        requested_quantity = 1
    if requested_quantity <= 0:
        requested_quantity = 1

    product = await main_module.shop_repo.get_product_by_id(
        product_id,
        company_id=company_id,
    )
    if not product:
        message = quote("Product is unavailable.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    if main_module.shop_service.is_price_below_dbp_threshold(product, is_vip=is_vip):
        message = quote("Product is unavailable.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    subscription_category_id = product.get("subscription_category_id")
    if subscription_category_id is not None:
        active_subscription_product_ids = await main_module.subscriptions_repo.get_active_subscription_product_ids(company_id)
        if product_id in active_subscription_product_ids:
            message = quote("You already have an active subscription for this product. Please manage your subscription from the Subscriptions page.")
            return RedirectResponse(
                url=f"{request.url_for('shop_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    existing = await main_module.cart_repo.get_item(session.id, product_id)
    existing_quantity = existing.get("quantity") if existing else 0

    selected_upgrade_source: int | None = None
    if upgrade_source_candidates:
        source_products = await main_module.shop_repo.list_products_by_ids(
            upgrade_source_candidates,
            company_id=company_id,
        )
        valid_upgrade_sources: dict[int, set[int]] = {}
        for source_product in source_products:
            try:
                source_id = int(source_product.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if source_id <= 0:
                continue
            related_ids: set[int] = set()
            for related_id in source_product.get("upsell_product_ids") or []:
                try:
                    resolved_related = int(related_id)
                except (TypeError, ValueError):
                    continue
                if resolved_related > 0:
                    related_ids.add(resolved_related)
            if related_ids:
                valid_upgrade_sources[source_id] = related_ids

        if valid_upgrade_sources:
            for candidate in upgrade_source_candidates:
                related = valid_upgrade_sources.get(candidate)
                if not related or product_id not in related:
                    continue
                existing_source = await main_module.cart_repo.get_item(session.id, candidate)
                if not existing_source:
                    continue
                selected_upgrade_source = candidate
                break

    available_stock = int(product.get("stock") or 0)
    if available_stock <= 0 or existing_quantity + requested_quantity > available_stock:
        remaining = max(available_stock - existing_quantity, 0)
        message = quote(f"Cannot add item. Only {remaining} left in stock.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    unit_price = main_module.shop_service.get_product_price(product, is_vip=is_vip)
    new_quantity = existing_quantity + requested_quantity

    if selected_upgrade_source is not None:
        await main_module.cart_repo.remove_items(session.id, [selected_upgrade_source])

    await main_module.cart_repo.upsert_item(
        session_id=session.id,
        product_id=product_id,
        quantity=new_quantity,
        unit_price=unit_price,
        name=str(product.get("name") or ""),
        sku=str(product.get("sku") or ""),
        vendor_sku=product.get("vendor_sku"),
        description=product.get("description"),
        image_url=product.get("image_url"),
    )

    cart_url = request.url_for("cart_page")
    if selected_upgrade_source is not None:
        cart_message = quote("Upgrade applied.")
    else:
        cart_message = quote("Item added to cart.")
    return RedirectResponse(
        url=f"{cart_url}?cartMessage={cart_message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/cart/add-package", response_class=RedirectResponse, include_in_schema=False)
async def add_package_to_cart(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    package_id_raw = form.get("packageId")
    quantity_raw = form.get("quantity")

    try:
        package_id = int(package_id_raw)
    except (TypeError, ValueError):
        return RedirectResponse(url=request.url_for("shop_packages_page"), status_code=status.HTTP_303_SEE_OTHER)

    try:
        requested_quantity = int(quantity_raw) if quantity_raw is not None else 1
    except (TypeError, ValueError):
        requested_quantity = 1
    if requested_quantity <= 0:
        requested_quantity = 1

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    packages = await main_module.shop_packages_service.load_company_packages(
        company_id=company_id,
        is_vip=is_vip,
    )
    selected_package = next((pkg for pkg in packages if int(pkg.get("id") or 0) == package_id), None)
    if not selected_package or not selected_package.get("is_available"):
        message = quote("Selected package is not currently available.")
        return RedirectResponse(
            url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    items = list(selected_package.get("items") or [])
    if not items:
        message = quote("Package does not contain any products.")
        return RedirectResponse(
            url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    active_subscription_product_ids = await main_module.subscriptions_repo.get_active_subscription_product_ids(company_id)

    cart_updates: list[tuple[int, int, Decimal, dict[str, Any]]] = []
    for item in items:
        resolved = item.get("resolved_product") or {}
        product_id = int(resolved.get("product_id") or 0)
        if product_id <= 0:
            message = quote("One or more products in the package are unavailable.")
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        quantity_per_package = int(item.get("quantity") or 0)
        if quantity_per_package <= 0:
            continue
        required_quantity = quantity_per_package * requested_quantity
        product = await main_module.shop_repo.get_product_by_id(
            product_id,
            company_id=company_id,
        )
        if not product:
            message = quote("One or more products in the package are unavailable.")
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        subscription_category_id = product.get("subscription_category_id")
        if subscription_category_id is not None and product_id in active_subscription_product_ids:
            product_name = str(product.get("name") or "a product")
            message = quote(
                f"Cannot add package. You already have an active subscription for {product_name}. Please manage your subscription from the Subscriptions page."
            )
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        stock_available = int(product.get("stock") or 0)
        existing_item = await main_module.cart_repo.get_item(session.id, product_id)
        existing_quantity = existing_item.get("quantity") if existing_item else 0
        if stock_available <= 0 or existing_quantity + required_quantity > stock_available:
            remaining = max(stock_available - existing_quantity, 0)
            product_name = str(product.get("name") or "the product")
            message = quote(
                f"Cannot add package. {product_name} has only {remaining} left in stock."
            )
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        unit_price = main_module.shop_service.get_product_price(product, is_vip=is_vip)
        new_quantity = existing_quantity + required_quantity
        cart_updates.append(
            (
                product_id,
                new_quantity,
                unit_price,
                product,
            )
        )

    for product_id, new_quantity, unit_price, product in cart_updates:
        await main_module.cart_repo.upsert_item(
            session_id=session.id,
            product_id=product_id,
            quantity=new_quantity,
            unit_price=unit_price,
            name=str(product.get("name") or ""),
            sku=str(product.get("sku") or ""),
            vendor_sku=product.get("vendor_sku"),
            description=product.get("description"),
            image_url=product.get("image_url"),
        )

    main_module.log_info(
        "Shop package added to cart",
        package_id=package_id,
        quantity=requested_quantity,
        added_by=user.get("id") if user else None,
    )

    return RedirectResponse(
        url=f"{request.url_for('shop_page')}?category=packages",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/cart", response_class=HTMLResponse, name="cart_page")
async def view_cart(
    request: Request,
    order_message: str | None = Query(None, alias="orderMessage"),
    cart_error: str | None = Query(None, alias="cartError"),
    cart_message: str | None = Query(None, alias="cartMessage"),
):
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    items = await main_module.cart_repo.list_items(session.id)
    cart_items: list[dict[str, Any]] = []
    cart_items_payload: list[dict[str, Any]] = []
    cart_product_ids: list[int] = []
    total = Decimal("0")
    removed_product_ids: list[int] = []
    price_updates = 0
    cart_message_parts = [cart_message] if cart_message else []
    cart_error_parts = [cart_error] if cart_error else []

    try:
        is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    except (TypeError, ValueError):
        is_vip = False

    product_lookup: dict[int, dict[str, Any]] = {}
    if items:
        product_ids = []
        for item in items:
            try:
                product_ids.append(int(item.get("product_id") or 0))
            except (TypeError, ValueError):
                continue
        if product_ids:
            base_products = await main_module.shop_repo.list_products_by_ids(
                product_ids,
                company_id=company_id,
            )
            product_lookup = {
                int(product.get("id") or 0): product for product in base_products if product.get("id") is not None
            }

    def _resolve_product_price(product: Mapping[str, Any]) -> Decimal:
        try:
            value = main_module.shop_service.get_product_price(product, is_vip=is_vip).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, TypeError, ValueError):
            value = Decimal("0.00")
        return value

    def _normalise_price(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        try:
            return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.00")

    for item in items:
        quantity = int(item.get("quantity") or 0)
        product_identifier = item.get("product_id")
        try:
            resolved_product_id = int(product_identifier)
        except (TypeError, ValueError):
            resolved_product_id = 0

        product = product_lookup.get(resolved_product_id)
        if not product or resolved_product_id <= 0:
            if resolved_product_id > 0:
                removed_product_ids.append(resolved_product_id)
            continue

        current_price = _resolve_product_price(product)
        stored_price = _normalise_price(item.get("unit_price"))
        if current_price != stored_price:
            price_updates += 1

        name = str(product.get("name") or item.get("product_name") or "")
        sku = str(product.get("sku") or item.get("product_sku") or "")
        vendor_sku = product.get("vendor_sku")
        description = product.get("description")
        image_url = product.get("image_url")
        line_total = current_price * quantity
        total += line_total

        cart_product_ids.append(resolved_product_id)

        hydrated = dict(item)
        hydrated.update(
            {
                "unit_price": current_price,
                "line_total": line_total,
                "available_stock": int(product.get("stock") or 0),
                "product_name": name,
                "product_sku": sku,
                "product_vendor_sku": vendor_sku,
                "product_description": description,
                "product_image_url": image_url,
            }
        )
        cart_items.append(hydrated)

        if any(
            [
                current_price != stored_price,
                name != item.get("product_name"),
                sku != item.get("product_sku"),
                vendor_sku != item.get("product_vendor_sku"),
                description != item.get("product_description"),
                image_url != item.get("product_image_url"),
            ]
        ):
            await main_module.cart_repo.upsert_item(
                session_id=session.id,
                product_id=resolved_product_id,
                quantity=quantity,
                unit_price=current_price,
                name=name,
                sku=sku,
                vendor_sku=vendor_sku,
                description=description,
                image_url=image_url,
            )

        cart_items_payload.append(
            {
                "product_id": resolved_product_id,
                "name": name,
                "sku": sku,
                "vendor_sku": vendor_sku,
                "description": description,
                "image_url": image_url,
                "unit_price": f"{current_price:.2f}",
                "quantity": quantity,
                "line_total": f"{line_total:.2f}",
            }
        )

    if removed_product_ids:
        await main_module.cart_repo.remove_items(session.id, removed_product_ids)
        cart_error_parts.append("Some items were removed because they are no longer available.")

    if price_updates:
        cart_message_parts.append("Prices updated to reflect the latest catalog.")

    recommendations: dict[str, list[dict[str, Any]]] = {"cross_sell": [], "upsell": []}
    if cart_product_ids:
        base_products = await main_module.shop_repo.list_products_by_ids(
            cart_product_ids,
            company_id=company_id,
        )
        product_lookup = {}
        for product in base_products:
            try:
                product_lookup[int(product.get("id") or 0)] = product
            except (TypeError, ValueError):
                continue

        for item in cart_items:
            try:
                product_id = int(item.get("product_id") or 0)
            except (TypeError, ValueError):
                product_id = 0
            product = product_lookup.get(product_id)
            available_stock = 0
            if product:
                try:
                    available_stock = int(product.get("stock") or 0)
                except (TypeError, ValueError):
                    available_stock = 0
            item["available_stock"] = available_stock

        cart_product_id_set = {pid for pid in cart_product_ids}
        cross_sell_targets: dict[int, set[str]] = {}
        upsell_targets: dict[int, dict[str, set[str] | set[int]]] = {}
        for product in base_products:
            base_id = int(product.get("id") or 0)
            base_name = str(product.get("name") or "")
            for entry in product.get("cross_sell_products", []) or []:
                target_id = int(entry.get("id") or 0)
                if (
                    target_id > 0
                    and target_id not in cart_product_id_set
                    and target_id != base_id
                ):
                    cross_sell_targets.setdefault(target_id, set()).add(base_name)
            for entry in product.get("upsell_products", []) or []:
                target_id = int(entry.get("id") or 0)
                if (
                    target_id > 0
                    and target_id not in cart_product_id_set
                    and target_id != base_id
                ):
                    bucket = upsell_targets.setdefault(
                        target_id,
                        {"names": set(), "ids": set()},
                    )
                    bucket["names"].add(base_name)
                    bucket["ids"].add(base_id)

        target_ids = sorted(set(cross_sell_targets) | set(upsell_targets))
        if target_ids:
            related_products = await main_module.shop_repo.list_products_by_ids(
                target_ids,
                company_id=company_id,
            )
            related_map = {int(prod.get("id") or 0): prod for prod in related_products}
            is_vip = bool(company and int(company.get("is_vip") or 0) == 1)

            def _prepare_recommendation(
                *,
                target_id: int,
                source_names: Iterable[str],
                source_ids: Iterable[int] | None = None,
                kind: str,
            ) -> None:
                product = related_map.get(target_id)
                if not product:
                    return
                try:
                    stock_level = int(product.get("stock") or 0)
                except (TypeError, ValueError):
                    stock_level = 0
                if stock_level <= 0:
                    return
                try:
                    price_value = main_module.shop_service.get_product_price(product, is_vip=is_vip).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                except (InvalidOperation, TypeError, ValueError):
                    price_value = Decimal("0.00")
                entry = {
                    "product_id": target_id,
                    "name": product.get("name"),
                    "sku": product.get("sku"),
                    "image_url": product.get("image_url"),
                    "price": price_value,
                    "source_names": sorted({name for name in source_names if name}),
                    "source_product_ids": sorted(
                        {
                            int(pid)
                            for pid in (source_ids or [])
                            if isinstance(pid, int) and pid > 0
                        }
                    ),
                    "kind": kind,
                }
                recommendations.setdefault(kind, []).append(entry)

            for target_id, names in cross_sell_targets.items():
                _prepare_recommendation(
                    target_id=target_id,
                    source_names=names,
                    source_ids=None,
                    kind="cross_sell",
                )
            for target_id, payload in upsell_targets.items():
                _prepare_recommendation(
                    target_id=target_id,
                    source_names=payload.get("names", set()),
                    source_ids=payload.get("ids", set()),
                    kind="upsell",
                )

            for bucket in recommendations.values():
                bucket.sort(key=lambda item: str(item.get("name") or "").lower())

    normalised_cart_message = " ".join(filter(None, cart_message_parts)) or None
    normalised_cart_error = " ".join(filter(None, cart_error_parts)) or None

    extra = {
        "title": "Cart",
        "cart_items": cart_items,
        "cart_total": total,
        "order_message": order_message,
        "cart_error": normalised_cart_error,
        "cart_message": normalised_cart_message,
        "cart_items_payload": cart_items_payload,
        "cart_recommendations": recommendations,
        "low_stock_threshold": main_module.SHOP_LOW_STOCK_THRESHOLD,
        "payment_method": (company.get("payment_method") or "invoice_prepay") if company else "invoice_prepay",
        "require_po": bool(company.get("require_po")) if company else False,
    }
    response = await main_module._render_template("shop/cart.html", request, user, extra=extra)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.post(
    "/cart/update",
    response_class=RedirectResponse,
    name="cart_update_items",
    include_in_schema=False,
)
async def update_cart_items(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    updates: dict[int, int] = {}
    removals: set[int] = set()
    invalid_entries = False
    stock_conflicts: set[int] = set()
    max_quantity = 9999

    if isinstance(form, FormData):
        items = form.multi_items()
    else:
        items = form.items()

    for key, raw_value in items:
        if not isinstance(key, str) or not key.startswith("quantity_"):
            continue
        suffix = key[len("quantity_") :]
        try:
            product_id = int(suffix)
        except (TypeError, ValueError):
            invalid_entries = True
            continue

        value: str | None
        if isinstance(raw_value, (list, tuple)):
            value = str(raw_value[0]).strip() if raw_value else None
        else:
            value = str(raw_value).strip() if raw_value is not None else None

        try:
            quantity = int(value) if value is not None else None
        except (TypeError, ValueError):
            invalid_entries = True
            continue

        if quantity is None:
            invalid_entries = True
            continue

        if quantity <= 0:
            removals.add(product_id)
            continue

        if quantity > max_quantity:
            quantity = max_quantity
            invalid_entries = True

        updates[product_id] = quantity

    updated_count = 0
    for product_id, desired_quantity in updates.items():
        if product_id in removals:
            continue
        existing = await main_module.cart_repo.get_item(session.id, product_id)
        if not existing:
            invalid_entries = True
            removals.add(product_id)
            continue

        current_quantity = int(existing.get("quantity") or 0)
        if current_quantity == desired_quantity:
            continue

        product = await main_module.shop_repo.get_product_by_id(
            product_id,
            company_id=company_id,
        )
        if not product:
            invalid_entries = True
            removals.add(product_id)
            continue

        raw_stock = product.get("stock")
        try:
            available_stock = int(raw_stock)
        except (TypeError, ValueError):
            available_stock = 0

        if available_stock <= 0:
            stock_conflicts.add(product_id)
            removals.add(product_id)
            invalid_entries = True
            continue

        if desired_quantity > available_stock:
            stock_conflicts.add(product_id)
            invalid_entries = True
            continue

        await main_module.cart_repo.update_item_quantity(
            session_id=session.id,
            product_id=product_id,
            quantity=desired_quantity,
        )
        updated_count += 1

    removed_count = 0
    if removals:
        await main_module.cart_repo.remove_items(session.id, removals)
        removed_count = len(removals)

    url = URL(str(request.url_for("cart_page")))
    params: dict[str, str] = {}

    if updated_count:
        fragments = ["Quantities updated"]
        if removed_count:
            fragments.append("items removed")
        params["cartMessage"] = ", ".join(fragments) + "."
    elif removed_count and not stock_conflicts:
        params["cartMessage"] = "Items removed."

    if stock_conflicts:
        params["cartError"] = "Unable to increase some quantities due to limited stock."
    elif invalid_entries:
        if "cartMessage" in params:
            params["cartError"] = "Some quantities were adjusted."
        else:
            params["cartError"] = "No changes were applied. Please review the quantities entered."

    redirect_url = url.include_query_params(**params) if params else url
    return RedirectResponse(
        url=str(redirect_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/cart/remove",
    response_class=RedirectResponse,
    name="cart_remove_items",
    include_in_schema=False,
)
async def remove_cart_items(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    product_ids: list[int] = []
    if isinstance(form, FormData):
        raw_values = form.getlist("remove")
    else:
        raw_value = form.get("remove")
        raw_values = raw_value if isinstance(raw_value, list) else [raw_value] if raw_value is not None else []
    for value in raw_values:
        try:
            product_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    await main_module.cart_repo.remove_items(session.id, product_ids)
    return RedirectResponse(url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/cart/place-order",
    response_class=RedirectResponse,
    name="cart_place_order",
    include_in_schema=False,
)
async def place_order(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await main_module.session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if company_id is None:
        raise main_module.HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")

    payment_method = str((company or {}).get("payment_method") or "invoice").strip().lower()
    if payment_method == "stripe":
        message = quote("Order placement is unavailable while Stripe checkout is required.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    form = await request.form()
    po_number_raw = form.get("poNumber")
    po_number = (str(po_number_raw).strip() or None) if po_number_raw is not None else None
    if po_number and len(po_number) > 100:
        po_number = po_number[:100]

    require_po = bool(company.get("require_po")) if company else False
    if require_po and not po_number:
        message = quote("A purchase order number is required to place an order.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?orderMessage={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    items = await main_module.cart_repo.list_items(session.id)
    if not items:
        return RedirectResponse(url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER)

    order_number = "ORD" + "".join(secrets.choice("0123456789") for _ in range(12))

    if main_module.settings.shop_webhook_url and main_module.settings.shop_webhook_api_key:
        try:
            await main_module.webhook_monitor.enqueue_event(
                name="shop-order",
                target_url=str(main_module.settings.shop_webhook_url),
                payload={
                    "cart": [
                        {
                            "productId": item.get("product_id"),
                            "quantity": item.get("quantity"),
                            "price": float(item.get("unit_price", 0)),
                            "name": item.get("product_name"),
                            "sku": item.get("product_sku"),
                            "vendorSku": item.get("product_vendor_sku"),
                        }
                        for item in items
                    ],
                    "poNumber": po_number,
                    "orderNumber": order_number,
                    "companyId": company_id,
                },
                headers={
                    "x-api-key": main_module.settings.shop_webhook_api_key,
                    "Content-Type": "application/json",
                },
                max_attempts=5,
                backoff_seconds=300,
                attempt_immediately=True,
            )
        except Exception as exc:  # pragma: no cover - webhook safety
            main_module.log_error("Failed to enqueue shop webhook", error=str(exc))

    for item in items:
        try:
            previous_stock, new_stock = await main_module.shop_repo.create_order(
                user_id=int(user["id"]),
                company_id=company_id,
                product_id=int(item.get("product_id")),
                quantity=int(item.get("quantity")),
                order_number=order_number,
                status="pending",
                po_number=po_number,
            )
        except ValueError as exc:
            message = quote(str(exc))
            return RedirectResponse(
                url=f"{request.url_for('cart_page')}?orderMessage={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        await main_module.shop_service.maybe_send_stock_notification_by_id(
            int(item.get("product_id")),
            previous_stock,
            new_stock,
        )

    await main_module.cart_repo.clear_cart(session.id)

    try:
        await main_module.subscription_shop_integration.create_subscriptions_from_order(
            order_number=order_number,
            company_id=company_id,
            user_id=int(user["id"]),
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error(
            "Failed to create subscriptions from order",
            order_number=order_number,
            company_id=company_id,
            error=str(exc),
        )

    try:
        user_record = await main_module.user_repo.get_user_by_id(int(user["id"]))
        user_name = None
        if user_record:
            first_name = user_record.get("first_name", "").strip()
            last_name = user_record.get("last_name", "").strip()
            if first_name or last_name:
                user_name = f"{first_name} {last_name}".strip()
            elif user_record.get("email"):
                user_name = str(user_record["email"])

        await main_module.xero_service.send_order_to_xero(
            order_number=order_number,
            company_id=company_id,
            user_name=user_name,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error(
            "Failed to send order to Xero",
            order_number=order_number,
            company_id=company_id,
            error=str(exc),
        )

    success = quote("Your order is being processed.")
    return RedirectResponse(
        url=f"{request.url_for('cart_page')}?orderMessage={success}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


__all__ = [
    "add_package_to_cart",
    "add_to_cart",
    "place_order",
    "remove_cart_items",
    "router",
    "update_cart_items",
    "view_cart",
]
