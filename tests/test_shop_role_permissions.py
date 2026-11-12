"""Shop role levels govern browsing and every way of changing the cart."""
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.features.cart import routes as cart_routes
from app.features.quotes import routes as quote_routes
from app.repositories import user_companies
from app.security.menu_permissions import build_menu_access_map, normalize_menu_permissions
from app.services.role_switching import _virtual_membership


@pytest.mark.parametrize('level,browse,purchase', [
    ('none', False, False), ('read', True, False), ('write', True, True),
])
def test_role_membership_shop_capabilities(level, browse, purchase):
    permissions = {'menu.shop': level, 'menu.subscriptions': 'write'}
    membership = {'can_access_shop': True, 'can_access_cart': True}
    user_companies._enrich_with_role_permissions(membership, permissions)
    simulated = _virtual_membership({'permissions': permissions}, 1)
    for record in (membership, simulated):
        assert record['can_access_shop'] is browse
        assert record['can_access_cart'] is purchase
    # Explicit role levels also override legacy flags before enrichment.
    menu = build_menu_access_map(is_super_admin=False, membership_data={
        'menu_permissions': permissions, 'can_access_shop': True, 'can_access_cart': True,
    })
    assert menu['menu.shop'] == level


def test_legacy_cart_access_preserves_purchasing():
    assert normalize_menu_permissions(['shop.access'])['menu.shop'] == 'read'
    assert normalize_menu_permissions(['cart.access'])['menu.shop'] == 'write'
    menu = build_menu_access_map(is_super_admin=False, membership_data={'can_access_cart': True})
    assert menu['menu.shop'] == 'write'


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['none', 'read', 'write'])
@pytest.mark.parametrize('permission_field', ['can_access_shop', 'can_access_cart'])
async def test_shop_section_authorization(monkeypatch, level, permission_field):
    user = {'id': 9, 'company_id': 5}
    # Conflicting old flags must neither grant nor block the role's decision.
    membership = {'menu_permissions': {'menu.shop': level},
                  'can_access_shop': level == 'none', 'can_access_cart': level != 'write'}
    monkeypatch.setattr(main, '_require_authenticated_user', AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main, '_get_effective_company_membership', AsyncMock(return_value=membership))
    company_lookup = AsyncMock(return_value={'id': 5})
    monkeypatch.setattr(main.company_repo, 'get_company_by_id', company_lookup)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/cart', 'headers': []})
    result = await main._load_company_section_context(request, permission_field=permission_field)
    allowed = level == 'write' or (level == 'read' and permission_field == 'can_access_shop')
    assert (result[-1] is None) is allowed
    assert company_lookup.await_count == int(allowed)


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', [
    cart_routes.add_to_cart, cart_routes.add_package_to_cart, cart_routes.view_cart,
    cart_routes.update_cart_items, cart_routes.remove_cart_items, cart_routes.place_order,
    cart_routes.order_placed_page, quote_routes.load_quote_to_cart, quote_routes.save_as_quote,
])
async def test_read_only_cannot_change_cart_or_checkout(monkeypatch, handler):
    user = {'id': 9, 'company_id': 5}
    membership = {'menu_permissions': {'menu.shop': 'read', 'menu.quotes': 'write'},
                  'can_access_shop': True, 'can_access_cart': True, 'can_access_quotes': True}
    monkeypatch.setattr(main, '_require_authenticated_user', AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main, '_get_effective_company_membership', AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, 'get_company_by_id', AsyncMock(return_value={'id': 5}))
    session_lookup = AsyncMock(side_effect=AssertionError('Read-only request reached cart session'))
    monkeypatch.setattr(main.session_manager, 'load_session', session_lookup)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/cart', 'headers': []})
    args = (request, 'QUOTE-1') if handler == quote_routes.load_quote_to_cart else (request,)
    response = await handler(*args)
    assert response.status_code == 303
    assert response.headers['location'] == '/'
    session_lookup.assert_not_awaited()


@pytest.mark.parametrize('template', ['shop/index.html', 'shop/packages.html'])
@pytest.mark.parametrize('allowed', [False, True])
def test_shop_templates_respect_resolved_cart_permission(template, allowed):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    # Render the real child template's setup against a parent that exposes its flag.
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{{ cart_allowed }}'}), FileSystemLoader('app/templates'),
    ]))
    rendered = env.get_template(template).render(
        can_access_cart=allowed, is_super_admin=False, current_user={},
        active_membership={'can_access_cart': True},
    )
    assert rendered.strip() == str(allowed)


@pytest.mark.asyncio
async def test_shop_write_can_add_and_complete_checkout(monkeypatch):
    from types import SimpleNamespace
    from starlette.routing import Router
    from app.repositories import freight_rules

    user = {'id': 9, 'company_id': 5, 'email': 'buyer@example.com'}
    membership = {'menu_permissions': {'menu.shop': 'write'}, 'can_access_cart': False}
    company = {'id': 5, 'payment_method': 'invoice'}
    monkeypatch.setattr(main, '_require_authenticated_user', AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main, '_get_effective_company_membership', AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, 'get_company_by_id', AsyncMock(return_value=company))
    monkeypatch.setattr(main.session_manager, 'load_session', AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(main.user_repo, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(main.settings, 'shop_webhook_url', None)
    product = {'id': 1, 'name': 'Widget', 'price': '10.00', 'stock': 10}
    monkeypatch.setattr(main.shop_repo, 'get_product_by_id', AsyncMock(return_value=product))
    monkeypatch.setattr(main.shop_repo, 'list_products_by_ids', AsyncMock(return_value=[product]))
    monkeypatch.setattr(main.cart_repo, 'get_item', AsyncMock(return_value=None))
    add = AsyncMock()
    monkeypatch.setattr(main.cart_repo, 'upsert_item', add)
    monkeypatch.setattr(main.cart_repo, 'list_items', AsyncMock(return_value=[{
        'product_id': 1, 'product_name': 'Widget', 'quantity': 2, 'unit_price': '10.00',
    }]))
    order = AsyncMock(return_value=(10, 8))
    monkeypatch.setattr(main.shop_repo, 'create_order', order)
    clear = AsyncMock()
    monkeypatch.setattr(main.cart_repo, 'clear_cart', clear)
    monkeypatch.setattr(main.shop_service, 'maybe_send_stock_notification_by_id', AsyncMock())
    monkeypatch.setattr(main.subscription_shop_integration, 'create_subscriptions_from_order', AsyncMock())
    monkeypatch.setattr(freight_rules, 'list_rules', AsyncMock(return_value=[]))
    invoice = AsyncMock(return_value={'status': 'succeeded', 'xero_result': {'status': 'succeeded'}})
    monkeypatch.setattr(cart_routes.invoice_generator_service, 'generate_order_invoice', invoice)
    monkeypatch.setattr(main.tickets_service, 'resolve_status_or_default', AsyncMock(return_value='open'))
    ticket = AsyncMock()
    monkeypatch.setattr(main.tickets_service, 'create_ticket', ticket)

    def request_for(path, form):
        request = Request({'type': 'http', 'method': 'POST', 'path': path, 'headers': [],
                           'scheme': 'http', 'server': ('test', 80),
                           'router': Router(routes=cart_routes.router.routes)})
        request._form = form
        return request

    response = await cart_routes.add_to_cart(request_for('/cart/add', {'productId': '1', 'quantity': '2'}))
    assert response.status_code == 303
    add.assert_awaited_once()
    assert add.await_args.kwargs['quantity'] == 2
    response = await cart_routes.place_order(request_for('/cart/place-order', {'shippingOption': 'local_pickup'}))
    assert response.status_code == 303
    assert '/cart/order-placed?orderNumber=ORD' in response.headers['location']
    order.assert_awaited_once()
    assert order.await_args.kwargs['company_id'] == 5
    clear.assert_awaited_once_with(7)
    invoice.assert_awaited_once()
    ticket.assert_awaited_once()


@pytest.mark.parametrize('level', ['none', 'read', 'write'])
def test_explicit_shop_level_wins_over_nested_legacy_cart_access(level):
    permissions = normalize_menu_permissions({
        'menu': {'menu.shop': level}, 'legacy': ['shop.access', 'cart.access'],
    })
    assert permissions['menu.shop'] == level


@pytest.mark.asyncio
@pytest.mark.parametrize('level,allowed', [('none', False), ('read', False), ('write', True)])
async def test_base_context_cart_permission_matches_shop_role(monkeypatch, level, allowed):
    from datetime import datetime, timedelta, timezone
    from app.security.session import SessionData

    now = datetime.now(timezone.utc)
    session = SessionData(
        id=7, user_id=9, session_token='test', csrf_token='csrf', created_at=now,
        expires_at=now + timedelta(hours=1), last_seen_at=now, ip_address=None,
        user_agent=None, active_company_id=5,
    )
    monkeypatch.setattr(main.session_manager, 'load_session', AsyncMock(return_value=session))
    monkeypatch.setattr(main.company_access, 'list_accessible_companies', AsyncMock(return_value=[{'company_id': 5}]))
    monkeypatch.setattr(main.user_company_repo, 'get_user_company', AsyncMock(return_value={
        'menu_permissions': {'menu.shop': level}, 'can_access_cart': not allowed,
        'can_access_shop': True,
    }))
    for name in ('_is_helpdesk_technician', '_has_admin_technician_access', '_has_issue_tracker_access', '_has_marketing_access'):
        monkeypatch.setattr(main, name, AsyncMock(return_value=False))
    monkeypatch.setattr(main.membership_repo, 'user_has_permission', AsyncMock(return_value=False))
    monkeypatch.setattr(main.modules_service, 'list_modules', AsyncMock(return_value=[]))
    monkeypatch.setattr(main.notifications_repo, 'count_notifications', AsyncMock(return_value=0))
    monkeypatch.setattr(main.cart_repo, 'summarise_cart', AsyncMock(return_value={'total_quantity': 0}))
    request = Request({'type': 'http', 'method': 'GET', 'path': '/shop', 'headers': []})
    context = await main._build_base_context(request, {'id': 9})
    assert context['can_access_cart'] is allowed
    assert context['can_access_shop'] is (level != 'none')
