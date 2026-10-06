"""Browser regression checks for multi-select conversation filters."""
from pathlib import Path
import shutil

import pytest
from jinja2 import Environment
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'app/static/js/ticket_detail.js').read_text()
HISTORY_SCRIPT = SCRIPT[SCRIPT.index('  function initialiseTicketHistory() {'):SCRIPT.index('  function initialiseReplyVisibility() {')]
TEMPLATE = (ROOT / 'app/templates/admin/ticket_detail.html').read_text()
CONTROLS = TEMPLATE[TEMPLATE.index('              <div class="stat-strip ticket-history-filters"'):TEMPLATE.index('              <p class="form-help">Toggle reply types')]


@pytest.mark.parametrize('reply, expected', [
    ({'is_internal': True, 'author_id': None, 'external_reference': 'shipment-watch:provider:update'}, 'automation'),
    ({'is_internal': False, 'author_id': None, 'external_reference': 'imap:customer-message'}, 'customer'),
    ({'is_internal': True, 'author_id': 2, 'external_reference': None}, 'internal'),
    ({'is_internal': False, 'author_id': 2, 'external_reference': None}, 'technician'),
])
def test_reply_category_preserves_origin_and_visibility(reply, expected):
    classification = TEMPLATE[TEMPLATE.index('                      {% set reply_is_automation'):TEMPLATE.index('                      <article', TEMPLATE.index('                      {% set reply_is_automation'))]
    rendered = Environment().from_string(classification + '{{ reply_kind }}').render(reply=reply, ticket={'requester_id': 1})
    assert rendered.strip() == expected


@pytest.fixture
def history_page():
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required for these browser checks')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium)
        page = browser.new_page()
        page.route('http://history.test/**', lambda route: route.fulfill(body='<html><body></body></html>', content_type='text/html'))
        page.goto('http://history.test/admin/tickets/1')
        yield page
        browser.close()


def mount(page, kinds=None, user_id=7, extra_filter=None):
    kinds = kinds or ['customer', 'technician', 'internal', 'automation'] * 3
    controls = Environment().from_string(CONTROLS).render(current_user={'id': user_id})
    if extra_filter:
        controls = controls.replace('</div>', f'<button data-history-filter="{extra_filter}" type="button"><span data-history-count></span>{extra_filter}</button></div>')
    messages = ''.join(f'<article data-ticket-reply data-message-kind="{kind}">{index}</article>' for index, kind in enumerate(kinds))
    page.set_content(controls + '<button data-show-split-hidden aria-pressed="false">Split history</button><div data-ticket-timeline>' + messages + '<p data-history-empty hidden>No replies match</p><button data-history-load-earlier hidden></button></div>')
    page.evaluate('() => {\n' + HISTORY_SCRIPT + '\ninitialiseTicketHistory();\n}')


def tile(page, kind):
    return page.locator(f'[data-history-filter="{kind}"]')


def visible_count(page, kind=None):
    selector = '[data-ticket-reply]'
    if kind:
        selector += f'[data-message-kind="{kind}"]'
    return page.locator(selector + ':visible').count()


def test_independent_toggles_counts_paging_and_all(history_page):
    page = history_page
    mount(page)
    assert tile(page, 'all').locator('[data-history-count]').inner_text() == '12'
    assert tile(page, 'automation').locator('[data-history-count]').inner_text() == '3'
    assert visible_count(page) == 8
    tile(page, 'automation').click()
    assert visible_count(page, 'automation') == 0
    assert visible_count(page) == 8
    assert tile(page, 'customer').get_attribute('aria-pressed') == 'true'
    assert tile(page, 'all').get_attribute('aria-pressed') == 'false'
    assert tile(page, 'automation').locator('[data-history-count]').inner_text() == '3'
    assert page.locator('[data-history-load-earlier]').inner_text() == 'Load earlier messages (1)'
    page.locator('[data-history-load-earlier]').click()
    assert visible_count(page) == 9
    tile(page, 'customer').focus()
    page.keyboard.press('Enter')
    assert visible_count(page, 'customer') == 0
    assert visible_count(page, 'internal') == 3
    tile(page, 'all').click()
    assert all(tile(page, kind).get_attribute('aria-pressed') == 'true' for kind in ['all', 'customer', 'technician', 'internal', 'automation'])
    tile(page, 'all').click()
    assert visible_count(page) == 0
    assert page.locator('[data-history-empty]').is_visible()
    page.reload()
    mount(page)
    assert visible_count(page) == 0
    assert page.locator('[data-history-empty]').is_visible()
    tile(page, 'internal').click()
    assert visible_count(page, 'internal') == 3
    assert not page.locator('[data-history-empty]').is_visible()


def test_persistence_user_isolation_and_new_ai_type(history_page):
    page = history_page
    mount(page)
    tile(page, 'automation').click()
    page.reload()
    mount(page)
    assert tile(page, 'automation').get_attribute('aria-pressed') == 'false'
    page.goto('http://history.test/admin/tickets/999')
    mount(page, kinds=['customer', 'ai', 'automation'], extra_filter='ai')
    assert visible_count(page, 'automation') == 0
    assert visible_count(page, 'ai') == 1
    tile(page, 'ai').click()
    page.reload()
    mount(page, kinds=['customer', 'ai', 'automation'], extra_filter='ai')
    assert visible_count(page, 'ai') == 0
    # A version without the AI tile must not discard that preference.
    mount(page)
    tile(page, 'customer').click()
    mount(page, kinds=['customer', 'ai', 'automation'], extra_filter='ai')
    assert visible_count(page, 'ai') == 0
    mount(page, user_id=8)
    assert tile(page, 'automation').get_attribute('aria-pressed') == 'true'


def test_split_visibility_counts_and_filters_compose(history_page):
    page = history_page
    mount(page, kinds=['automation', 'customer'])
    page.locator('[data-ticket-reply]').first.evaluate("element => element.dataset.splitHidden = 'true'")
    page.evaluate("document.querySelector('[data-ticket-timeline]').dispatchEvent(new Event('ticket-history-change'))")
    assert tile(page, 'all').locator('[data-history-count]').inner_text() == '1'
    tile(page, 'automation').click()
    page.evaluate("document.querySelector('[data-show-split-hidden]').setAttribute('aria-pressed', 'true'); document.querySelector('[data-ticket-timeline]').dispatchEvent(new Event('ticket-history-change'))")
    assert tile(page, 'all').locator('[data-history-count]').inner_text() == '2'
    assert visible_count(page, 'automation') == 0
    tile(page, 'automation').click()
    assert visible_count(page, 'automation') == 1


@pytest.mark.parametrize('stored', ['not-json', '{"hidden":"automation"}', '{"hidden":[null]}'])
def test_invalid_storage_falls_back_to_all(history_page, stored):
    page = history_page
    page.evaluate('(value) => localStorage.setItem("portal.tickets.history.7", value)', stored)
    mount(page)
    assert tile(page, 'all').get_attribute('aria-pressed') == 'true'


def test_storage_failure_does_not_break_filtering(history_page):
    page = history_page
    page.evaluate("() => { Storage.prototype.getItem = () => { throw new Error('blocked'); }; Storage.prototype.setItem = () => { throw new Error('blocked'); }; }")
    mount(page)
    tile(page, 'automation').click()
    assert visible_count(page, 'automation') == 0


def test_stat_strip_fits_responsive_widths(history_page):
    page = history_page
    mount(page)
    page.add_style_tag(content=(ROOT / 'app/static/css/app.css').read_text())
    for width in [320, 768, 1024, 1440]:
        page.set_viewport_size({'width': width, 'height': 900})
        for kind in ['all', 'customer', 'technician', 'internal', 'automation']:
            bounds = tile(page, kind).bounding_box()
            assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
    tile(page, 'automation').focus()
    assert tile(page, 'automation').evaluate("element => getComputedStyle(element).outlineStyle") == 'solid'
