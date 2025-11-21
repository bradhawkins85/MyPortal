(function () {
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

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getMetaContent(name) {
    const meta = document.querySelector(`meta[name="${name}"]`);
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function getCsrfToken() {
    const metaToken = getMetaContent('csrf-token');
    if (metaToken) {
      return metaToken;
    }
    return getCookie('myportal_session_csrf');
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
    const tacticalBaseUrlRaw = linkedContainer.dataset.tacticalBaseUrl || '';
    const initialCompanyId = select.dataset.initialCompanyId || '';

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
      const tacticalId = typeof option.tactical_asset_id === 'string' ? option.tactical_asset_id.trim() : '';
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

    function buildTacticalUrl(tacticalId) {
      if (!tacticalBaseUrl || typeof tacticalId !== 'string') {
        return null;
      }
      const trimmed = tacticalId.trim();
      if (!trimmed) {
        return null;
      }
      return `${tacticalBaseUrl}/web/agents/${encodeURIComponent(trimmed)}`;
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
        item.className = 'ticket-assets-linked__item';
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
        item.appendChild(hiddenInput);

        const label = formatAssetLabel(record);
        const displayName = typeof record.name === 'string' && record.name.trim() ? record.name.trim() : label;
        const tacticalUrl = buildTacticalUrl(record.tactical_asset_id);
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

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--ghost button--icon ticket-assets-linked__remove';
        removeButton.setAttribute('data-linked-asset-remove', '');
        removeButton.innerHTML = '<span class="visually-hidden">Remove asset</span>×';
        item.appendChild(removeButton);

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
      const removeButton = target ? target.closest('[data-linked-asset-remove]') : null;
      if (!removeButton) {
        return;
      }
      const parent = removeButton.closest('[data-linked-asset]');
      if (!parent) {
        return;
      }
      const assetId = parent.getAttribute('data-asset-id');
      removeLinkedAssetById(assetId);
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
        if (text) {
          summaryElement.textContent = text;
          summaryElement.hidden = false;
        } else {
          summaryElement.textContent = '';
          summaryElement.hidden = true;
        }
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
        if (text) {
          summaryElement.textContent = text;
          summaryElement.hidden = false;
        } else {
          summaryElement.textContent = '';
          summaryElement.hidden = true;
        }
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
      const userId = selectElement.value;
      if (!userId) {
        return;
      }

      const selectedOption = selectElement.options[selectElement.selectedIndex];
      const userEmail = selectedOption.textContent || '';

      addButton.disabled = true;
      addButton.setAttribute('aria-busy', 'true');

      try {
        const csrfToken = getCsrfToken();
        const response = await fetch(`/api/tickets/${ticketId}/watchers/${userId}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || 'Failed to add watcher');
        }

        // Remove the option from select
        selectedOption.remove();
        selectElement.value = '';

        // Add to watchers list
        addWatcherToList(userId, userEmail, null);
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
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || 'Failed to add watcher');
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

  function getTicketIdFromPath() {
    const match = window.location.pathname.match(/\/admin\/tickets\/(\d+)/);
    return match ? match[1] : null;
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

    initialiseReplyTimeEditing();
    initialiseCallRecordingTimeEditing();
    initialiseAssetSelector();
    initialiseTaskManagement();
    initialiseWatcherManagement();
    initialiseBookingModal();
  }

  function initialiseBookingModal() {
    // Find all booking buttons with cal.com embed
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

      // Build ticket URL for additional notes
      const ticketUrl = ticketId ? `${window.location.origin}/tickets/${ticketId}` : '';

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

      // Build notes with ticket information
      let notes = '';
      if (ticketId) {
        notes += `Ticket #${ticketId}`;
      }
      if (ticketSubject) {
        notes += ` - ${ticketSubject}`;
      }
      if (ticketUrl) {
        notes += `\n\nTicket URL: ${ticketUrl}`;
      }
      
      if (notes) {
        url.searchParams.set('notes', notes);
      }

      // Store the final URL with prefill parameters as the data-cal-link
      button.setAttribute('data-cal-link', url.toString());
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
