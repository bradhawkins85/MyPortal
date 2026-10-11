(function () {
  'use strict';

  // Script library (rmm/scripts.html): the left navigation lists folders and
  // scripts; choosing one swaps the right-hand panel without a full page load.
  // Without JavaScript every link is an ordinary page load.

  const library = document.querySelector('[data-rmm-library]');
  const nav = library && library.querySelector('[data-rmm-nav]');
  const detail = library && library.querySelector('[data-rmm-detail]');
  if (!library || !nav || !detail) {
    return;
  }

  const shared = window.MyPortalRmm || {};
  const attempted = new Set();
  let loadToken = 0;

  function formatDate(value) {
    if (typeof shared.formatDate === 'function') {
      return shared.formatDate(value);
    }
    return value;
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  // ------------------------------------------------------------------ //
  // Search
  // ------------------------------------------------------------------ //

  const search = nav.querySelector('[data-rmm-script-search]');
  if (search) {
    search.addEventListener('input', () => {
      const term = search.value.trim().toLowerCase();
      let visible = 0;
      nav.querySelectorAll('[data-rmm-group]').forEach((group) => {
        let groupVisible = 0;
        group.querySelectorAll('[data-rmm-script-item]').forEach((item) => {
          const match = !term || (item.dataset.search || '').includes(term);
          item.hidden = !match;
          if (match) {
            groupVisible += 1;
          }
        });
        group.hidden = groupVisible === 0;
        visible += groupVisible;
      });
      const empty = nav.querySelector('[data-rmm-script-empty]');
      if (empty) {
        empty.hidden = visible > 0;
      }
    });
  }

  // ------------------------------------------------------------------ //
  // Panel navigation
  // ------------------------------------------------------------------ //

  // Only ever load this page: a script id, or '' for the overview.
  function scriptIdFrom(href) {
    const value = new URL(href, window.location.href).searchParams.get('script') || '';
    return /^[0-9]+$/.test(value) ? value : '';
  }

  function panelUrl(scriptId) {
    return '/rmm/scripts' + (scriptId ? '?script=' + encodeURIComponent(scriptId) : '');
  }

  function markCurrent(scriptId) {
    nav.querySelectorAll('[data-rmm-nav-link]').forEach((link) => {
      const current = (link.getAttribute('data-rmm-script-link') || '') === scriptId;
      if (current) {
        link.setAttribute('aria-current', 'true');
      } else {
        link.removeAttribute('aria-current');
      }
    });
  }

  async function loadPanel(scriptId, options) {
    const url = panelUrl(scriptId);
    const settings = Object.assign({ push: true, focus: true }, options || {});
    const token = ++loadToken;
    detail.setAttribute('aria-busy', 'true');
    let response;
    try {
      response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
    } catch (error) {
      window.location.href = url;
      return;
    }
    if (token !== loadToken) {
      return;
    }
    const html = response.ok ? await response.text() : '';
    const doc = html ? new DOMParser().parseFromString(html, 'text/html') : null;
    const incoming = doc && doc.querySelector('[data-rmm-detail]');
    if (!incoming) {
      // Signed out, lost access or a server error: let the browser show it.
      window.location.href = url;
      return;
    }
    detail.replaceChildren(...Array.from(incoming.childNodes).map((node) => document.importNode(node, true)));
    detail.removeAttribute('aria-busy');
    if (doc.title) {
      document.title = doc.title;
    }
    if (settings.push) {
      window.history.pushState({ rmmLibrary: true }, '', url);
    }
    markCurrent(scriptId);
    initPanel();
    if (settings.focus) {
      const heading = detail.querySelector('h2');
      if (heading) {
        heading.setAttribute('tabindex', '-1');
        heading.focus({ preventScroll: true });
      }
      if (detail.getBoundingClientRect().top < 0 || window.matchMedia('(max-width: 1024px)').matches) {
        detail.scrollIntoView({ block: 'start' });
      }
    }
  }

  nav.addEventListener('click', (event) => {
    const link = event.target.closest('[data-rmm-nav-link]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    if (link.getAttribute('aria-current') === 'true') {
      return;
    }
    loadPanel(scriptIdFrom(link.href));
  });

  window.addEventListener('popstate', () => {
    loadPanel(scriptIdFrom(window.location.href), { push: false, focus: false });
  });

  // ------------------------------------------------------------------ //
  // AI summary and source
  // ------------------------------------------------------------------ //

  async function generateSummary(card) {
    const scriptId = card.getAttribute('data-rmm-ai');
    const button = card.querySelector('[data-rmm-ai-generate]');
    const busy = card.querySelector('[data-rmm-ai-busy]');
    const failure = card.querySelector('[data-rmm-ai-error]');
    const empty = card.querySelector('[data-rmm-ai-empty]');
    attempted.add(scriptId);
    if (button) {
      button.disabled = true;
    }
    if (busy) {
      busy.hidden = false;
    }
    if (empty) {
      empty.hidden = true;
    }
    if (failure) {
      failure.hidden = true;
    }
    try {
      const response = await fetch('/api/rmm/scripts/' + encodeURIComponent(scriptId) + '/summary', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken() },
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'The summary could not be written (' + response.status + ').');
      }
      // Show the new summary only if the same script is still open.
      const open = detail.querySelector('[data-rmm-script-detail]');
      if (open && open.getAttribute('data-rmm-script-detail') === scriptId) {
        await loadPanel(scriptId, { push: false, focus: false });
      }
    } catch (error) {
      if (!card.isConnected) {
        return;
      }
      if (busy) {
        busy.hidden = true;
      }
      if (empty) {
        empty.hidden = false;
      }
      if (failure) {
        failure.textContent = error.message;
        failure.hidden = false;
      }
      if (button) {
        button.disabled = false;
      }
    }
  }

  function initPanel() {
    detail.querySelectorAll('[data-utc]').forEach((node) => {
      node.textContent = formatDate(node.getAttribute('data-utc'));
    });
    const card = detail.querySelector('[data-rmm-ai]');
    if (card) {
      const button = card.querySelector('[data-rmm-ai-generate]');
      if (button) {
        button.addEventListener('click', () => generateSummary(card));
      }
      // Write a missing or out-of-date summary once per script per visit.
      if (card.hasAttribute('data-rmm-ai-auto') && !attempted.has(card.getAttribute('data-rmm-ai'))) {
        generateSummary(card);
      }
    }
    const copy = detail.querySelector('[data-rmm-copy-source]');
    const source = detail.querySelector('[data-rmm-source]');
    if (copy && source && navigator.clipboard) {
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(source.textContent.replace(/\n$/, ''));
          copy.textContent = 'Copied';
        } catch (error) {
          copy.textContent = 'Copy failed';
        }
        window.setTimeout(() => { copy.textContent = 'Copy'; }, 2000);
      });
    } else if (copy) {
      copy.hidden = true;
    }
  }

  window.history.replaceState({ rmmLibrary: true }, '');
  initPanel();
})();
