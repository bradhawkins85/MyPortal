(function () {

  const TICKET_SECTION_STATE_STORAGE_KEY = 'myportal.admin.ticketDetail.sectionState.v1';

  function formatApiErrorDetail(detail) {
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map(formatApiErrorDetail)
        .filter(Boolean);
      return messages.join('; ');
    }

    if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string' && detail.message.trim()) {
        return detail.message;
      }
      if (typeof detail.msg === 'string' && detail.msg.trim()) {
        return detail.msg;
      }
      if (typeof detail.error === 'string' && detail.error.trim()) {
        return detail.error;
      }
      try {
        return JSON.stringify(detail);
      } catch (error) {
        return '';
      }
    }

    return '';
  }

  async function getApiErrorMessage(response, fallback) {
    const fallbackMessage = fallback || 'Request failed';
    const contentType = response.headers && response.headers.get
      ? response.headers.get('content-type') || ''
      : '';

    if (contentType.includes('application/json')) {
      const errorData = await response.json().catch(() => ({}));
      return (
        formatApiErrorDetail(errorData.detail)
        || formatApiErrorDetail(errorData)
        || fallbackMessage
      );
    }

    const text = await response.text().catch(() => '');
    return text.trim() || fallbackMessage;
  }

  function readTicketSectionState() {
    try {
      const raw = window.localStorage.getItem(TICKET_SECTION_STATE_STORAGE_KEY);
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (error) {
      console.warn('Unable to read ticket section preferences:', error);
      return {};
    }
  }

  function writeTicketSectionState(state) {
    try {
      window.localStorage.setItem(TICKET_SECTION_STATE_STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
      console.warn('Unable to save ticket section preferences:', error);
    }
  }

  function getTicketSectionStateKey(details, index) {
    if (!(details instanceof HTMLDetailsElement)) {
      return '';
    }

    const explicitKey = details.getAttribute('data-ticket-section-state-key');
    if (explicitKey && explicitKey.trim()) {
      return explicitKey.trim();
    }

    const title = details.querySelector('summary .card__title, summary h2, summary h3, summary');
    const label = title ? title.textContent.replace(/\s+/g, ' ').trim().toLowerCase() : '';
    const slug = label.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return slug || `section-${index + 1}`;
  }

  function initialisePersistentTicketSections() {
    const sections = Array.from(document.querySelectorAll('details.card-collapsible'));
    if (!sections.length || !window.localStorage) {
      return;
    }

    const state = readTicketSectionState();

    sections.forEach((section, index) => {
      if (!(section instanceof HTMLDetailsElement)) {
        return;
      }

      const key = getTicketSectionStateKey(section, index);
      if (!key) {
        return;
      }

      section.setAttribute('data-ticket-section-state-key', key);

      if (Object.prototype.hasOwnProperty.call(state, key)) {
        section.open = state[key] === true;
      }

      section.addEventListener('toggle', () => {
        const latestState = readTicketSectionState();
        latestState[key] = section.open;
        writeTicketSectionState(latestState);
      });
    });
  }

  function getLineHeightPx(element) {
    const computed = window.getComputedStyle(element);
    const lineHeight = parseFloat(computed.lineHeight);
    if (!Number.isNaN(lineHeight)) {
      return lineHeight;
    }

    const fontSize = parseFloat(computed.fontSize);
    if (!Number.isNaN(fontSize)) {
      return fontSize * 1.5;
    }

    return 24;
  }

  function initialiseTimelineMessage(wrapper, toggle) {
    const content = wrapper.querySelector('[data-timeline-message-content]');
    if (!content) {
      return;
    }

    const lineHeight = getLineHeightPx(content);
    const maxCollapsedHeight = lineHeight * 5;
    const currentScrollHeight = content.scrollHeight;

    if (currentScrollHeight <= maxCollapsedHeight + 1) {
      return;
    }

    const moreLabel = toggle.dataset.moreLabel || 'Show more';
    const lessLabel = toggle.dataset.lessLabel || 'Show less';

    function collapse() {
      const collapsedHeight = `${maxCollapsedHeight}px`;
      wrapper.style.maxHeight = collapsedHeight;
      content.style.maxHeight = collapsedHeight;
      wrapper.classList.add('timeline__message--collapsed');
      content.classList.add('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = moreLabel;
    }

    function expand() {
      wrapper.style.maxHeight = '';
      content.style.maxHeight = '';
      wrapper.classList.remove('timeline__message--collapsed');
      content.classList.remove('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.textContent = lessLabel;
    }

    collapse();
    toggle.hidden = false;

    toggle.addEventListener('click', () => {
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
      if (isExpanded) {
        collapse();
      } else {
        expand();
      }
    });
  }

  function formatTimeSummary(minutes, isBillable, labourName) {
    if (typeof minutes !== 'number' || Number.isNaN(minutes) || minutes < 0) {
      return '';
    }
    const label = minutes === 1 ? 'minute' : 'minutes';
    const billing = isBillable ? 'Billable' : 'Non-billable';
    let summary = `${minutes} ${label} · ${billing}`;
    if (typeof labourName === 'string' && labourName.trim() !== '') {
      summary = `${summary} · ${labourName.trim()}`;
    }
    return summary;
  }

  function renderTimeSummary(container, minutes, isBillable, labourName, fallbackText = '') {
    if (!(container instanceof HTMLElement)) {
      return;
    }

    const summaryText = fallbackText || formatTimeSummary(minutes, isBillable, labourName);
    const hasSummary = typeof summaryText === 'string' && summaryText.trim() !== '';

    container.hidden = !hasSummary;
    container.replaceChildren();

    if (!hasSummary) {
      return;
    }

    const chips = [];
    const hasMinutes = typeof minutes === 'number' && !Number.isNaN(minutes) && minutes >= 0;
    if (hasMinutes) {
      const timeChip = document.createElement('span');
      timeChip.className = 'timeline__chip timeline__chip--time';
      const unit = minutes === 1 ? 'minute' : 'minutes';
      timeChip.textContent = `⏱️ ${minutes} ${unit}`;
      chips.push(timeChip);
    }

    const billableChip = document.createElement('span');
    billableChip.className = `timeline__chip ${isBillable ? 'timeline__chip--billable' : 'timeline__chip--nonbillable'}`;
    billableChip.textContent = isBillable ? 'Billable' : 'Non-billable';
    chips.push(billableChip);

    if (typeof labourName === 'string' && labourName.trim() !== '') {
      const labourChip = document.createElement('span');
      labourChip.className = 'timeline__chip timeline__chip--labour';
      labourChip.textContent = labourName.trim();
      chips.push(labourChip);
    }

    if (chips.length === 0) {
      container.textContent = summaryText;
      return;
    }

    chips.forEach((chip) => container.appendChild(chip));
  }

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getMetaContent(name) {
    const meta = document.querySelector(`meta[name="${name}"]`);
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function convertUtcElements(root) {
    const context = root && typeof root.querySelectorAll === 'function' ? root : document;
    context.querySelectorAll('[data-utc]').forEach((element) => {
      const iso = element.getAttribute('data-utc');
      if (!iso) {
        return;
      }
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      element.textContent = date.toLocaleString();
    });
  }

  function getCsrfToken() {
    const metaToken = getMetaContent('csrf-token');
    if (metaToken) {
      return metaToken;
    }
    return getCookie('myportal_session_csrf');
  }

  function initialiseTicketPageClock() {
    const clock = document.querySelector('[data-ticket-page-clock]');
    if (!clock) return;

    const ticketId = clock.dataset.ticketId;
    const display = clock.querySelector('[data-ticket-page-clock-display]');
    const historyButton = clock.querySelector('[data-ticket-page-clock-history]');
    const dialog = document.querySelector('[data-ticket-page-clock-dialog]');
    const historyContent = dialog && dialog.querySelector('[data-ticket-page-clock-history-content]');
    const closeButton = dialog && dialog.querySelector('[data-ticket-page-clock-close]');
    let clockId = null;
    const openedAt = Date.now();

    const headers = () => {
      const result = { Accept: 'application/json' };
      const csrfToken = getCsrfToken();
      if (csrfToken) result['X-CSRF-Token'] = csrfToken;
      return result;
    };
    const formatDuration = (seconds) => {
      const total = Math.max(0, Math.floor(seconds));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    };
    const tick = () => { if (display) display.textContent = formatDuration((Date.now() - openedAt) / 1000); };
    tick();
    window.setInterval(tick, 1000);

    async function send(path, keepalive) {
      return fetch(`/admin/tickets/${encodeURIComponent(ticketId)}/page-clocks${path}`, {
        method: 'POST', headers: headers(), keepalive: Boolean(keepalive), credentials: 'same-origin',
      });
    }
    async function start() {
      try {
        const response = await send('');
        if (!response.ok) return;
        const payload = await response.json();
        clockId = payload.clockId;
      } catch (error) { console.warn('Unable to start ticket clock:', error); }
    }
    function heartbeat() {
      if (clockId) send(`/${encodeURIComponent(clockId)}/heartbeat`).catch(() => {});
    }
    function stop() {
      if (clockId) send(`/${encodeURIComponent(clockId)}/stop`, true).catch(() => {});
    }
    start();
    window.setInterval(heartbeat, 60000);
    window.addEventListener('pagehide', stop, { once: true });

    // A screen wake lock reduces the chance of an actively viewed ticket page
    // being suspended. Browsers may still suspend background tabs by design.
    let wakeLock = null;
    async function requestWakeLock() {
      if (document.visibilityState !== 'visible' || !navigator.wakeLock) return;
      try { wakeLock = await navigator.wakeLock.request('screen'); } catch (error) { /* permission/device dependent */ }
    }
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') requestWakeLock(); });
    requestWakeLock();

    if (historyButton && dialog && historyContent) {
      historyButton.addEventListener('click', async () => {
        dialog.showModal();
        historyContent.textContent = 'Loading clock history…';
        try {
          const response = await fetch(`/admin/tickets/${encodeURIComponent(ticketId)}/page-clocks`, { headers: headers(), credentials: 'same-origin' });
          if (!response.ok) throw new Error('Unable to load clock history.');
          const payload = await response.json();
          const rows = Array.isArray(payload.clocks) ? payload.clocks : [];
          if (!rows.length) { historyContent.textContent = 'No ticket clock history yet.'; return; }
          const table = document.createElement('table'); table.className = 'ticket-page-clock-history';
          table.innerHTML = '<thead><tr><th>Technician</th><th>Opened</th><th>Duration</th></tr></thead>';
          const body = document.createElement('tbody');
          rows.forEach((entry) => {
            const started = new Date(entry.started_at); const finished = new Date(entry.ended_at || entry.last_seen_at);
            const row = document.createElement('tr');
            [entry.user_display_name || entry.user_email || 'Unknown', Number.isNaN(started) ? '—' : started.toLocaleString(), Number.isNaN(started) || Number.isNaN(finished) ? '—' : formatDuration((finished - started) / 1000)].forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell); });
            body.appendChild(row);
          });
          table.appendChild(body); historyContent.replaceChildren(table);
        } catch (error) { historyContent.textContent = error.message || 'Unable to load clock history.'; }
      });
      closeButton.addEventListener('click', () => dialog.close());
    }
  }


  function initTicketRelated() {
    const panel = document.querySelector('[data-ticket-related]');
    if (!panel) {
      return;
    }
    const ticketId = panel.getAttribute('data-ticket-id');
    const autoScan = panel.getAttribute('data-auto-scan') === 'true';
    const refreshButton = panel.querySelector('[data-ticket-related-refresh]');
    const status = panel.querySelector('[data-ticket-related-status]');
    const list = panel.querySelector('[data-ticket-related-list]');
    let hasScanned = false;
    let isScanning = false;

    function setStatus(message) {
      if (status) {
        status.textContent = message || '';
        status.hidden = !message;
      }
    }

    function renderItems(items) {
      if (!list) {
        return;
      }
      list.innerHTML = '';
      if (!Array.isArray(items) || items.length === 0) {
        list.hidden = true;
        setStatus('No related MyPortal content was found for this ticket.');
        return;
      }
      items.forEach((item) => {
        const li = document.createElement('li');
        li.className = 'ticket-related__item';
        const link = document.createElement('a');
        link.className = 'ticket-related__link';
        link.href = item.url || '#';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = item.label || item.url || 'Related item';
        link.title = link.textContent;
        li.appendChild(link);
        list.appendChild(li);
      });
      list.hidden = false;
      setStatus('');
    }

    async function scanRelated() {
      if (!ticketId || isScanning) {
        return;
      }
      isScanning = true;
      hasScanned = true;
      if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.classList.add('is-loading');
      }
      setStatus('Scanning this ticket for related MyPortal content...');
      try {
        const headers = { Accept: 'application/json' };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }
        const response = await fetch(`/admin/tickets/${encodeURIComponent(ticketId)}/related/rescan`, {
          method: 'POST',
          headers,
        });
        if (!response.ok) {
          throw new Error(`Scan failed (${response.status})`);
        }
        const payload = await response.json();
        renderItems(payload.items || []);
      } catch (error) {
        console.error('Failed to scan ticket related content:', error);
        setStatus(error.message || 'Failed to scan related content.');
      } finally {
        isScanning = false;
        if (refreshButton) {
          refreshButton.disabled = false;
          refreshButton.classList.remove('is-loading');
        }
      }
    }

    if (refreshButton) {
      refreshButton.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        scanRelated();
      });
    }

    panel.addEventListener('toggle', () => {
      if (panel.open && autoScan && !hasScanned) {
        scanRelated();
      }
    });
  }

  function parseMinutesValue(value) {
    if (typeof value !== 'string' || value.trim() === '') {
      return null;
    }
    const parsed = Number.parseInt(value, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      return null;
    }
    return parsed;
  }

  function parseDisplayNumber(element) {
    if (!element) {
      return 0;
    }
    const text = element.textContent || '';
    const parsed = Number.parseInt(text, 10);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function adjustTimeTotals(oldMinutes, oldBillable, newMinutes, newBillable) {
    const billableElement = document.querySelector('[data-ticket-billable-total]');
    const nonBillableElement = document.querySelector('[data-ticket-non-billable-total]');
    if (!billableElement || !nonBillableElement) {
      return;
    }

    let billableTotal = parseDisplayNumber(billableElement);
    let nonBillableTotal = parseDisplayNumber(nonBillableElement);

    if (typeof oldMinutes === 'number') {
      if (oldBillable) {
        billableTotal -= oldMinutes;
      } else {
        nonBillableTotal -= oldMinutes;
      }
    }

    if (typeof newMinutes === 'number') {
      if (newBillable) {
        billableTotal += newMinutes;
      } else {
        nonBillableTotal += newMinutes;
      }
    }

    billableTotal = Math.max(billableTotal, 0);
    nonBillableTotal = Math.max(nonBillableTotal, 0);

    billableElement.textContent = billableTotal.toString();
    nonBillableElement.textContent = nonBillableTotal.toString();
  }

  function parseJsonArray(value, fallback = []) {
    if (typeof value !== 'string' || value.trim() === '') {
      return Array.isArray(fallback) ? fallback : [];
    }
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : Array.isArray(fallback) ? fallback : [];
    } catch (error) {
      console.warn('Failed to parse JSON dataset value', error);
      return Array.isArray(fallback) ? fallback : [];
    }
  }

  function normaliseIdList(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    return values
      .map((value) => {
        if (typeof value === 'number') {
          return value.toString();
        }
        if (typeof value === 'string') {
          return value.trim();
        }
        if (value && typeof value === 'object' && 'id' in value) {
          return String(value.id);
        }
        return '';
      })
      .filter((value) => value !== '');
  }

  function formatAssetLabel(record) {
    if (!record || typeof record !== 'object') {
      return '';
    }
    const idValue = 'id' in record ? String(record.id) : '';
    let name = '';
    if (typeof record.label === 'string' && record.label.trim() !== '') {
      return record.label.trim();
    }
    if (typeof record.name === 'string' && record.name.trim() !== '') {
      name = record.name.trim();
    } else if (idValue) {
      name = `Asset ${idValue}`;
    }
    const serial = typeof record.serial_number === 'string' ? record.serial_number.trim() : '';
    const status = typeof record.status === 'string' ? record.status.trim() : '';
    const parts = [name];
    if (serial) {
      parts.push(`SN ${serial}`);
    }
    if (status) {
      const cleanedStatus = status
        .split('_')
        .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
        .join(' ');
      parts.push(cleanedStatus);
    }
    return parts.join(' · ');
  }


  function initialiseTicketDetailsAutosave() {
    const form = document.querySelector('[data-ticket-details-autosave]');
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    const statusElements = Array.from(document.querySelectorAll('[data-ticket-details-autosave-status]'));
    const externalSubmitButtons = form.id
      ? Array.from(document.querySelectorAll(`button[type="submit"][form="${form.id}"], input[type="submit"][form="${form.id}"]`))
      : [];
    const submitButtons = externalSubmitButtons.concat(
      Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]')),
    );
    let lastSavedSnapshot = new URLSearchParams(new FormData(form)).toString();
    let pendingTimer = null;
    let inFlight = null;
    let queued = false;

    function setStatus(message, state) {
      statusElements.forEach((element) => {
        element.textContent = message;
        element.classList.toggle('form-help--error', state === 'error');
      });
    }

    function currentSnapshot() {
      return new URLSearchParams(new FormData(form)).toString();
    }

    async function saveNow() {
      if (!form.reportValidity()) {
        setStatus('Fix the highlighted fields before changes can be saved.', 'error');
        return;
      }

      const snapshot = currentSnapshot();
      if (snapshot === lastSavedSnapshot) {
        return;
      }

      if (inFlight) {
        queued = true;
        return inFlight;
      }

      setStatus('Saving changes…', 'saving');
      submitButtons.forEach((button) => {
        button.disabled = true;
        button.setAttribute('aria-disabled', 'true');
      });

      inFlight = fetch(form.action, {
        method: form.method || 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: {
          Accept: 'text/html',
          'X-Requested-With': 'fetch',
        },
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.text()) || `Save failed with status ${response.status}`);
          }
          lastSavedSnapshot = snapshot;
          setStatus('Saved.', 'saved');
        })
        .catch((error) => {
          console.error('Failed to autosave ticket details', error);
          setStatus('Autosave failed. Use Save changes to retry.', 'error');
        })
        .finally(() => {
          inFlight = null;
          submitButtons.forEach((button) => {
            button.disabled = false;
            button.removeAttribute('aria-disabled');
          });
          if (queued) {
            queued = false;
            scheduleSave(0);
          }
        });

      return inFlight;
    }

    function scheduleSave(delayMs = 250) {
      window.clearTimeout(pendingTimer);
      pendingTimer = window.setTimeout(() => {
        saveNow();
      }, delayMs);
    }

    function isAutosavedControl(target) {
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
        return false;
      }
      return target.form === form;
    }

    document.addEventListener('focusout', (event) => {
      if (isAutosavedControl(event.target)) {
        scheduleSave();
      }
    });

    document.addEventListener('change', (event) => {
      if (isAutosavedControl(event.target)) {
        scheduleSave();
      }
    });

    document.addEventListener('ticket:details-autosave', () => {
      scheduleSave();
    });
  }

  function initialiseShipmentWatchProviderDetection() {
    const urlInput = document.querySelector('[data-shipment-watch-url]');
    const providerLabel = document.querySelector('[data-shipment-watch-provider]');
    if (!(urlInput instanceof HTMLInputElement) || !(providerLabel instanceof HTMLElement)) {
      return;
    }

    let inFlight = null;

    async function detectProvider() {
      const url = urlInput.value.trim();
      if (!url) {
        providerLabel.textContent = 'Not detected';
        urlInput.setCustomValidity('');
        return;
      }

      if (inFlight) {
        return;
      }

      providerLabel.textContent = 'Detecting…';
      const csrfToken = getCsrfToken();
      const headers = { Accept: 'application/json' };
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      inFlight = fetch(`/api/tickets/shipment-watch/detect?url=${encodeURIComponent(url)}`, {
        method: 'GET',
        headers,
        credentials: 'same-origin',
      })
        .then(async (response) => {
          if (!response.ok) {
            const message = await getApiErrorMessage(response, 'Unable to validate tracking URL');
            throw new Error(message);
          }
          const payload = await response.json();
          if (!payload || !payload.supported || !payload.provider) {
            urlInput.setCustomValidity('Unsupported tracking provider URL.');
            providerLabel.textContent = 'Unsupported provider';
            return;
          }
          urlInput.setCustomValidity('');
          providerLabel.textContent = String(payload.provider || '').trim() || 'Not detected';
        })
        .catch((error) => {
          providerLabel.textContent = 'Invalid URL';
          urlInput.setCustomValidity(error instanceof Error ? error.message : 'Invalid tracking URL');
        })
        .finally(() => {
          inFlight = null;
        });

      return inFlight;
    }

    urlInput.addEventListener('blur', () => {
      detectProvider();
    });
    urlInput.addEventListener('change', () => {
      detectProvider();
    });
  }

  function initialiseAssetSelector() {
    const select = document.querySelector('[data-ticket-asset-selector]');
    if (!(select instanceof HTMLSelectElement)) {
      return;
    }

    const companySelect = document.getElementById('ticket-company-detail');
    if (!(companySelect instanceof HTMLSelectElement)) {
      return;
    }

    const linkedContainer = document.querySelector('[data-ticket-linked-assets]');
    if (!linkedContainer) {
      return;
    }

    const linkedList = linkedContainer.querySelector('[data-linked-assets-list]');
    const emptyState = linkedContainer.querySelector('[data-linked-assets-empty]');
    const helpElement = document.querySelector('[data-ticket-asset-help]');
    const endpointTemplate = select.dataset.assetsEndpointTemplate || '';
    const initialOptions = parseJsonArray(select.dataset.initialOptions, []);
    const initialSelection = normaliseIdList(parseJsonArray(select.dataset.selectedAssets, []));
    const initialLinkedRecords = parseJsonArray(linkedContainer.dataset.initialLinked, []);
    const emptyMessage = linkedContainer.dataset.emptyMessage || 'No assets are linked to this ticket yet.';
    const detailsFormId = linkedContainer.dataset.detailsFormId || '';
    const tacticalBaseUrlRaw = linkedContainer.dataset.tacticalBaseUrl || '';
    const initialCompanyId = select.dataset.initialCompanyId || '';
    const ticketNumber = (linkedContainer.dataset.ticketNumber || '').trim();
    const ticketSubject = (linkedContainer.dataset.ticketSubject || '').trim();

    const tacticalBaseUrl = tacticalBaseUrlRaw.replace(/\/+$/, '');
    const optionLookup = new Map();
    const linkedMap = new Map();
    let allOptions = [];
    let currentCompanyId = initialCompanyId || companySelect.value || '';

    function setHelpState(state, overrideMessage) {
      if (!helpElement) {
        return;
      }
      const enabledMessage = helpElement.dataset.helpEnabled || helpElement.textContent || '';
      const disabledMessage = helpElement.dataset.helpDisabled || enabledMessage;
      const emptyLabel = helpElement.dataset.helpEmpty || 'No assets are available for the linked company.';
      const exhaustedLabel = helpElement.dataset.helpExhausted || emptyLabel;
      let message = enabledMessage;
      let isError = false;

      if (state === 'disabled') {
        message = overrideMessage || disabledMessage;
      } else if (state === 'loading') {
        message = overrideMessage || 'Loading assets…';
      } else if (state === 'error') {
        message = overrideMessage || 'Unable to load assets. Please try again.';
        isError = true;
      } else if (state === 'empty') {
        message = overrideMessage || emptyLabel;
      } else if (state === 'exhausted') {
        message = overrideMessage || exhaustedLabel;
      } else if (overrideMessage) {
        message = overrideMessage;
      }

      helpElement.textContent = message;
      helpElement.classList.toggle('form-help--error', isError);
    }

    function normaliseOption(option) {
      if (!option || typeof option !== 'object') {
        return null;
      }
      const rawId = option.id ?? option.asset_id;
      if (rawId === null || rawId === undefined) {
        return null;
      }
      const idText = String(rawId).trim();
      if (!idText) {
        return null;
      }
      const serialNumber = typeof option.serial_number === 'string' ? option.serial_number.trim() : '';
      const statusValue = typeof option.status === 'string' ? option.status.trim() : '';
      const tacticalIdRaw = option.tactical_asset_id ?? option.tactical_asset ?? option.tactical_id;
      const tacticalId =
        typeof tacticalIdRaw === 'string' || typeof tacticalIdRaw === 'number'
          ? String(tacticalIdRaw).trim()
          : '';
      const trayDeviceUidRaw = option.tray_device_uid ?? option.device_uid ?? option.trayDeviceUid;
      const trayDeviceUid =
        typeof trayDeviceUidRaw === 'string' || typeof trayDeviceUidRaw === 'number'
          ? String(trayDeviceUidRaw).trim()
          : '';
      let name = '';
      if (typeof option.name === 'string' && option.name.trim() !== '') {
        name = option.name.trim();
      }
      let label = '';
      if (typeof option.label === 'string' && option.label.trim() !== '') {
        label = option.label.trim();
      }
      if (!label) {
        label = formatAssetLabel({
          id: idText,
          name,
          serial_number: serialNumber,
          status: statusValue,
        });
      }
      if (!name) {
        name = label;
      }
      return {
        id: idText,
        label,
        name,
        serial_number: serialNumber || null,
        status: statusValue || null,
        tactical_asset_id: tacticalId || null,
        tray_device_uid: trayDeviceUid || null,
      };
    }

    function normaliseLinkedRecord(record) {
      if (!record || typeof record !== 'object') {
        return null;
      }
      const base = normaliseOption(record);
      if (!base) {
        return null;
      }
      const assetId = record.asset_id ?? record.id ?? base.id;
      const assetIdInt = Number.parseInt(String(assetId), 10);
      return {
        ...base,
        asset_id: Number.isNaN(assetIdInt) ? base.id : assetIdInt,
      };
    }

    function setAvailableOptions(options) {
      optionLookup.clear();
      allOptions = [];
      options.forEach((option) => {
        const normalised = normaliseOption(option);
        if (!normalised) {
          return;
        }
        optionLookup.set(normalised.id, normalised);
        allOptions.push(normalised);
      });
    }

    function buildTacticalUrl(assetName) {
      if (!tacticalBaseUrl || typeof assetName !== 'string') {
        return null;
      }
      const trimmed = assetName.trim();
      if (!trimmed) {
        return null;
      }
      return `${tacticalBaseUrl}/?search=${encodeURIComponent(trimmed)}`;
    }

    function renderLinkedAssets() {
      if (!linkedList) {
        return;
      }
      linkedList.innerHTML = '';

      const entries = Array.from(linkedMap.values());
      entries.sort((a, b) => {
        const labelA = formatAssetLabel(a).toLowerCase();
        const labelB = formatAssetLabel(b).toLowerCase();
        if (labelA < labelB) {
          return -1;
        }
        if (labelA > labelB) {
          return 1;
        }
        return 0;
      });

      entries.forEach((record) => {
        const item = document.createElement('li');
        item.className = 'ticket-assets-linked__item ticket-assets-linked__item--actions';
        item.setAttribute('data-linked-asset', '');
        const assetIdValue = String(record.asset_id ?? record.id);
        item.setAttribute('data-asset-id', assetIdValue);
        if (record.tactical_asset_id) {
          item.setAttribute('data-tactical-id', record.tactical_asset_id);
        }

        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = 'assetIds';
        hiddenInput.value = assetIdValue;
        if (detailsFormId) {
          hiddenInput.setAttribute('form', detailsFormId);
        }
        item.appendChild(hiddenInput);

        const label = formatAssetLabel(record);
        const displayName = typeof record.name === 'string' && record.name.trim() ? record.name.trim() : label;
        const tacticalUrl = buildTacticalUrl(displayName);
        const nameElement = tacticalUrl ? document.createElement('a') : document.createElement('span');
        nameElement.className = 'ticket-assets-linked__name';
        nameElement.textContent = displayName;
        nameElement.setAttribute('data-linked-asset-name', '');
        if (label && label !== displayName) {
          nameElement.title = label;
        }
        if (tacticalUrl && nameElement instanceof HTMLAnchorElement) {
          nameElement.href = tacticalUrl;
          nameElement.target = '_blank';
          nameElement.rel = 'noreferrer noopener';
        }
        item.appendChild(nameElement);

        const metaParts = [];
        if (record.serial_number) {
          metaParts.push(`SN ${record.serial_number}`);
        }
        if (record.status) {
          const cleanedStatus = record.status
            .split('_')
            .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
            .join(' ');
          metaParts.push(cleanedStatus);
        }
        if (metaParts.length) {
          const metaElement = document.createElement('p');
          metaElement.className = 'ticket-assets-linked__meta';
          metaElement.textContent = metaParts.join(' · ');
          item.appendChild(metaElement);
        }

        const actions = document.createElement('div');
        actions.className = 'ticket-assets-linked__actions';

        if (tacticalUrl) {
          const tacticalAction = document.createElement('a');
          tacticalAction.href = tacticalUrl;
          tacticalAction.target = '_blank';
          tacticalAction.rel = 'noreferrer noopener';
          tacticalAction.className = 'button button--ghost button--icon ticket-assets-linked__action';
          tacticalAction.title = 'Open in Tactical RMM';
          tacticalAction.setAttribute('aria-label', `Open ${displayName} in Tactical RMM`);
          tacticalAction.innerHTML = '<span aria-hidden="true">🖥️</span>';
          actions.appendChild(tacticalAction);
        } else {
          const tacticalAction = document.createElement('button');
          tacticalAction.type = 'button';
          tacticalAction.className = 'button button--ghost button--icon ticket-assets-linked__action';
          tacticalAction.disabled = true;
          tacticalAction.setAttribute('aria-disabled', 'true');
          tacticalAction.title = 'Tactical RMM search is not configured';
          tacticalAction.setAttribute('aria-label', `Tactical RMM search is not configured for ${displayName}`);
          tacticalAction.innerHTML = '<span aria-hidden="true">🖥️</span>';
          actions.appendChild(tacticalAction);
        }

        const chatButton = document.createElement('button');
        chatButton.type = 'button';
        chatButton.className = 'button button--ghost button--icon ticket-assets-linked__action';
        chatButton.setAttribute('data-linked-asset-chat', '');
        chatButton.setAttribute('data-asset-name', displayName);
        chatButton.title = 'Open chat';
        chatButton.setAttribute('aria-label', `Open chat with ${displayName}`);
        if (record.tray_device_uid) {
          chatButton.setAttribute('data-device-uid', record.tray_device_uid);
        } else {
          chatButton.disabled = true;
          chatButton.setAttribute('aria-disabled', 'true');
        }
        chatButton.innerHTML = '<span aria-hidden="true">💬</span>';
        actions.appendChild(chatButton);

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--ghost button--icon ticket-assets-linked__remove';
        removeButton.setAttribute('data-linked-asset-remove', '');
        removeButton.innerHTML = '<span class="visually-hidden">Remove asset</span>×';
        actions.appendChild(removeButton);
        item.appendChild(actions);

        linkedList.appendChild(item);
      });

      if (emptyState) {
        emptyState.hidden = entries.length > 0;
        if (!entries.length) {
          emptyState.textContent = emptyMessage;
        }
      }
    }

    function renderOptions() {
      const placeholder = select.dataset.placeholder || 'Select an asset to link';
      select.innerHTML = '';
      const placeholderOption = document.createElement('option');
      placeholderOption.value = '';
      placeholderOption.textContent = placeholder;
      select.appendChild(placeholderOption);

      let availableCount = 0;
      allOptions.forEach((option) => {
        if (linkedMap.has(option.id)) {
          return;
        }
        const optionElement = document.createElement('option');
        optionElement.value = option.id;
        optionElement.textContent = option.label;
        select.appendChild(optionElement);
        availableCount += 1;
      });

      if (availableCount === 0) {
        select.disabled = true;
        select.setAttribute('aria-disabled', 'true');
      } else {
        select.disabled = false;
        select.removeAttribute('aria-disabled');
      }

      if (!companySelect.value) {
        setHelpState('disabled');
      } else if (!allOptions.length) {
        setHelpState('empty');
      } else if (availableCount === 0) {
        setHelpState('exhausted');
      } else {
        setHelpState('enabled');
      }
    }

    function addLinkedAssetById(assetId) {
      const id = String(assetId || '').trim();
      if (!id || linkedMap.has(id)) {
        return;
      }
      const option = optionLookup.get(id);
      if (!option) {
        return;
      }
      const numericId = Number.parseInt(id, 10);
      linkedMap.set(id, {
        ...option,
        asset_id: Number.isNaN(numericId) ? id : numericId,
      });
      renderLinkedAssets();
      renderOptions();
      document.dispatchEvent(new CustomEvent('ticket:details-autosave'));
    }

    function removeLinkedAssetById(assetId) {
      const id = String(assetId || '').trim();
      if (!id) {
        return;
      }
      if (!linkedMap.has(id)) {
        return;
      }
      linkedMap.delete(id);
      renderLinkedAssets();
      renderOptions();
      document.dispatchEvent(new CustomEvent('ticket:details-autosave'));
    }

    function clearLinkedAssets() {
      if (!linkedMap.size) {
        return;
      }
      linkedMap.clear();
      renderLinkedAssets();
      renderOptions();
    }

    async function fetchAssets(companyId) {
      if (!endpointTemplate || !companyId) {
        return [];
      }
      const endpoint = endpointTemplate.replace('{companyId}', encodeURIComponent(companyId));
      const response = await fetch(endpoint, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }
      const payload = await response.json();
      if (!Array.isArray(payload)) {
        return [];
      }
      return payload
        .map((record) => {
          if (!record || typeof record !== 'object') {
            return null;
          }
          return {
            id: record.id,
            label: formatAssetLabel(record),
            name: record.name,
            serial_number: record.serial_number,
            status: record.status,
            tactical_asset_id: record.tactical_asset_id,
            tray_device_uid: record.tray_device_uid,
          };
        })
        .filter((option) => option !== null);
    }

    async function reloadAssets(companyId) {
      if (!companyId) {
        setAvailableOptions([]);
        select.value = '';
        renderOptions();
        return;
      }

      select.disabled = true;
      select.setAttribute('aria-disabled', 'true');
      setHelpState('loading');

      try {
        const assets = await fetchAssets(companyId);
        setAvailableOptions(assets);
        renderOptions();
      } catch (error) {
        console.error('Failed to load company assets', error);
        setAvailableOptions([]);
        renderOptions();
        setHelpState('error');
      } finally {
        if (companySelect.value && allOptions.length) {
          select.disabled = false;
          select.removeAttribute('aria-disabled');
        }
      }
    }

    setAvailableOptions(initialOptions);
    initialLinkedRecords.forEach((record) => {
      const normalised = normaliseLinkedRecord(record);
      if (!normalised) {
        return;
      }
      linkedMap.set(String(normalised.id), normalised);
    });
    initialSelection.forEach((assetId) => {
      const id = String(assetId || '').trim();
      if (!id || linkedMap.has(id)) {
        return;
      }
      const option = optionLookup.get(id);
      if (option) {
        const numericId = Number.parseInt(id, 10);
        linkedMap.set(id, {
          ...option,
          asset_id: Number.isNaN(numericId) ? id : numericId,
        });
      }
    });

    renderLinkedAssets();
    renderOptions();

    if (!companySelect.value) {
      select.disabled = true;
      select.setAttribute('aria-disabled', 'true');
      setHelpState('disabled');
    }

    select.addEventListener('change', () => {
      const selectedId = select.value;
      if (!selectedId) {
        return;
      }
      addLinkedAssetById(selectedId);
      select.value = '';
    });

    linkedContainer.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }

      const chatButton = target.closest('[data-linked-asset-chat]');
      if (chatButton) {
        const uid = chatButton.getAttribute('data-device-uid') || '';
        if (!uid) {
          return;
        }
        const assetElement = chatButton.closest('[data-linked-asset]');
        const nameEl = assetElement ? assetElement.querySelector('[data-linked-asset-name]') : null;
        const assetName = (chatButton.getAttribute('data-asset-name') || (nameEl ? nameEl.textContent : '') || 'Asset').trim();
        const ticketLabel = ticketNumber ? `#${ticketNumber}` : 'ticket';
        const chatSubject = [assetName, ticketLabel, ticketSubject].filter(Boolean).join(' - ');
        chatButton.disabled = true;
        fetch(`/api/tray/${encodeURIComponent(uid)}/chat/start`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': getCsrfToken(),
            Accept: 'application/json',
          },
          body: JSON.stringify({ subject: chatSubject || 'Helpdesk chat' }),
        })
          .then(async (response) => {
            if (!response.ok) {
              throw new Error(await response.text());
            }
            return response.json();
          })
          .then((data) => {
            if (data.room_id) {
              window.location.href = `/chat?room=${encodeURIComponent(data.room_id)}`;
            }
          })
          .catch((error) => {
            console.error('Failed to open tray chat', error);
            window.alert(`Failed to open chat: ${error.message || 'Unknown error'}`);
            chatButton.disabled = false;
          });
        return;
      }

      const removeButton = target.closest('[data-linked-asset-remove]');
      if (removeButton) {
        const parent = removeButton.closest('[data-linked-asset]');
        if (!parent) {
          return;
        }
        const assetId = parent.getAttribute('data-asset-id');
        removeLinkedAssetById(assetId);
        return;
      }

      const assetElement = target.closest('[data-linked-asset]');
      if (!assetElement) {
        return;
      }
      const nameEl = assetElement.querySelector('[data-linked-asset-name]');
      const assetName = nameEl ? nameEl.textContent.trim() : '';
      const tacticalUrl = buildTacticalUrl(assetName);
      if (!tacticalUrl) {
        return;
      }
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      if (target.closest('a[href]')) {
        return;
      }

      event.preventDefault();
      window.open(tacticalUrl, '_blank', 'noreferrer');
    });

    companySelect.addEventListener('change', () => {
      const companyId = companySelect.value;
      if (!companyId) {
        currentCompanyId = '';
        clearLinkedAssets();
        select.value = '';
        setAvailableOptions([]);
        renderOptions();
        return;
      }
      if (companyId !== currentCompanyId) {
        currentCompanyId = companyId;
        clearLinkedAssets();
      }
      reloadAssets(companyId);
    });
  }

  function initialiseRequesterSelector() {
    const companySelect = document.getElementById('ticket-company-detail');
    if (!(companySelect instanceof HTMLSelectElement)) {
      return;
    }

    const requesterSelect = document.querySelector('[data-ticket-requester-select]');
    if (!(requesterSelect instanceof HTMLSelectElement)) {
      return;
    }

    const helpElement = document.querySelector('[data-ticket-requester-help]');

    function setRequesterHelpState(state) {
      if (!helpElement) {
        return;
      }
      if (state === 'disabled') {
        helpElement.textContent = helpElement.dataset.helpDisabled || 'Link a company before selecting a requester.';
        helpElement.removeAttribute('hidden');
      } else if (state === 'empty') {
        helpElement.textContent = helpElement.dataset.helpEmpty || 'No enabled staff members are available for the linked company.';
        helpElement.removeAttribute('hidden');
      } else {
        helpElement.textContent = '';
        helpElement.setAttribute('hidden', '');
      }
    }

    async function reloadRequesterOptions(companyId) {
      requesterSelect.value = '';
      Array.from(requesterSelect.options).forEach((opt) => {
        if (opt.value !== '') {
          opt.remove();
        }
      });

      if (!companyId) {
        requesterSelect.disabled = true;
        requesterSelect.setAttribute('aria-disabled', 'true');
        setRequesterHelpState('disabled');
        return;
      }

      try {
        const response = await fetch(`/api/companies/${encodeURIComponent(companyId)}/staff-users`, {
          credentials: 'same-origin',
        });
        if (!response.ok) {
          setRequesterHelpState('empty');
          return;
        }
        const users = await response.json();
        if (!Array.isArray(users) || users.length === 0) {
          requesterSelect.disabled = false;
          requesterSelect.removeAttribute('aria-disabled');
          setRequesterHelpState('empty');
          return;
        }
        users.forEach((user) => {
          if (!user || !user.id || !user.email) {
            return;
          }
          const option = document.createElement('option');
          option.value = user.requester_value || (user.user_id ? `user:${user.user_id}` : `staff:${user.staff_id || user.id}`);
          const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
          option.textContent = fullName ? `${fullName} (${user.email})` : user.email;
          requesterSelect.appendChild(option);
        });
        requesterSelect.disabled = false;
        requesterSelect.removeAttribute('aria-disabled');
        setRequesterHelpState('hidden');
      } catch (_err) {
        setRequesterHelpState('empty');
      }
    }

    companySelect.addEventListener('change', () => {
      reloadRequesterOptions(companySelect.value);
    });
  }

  function initialiseReplyTimeEditing() {
    const modal = document.getElementById('reply-time-modal');
    const timeline = document.querySelector('[data-ticket-timeline]');
    if (!modal || !timeline) {
      return;
    }

    const ticketId = timeline.getAttribute('data-ticket-id');
    const form = modal.querySelector('[data-reply-time-form]');
    const minutesInput = modal.querySelector('[data-reply-time-minutes]');
    const billableCheckbox = modal.querySelector('[data-reply-time-billable]');
    const labourSelect = modal.querySelector('[data-reply-time-labour]');
    const errorMessage = modal.querySelector('[data-reply-time-error]');
    const submitButton = modal.querySelector('[data-reply-time-submit]');
    const triggers = document.querySelectorAll('[data-reply-edit]');
    if (
      !form ||
      !minutesInput ||
      !billableCheckbox ||
      !errorMessage ||
      !submitButton ||
      !labourSelect ||
      !triggers.length
    ) {
      return;
    }

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let activeTrigger = null;
    let activeReply = null;

    function setError(message) {
      if (!errorMessage) {
        return;
      }
      if (message) {
        errorMessage.textContent = message;
        errorMessage.hidden = false;
      } else {
        errorMessage.textContent = '';
        errorMessage.hidden = true;
      }
    }

    function getFocusableElements() {
      return Array.from(modal.querySelectorAll(focusableSelector)).filter((element) => {
        if (element.hasAttribute('disabled')) {
          return false;
        }
        if (element.getAttribute('aria-hidden') === 'true') {
          return false;
        }
        return element.offsetParent !== null;
      });
    }

    function focusFirstElement() {
      const [first] = getFocusableElements();
      if (first && typeof first.focus === 'function') {
        first.focus();
      }
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', handleKeydown);
      setError('');
      if (activeTrigger && typeof activeTrigger.focus === 'function') {
        activeTrigger.focus();
      }
      activeTrigger = null;
      activeReply = null;
      form.reset();
    }

    function handleKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = getFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;
      if (event.shiftKey) {
        if (current === first) {
          event.preventDefault();
          last.focus();
        }
      } else if (current === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openModal(trigger, replyArticle) {
      activeTrigger = trigger instanceof HTMLElement ? trigger : null;
      activeReply = replyArticle;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.addEventListener('keydown', handleKeydown);
      focusFirstElement();
    }

    function updateReplyDisplay(replyArticle, minutes, billable, summary, labourTypeId, labourTypeName) {
      if (!replyArticle) {
        return;
      }
      replyArticle.dataset.replyMinutes = typeof minutes === 'number' ? String(minutes) : '';
      replyArticle.dataset.replyBillable = billable ? 'true' : 'false';
      replyArticle.dataset.replyLabour =
        typeof labourTypeId === 'number' && !Number.isNaN(labourTypeId) && labourTypeId > 0
          ? String(labourTypeId)
          : '';
      const summaryElement = replyArticle.querySelector('[data-reply-time-summary]');
      if (summaryElement) {
        const text = summary || formatTimeSummary(minutes, billable, labourTypeName);
        renderTimeSummary(summaryElement, minutes, billable, labourTypeName, text);
      }
    }

    triggers.forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        if (!ticketId) {
          return;
        }
        const replyArticle = button.closest('[data-ticket-reply]');
        if (!(replyArticle instanceof HTMLElement)) {
          return;
        }
        const minutesValue = replyArticle.getAttribute('data-reply-minutes') || '';
        const billableValue = replyArticle.getAttribute('data-reply-billable') === 'true';
        const labourValue = replyArticle.getAttribute('data-reply-labour') || '';
        minutesInput.value = minutesValue;
        billableCheckbox.checked = billableValue;
        labourSelect.value = labourValue;
        setError('');
        openModal(button, replyArticle);
      });
    });

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!ticketId || !(activeReply instanceof HTMLElement)) {
        setError('Unable to determine which reply to update.');
        return;
      }
      const replyId = activeReply.getAttribute('data-reply-id');
      if (!replyId) {
        setError('Unable to determine which reply to update.');
        return;
      }

      const rawMinutes = minutesInput.value.trim();
      const labourValue = labourSelect.value.trim();
      let minutes = null;
      if (rawMinutes !== '') {
        const parsed = Number.parseInt(rawMinutes, 10);
        if (Number.isNaN(parsed)) {
          setError('Enter minutes as a whole number.');
          return;
        }
        if (parsed < 0) {
          setError('Minutes cannot be negative.');
          return;
        }
        if (parsed > 1440) {
          setError('Minutes cannot exceed 1440.');
          return;
        }
        minutes = parsed;
      }

      const isBillable = billableCheckbox.checked;
      let labourTypeId = null;
      if (labourValue !== '') {
        const parsedLabour = Number.parseInt(labourValue, 10);
        if (Number.isNaN(parsedLabour) || parsedLabour <= 0) {
          setError('Select a valid labour type.');
          return;
        }
        labourTypeId = parsedLabour;
      }
      const csrfToken = getCsrfToken();
      const headers = {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      };
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      const previousMinutes = parseMinutesValue(activeReply.getAttribute('data-reply-minutes'));
      const previousBillable = activeReply.getAttribute('data-reply-billable') === 'true';

      submitButton.disabled = true;
      submitButton.setAttribute('aria-busy', 'true');
      setError('');

      try {
        const response = await fetch(`/api/tickets/${ticketId}/replies/${replyId}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers,
          body: JSON.stringify({
            minutes_spent: minutes,
            is_billable: isBillable,
            labour_type_id: labourTypeId,
          }),
        });

        if (!response.ok) {
          let detail = `${response.status} ${response.statusText}`;
          try {
            const payload = await response.json();
            if (payload && payload.detail) {
              detail = Array.isArray(payload.detail)
                ? payload.detail.map((entry) => entry.msg || entry).join(', ')
                : payload.detail;
            }
          } catch (error) {
            /* ignore parse errors */
          }
          throw new Error(detail);
        }

        const data = await response.json();
        const replyData = data && data.reply ? data.reply : null;
        if (!replyData) {
          throw new Error('No reply data returned.');
        }
        const updatedMinutes =
          typeof replyData.minutes_spent === 'number' ? replyData.minutes_spent : null;
        const updatedBillable = Boolean(replyData.is_billable);
        const summaryText = typeof replyData.time_summary === 'string' ? replyData.time_summary : '';
        const updatedLabourId =
          typeof replyData.labour_type_id === 'number' && replyData.labour_type_id > 0
            ? replyData.labour_type_id
            : null;
        const labourName =
          typeof replyData.labour_type_name === 'string' ? replyData.labour_type_name : '';

        updateReplyDisplay(
          activeReply,
          updatedMinutes,
          updatedBillable,
          summaryText,
          updatedLabourId,
          labourName,
        );
        adjustTimeTotals(previousMinutes, previousBillable, updatedMinutes, updatedBillable);
        closeModal();
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to update time entry.';
        setError(message);
      } finally {
        submitButton.disabled = false;
        submitButton.removeAttribute('aria-busy');
      }
    });
  }

  function initialiseTaskManagement() {
    const taskLists = document.querySelectorAll('[data-task-list]');
    if (!taskLists.length) {
      return;
    }

    taskLists.forEach((taskList) => {
      const ticketId = taskList.dataset.ticketId;
      if (!ticketId) {
        return;
      }

      const card = taskList.closest('[data-ticket-tasks-card]');
      const emptyMessage = card ? card.querySelector('[data-tasks-empty]') : null;
      const addButton = card ? card.querySelector('[data-add-task-button]') : null;
      const countBadge = card ? card.querySelector('[data-tasks-count-badge]') : null;

      async function loadTasks() {
        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks`, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
          });
          if (!response.ok) {
            throw new Error('Failed to load tasks');
          }
          const data = await response.json();
          renderTasks(data.items || []);
        } catch (error) {
          console.error('Failed to load tasks', error);
        }
      }

      function renderTasks(tasks) {
        taskList.innerHTML = '';
        if (!tasks || !tasks.length) {
          if (emptyMessage) {
            emptyMessage.hidden = false;
          }
          updateTaskCount(0, 0);
          return;
        }
        if (emptyMessage) {
          emptyMessage.hidden = true;
        }
        tasks.forEach((task) => {
          const li = createTaskElement(task);
          taskList.appendChild(li);
        });
        
        // Update task count badge
        const incompleteCount = tasks.filter(task => !task.is_completed).length;
        updateTaskCount(incompleteCount, tasks.length);
      }

      function updateTaskCount(incompleteCount, totalCount) {
        if (!countBadge) {
          return;
        }
        if (totalCount === 0) {
          countBadge.hidden = true;
          countBadge.textContent = '';
        } else if (incompleteCount > 0) {
          countBadge.hidden = false;
          countBadge.textContent = `(${incompleteCount})`;
        } else {
          countBadge.hidden = true;
          countBadge.textContent = '';
        }
      }

      function createTaskElement(task) {
        const li = document.createElement('li');
        li.className = 'task-list__item';
        li.dataset.taskId = task.id;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'task-list__checkbox';
        checkbox.checked = task.is_completed || false;
        checkbox.addEventListener('change', () => handleTaskToggle(task.id, checkbox.checked));

        const label = document.createElement('span');
        label.className = 'task-list__label';
        label.textContent = task.task_name || '';
        if (task.is_completed) {
          label.classList.add('task-list__label--completed');
        }

        const actions = document.createElement('div');
        actions.className = 'task-list__actions';

        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'button button--ghost button--icon button--small';
        editButton.setAttribute('aria-label', 'Edit task');
        editButton.innerHTML = '✎';
        editButton.addEventListener('click', () => handleEditTask(task));

        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'button button--ghost button--icon button--small';
        deleteButton.setAttribute('aria-label', 'Delete task');
        deleteButton.innerHTML = '×';
        deleteButton.addEventListener('click', () => handleDeleteTask(task.id));

        actions.appendChild(editButton);
        actions.appendChild(deleteButton);

        li.appendChild(checkbox);
        li.appendChild(label);
        li.appendChild(actions);

        return li;
      }

      async function handleTaskToggle(taskId, isCompleted) {
        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${taskId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ isCompleted }),
          });
          if (!response.ok) {
            throw new Error('Failed to update task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to update task', error);
          await loadTasks();
        }
      }

      async function handleEditTask(task) {
        const newName = prompt('Edit task name:', task.task_name);
        if (!newName || newName.trim() === '' || newName === task.task_name) {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${task.id}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ taskName: newName.trim() }),
          });
          if (!response.ok) {
            throw new Error('Failed to update task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to update task', error);
          alert('Failed to update task. Please try again.');
        }
      }

      async function handleDeleteTask(taskId) {
        if (!confirm('Delete this task? This cannot be undone.')) {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = { Accept: 'application/json' };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${taskId}`, {
            method: 'DELETE',
            credentials: 'same-origin',
            headers,
          });
          if (!response.ok) {
            throw new Error('Failed to delete task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to delete task', error);
          alert('Failed to delete task. Please try again.');
        }
      }

      async function handleAddTask() {
        const taskName = prompt('Enter task name:');
        if (!taskName || taskName.trim() === '') {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks`, {
            method: 'POST',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ taskName: taskName.trim(), sortOrder: 0 }),
          });
          if (!response.ok) {
            throw new Error('Failed to create task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to create task', error);
          alert('Failed to create task. Please try again.');
        }
      }

      if (addButton) {
        addButton.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          handleAddTask();
        });
      }

      loadTasks();
    });
  }

  function initialiseCallRecordingTimeEditing() {
    const modal = document.getElementById('recording-time-modal');
    const timeline = document.querySelector('[data-ticket-timeline]');
    if (!modal || !timeline) {
      return;
    }

    const ticketId = timeline.getAttribute('data-ticket-id');
    const form = modal.querySelector('[data-recording-time-form]');
    const minutesInput = modal.querySelector('[data-recording-time-minutes]');
    const billableCheckbox = modal.querySelector('[data-recording-time-billable]');
    const labourSelect = modal.querySelector('[data-recording-time-labour]');
    const errorMessage = modal.querySelector('[data-recording-time-error]');
    const submitButton = modal.querySelector('[data-recording-time-submit]');
    const triggers = document.querySelectorAll('[data-recording-edit]');
    if (
      !form ||
      !minutesInput ||
      !billableCheckbox ||
      !errorMessage ||
      !submitButton ||
      !labourSelect ||
      !triggers.length
    ) {
      return;
    }

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let activeTrigger = null;
    let activeRecording = null;

    function setError(message) {
      if (!errorMessage) {
        return;
      }
      if (message) {
        errorMessage.textContent = message;
        errorMessage.hidden = false;
      } else {
        errorMessage.textContent = '';
        errorMessage.hidden = true;
      }
    }

    function getFocusableElements() {
      return Array.from(modal.querySelectorAll(focusableSelector)).filter((element) => {
        if (element.hasAttribute('disabled')) {
          return false;
        }
        if (element.getAttribute('aria-hidden') === 'true') {
          return false;
        }
        return element.offsetParent !== null;
      });
    }

    function focusFirstElement() {
      const [first] = getFocusableElements();
      if (first && typeof first.focus === 'function') {
        first.focus();
      }
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', handleKeydown);
      setError('');
      if (activeTrigger && typeof activeTrigger.focus === 'function') {
        activeTrigger.focus();
      }
      activeTrigger = null;
      activeRecording = null;
      form.reset();
    }

    function handleKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = getFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;
      if (event.shiftKey) {
        if (current === first) {
          event.preventDefault();
          last.focus();
        }
      } else if (current === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openModal(trigger, recordingArticle) {
      activeTrigger = trigger instanceof HTMLElement ? trigger : null;
      activeRecording = recordingArticle;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.addEventListener('keydown', handleKeydown);
      focusFirstElement();
    }

    function updateRecordingDisplay(recordingArticle, minutes, billable, summary, labourTypeId, labourTypeName) {
      if (!recordingArticle) {
        return;
      }
      recordingArticle.dataset.recordingMinutes = typeof minutes === 'number' ? String(minutes) : '';
      recordingArticle.dataset.recordingBillable = billable ? 'true' : 'false';
      recordingArticle.dataset.recordingLabour =
        typeof labourTypeId === 'number' && !Number.isNaN(labourTypeId) && labourTypeId > 0
          ? String(labourTypeId)
          : '';
      const summaryElement = recordingArticle.querySelector('[data-recording-time-summary]');
      if (summaryElement) {
        const text = summary || formatTimeSummary(minutes, billable, labourTypeName);
        renderTimeSummary(summaryElement, minutes, billable, labourTypeName, text);
      }
    }

    triggers.forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        if (!ticketId) {
          return;
        }
        const recordingArticle = button.closest('[data-call-recording]');
        if (!(recordingArticle instanceof HTMLElement)) {
          return;
        }
        const minutesValue = recordingArticle.getAttribute('data-recording-minutes') || '';
        const billableValue = recordingArticle.getAttribute('data-recording-billable') === 'true';
        const labourValue = recordingArticle.getAttribute('data-recording-labour') || '';
        minutesInput.value = minutesValue;
        billableCheckbox.checked = billableValue;
        labourSelect.value = labourValue;
        setError('');
        openModal(button, recordingArticle);
      });
    });

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!ticketId || !(activeRecording instanceof HTMLElement)) {
        setError('Unable to determine which call recording to update.');
        return;
      }
      const recordingId = activeRecording.getAttribute('data-recording-id');
      if (!recordingId) {
        setError('Unable to determine which call recording to update.');
        return;
      }

      const rawMinutes = minutesInput.value.trim();
      const labourValue = labourSelect.value.trim();
      let minutes = null;
      if (rawMinutes !== '') {
        const parsed = Number.parseInt(rawMinutes, 10);
        if (Number.isNaN(parsed)) {
          setError('Enter minutes as a whole number.');
          return;
        }
        if (parsed < 0) {
          setError('Minutes cannot be negative.');
          return;
        }
        if (parsed > 1440) {
          setError('Minutes cannot exceed 1440.');
          return;
        }
        minutes = parsed;
      }

      const isBillable = billableCheckbox.checked;
      let labourTypeId = null;
      if (labourValue !== '') {
        const parsedLabour = Number.parseInt(labourValue, 10);
        if (Number.isNaN(parsedLabour) || parsedLabour <= 0) {
          setError('Select a valid labour type.');
          return;
        }
        labourTypeId = parsedLabour;
      }
      const csrfToken = getCsrfToken();
      const headers = {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      };
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      const previousMinutes = parseMinutesValue(activeRecording.getAttribute('data-recording-minutes'));
      const previousBillable = activeRecording.getAttribute('data-recording-billable') === 'true';

      submitButton.disabled = true;
      submitButton.setAttribute('aria-busy', 'true');
      setError('');

      try {
        const response = await fetch(`/api/call-recordings/${recordingId}`, {
          method: 'PUT',
          credentials: 'same-origin',
          headers,
          body: JSON.stringify({
            minutesSpent: minutes,
            isBillable: isBillable,
            labourTypeId: labourTypeId,
          }),
        });

        if (!response.ok) {
          let detail = `${response.status} ${response.statusText}`;
          try {
            const payload = await response.json();
            if (payload && payload.detail) {
              detail = Array.isArray(payload.detail)
                ? payload.detail.map((entry) => entry.msg || entry).join(', ')
                : payload.detail;
            }
          } catch (error) {
            /* ignore parse errors */
          }
          throw new Error(detail);
        }

        const data = await response.json();
        if (!data) {
          throw new Error('No recording data returned.');
        }
        const updatedMinutes =
          typeof data.minutes_spent === 'number' ? data.minutes_spent : null;
        const updatedBillable = Boolean(data.is_billable);
        
        // Format the time summary manually since the API doesn't return it
        const labourName = typeof data.labour_type_name === 'string' ? data.labour_type_name : '';
        const summaryText = formatTimeSummary(updatedMinutes, updatedBillable, labourName);
        
        const updatedLabourId =
          typeof data.labour_type_id === 'number' && data.labour_type_id > 0
            ? data.labour_type_id
            : null;

        updateRecordingDisplay(
          activeRecording,
          updatedMinutes,
          updatedBillable,
          summaryText,
          updatedLabourId,
          labourName,
        );
        adjustTimeTotals(previousMinutes, previousBillable, updatedMinutes, updatedBillable);
        closeModal();
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to update time entry.';
        setError(message);
      } finally {
        submitButton.disabled = false;
        submitButton.removeAttribute('aria-busy');
      }
    });
  }

  function initialiseWatcherManagement() {
    const container = document.querySelector('[data-watchers-container]');
    if (!container) {
      return;
    }

    const selectElement = container.querySelector('[data-watcher-select]');
    const addButton = container.querySelector('[data-add-watcher]');
    const emailInput = container.querySelector('[data-watcher-email-input]');
    const addEmailButton = container.querySelector('[data-add-watcher-email]');
    const watchersList = container.querySelector('[data-watchers-list]');
    const emptyMessage = container.querySelector('[data-watchers-empty]');

    if (!selectElement || !addButton || !watchersList) {
      return;
    }

    const ticketId = getTicketIdFromPath();
    if (!ticketId) {
      return;
    }

    // Enable/disable add button based on selection
    selectElement.addEventListener('change', () => {
      addButton.disabled = !selectElement.value;
    });

    // Enable/disable email add button based on input
    if (emailInput && addEmailButton) {
      emailInput.addEventListener('input', () => {
        const email = emailInput.value.trim();
        addEmailButton.disabled = !email || !email.includes('@');
      });
    }

    // Handle add watcher by user ID
    addButton.addEventListener('click', async () => {
      const watcherValue = selectElement.value;
      if (!watcherValue) {
        return;
      }

      const selectedOption = selectElement.options[selectElement.selectedIndex];
      const watcherKind = selectedOption.dataset.watcherKind || 'user';
      const userEmail = selectedOption.textContent || '';
      const watcherUrl = watcherKind === 'email'
        ? `/api/tickets/${ticketId}/watchers/email?email=${encodeURIComponent(watcherValue)}`
        : `/api/tickets/${ticketId}/watchers/${watcherValue}`;

      addButton.disabled = true;
      addButton.setAttribute('aria-busy', 'true');

      try {
        const csrfToken = getCsrfToken();
        const response = await fetch(watcherUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
        });

        if (!response.ok) {
          throw new Error(await getApiErrorMessage(response, 'Failed to add watcher'));
        }

        // Remove the option from select
        selectedOption.remove();
        selectElement.value = '';

        // Add to watchers list
        addWatcherToList(
          watcherKind === 'user' ? watcherValue : null,
          userEmail,
          watcherKind === 'email' ? watcherValue : null,
        );
      } catch (error) {
        console.error('Failed to add watcher:', error);
        alert(error instanceof Error ? error.message : 'Failed to add watcher');
      } finally {
        addButton.disabled = !selectElement.value;
        addButton.removeAttribute('aria-busy');
      }
    });

    // Handle add watcher by email
    if (emailInput && addEmailButton) {
      addEmailButton.addEventListener('click', async () => {
        const email = emailInput.value.trim();
        if (!email || !email.includes('@')) {
          return;
        }

        addEmailButton.disabled = true;
        addEmailButton.setAttribute('aria-busy', 'true');

        try {
          const csrfToken = getCsrfToken();
          const response = await fetch(`/api/tickets/${ticketId}/watchers/email?email=${encodeURIComponent(email)}`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRF-Token': csrfToken,
            },
          });

          if (!response.ok) {
            throw new Error(await getApiErrorMessage(response, 'Failed to add watcher'));
          }

          // Clear the input
          emailInput.value = '';

          // Add to watchers list
          addWatcherToList(null, email, email);
        } catch (error) {
          console.error('Failed to add watcher by email:', error);
          alert(error instanceof Error ? error.message : 'Failed to add watcher');
        } finally {
          addEmailButton.disabled = true;
          addEmailButton.removeAttribute('aria-busy');
        }
      });

      // Handle Enter key in email input
      emailInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !addEmailButton.disabled) {
          e.preventDefault();
          addEmailButton.click();
        }
      });
    }

    // Helper function to add watcher to list
    function addWatcherToList(userId, displayText, watcherEmail) {
      const listItem = document.createElement('li');
      listItem.className = 'list__item';
      listItem.setAttribute('data-watcher-item', '');
      if (userId) {
        listItem.setAttribute('data-user-id', userId);
      }
      if (watcherEmail) {
        listItem.setAttribute('data-watcher-email', watcherEmail);
      }

      const now = new Date();
      const formattedDate = now.toLocaleString('en-CA', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).replace(',', '');

      // Create elements safely without using innerHTML
      const itemContainer = document.createElement('div');
      itemContainer.style.display = 'flex';
      itemContainer.style.alignItems = 'center';
      itemContainer.style.justifyContent = 'space-between';

      const infoContainer = document.createElement('div');
      
      const emailStrong = document.createElement('strong');
      emailStrong.textContent = displayText;
      
      const metaDiv = document.createElement('div');
      metaDiv.className = 'list__meta';
      metaDiv.textContent = `Watching since ${formattedDate}`;
      
      infoContainer.appendChild(emailStrong);
      infoContainer.appendChild(metaDiv);

      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'button button--small button--ghost';
      removeButton.setAttribute('data-remove-watcher', '');
      if (userId) {
        removeButton.setAttribute('data-user-id', userId);
      }
      if (watcherEmail) {
        removeButton.setAttribute('data-watcher-email', watcherEmail);
      }
      removeButton.setAttribute('data-user-display', displayText);
      removeButton.setAttribute('title', 'Remove watcher');
      removeButton.textContent = 'Remove';

      itemContainer.appendChild(infoContainer);
      itemContainer.appendChild(removeButton);
      listItem.appendChild(itemContainer);

      watchersList.appendChild(listItem);

      // Show list, hide empty message
      if (emptyMessage) {
        emptyMessage.style.display = 'none';
      }
      watchersList.style.display = '';

      // Attach remove handler to the new button
      attachRemoveHandler(removeButton);
    }

    // Handle remove watcher
    function attachRemoveHandler(button) {
      button.addEventListener('click', async () => {
        const userId = button.getAttribute('data-user-id');
        const watcherEmail = button.getAttribute('data-watcher-email');
        const displayText = button.getAttribute('data-user-display');

        if ((!userId && !watcherEmail) || !confirm(`Remove ${displayText} from watchers?`)) {
          return;
        }

        button.disabled = true;
        button.setAttribute('aria-busy', 'true');

        try {
          const csrfToken = getCsrfToken();
          let url;
          
          if (userId) {
            url = `/api/tickets/${ticketId}/watchers/${userId}`;
          } else if (watcherEmail) {
            url = `/api/tickets/${ticketId}/watchers/email/${encodeURIComponent(watcherEmail)}`;
          } else {
            throw new Error('No user ID or email found');
          }

          const response = await fetch(url, {
            method: 'DELETE',
            headers: {
              'X-CSRF-Token': csrfToken,
            },
          });

          if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || 'Failed to remove watcher');
          }

          // Remove from list
          const listItem = button.closest('[data-watcher-item]');
          if (listItem) {
            listItem.remove();
          }

          // Add back to select if it was a user ID
          if (userId && displayText) {
            const option = document.createElement('option');
            option.value = userId;
            option.textContent = displayText;
            selectElement.appendChild(option);

            // Sort options alphabetically
            const options = Array.from(selectElement.options).slice(1); // Skip first "Select..." option
            options.sort((a, b) => a.textContent.localeCompare(b.textContent));
            options.forEach(opt => selectElement.appendChild(opt));
          }

          // Show empty message if no watchers
          const remainingWatchers = watchersList.querySelectorAll('[data-watcher-item]');
          if (remainingWatchers.length === 0) {
            watchersList.style.display = 'none';
            if (emptyMessage) {
              emptyMessage.style.display = '';
            }
          }
        } catch (error) {
          console.error('Failed to remove watcher:', error);
          alert(error instanceof Error ? error.message : 'Failed to remove watcher');
        } finally {
          button.disabled = false;
          button.removeAttribute('aria-busy');
        }
      });
    }

    // Attach handlers to existing remove buttons
    const removeButtons = container.querySelectorAll('[data-remove-watcher]');
    removeButtons.forEach(button => attachRemoveHandler(button));
  }

  function initialiseAttachmentActions() {
    const container = document.querySelector('[data-attachments-grid]');
    if (!container) {
      return;
    }

    const ticketId = container.getAttribute('data-ticket-id');
    const emptyMessage = document.querySelector('[data-attachments-empty]');

    function updateEmptyState() {
      const remaining = container.querySelector('[data-attachment-id]');
      if (emptyMessage) {
        emptyMessage.hidden = Boolean(remaining);
      }
    }

    async function handleRemove(button) {
      const attachmentId = button.getAttribute('data-attachment-id');
      if (!attachmentId || !ticketId) {
        return;
      }

      if (!window.confirm('Remove this attachment?')) {
        return;
      }

      button.disabled = true;
      button.setAttribute('aria-busy', 'true');

      try {
        const response = await fetch(`/api/tickets/${ticketId}/attachments/${attachmentId}`, {
          method: 'DELETE',
          headers: {
            'X-CSRF-Token': getCsrfToken(),
          },
        });

        if (!response.ok) {
          let detail = 'Failed to remove attachment';
          try {
            const payload = await response.json();
            if (payload && payload.detail) {
              detail = payload.detail;
            }
          } catch (error) {
            /* ignore parse errors */
          }
          throw new Error(detail);
        }

        const card = button.closest('[data-attachment-id]');
        if (card) {
          card.remove();
        }
        updateEmptyState();
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to remove attachment';
        alert(message);
      } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
    }

    const removeButtons = container.querySelectorAll('[data-remove-attachment]');
    removeButtons.forEach((button) => {
      button.addEventListener('click', () => {
        handleRemove(button);
      });
    });
  }

  function getTicketIdFromPath() {
    const match = window.location.pathname.match(/\/admin\/tickets\/(\d+)/);
    return match ? match[1] : null;
  }

  function initialiseTicketSplit() {
    const timeline = document.querySelector('[data-ticket-timeline]');
    if (!timeline) return;
    const ticketId = timeline.getAttribute('data-ticket-id');
    const splitButton = document.querySelector('[data-split-selected]');
    const showHiddenButton = document.querySelector('[data-show-split-hidden]');
    const checkboxes = Array.from(document.querySelectorAll('[data-split-reply-checkbox]'));
    const hiddenReplies = Array.from(document.querySelectorAll('[data-split-hidden="true"]'));

    if (showHiddenButton && hiddenReplies.length > 0) {
      showHiddenButton.hidden = false;
      showHiddenButton.addEventListener('click', () => {
        const shouldShow = showHiddenButton.getAttribute('aria-pressed') !== 'true';
        hiddenReplies.forEach((reply) => { reply.hidden = !shouldShow; });
        showHiddenButton.setAttribute('aria-pressed', shouldShow ? 'true' : 'false');
        showHiddenButton.textContent = shouldShow ? 'Hide split history' : 'Show hidden split history';
      });
    }

    if (!splitButton || !ticketId || checkboxes.length === 0) return;

    function selectedIds() {
      return checkboxes.filter((box) => box.checked).map((box) => Number.parseInt(box.value, 10)).filter(Number.isFinite);
    }

    function updateButton() {
      splitButton.disabled = selectedIds().length === 0;
    }

    checkboxes.forEach((box) => box.addEventListener('change', updateButton));

    splitButton.addEventListener('click', async () => {
      const ids = selectedIds();
      if (ids.length === 0) return;
      const subject = window.prompt('Subject for the new split ticket:', `Split from ticket #${ticketId}`);
      if (!subject || !subject.trim()) return;
      splitButton.disabled = true;
      const originalLabel = splitButton.textContent;
      splitButton.textContent = 'Splitting…';
      try {
        const response = await fetch(`/api/tickets/${encodeURIComponent(ticketId)}/split`, {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-CSRF-Token': getCsrfToken(),
          },
          body: JSON.stringify({ reply_ids: ids, new_subject: subject.trim() }),
        });
        if (!response.ok) {
          let message = 'Unable to split ticket.';
          try {
            const payload = await response.json();
            if (payload && payload.detail) message = typeof payload.detail === 'string' ? payload.detail : message;
          } catch (_err) {}
          throw new Error(message);
        }
        const payload = await response.json();
        const newId = payload && payload.new_ticket ? payload.new_ticket.id : null;
        window.alert(newId ? `Ticket split to #${newId}.` : 'Ticket split successfully.');
        window.location.reload();
      } catch (error) {
        window.alert(error instanceof Error ? error.message : 'Unable to split ticket.');
        splitButton.disabled = false;
        splitButton.textContent = originalLabel;
        updateButton();
      }
    });

    updateButton();
  }


  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatAutomationHistoryDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function formatAutomationHistoryJson(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'string') return escapeHtml(value);
    try {
      return escapeHtml(JSON.stringify(value, null, 2));
    } catch (error) {
      return escapeHtml(String(value));
    }
  }

  function renderAutomationHistoryRows(tbody, rows) {
    if (!tbody) return;
    if (!Array.isArray(rows) || rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="table__empty">No automation audit trail has been recorded for this ticket yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((item) => {
      const automation = item.automation_name || (item.automation_id ? `#${item.automation_id}` : '—');
      const action = item.action_name || item.action_module || '—';
      const details = item.error_message || item.result_payload || null;
      return `
        <tr>
          <td data-label="When" data-column-key="occurred_at">${escapeHtml(formatAutomationHistoryDate(item.occurred_at))}</td>
          <td data-label="Automation" data-column-key="automation">${escapeHtml(automation)}</td>
          <td data-label="Action" data-column-key="action">${escapeHtml(action)}</td>
          <td data-label="Status" data-column-key="status">${escapeHtml(item.status || 'unknown')}</td>
          <td data-label="Previous values" data-column-key="previous"><code>${formatAutomationHistoryJson(item.previous_values)}</code></td>
          <td data-label="Details" data-column-key="details"><code>${formatAutomationHistoryJson(details)}</code></td>
        </tr>`;
    }).join('');
  }

  function initialiseAutomationHistoryModal() {
    const modal = document.getElementById('ticket-automation-history-modal');
    const tbody = document.querySelector('[data-ticket-automation-history-rows]');
    const openButton = document.querySelector('[data-ticket-automation-history-open]');
    if (!modal || !tbody || !openButton) return;

    const closeModal = () => { modal.hidden = true; };
    const openModal = () => { modal.hidden = false; };

    document.querySelectorAll('[data-ticket-automation-history-close]').forEach((button) => {
      button.addEventListener('click', closeModal);
    });

    openButton.addEventListener('click', async () => {
      const ticketId = openButton.getAttribute('data-ticket-id');
      if (!ticketId) return;
      tbody.innerHTML = '<tr><td colspan="6" class="table__empty">Loading automation audit trail…</td></tr>';
      openModal();
      try {
        const response = await fetch(`/admin/tickets/${encodeURIComponent(ticketId)}/automation-history`, {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        if (!response.ok) throw new Error(`Request failed with ${response.status}`);
        const payload = await response.json();
        renderAutomationHistoryRows(tbody, payload.history || []);
      } catch (error) {
        tbody.innerHTML = `<tr><td colspan="6" class="table__empty">Unable to load automation audit trail: ${escapeHtml(error.message)}</td></tr>`;
      }
    });
  }



  function initialiseTicketMentions() {
    document.querySelectorAll('[data-ticket-mention-users]').forEach((editor) => {
      const surface = editor.querySelector('[data-rich-text-content]');
      const form = editor.closest('form');
      const hidden = form ? form.querySelector('[data-ticket-mention-user-ids]') : null;
      if (!(surface instanceof HTMLElement) || !(hidden instanceof HTMLInputElement)) {
        return;
      }

      let users = [];
      try {
        users = JSON.parse(editor.getAttribute('data-ticket-mention-users') || '[]');
      } catch (error) {
        users = [];
      }
      users = users
        .map((user) => ({
          id: Number(user.id),
          label: String(user.label || user.email || '').trim(),
          email: String(user.email || '').trim(),
        }))
        .filter((user) => Number.isInteger(user.id) && user.id > 0 && user.label);
      if (!users.length) {
        return;
      }

      const selected = new Set();
      const menu = document.createElement('div');
      menu.className = 'ticket-mention-menu';
      menu.setAttribute('role', 'listbox');
      menu.hidden = true;
      editor.appendChild(menu);

      let activeIndex = 0;
      let currentRange = null;
      let currentQuery = '';

      function syncHidden() {
        hidden.value = Array.from(selected).join(',');
      }

      function getMentionContext() {
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || !surface.contains(selection.anchorNode)) {
          return null;
        }
        const range = selection.getRangeAt(0).cloneRange();
        const prefixRange = range.cloneRange();
        prefixRange.selectNodeContents(surface);
        prefixRange.setEnd(range.endContainer, range.endOffset);
        const text = prefixRange.toString();
        const match = text.match(/(^|\s)@([\p{L}\p{N}._'-]{0,40})$/u);
        if (!match) {
          return null;
        }
        return { query: match[2].toLowerCase(), length: match[2].length + 1 };
      }

      function matchingUsers(query) {
        return users.filter((user) => {
          const haystack = `${user.label} ${user.email}`.toLowerCase();
          return haystack.includes(query);
        }).slice(0, 8);
      }

      function positionMenu() {
        const rect = surface.getBoundingClientRect();
        menu.style.left = '0px';
        menu.style.top = `${rect.height + 6}px`;
      }

      function hideMenu() {
        menu.hidden = true;
        menu.innerHTML = '';
        currentRange = null;
      }

      function renderMenu(matches) {
        if (!matches.length) {
          hideMenu();
          return;
        }
        activeIndex = Math.min(activeIndex, matches.length - 1);
        menu.innerHTML = matches.map((user, index) => `
          <button type="button" class="ticket-mention-menu__item${index === activeIndex ? ' is-active' : ''}" data-mention-index="${index}" role="option" aria-selected="${index === activeIndex ? 'true' : 'false'}">
            <span class="ticket-mention-menu__name"></span>
            <span class="ticket-mention-menu__email"></span>
          </button>
        `).join('');
        menu.querySelectorAll('[data-mention-index]').forEach((button) => {
          const user = matches[Number(button.getAttribute('data-mention-index'))];
          button.querySelector('.ticket-mention-menu__name').textContent = user.label;
          button.querySelector('.ticket-mention-menu__email').textContent = user.email;
          button.addEventListener('mousedown', (event) => {
            event.preventDefault();
            confirmMention(user);
          });
        });
        menu.hidden = false;
        positionMenu();
      }

      function updateMenu() {
        const context = getMentionContext();
        if (!context) {
          hideMenu();
          return;
        }
        currentQuery = context.query;
        const selection = window.getSelection();
        currentRange = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
        renderMenu(matchingUsers(currentQuery));
      }

      function confirmMention(user) {
        if (!currentRange) {
          return;
        }
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(currentRange);
        if (selection.modify) {
          for (let i = 0; i < currentQuery.length + 1; i += 1) {
            selection.modify('extend', 'backward', 'character');
          }
        }
        document.execCommand('insertText', false, `@${user.label} `);
        selected.add(user.id);
        syncHidden();
        surface.dispatchEvent(new Event('input', { bubbles: true }));
        hideMenu();
      }

      surface.addEventListener('input', updateMenu);
      surface.addEventListener('keyup', updateMenu);
      surface.addEventListener('blur', () => window.setTimeout(hideMenu, 150));
      surface.addEventListener('keydown', (event) => {
        if (menu.hidden) {
          return;
        }
        const matches = matchingUsers(currentQuery);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          activeIndex = (activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
          renderMenu(matches);
        } else if (event.key === 'Enter') {
          event.preventDefault();
          confirmMention(matches[activeIndex]);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          hideMenu();
        }
      });
    });
  }


  function initialiseCannedResponses() {
    const pickerModal = document.querySelector('[data-canned-responses-modal]');
    const createModal = document.querySelector('[data-canned-response-create-modal]');
    const editor = document.querySelector('[data-rich-text-content]');
    const fallback = document.querySelector('[data-rich-text-value]');
    const ticketId = pickerModal ? pickerModal.getAttribute('data-ticket-id') : '';
    let loadedResponses = null;

    function show(modal) {
      if (modal instanceof HTMLElement) {
        modal.hidden = false;
      }
    }

    function hide(modal) {
      if (modal instanceof HTMLElement) {
        modal.hidden = true;
      }
    }

    async function loadResponses() {
      if (loadedResponses || !ticketId) {
        return loadedResponses || [];
      }
      const response = await fetch(`/admin/tickets/${ticketId}/canned-responses`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(await getApiErrorMessage(response, 'Unable to load canned responses.'));
      }
      const data = await response.json();
      loadedResponses = Array.isArray(data.responses) ? data.responses : [];
      return loadedResponses;
    }

    function cannedResponseTextToHtml(text) {
      return String(text)
        .replace(/\r\n?/g, '\n')
        .trim()
        .split(/\n{2,}/)
        .map((paragraph) => paragraph
          .split('\n')
          .map((line) => escapeHtml(line))
          .join('<br>'))
        .map((paragraph) => `<p>${paragraph || '<br>'}</p>`)
        .join('');
    }

    function appendResponse(text) {
      const responseText = typeof text === 'string' ? text.replace(/\r\n?/g, '\n').trim() : '';
      if (!responseText) {
        return;
      }
      if (editor instanceof HTMLElement) {
        const current = editor.innerHTML.trim();
        const responseHtml = cannedResponseTextToHtml(responseText);
        editor.innerHTML = current ? `${current}${responseHtml}` : responseHtml;
        editor.dispatchEvent(new Event('input', { bubbles: true }));
        editor.focus();
        return;
      }
      if (fallback instanceof HTMLTextAreaElement) {
        fallback.value = fallback.value.trim() ? `${fallback.value}\n\n${responseText}` : responseText;
        fallback.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }

    document.querySelectorAll('[data-canned-responses-open]').forEach((button) => {
      button.addEventListener('click', async () => {
        show(pickerModal);
        const error = pickerModal ? pickerModal.querySelector('[data-canned-responses-error]') : null;
        if (error instanceof HTMLElement) {
          error.hidden = true;
          error.textContent = '';
        }
        try {
          await loadResponses();
        } catch (loadError) {
          if (error instanceof HTMLElement) {
            error.textContent = loadError instanceof Error ? loadError.message : 'Unable to load canned responses.';
            error.hidden = false;
          }
        }
      });
    });

    document.querySelectorAll('[data-canned-response-insert]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-response-id');
        try {
          const responses = await loadResponses();
          const selected = responses.find((response) => String(response.id) === String(id));
          appendResponse(selected ? selected.body : '');
          hide(pickerModal);
        } catch (error) {
          console.error('Unable to insert canned response:', error);
        }
      });
    });

    document.querySelectorAll('[data-canned-responses-close]').forEach((button) => {
      button.addEventListener('click', () => hide(pickerModal));
    });
    document.querySelectorAll('[data-canned-response-create-open]').forEach((button) => {
      button.addEventListener('click', () => show(createModal));
    });
    document.querySelectorAll('[data-canned-response-create-close]').forEach((button) => {
      button.addEventListener('click', () => hide(createModal));
    });
  }

  function ready() {
    const messageWrappers = document.querySelectorAll('[data-timeline-message]');

    messageWrappers.forEach((wrapper) => {
      const toggle = wrapper.parentElement?.querySelector('[data-timeline-message-toggle]');
      if (!(toggle instanceof HTMLButtonElement)) {
        return;
      }

      initialiseTimelineMessage(wrapper, toggle);
    });

    initialisePersistentTicketSections();
    initialiseTicketSplit();
    initialiseReplyTimeEditing();
    initialiseCallRecordingTimeEditing();
    initialiseTicketDetailsAutosave();
    initialiseShipmentWatchProviderDetection();
    initialiseAssetSelector();
    initialiseRequesterSelector();
    initialiseTaskManagement();
    initialiseWatcherManagement();
    initialiseTicketMentions();
    initialiseCannedResponses();
    initialiseAttachmentActions();
    initTicketRelated();
    convertUtcElements();
    initialiseBookingModal();
    initialiseAutomationHistoryModal();
  }

  function initialiseBookingModal() {
    // Find all booking buttons with cal.com links and configure them to open in new tabs
    const bookingButtons = document.querySelectorAll('[data-cal-link]');
    
    if (!bookingButtons.length) {
      return;
    }

    bookingButtons.forEach((button) => {
      const calLink = button.dataset.calLink;
      const ticketId = button.dataset.ticketId;
      const ticketSubject = button.dataset.ticketSubject || '';
      const userName = button.dataset.userName || '';
      const userEmail = button.dataset.userEmail || '';
      const userPhone = button.dataset.userPhone || '';

      if (!calLink) {
        return;
      }

      // Build ticket URL for additional notes (use current page URL)
      const ticketUrl = window.location.href;

      try {
        // Build Cal.com URL with query parameters for prefill
        // Cal.com supports query parameters: name, email, phone, notes, etc.
        const url = new URL(calLink);
        
        // Add name if available
        if (userName && userName.trim()) {
          url.searchParams.set('name', userName.trim());
        }
        
        // Add email if available
        if (userEmail && userEmail.trim()) {
          url.searchParams.set('email', userEmail.trim());
        }

        // Add phone if available
        if (userPhone && userPhone.trim()) {
          url.searchParams.set('phone', userPhone.trim());
        }

        // Add ticket-specific parameters for Cal.com
        if (ticketId) {
          // Add TicketNumber parameter
          url.searchParams.set('TicketNumber', ticketId);
          
          // Add title parameter with format: {ticketId} - {ticketSubject}
          const trimmedSubject = ticketSubject ? ticketSubject.trim() : '';
          url.searchParams.set('title', trimmedSubject ? `${ticketId} - ${trimmedSubject}` : ticketId);
          
          // Add TicketURL parameter with direct link to ticket
          url.searchParams.set('TicketURL', ticketUrl);
        }

        // Build notes with ticket information using array for cleaner handling
        const noteParts = [];
        if (ticketId && ticketSubject) {
          noteParts.push(`Ticket #${ticketId} - ${ticketSubject}`);
        } else if (ticketId) {
          noteParts.push(`Ticket #${ticketId}`);
        }
        noteParts.push(`Ticket URL: ${ticketUrl}`);
        
        const notes = noteParts.join('\n\n');
        if (notes) {
          url.searchParams.set('notes', notes);
        }

        // Store the final URL with prefill parameters as the data-cal-link
        button.setAttribute('data-cal-link', url.toString());
        
        // Add click handler to open link in new tab
        button.addEventListener('click', (event) => {
          event.preventDefault();
          const finalUrl = button.getAttribute('data-cal-link');
          if (finalUrl) {
            window.open(finalUrl, '_blank', 'noopener,noreferrer');
          }
        });
      } catch (error) {
        // Log error if URL construction fails, but don't break the page
        console.error('Failed to build Cal.com booking URL:', error);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { ready(); initialiseTicketPageClock(); });
  } else {
    ready();
    initialiseTicketPageClock();
  }
})();
