from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

WAREHOUSE_STOCK_FIELDS: tuple[tuple[str, str], ...] = (
    ("NSW", "stock_nsw"),
    ("QLD", "stock_qld"),
    ("VIC", "stock_vic"),
    ("SA", "stock_sa"),
    ("WA", "stock_wa"),
)

_SMALL = "small"
_MEDIUM = "medium"
_LARGE = "large"


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _quantize_money(amount: Any) -> Decimal:
    return _to_decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _classify_item_size(product: Mapping[str, Any]) -> str:
    """Classify size from product dimensions using coarse freight tiers.

    Thresholds are intentionally broad defaults so rules can target
    small/medium/large groupings without requiring strict dimension units.
    Product dimensions are treated as centimeters and volume as cubic
    centimeters to align with existing stock-feed product fields.
    """
    length = _to_decimal(product.get("length"))
    width = _to_decimal(product.get("width"))
    height = _to_decimal(product.get("height"))
    longest = max(length, width, height)
    volume = length * width * height
    if longest >= Decimal("100") or volume >= Decimal("1000000"):
        return _LARGE
    if longest >= Decimal("40") or volume >= Decimal("100000"):
        return _MEDIUM
    return _SMALL


def _allocate_quantity_by_warehouse(
    quantity: int,
    product: Mapping[str, Any],
) -> list[tuple[str, int]]:
    if quantity <= 0:
        return []
    candidates: list[tuple[int, int, str]] = []
    for index, (warehouse, field) in enumerate(WAREHOUSE_STOCK_FIELDS):
        available = _to_int(product.get(field), default=0)
        if available > 0:
            candidates.append((index, available, warehouse))
    candidates.sort(key=lambda entry: (-entry[1], entry[0]))

    allocations: list[tuple[str, int]] = []
    remaining = quantity
    for _index, available, warehouse in candidates:
        if remaining <= 0:
            break
        allocated = min(remaining, available)
        if allocated > 0:
            allocations.append((warehouse, allocated))
            remaining -= allocated
    if remaining > 0:
        # Keep remaining items as a pseudo-shipment so fallback freight can still apply.
        allocations.append(("UNALLOCATED", remaining))
    return allocations


def _parse_between(value: str) -> tuple[Decimal, Decimal] | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    for separator in (",", "-", ".."):
        if separator in cleaned:
            parts = [part.strip() for part in cleaned.split(separator, 1)]
            if len(parts) != 2:
                return None
            return _to_decimal(parts[0]), _to_decimal(parts[1])
    return None


def _parse_list(value: str) -> list[str]:
    return [segment.strip().lower() for segment in value.split(",") if segment.strip()]


def _matches_numeric(operator: str, actual: Decimal, raw_value: str) -> bool:
    op = operator.lower()
    if op == "between":
        parsed = _parse_between(raw_value)
        if not parsed:
            return False
        minimum, maximum = parsed
        return minimum <= actual <= maximum

    comparison = _to_decimal(raw_value)
    if op in {"gte", ">="}:
        return actual >= comparison
    if op in {"lte", "<="}:
        return actual <= comparison
    if op in {"equals", "=="}:
        return actual == comparison
    return False


def _evaluate_condition(
    condition: Mapping[str, Any],
    *,
    cart_total: Decimal,
    shipment: Mapping[str, Any],
) -> bool:
    condition_type = str(condition.get("type") or "").strip().lower()
    operator = str(condition.get("operator") or "equals").strip().lower()
    value = str(condition.get("value") or "").strip()

    if not condition_type:
        return False

    if condition_type == "dispatch_warehouse":
        actual = str(shipment.get("dispatch_warehouse") or "").lower()
        if operator == "equals":
            return actual == value.lower()
        if operator == "in":
            return actual in _parse_list(value)
        return False

    if condition_type == "product_id":
        allowed = {_to_int(item, default=0) for item in _parse_list(value)}
        allowed = {item for item in allowed if item > 0}
        products = set(shipment.get("product_ids") or [])
        if operator in {"in", "contains"}:
            return bool(products & allowed)
        if operator == "equals":
            return len(products) == 1 and next(iter(products), 0) in allowed
        return False

    if condition_type == "item_size":
        sizes = {str(item).lower() for item in (shipment.get("item_sizes") or set())}
        allowed_sizes = set(_parse_list(value))
        if operator in {"in", "contains"}:
            return bool(sizes & allowed_sizes)
        if operator == "equals":
            return len(sizes) == 1 and next(iter(sizes), "") in allowed_sizes
        return False

    if condition_type == "cart_total":
        return _matches_numeric(operator, cart_total, value)

    if condition_type == "quantity":
        quantity = Decimal(str(_to_int(shipment.get("quantity"), default=0)))
        return _matches_numeric(operator, quantity, value)

    if condition_type == "item_weight":
        weight = _to_decimal(shipment.get("max_item_weight"))
        return _matches_numeric(operator, weight, value)

    return False


def _matches_rule(
    rule: Mapping[str, Any],
    *,
    cart_total: Decimal,
    shipment: Mapping[str, Any],
) -> bool:
    conditions = rule.get("conditions") or []
    if not conditions:
        return bool(rule.get("is_default"))
    return all(
        _evaluate_condition(condition, cart_total=cart_total, shipment=shipment)
        for condition in conditions
    )


def _select_rules_for_shipment(
    rules: Sequence[Mapping[str, Any]],
    *,
    cart_total: Decimal,
    shipment: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    default_rule: Mapping[str, Any] | None = None
    matched_rules: list[Mapping[str, Any]] = []
    for rule in rules:
        if rule.get("is_default"):
            default_rule = rule
            continue
        if _matches_rule(rule, cart_total=cart_total, shipment=shipment):
            matched_rules.append(rule)
            if bool(rule.get("stop_processing")):
                break
    if matched_rules:
        return matched_rules
    if default_rule:
        return [default_rule]
    return []


def _build_shipments(
    cart_items: Sequence[Mapping[str, Any]],
    product_lookup: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    shipments: dict[str, dict[str, Any]] = {}
    for cart_item in cart_items:
        product_id = _to_int(cart_item.get("product_id"), default=0)
        quantity = _to_int(cart_item.get("quantity"), default=0)
        if product_id <= 0 or quantity <= 0:
            continue
        product = product_lookup.get(product_id) or {}
        unit_price = _to_decimal(cart_item.get("unit_price"))
        max_item_weight = _to_decimal(product.get("weight"))
        item_size = _classify_item_size(product)
        for warehouse, allocated_quantity in _allocate_quantity_by_warehouse(quantity, product):
            bucket = shipments.setdefault(
                warehouse,
                {
                    "dispatch_warehouse": warehouse,
                    "subtotal": Decimal("0"),
                    "quantity": 0,
                    "max_item_weight": Decimal("0"),
                    "item_sizes": set(),
                    "product_ids": set(),
                },
            )
            bucket["subtotal"] = bucket["subtotal"] + (unit_price * allocated_quantity)
            bucket["quantity"] += allocated_quantity
            bucket["max_item_weight"] = max(bucket["max_item_weight"], max_item_weight)
            bucket["item_sizes"].add(item_size)
            bucket["product_ids"].add(product_id)
    return list(shipments.values())


def build_cart_shipments(
    cart_items: Sequence[Mapping[str, Any]],
    product_lookup: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group cart items into dispatch shipments by warehouse.

    Freight rules are charged once for each returned shipment, not once per
    cart line. This helper is intentionally public so checkout, previews, and
    future admin tooling can use the same warehouse grouping before applying
    freight rules.
    """
    return _build_shipments(cart_items, product_lookup)


def calculate_cart_freight(
    cart_items: Sequence[Mapping[str, Any]],
    product_lookup: Mapping[int, Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cart_subtotal = Decimal("0")
    for item in cart_items:
        line_total = _to_decimal(item.get("line_total"))
        if line_total == Decimal("0"):
            line_total = _to_decimal(item.get("unit_price")) * Decimal(
                str(_to_int(item.get("quantity"), default=0))
            )
        cart_subtotal += line_total
    cart_subtotal = _quantize_money(cart_subtotal)

    shipments = build_cart_shipments(cart_items, product_lookup)
    freight_total = Decimal("0")
    breakdown: list[dict[str, Any]] = []
    for shipment in shipments:
        applied_rules = _select_rules_for_shipment(
            rules,
            cart_total=cart_subtotal,
            shipment=shipment,
        )
        freight_amount = _quantize_money(
            sum(
                _to_decimal(rule.get("freight_amount"))
                for rule in applied_rules
            )
        )
        freight_total += freight_amount
        first_rule = applied_rules[0] if applied_rules else {}
        breakdown.append(
            {
                "dispatch_warehouse": shipment.get("dispatch_warehouse"),
                "shipment_quantity": shipment.get("quantity"),
                "shipment_subtotal": _quantize_money(shipment.get("subtotal")),
                "rule_id": first_rule.get("id"),
                "rule_name": first_rule.get("name"),
                "applied_rule_ids": [
                    rule.get("id")
                    for rule in applied_rules
                    if rule.get("id") is not None
                ],
                "applied_rule_names": [
                    str(rule.get("name"))
                    for rule in applied_rules
                    if rule.get("name")
                ],
                "amount": freight_amount,
            }
        )
    return {
        "cart_subtotal": cart_subtotal,
        "freight_total": _quantize_money(freight_total),
        "breakdown": breakdown,
    }
