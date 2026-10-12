/* RMM findings page (rmm/findings.html): header filters, the finding review
   modal and live status while an analysis runs. */
(function () {
  'use strict';

  const workspace = document.querySelector('[data-findings-workspace]');
  if (!workspace) {
    return;
  }

  const STORAGE_KEY = 'myportal.rmmFindings.filters';
  const POLL_MS = 5000;
  const POLL_LIMIT_MS = 20 * 60 * 1000;

  let findings = [];
  try {
    findings = JSON.parse(document.getElementById('findings-data')?.textContent || '[]');
  } catch (error) {
    findings = [];
  }
  const byId = new Map(findings.map((finding) => [String(finding.id), finding]));

  function formatDate(value) {
    if (!value) {
      return '';
    }
    const date = new Date(/Z|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  // Times are stored in UTC; show them in the viewer's time zone.
  document.querySelectorAll('[data-findings-workspace] [data-utc], [data-findings-run-status] [data-utc]').forEach((node) => {
    node.textContent = formatDate(node.getAttribute('data-utc'));
  });

  // ------------------------------------------------------------------ //
  // Filters (the header renders outside the content, so look up globally)
  // ------------------------------------------------------------------ //

  const filterGroup = document.querySelector('[data-findings-filters]');
  const searchInput = document.querySelector('[data-findings-search]');
  const selects = Array.from(document.querySelectorAll('[data-findings-filter]'));
  const info = document.querySelector('[data-findings-info]');
  const noResults = workspace.querySelector('[data-findings-no-results]');
  const cards = Array.from(workspace.querySelectorAll('[data-finding-card]'));
  const viewSelect = document.querySelector('[data-findings-view]');

  if (viewSelect) {
    viewSelect.addEventListener('change', () => viewSelect.form?.submit());
  }

  function loadFilters() {
    try {
      return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') || {};
    } catch (error) {
      return {};
    }
  }

  function saveFilters(values) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
    } catch (error) {
      /* Private mode or blocked storage: filters just are not remembered. */
    }
  }

  function currentFilters() {
    const values = { q: (searchInput?.value || '').trim().toLowerCase() };
    selects.forEach((select) => {
      values[select.dataset.findingsFilter] = select.value;
    });
    return values;
  }

  function matches(card, filters) {
    if (filters.severity && card.dataset.severity !== filters.severity) {
      return false;
    }
    if (filters.category && card.dataset.category !== filters.category) {
      return false;
    }
    if (filters.status === 'review' && card.dataset.status === 'dismissed') {
      return false;
    }
    if (filters.status && filters.status !== 'review' && card.dataset.status !== filters.status) {
      return false;
    }
    if (filters.q) {
      return filters.q.split(/\s+/).every((word) => (card.dataset.search || '').includes(word));
    }
    return true;
  }

  function applyFilters() {
    const filters = currentFilters();
    let shown = 0;
    cards.forEach((card) => {
      const visible = matches(card, filters);
      card.parentElement.hidden = !visible;
      shown += visible ? 1 : 0;
    });
    workspace.querySelectorAll('[data-findings-group]').forEach((group) => {
      const count = Array.from(group.querySelectorAll('[data-finding-card]'))
        .filter((card) => !card.parentElement.hidden).length;
      group.hidden = count === 0;
      const badge = group.querySelector('[data-findings-group-count]');
      if (badge) {
        badge.textContent = String(count);
      }
    });
    if (noResults) {
      noResults.hidden = shown > 0 || cards.length === 0;
    }
    if (info) {
      info.textContent = shown === cards.length
        ? `${cards.length} finding${cards.length === 1 ? '' : 's'}`
        : `Showing ${shown} of ${cards.length}`;
    }
    saveFilters(filters);
  }

  if (filterGroup) {
    filterGroup.hidden = false;
    const saved = loadFilters();
    if (searchInput && typeof saved.q === 'string') {
      searchInput.value = saved.q;
    }
    selects.forEach((select) => {
      const value = saved[select.dataset.findingsFilter];
      if (typeof value === 'string' && Array.from(select.options).some((option) => option.value === value)) {
        select.value = value;
      }
      select.addEventListener('change', applyFilters);
    });
    let timer = null;
    searchInput?.addEventListener('input', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(applyFilters, 120);
    });
    // Enter in the search box filters; it must not submit the view form.
    searchInput?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        applyFilters();
      }
    });
    applyFilters();
  }

  // ------------------------------------------------------------------ //
  // Review modal
  // ------------------------------------------------------------------ //

  const modal = workspace.querySelector('[data-finding-modal]');
  const form = modal?.querySelector('[data-finding-form]');
  const note = modal?.querySelector('[data-finding-note]');
  let lastTrigger = null;
  let noteSnapshot = '';

  function field(name) {
    return modal.querySelector(`[data-finding-field="${name}"]`);
  }

  function setText(name, value) {
    const node = field(name);
    if (node) {
      node.textContent = value || '';
    }
  }

  function fillList(name, items, render, emptyText) {
    const list = field(name);
    if (!list) {
      return;
    }
    list.replaceChildren();
    if (!items.length) {
      const item = document.createElement('li');
      item.className = 'text-muted';
      item.textContent = emptyText;
      list.append(item);
      return;
    }
    items.forEach((value) => list.append(render(value)));
  }

  function reviewText(finding) {
    if (finding.status === 'open') {
      return 'Not reviewed yet.';
    }
    const when = finding.status_changed_at ? ` ${formatDate(finding.status_changed_at)}` : '';
    const text = `${finding.status_label}${when}.`;
    return finding.status_note ? `${text} “${finding.status_note}”` : text;
  }

  function openFinding(id, trigger) {
    const finding = byId.get(String(id));
    if (!finding || !modal || !form) {
      return;
    }
    lastTrigger = trigger || null;
    form.action = `/rmm/findings/${encodeURIComponent(finding.id)}/status`;
    setText('eyebrow', `${finding.category_label} · ${finding.source_label}`);
    setText('title', finding.title);
    const severity = field('severity');
    if (severity) {
      severity.className = `status status--${finding.severity_variant}`;
      severity.textContent = finding.severity_label;
    }
    const seen = [`First seen ${formatDate(finding.first_seen_at)}`];
    if (finding.occurrences > 1) {
      seen.push(`found in ${finding.occurrences} analyses`);
    }
    if (!finding.is_active) {
      seen.push('no longer detected');
    }
    setText('seen', seen.join(' · '));
    setText('summary', finding.summary);
    setText('recommendation', finding.recommendation);
    modal.querySelector('[data-finding-section="recommendation"]').hidden = !finding.recommendation;
    modal.querySelector('[data-finding-section="evidence"]').hidden = !finding.evidence.length;
    fillList('evidence', finding.evidence, (line) => {
      const item = document.createElement('li');
      item.textContent = line;
      return item;
    }, '');
    fillList('devices', finding.devices, (device) => {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = `/assets/${encodeURIComponent(device.asset_id)}`;
      link.textContent = device.name;
      item.append(link);
      if (device.company) {
        const company = document.createElement('small');
        company.textContent = device.company;
        item.append(company);
      }
      return item;
    }, 'No specific devices');
    fillList('companies', finding.companies, (company) => {
      const item = document.createElement('li');
      item.textContent = company.name;
      return item;
    }, 'Not specific to a company');
    setText('review', reviewText(finding));

    const confidence = modal.querySelector('[data-finding-confidence]');
    const hasConfidence = finding.source === 'ai' && finding.confidence !== null && finding.confidence !== undefined;
    confidence.hidden = !hasConfidence;
    if (hasConfidence) {
      const bar = confidence.querySelector('[data-finding-confidence-bar]');
      bar.setAttribute('aria-valuenow', String(finding.confidence));
      bar.setAttribute('aria-valuetext', `${finding.confidence}% confident`);
      bar.firstElementChild.style.width = `${Math.max(0, Math.min(100, finding.confidence))}%`;
      confidence.querySelector('[data-finding-confidence-text]').textContent =
        `${finding.confidence}% confident. AI insights are a best guess; check the evidence before acting.`;
    }

    modal.querySelectorAll('[data-finding-action]').forEach((button) => {
      button.hidden = button.dataset.findingAction === finding.status;
    });
    note.value = finding.status_note || '';
    noteSnapshot = note.value;

    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    const body = modal.querySelector('.modal__body');
    if (body) {
      body.scrollTop = 0;
    }
    window.requestAnimationFrame(() => modal.querySelector('.modal__close')?.focus());
  }

  function closeModal(force) {
    if (!force && note.value !== noteSnapshot && !window.confirm('Discard your note?')) {
      return;
    }
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (lastTrigger && typeof lastTrigger.focus === 'function') {
      lastTrigger.focus();
    }
  }

  if (modal && form) {
    workspace.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-finding-open]');
      if (trigger) {
        event.preventDefault();
        openFinding(trigger.dataset.findingOpen, trigger);
      }
    });
    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeModal(false);
      });
    });
    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal(false);
      }
    });
    form.addEventListener('submit', (event) => {
      const button = event.submitter;
      if (button) {
        button.classList.add('button--processing');
      }
      noteSnapshot = note.value;
    });
  }

  // ------------------------------------------------------------------ //
  // Analysis status
  // ------------------------------------------------------------------ //

  document.querySelectorAll('[data-findings-run-button]').forEach((button) => {
    button.closest('form')?.addEventListener('submit', () => {
      button.disabled = true;
      button.querySelector('span').textContent = 'Starting…';
    });
  });

  const runPanel = workspace.querySelector('[data-findings-run]');
  if (runPanel && runPanel.dataset.runStatus === 'running') {
    const status = runPanel.querySelector('[data-findings-run-status]');
    const started = Date.now();
    const poll = async () => {
      if (Date.now() - started > POLL_LIMIT_MS) {
        if (status) {
          status.textContent = 'Still analysing. Reload the page later to see the results.';
        }
        return;
      }
      try {
        const response = await fetch('/api/rmm/findings/runs/latest', {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        if (response.ok) {
          const data = await response.json();
          if (data.run && data.run.status !== 'running') {
            if (status) {
              status.textContent = 'Analysis finished. Loading the findings…';
            }
            window.location.reload();
            return;
          }
        }
      } catch (error) {
        /* Network blip: try again on the next poll. */
      }
      window.setTimeout(poll, POLL_MS);
    };
    window.setTimeout(poll, POLL_MS);
  }
})();
