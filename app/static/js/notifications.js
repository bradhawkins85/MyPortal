(function () {
  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    return getCookie('myportal_session_csrf');
  }

  const notificationSelectors = {
    table: '#notifications-table',
    selectAll: '[data-notification-select-all]',
    selection: '[data-notification-select]',
    markButtons: '[data-notification-mark]',
    markSelected: '[data-notification-mark-selected]',
    filtersForm: '#notification-filters',
    resetButton: '[data-notification-reset]',
    pageField: '[data-notification-page-field]',
    totalCount: '[data-total-notifications]',
    unreadVisible: '[data-unread-visible]',
    unreadTotal: '[data-unread-total]',
    navBadge: '[data-unread-nav]',
  };

  function parseCount(element) {
    if (!element) {
      return 0;
    }
    const cached = element.getAttribute('data-count');
    if (cached !== null) {
      const parsed = Number(cached);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    const text = element.textContent || '';
    const match = text.match(/(-?\d+)/);
    const value = match ? Number(match[1]) : 0;
    element.setAttribute('data-count', String(value));
    return value;
  }

  function updateCount(element, value) {
    if (!element) {
      return;
    }
    const safeValue = Math.max(0, value);
    element.setAttribute('data-count', String(safeValue));
    const label = element.getAttribute('data-label');
    element.textContent = label ? `${label}: ${safeValue}` : String(safeValue);
  }

  function updateNavUnread(count) {
    const navBadge = document.querySelector(notificationSelectors.navBadge);
    if (count > 0) {
      if (navBadge) {
        navBadge.textContent = String(count);
        navBadge.setAttribute('data-count', String(count));
        navBadge.removeAttribute('hidden');
        navBadge.style.display = '';
        return;
      }
      const link = document.querySelector('a[href="/notifications"] .menu__label');
      if (!link) {
        return;
      }
      const badge = document.createElement('span');
      badge.className = 'menu__badge';
      badge.setAttribute('data-unread-nav', '');
      badge.setAttribute('data-count', String(count));
      badge.textContent = String(count);
      link.appendChild(badge);
      return;
    }
    if (navBadge) {
      navBadge.remove();
    }
  }

  const summaryFieldMap = {
    q: 'search',
    search: 'search',
    read_state: 'read_state',
    event_type: 'event_type',
    created_from: 'created_from',
    created_to: 'created_to',
  };

  function buildSummaryParams() {
    const params = new URLSearchParams();
    const form = document.querySelector(notificationSelectors.filtersForm);
    if (!form) {
      return params;
    }
    const formData = new FormData(form);
    formData.forEach((value, key) => {
      if (!(key in summaryFieldMap)) {
        return;
      }
      const mappedKey = summaryFieldMap[key];
      if (typeof value === 'string') {
        const trimmed = value.trim();
        if (trimmed) {
          params.append(mappedKey, trimmed);
        }
        return;
      }
      if (value !== null && typeof value !== 'undefined') {
        params.append(mappedKey, String(value));
      }
    });
    return params;
  }

  function applySummary(summary) {
    if (!summary || typeof summary !== 'object') {
      return;
    }
    updateCount(
      document.querySelector(notificationSelectors.totalCount),
      Number.isFinite(summary.total_count) ? Number(summary.total_count) : 0
    );
    updateCount(
      document.querySelector(notificationSelectors.unreadVisible),
      Number.isFinite(summary.filtered_unread_count)
        ? Number(summary.filtered_unread_count)
        : 0
    );
    updateCount(
      document.querySelector(notificationSelectors.unreadTotal),
      Number.isFinite(summary.global_unread_count) ? Number(summary.global_unread_count) : 0
    );
    updateNavUnread(
      Number.isFinite(summary.global_unread_count) ? Number(summary.global_unread_count) : 0
    );
  }

  async function refreshNotificationSummary() {
    const params = buildSummaryParams();
    const query = params.toString();
    const response = await fetch(`/api/notifications/summary${query ? `?${query}` : ''}`, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error('Failed to refresh notification summary');
    }
    const data = await response.json();
    applySummary(data);
    return data;
  }

  function formatLocalTime(isoValue) {
    if (!isoValue) {
      return '';
    }
    const date = new Date(isoValue);
    if (Number.isNaN(date.getTime())) {
      return isoValue;
    }
    return date.toLocaleString();
  }

  function applyNotificationUpdate(record) {
    if (!record || typeof record.id === 'undefined') {
      return false;
    }
    const row = document.querySelector(`[data-notification-row="${record.id}"]`);
    if (!row) {
      return false;
    }
    const wasUnread = row.getAttribute('data-unread') === '1';
    const isUnread = !record.read_at;
    row.setAttribute('data-unread', isUnread ? '1' : '0');
    row.classList.toggle('notification-row--unread', isUnread);

    const statusCell = row.querySelector('td[data-label="Status"] span');
    if (statusCell) {
      statusCell.textContent = isUnread ? 'Unread' : 'Read';
      statusCell.classList.remove('status--unread', 'status--read');
      statusCell.classList.add(isUnread ? 'status--unread' : 'status--read');
    }

    const readCell = row.querySelector('td[data-label="Read at"]');
    if (readCell) {
      let readSpan = readCell.querySelector('[data-utc]');
      if (record.read_at) {
        if (!readSpan) {
          readSpan = document.createElement('span');
          readCell.innerHTML = '';
          readCell.appendChild(readSpan);
        }
        readSpan.setAttribute('data-utc', record.read_at);
        readSpan.textContent = formatLocalTime(record.read_at);
      } else {
        if (readSpan) {
          readSpan.remove();
        }
        readCell.innerHTML = '<span class="text-muted">Not read</span>';
      }
    }

    const createdSpan = row.querySelector('td[data-label="Created"] [data-utc]');
    if (createdSpan && record.created_at) {
      createdSpan.setAttribute('data-utc', record.created_at);
      createdSpan.textContent = formatLocalTime(record.created_at);
    }

    const actionButton = row.querySelector(notificationSelectors.markButtons);
    if (actionButton) {
      if (isUnread) {
        actionButton.disabled = false;
        actionButton.textContent = 'Mark as read';
      } else {
        actionButton.disabled = true;
        actionButton.textContent = 'Read';
      }
    }

    const checkbox = row.querySelector(notificationSelectors.selection);
    if (checkbox) {
      checkbox.checked = false;
    }

    return wasUnread && !isUnread;
  }

  async function markNotification(notificationId) {
    const response = await fetch(`/api/notifications/${notificationId}/read`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || 'Failed to update notification');
    }
    return response.json();
  }

  async function acknowledgeNotifications(ids) {
    const response = await fetch('/api/notifications/acknowledge', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ notification_ids: ids }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || 'Failed to acknowledge notifications');
    }
    return response.json();
  }

  function updateSelectionState() {
    const selectAll = document.querySelector(notificationSelectors.selectAll);
    const checkboxes = Array.from(document.querySelectorAll(notificationSelectors.selection));
    const markSelected = document.querySelector(notificationSelectors.markSelected);

    const selected = checkboxes.filter((checkbox) => checkbox.checked && !checkbox.disabled);
    if (markSelected) {
      markSelected.disabled = selected.length === 0;
    }

    if (!selectAll) {
      return;
    }

    if (selected.length === 0) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
      return;
    }

    if (selected.length === checkboxes.length) {
      selectAll.checked = true;
      selectAll.indeterminate = false;
      return;
    }

    selectAll.checked = false;
    selectAll.indeterminate = true;
  }

  function bindSelectionControls() {
    const selectAll = document.querySelector(notificationSelectors.selectAll);
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        const checkboxes = document.querySelectorAll(notificationSelectors.selection);
        checkboxes.forEach((checkbox) => {
          checkbox.checked = selectAll.checked;
        });
        updateSelectionState();
      });
    }

    document.querySelectorAll(notificationSelectors.selection).forEach((checkbox) => {
      checkbox.addEventListener('change', () => {
        updateSelectionState();
      });
    });
  }

  function bindInlineActions() {
    document.querySelectorAll(notificationSelectors.markButtons).forEach((button) => {
      button.addEventListener('click', async () => {
        const notificationId = button.getAttribute('data-notification-mark');
        if (!notificationId) {
          return;
        }
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = 'Marking…';
        try {
          const record = await markNotification(notificationId);
          const changed = applyNotificationUpdate(record);
          if (changed) {
            try {
              await refreshNotificationSummary();
            } catch (summaryError) {
              console.warn(summaryError);
            }
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = originalText;
          window.alert(error.message || 'Unable to mark notification as read');
          return;
        }
        button.textContent = 'Read';
        updateSelectionState();
      });
    });
  }

  function bindBulkActions() {
    const actionButton = document.querySelector(notificationSelectors.markSelected);
    if (!actionButton) {
      return;
    }

    actionButton.addEventListener('click', async () => {
      const selected = Array.from(document.querySelectorAll(notificationSelectors.selection))
        .filter((checkbox) => checkbox.checked && !checkbox.disabled)
        .map((checkbox) => Number(checkbox.value))
        .filter((value) => !Number.isNaN(value));

      if (!selected.length) {
        actionButton.disabled = true;
        return;
      }

      actionButton.disabled = true;
      const originalText = actionButton.textContent;
      actionButton.textContent = 'Marking…';

      try {
        const records = await acknowledgeNotifications(selected);
        let changes = 0;
        records.forEach((record) => {
          if (applyNotificationUpdate(record)) {
            changes += 1;
          }
        });
        if (changes) {
          try {
            await refreshNotificationSummary();
          } catch (summaryError) {
            console.warn(summaryError);
          }
        }
      } catch (error) {
        window.alert(error.message || 'Unable to acknowledge notifications');
      }

      actionButton.textContent = originalText;
      updateSelectionState();
    });
  }

  function bindFilters() {
    const form = document.querySelector(notificationSelectors.filtersForm);
    if (!form) {
      return;
    }
    const pageField = form.querySelector(notificationSelectors.pageField);

    const submitForm = () => {
      if (pageField) {
        pageField.value = '1';
      }
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.submit();
      }
    };

    form.querySelectorAll('select, input[type="datetime-local"]').forEach((element) => {
      element.addEventListener('change', submitForm);
    });

    const resetButton = form.querySelector(notificationSelectors.resetButton);
    if (resetButton) {
      resetButton.addEventListener('click', () => {
        form.querySelectorAll('input[type="search"], input[type="datetime-local"]').forEach((input) => {
          input.value = '';
        });
        form.querySelectorAll('select').forEach((select) => {
          const first = select.querySelector('option');
          if (first) {
            select.value = first.value;
          }
        });
        if (pageField) {
          pageField.value = '1';
        }
        submitForm();
      });
    }

    form.addEventListener('submit', () => {
      if (pageField) {
        pageField.value = '1';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindFilters();
    bindSelectionControls();
    bindInlineActions();
    bindBulkActions();
    updateSelectionState();

    const visibleCounter = document.querySelector(notificationSelectors.unreadVisible);
    if (visibleCounter) {
      updateCount(visibleCounter, parseCount(visibleCounter));
    }
    const totalCounter = document.querySelector(notificationSelectors.unreadTotal);
    if (totalCounter) {
      const totalValue = parseCount(totalCounter);
      updateCount(totalCounter, totalValue);
      updateNavUnread(totalValue);
    }
  });
})();
