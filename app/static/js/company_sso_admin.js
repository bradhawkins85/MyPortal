(function () {
  'use strict';

  // Microsoft sign-in section of the edit-company page.
  //
  // Everything works without JavaScript through plain forms (a pasted
  // Microsoft object ID links a person). This script enhances it: it filters
  // the people list, reveals the "who keeps password sign-in" warning as soon
  // as Required is picked, confirms unlinking, and swaps the object ID form
  // for a directory search modal that talks to the REST API.

  const SEARCH_DELAY_MS = 300;
  const MIN_QUERY_LENGTH = 2;
  const GENERIC_ERROR = 'Something went wrong. Try again.';
  const NETWORK_ERROR = "Couldn't reach MyPortal. Check your connection and try again.";

  function normalise(value) {
    return (value || '').toString().toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function readData() {
    const element = document.getElementById('company-sso-data');
    if (!element) {
      return null;
    }
    try {
      return JSON.parse(element.textContent || '{}');
    } catch (error) {
      return null;
    }
  }

  async function request(method, url, body) {
    const headers = { Accept: 'application/json', 'X-CSRF-Token': csrfToken() };
    const options = { method, headers, credentials: 'same-origin' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (response.ok) {
      return response.status === 204 ? null : response.json();
    }
    let message = GENERIC_ERROR;
    try {
      const payload = await response.json();
      if (payload && typeof payload.detail === 'string') {
        message = payload.detail;
      } else if (response.status === 422) {
        message = 'Check the details and try again.';
      }
    } catch (error) {
      // Keep the generic message when the body is not JSON.
    }
    const failure = new Error(message);
    failure.status = response.status;
    throw failure;
  }

  function enhancePanel(panel) {
    panel.querySelectorAll('[data-sso-fallback]').forEach((element) => {
      element.hidden = true;
    });
    panel.querySelectorAll('[data-sso-link]').forEach((button) => {
      button.hidden = false;
    });

    panel.querySelectorAll('form[data-sso-confirm]').forEach((form) => {
      form.addEventListener('submit', (event) => {
        if (!window.confirm(form.dataset.ssoConfirm || 'Are you sure?')) {
          event.preventDefault();
        }
      });
    });

    const warning = panel.querySelector('[data-sso-unlinked-warning]');
    const helpRows = Array.from(panel.querySelectorAll('[data-sso-mode-help]'));
    panel.querySelectorAll('[data-sso-mode-input]').forEach((input) => {
      input.addEventListener('change', () => {
        if (!input.checked) {
          return;
        }
        helpRows.forEach((row) => {
          row.classList.toggle('is-current', row.dataset.ssoModeHelp === input.value);
        });
        if (warning) {
          warning.hidden = input.value !== 'required';
        }
      });
    });

    const search = panel.querySelector('[data-sso-search]');
    const items = Array.from(panel.querySelectorAll('[data-sso-item]'));
    const noResults = panel.querySelector('[data-sso-no-results]');
    if (search) {
      search.addEventListener('input', () => {
        const term = normalise(search.value);
        let visible = 0;
        items.forEach((item) => {
          const match = !term || normalise(item.dataset.ssoSearchText).includes(term);
          item.hidden = !match;
          if (match) {
            visible += 1;
          }
        });
        if (noResults) {
          noResults.hidden = visible > 0 || !term;
        }
      });
    }
  }

  function initModal(modal, data) {
    const members = new Map((data.members || []).map((member) => [String(member.userId), member]));
    const subtitle = modal.querySelector('[data-sso-subtitle]');
    const current = modal.querySelector('[data-sso-current]');
    const query = modal.querySelector('[data-sso-query]');
    const status = modal.querySelector('[data-sso-status]');
    const results = modal.querySelector('[data-sso-results]');
    const errorBox = modal.querySelector('[data-sso-error]');
    const submit = modal.querySelector('[data-sso-submit]');
    const unlink = modal.querySelector('[data-sso-unlink]');
    if (!query || !results || !submit || !unlink) {
      return;
    }

    const state = { member: null, selected: null, timer: null, controller: null, sequence: 0, trigger: null };

    function showError(message) {
      if (!errorBox) {
        return;
      }
      errorBox.textContent = message || '';
      errorBox.hidden = !message;
    }

    function setStatus(message) {
      if (status) {
        status.textContent = message || '';
      }
    }

    function setBusy(busy) {
      submit.disabled = busy || !state.selected;
      unlink.disabled = busy;
      query.disabled = busy;
    }

    function clearResults() {
      results.replaceChildren();
      state.selected = null;
      submit.disabled = true;
    }

    function selectResult(item, button) {
      state.selected = item;
      results.querySelectorAll('[role="option"]').forEach((option) => {
        option.setAttribute('aria-selected', 'false');
        option.classList.remove('is-selected');
      });
      const option = button.closest('[role="option"]');
      if (option) {
        option.setAttribute('aria-selected', 'true');
        option.classList.add('is-selected');
      }
      submit.disabled = false;
      showError('');
    }

    function renderResults(items) {
      clearResults();
      if (!items.length) {
        setStatus('No one in this tenant matched that search.');
        return;
      }
      setStatus(items.length + (items.length === 1 ? ' match.' : ' matches.') + ' Choose one to link.');
      items.forEach((item) => {
        const option = document.createElement('li');
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sso-result';
        const name = document.createElement('span');
        name.className = 'sso-result__name';
        name.textContent = item.display_name || item.upn || item.mail || item.object_id;
        const detail = document.createElement('span');
        detail.className = 'sso-result__detail';
        detail.textContent = [item.upn, item.mail && item.mail !== item.upn ? item.mail : '']
          .filter(Boolean)
          .join(' · ');
        button.append(name, detail);
        button.addEventListener('click', () => selectResult(item, button));
        option.append(button);
        results.append(option);
      });
    }

    async function runSearch(term) {
      if (state.controller) {
        state.controller.abort();
      }
      state.controller = typeof AbortController === 'function' ? new AbortController() : null;
      const sequence = (state.sequence += 1);
      setStatus('Searching…');
      showError('');
      try {
        const url = '/api/companies/' + encodeURIComponent(data.companyId) + '/sso/directory?q=' + encodeURIComponent(term);
        const options = { headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken() }, credentials: 'same-origin' };
        if (state.controller) {
          options.signal = state.controller.signal;
        }
        const response = await fetch(url, options);
        if (sequence !== state.sequence) {
          return;
        }
        if (!response.ok) {
          let message = GENERIC_ERROR;
          try {
            const payload = await response.json();
            if (payload && typeof payload.detail === 'string') {
              message = payload.detail;
            }
          } catch (error) {
            // Keep the generic message.
          }
          clearResults();
          setStatus('');
          showError(message);
          return;
        }
        renderResults(await response.json());
      } catch (error) {
        if (error && error.name === 'AbortError') {
          return;
        }
        if (sequence === state.sequence) {
          clearResults();
          setStatus('');
          showError(NETWORK_ERROR);
        }
      }
    }

    query.addEventListener('input', () => {
      window.clearTimeout(state.timer);
      const term = query.value.trim();
      if (term.length < MIN_QUERY_LENGTH) {
        if (state.controller) {
          state.controller.abort();
        }
        state.sequence += 1;
        clearResults();
        setStatus(term ? 'Type at least two characters.' : '');
        showError('');
        return;
      }
      state.timer = window.setTimeout(() => runSearch(term), SEARCH_DELAY_MS);
    });

    function reloadAtSection() {
      window.location.hash = 'ce-microsoft-sign-in';
      window.location.reload();
    }

    submit.addEventListener('click', async () => {
      if (!state.member || !state.selected) {
        return;
      }
      showError('');
      setBusy(true);
      try {
        await request('PUT', '/api/users/' + encodeURIComponent(state.member.userId) + '/sso-identity', {
          company_id: Number(data.companyId),
          object_id: state.selected.object_id,
        });
        setStatus('Linked. Refreshing…');
        reloadAtSection();
      } catch (error) {
        setBusy(false);
        showError(error.status ? error.message : NETWORK_ERROR);
      }
    });

    unlink.addEventListener('click', async () => {
      if (!state.member) {
        return;
      }
      if (!window.confirm('Unlink ' + state.member.name + " from their Microsoft account? They will sign in with their MyPortal password instead.")) {
        return;
      }
      showError('');
      setBusy(true);
      try {
        await request(
          'DELETE',
          '/api/users/' + encodeURIComponent(state.member.userId) + '/sso-identity?company_id=' + encodeURIComponent(data.companyId),
        );
        setStatus('Unlinked. Refreshing…');
        reloadAtSection();
      } catch (error) {
        setBusy(false);
        showError(error.status ? error.message : NETWORK_ERROR);
      }
    });

    function openModal(member, trigger) {
      state.member = member;
      state.trigger = trigger || null;
      query.value = '';
      query.disabled = false;
      clearResults();
      setStatus('');
      showError('');
      if (subtitle) {
        subtitle.textContent = 'Choose the Microsoft account ' + member.name + ' signs in with.';
      }
      if (current) {
        current.hidden = !member.upn;
        current.textContent = member.upn ? 'Currently linked to ' + member.upn + '. Choosing another account replaces it.' : '';
      }
      unlink.hidden = !member.linkedVia;
      unlink.disabled = false;
      submit.textContent = member.linkedVia ? 'Re-link' : 'Link';
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      window.requestAnimationFrame(() => query.focus());
    }

    function closeModal() {
      window.clearTimeout(state.timer);
      if (state.controller) {
        state.controller.abort();
      }
      state.sequence += 1;
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      if (state.trigger && typeof state.trigger.focus === 'function') {
        state.trigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });

    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled])',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    document.querySelectorAll('[data-sso-link]').forEach((button) => {
      button.addEventListener('click', () => {
        const member = members.get(button.dataset.ssoLink);
        if (member && member.canManage) {
          openModal(member, button);
        }
      });
    });
  }

  function init() {
    const panel = document.querySelector('[data-sso-panel]');
    const modal = document.getElementById('sso-link-modal');
    const data = readData();
    if (!panel || !modal || !data) {
      return;
    }
    enhancePanel(panel);
    initModal(modal, data);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
