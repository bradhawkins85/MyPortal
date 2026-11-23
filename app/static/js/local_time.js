/*
 * Shows every server timestamp in the viewer's browser time zone.
 *
 * The server stores and emits times in UTC, often without a zone suffix
 * (``2026-01-02 03:04:05`` from the database). Browsers read such strings as
 * *local* time, so this module treats any zone-less date-time as UTC.
 *
 * Markup: ``<span data-utc="ISO" data-utc-format="datetime|date|time">``.
 * Date-only values (``2026-01-02``) are calendar dates and are never shifted.
 *
 * Exposes ``window.MyPortalTime`` for page scripts:
 *   parse(value)          -> Date | null (zone-less input is UTC)
 *   toDate(value)         -> Date (Invalid Date when unparseable)
 *   format(value, mode)   -> localised string ('' when unparseable)
 *   localise(root)        -> re-render [data-utc] elements under root
 */
(function () {
  'use strict';

  if (window.MyPortalTime) {
    return;
  }

  const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
  const HAS_ZONE_RE = /(?:[zZ]|[+-]\d{2}(?::?\d{2})?)$/;
  const NAIVE_RE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;

  function isDateOnly(value) {
    return typeof value === 'string' && DATE_ONLY_RE.test(value.trim());
  }

  function parse(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : value;
    }
    if (typeof value === 'number') {
      const fromNumber = new Date(value);
      return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
    }
    let text = String(value).trim();
    if (!text) {
      return null;
    }
    if (DATE_ONLY_RE.test(text)) {
      text += 'T00:00:00Z';
    } else {
      const naive = NAIVE_RE.exec(text);
      if (naive) {
        text = naive[1] + 'T' + naive[2] + 'Z';
      } else if (/^\d{4}-\d{2}-\d{2} /.test(text) && HAS_ZONE_RE.test(text)) {
        text = text.replace(' ', 'T');
      }
      // "+0000" -> "+00:00" for Safari.
      text = text.replace(/([+-]\d{2})(\d{2})$/, '$1:$2');
    }
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function format(value, mode) {
    const date = parse(value);
    if (!date) {
      return '';
    }
    // Calendar dates carry no time zone; render them as-is.
    if (isDateOnly(typeof value === 'string' ? value : '')) {
      return date.toLocaleDateString(undefined, { timeZone: 'UTC' });
    }
    switch (mode) {
      case 'date':
        return date.toLocaleDateString();
      case 'time':
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      case 'short':
        return date.toLocaleString([], {
          year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
      default:
        return date.toLocaleString();
    }
  }

  function render(element) {
    const raw = element.getAttribute('data-utc');
    if (!raw) {
      return;
    }
    const text = format(raw, element.getAttribute('data-utc-format') || 'datetime');
    if (!text) {
      return;
    }
    element.textContent = text;
    element.setAttribute('data-utc-localised', raw);
  }

  // title="Sent 2026-01-02 03:04 UTC" data-utc-title="2026-01-02T03:04:00Z"
  // -> the UTC fallback inside the tooltip becomes local time.
  function renderTitle(element) {
    const raw = element.getAttribute('data-utc-title');
    const title = element.getAttribute('title');
    const date = parse(raw);
    if (!date || !title || element.getAttribute('data-utc-title-localised') === raw) {
      return;
    }
    const fallback = date.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
    if (title.indexOf(fallback) !== -1) {
      element.setAttribute('title', title.split(fallback).join(format(raw, 'datetime')));
    }
    element.setAttribute('data-utc-title-localised', raw);
  }

  function titleCandidates(root) {
    const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
    const list = Array.from(scope.querySelectorAll('[data-utc-title]'));
    if (scope.nodeType === 1 && scope.hasAttribute('data-utc-title')) {
      list.unshift(scope);
    }
    return list;
  }

  function candidates(root) {
    const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
    const list = Array.from(scope.querySelectorAll('[data-utc]'));
    if (scope.nodeType === 1 && scope.hasAttribute('data-utc')) {
      list.unshift(scope);
    }
    return list;
  }

  function localise(root) {
    candidates(root).forEach(render);
    titleCandidates(root).forEach(renderTitle);
  }

  // Elements added later by page scripts may already hold their own chosen
  // wording ("3 hours ago"). Only fill in ones still showing the raw value,
  // nothing at all, or an explicit server fallback (data-utc-format).
  function shouldRenderDynamic(element) {
    const raw = element.getAttribute('data-utc');
    if (!raw || element.getAttribute('data-utc-localised') === raw) {
      return false;
    }
    if (element.hasAttribute('data-utc-format') || element.hasAttribute('data-utc-pending')) {
      return true;
    }
    const text = (element.textContent || '').trim();
    return text === '' || text === raw.trim() || text === '—';
  }

  function observe() {
    if (typeof MutationObserver !== 'function' || !document.body) {
      return;
    }
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'attributes') {
          const target = mutation.target;
          if (target.nodeType === 1 && shouldRenderDynamic(target)) {
            render(target);
          }
          return;
        }
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType !== 1) {
            return;
          }
          candidates(node).forEach((element) => {
            if (shouldRenderDynamic(element)) {
              render(element);
            }
          });
          titleCandidates(node).forEach(renderTitle);
        });
      });
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-utc'],
    });
  }

  // Like ``new Date(value)`` but zone-less input is UTC; invalid -> Invalid Date.
  function toDate(value) {
    return parse(value) || new Date(NaN);
  }

  window.MyPortalTime = { parse, toDate, format, localise, isDateOnly };

  function start() {
    localise(document);
    observe();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
