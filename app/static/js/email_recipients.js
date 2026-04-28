/**
 * Email recipients popup.
 *
 * Wires up the per-recipient delivery-status modal that opens when a
 * technician clicks the aggregate delivery-status badge on a ticket reply
 * that has more than one recipient (To/CC/BCC).
 *
 * The trigger is a `<button data-email-recipients-trigger data-reply-id>`
 * rendered around the existing badge. The shared modal markup lives in
 * `templates/tickets/_email_recipients_modal.html`.
 */
(function () {
  'use strict';

  const STATUS_LABELS = {
    bounced: 'Bounced',
    spam: 'Marked spam',
    rejected: 'Rejected',
    opened: 'Opened',
    delivered: 'Delivered',
    processed: 'Sent',
    sent: 'Sent',
    pending: 'Pending',
  };

  const STATUS_VARIANTS = {
    bounced: 'danger',
    spam: 'danger',
    rejected: 'danger',
    opened: 'success',
    delivered: 'info',
    processed: 'neutral',
    sent: 'neutral',
    pending: 'warning',
  };

  const ROLE_LABELS = {
    to: 'To',
    cc: 'CC',
    bcc: 'BCC',
  };

  function getModal() {
    return document.getElementById('email-recipients-modal');
  }

  function showModal(modal) {
    modal.hidden = false;
    modal.classList.add('is-open');
    document.body.classList.add('modal-open');
  }

  function hideModal(modal) {
    modal.hidden = true;
    modal.classList.remove('is-open');
    document.body.classList.remove('modal-open');
  }

  function setError(modal, message) {
    const errorEl = modal.querySelector('[data-email-recipients-error]');
    if (!errorEl) return;
    if (message) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    } else {
      errorEl.textContent = '';
      errorEl.hidden = true;
    }
  }

  function setLoading(modal, isLoading) {
    const loadingEl = modal.querySelector('[data-email-recipients-loading]');
    if (loadingEl) {
      loadingEl.hidden = !isLoading;
    }
  }

  function clearTable(modal) {
    const rows = modal.querySelector('[data-email-recipients-rows]');
    if (rows) {
      rows.textContent = '';
    }
    const wrap = modal.querySelector('[data-email-recipients-table-wrap]');
    if (wrap) wrap.hidden = true;
    const empty = modal.querySelector('[data-email-recipients-empty]');
    if (empty) empty.hidden = true;
  }

  function renderTimestamp(value) {
    if (!value) {
      const span = document.createElement('span');
      span.className = 'text-muted';
      span.textContent = '—';
      return span;
    }
    const span = document.createElement('span');
    span.setAttribute('data-utc', value);
    // Best-effort fallback rendering so the cell isn't blank before the
    // global UTC->local localiser in main.js runs.
    try {
      span.textContent = new Date(value).toLocaleString();
    } catch (e) {
      span.textContent = value;
    }
    return span;
  }

  function renderStatusPill(status) {
    const variant = STATUS_VARIANTS[status] || 'neutral';
    const label = STATUS_LABELS[status] || status || 'Unknown';
    const pill = document.createElement('span');
    pill.className = 'status status--' + variant;
    pill.textContent = label;
    return pill;
  }

  function renderRecipientRow(recipient) {
    const tr = document.createElement('tr');

    const recipientCell = document.createElement('td');
    if (recipient.recipient_name) {
      const name = document.createElement('div');
      name.textContent = recipient.recipient_name;
      recipientCell.appendChild(name);
      const email = document.createElement('div');
      email.className = 'text-muted';
      email.textContent = recipient.recipient_email || '';
      recipientCell.appendChild(email);
    } else {
      recipientCell.textContent = recipient.recipient_email || '';
    }
    tr.appendChild(recipientCell);

    const roleCell = document.createElement('td');
    roleCell.textContent = ROLE_LABELS[recipient.recipient_role] || recipient.recipient_role || 'To';
    tr.appendChild(roleCell);

    const statusCell = document.createElement('td');
    statusCell.appendChild(renderStatusPill(recipient.status));
    tr.appendChild(statusCell);

    const sentCell = document.createElement('td');
    sentCell.appendChild(renderTimestamp(recipient.sent_at || recipient.processed_at));
    tr.appendChild(sentCell);

    const deliveredCell = document.createElement('td');
    deliveredCell.appendChild(renderTimestamp(recipient.delivered_at));
    tr.appendChild(deliveredCell);

    const openedCell = document.createElement('td');
    if (recipient.opened_at) {
      const ts = renderTimestamp(recipient.opened_at);
      openedCell.appendChild(ts);
      if (recipient.open_count && recipient.open_count > 1) {
        const meta = document.createElement('div');
        meta.className = 'text-muted';
        meta.textContent = '× ' + recipient.open_count;
        openedCell.appendChild(meta);
      }
    } else {
      const dash = document.createElement('span');
      dash.className = 'text-muted';
      dash.textContent = '—';
      openedCell.appendChild(dash);
    }
    tr.appendChild(openedCell);

    const lastEventCell = document.createElement('td');
    if (recipient.last_event_at || recipient.last_event_type) {
      if (recipient.last_event_type) {
        const t = document.createElement('div');
        t.textContent = recipient.last_event_type;
        lastEventCell.appendChild(t);
      }
      if (recipient.last_event_at) {
        lastEventCell.appendChild(renderTimestamp(recipient.last_event_at));
      }
      if (recipient.last_event_detail) {
        const detail = document.createElement('div');
        detail.className = 'text-muted';
        detail.textContent = recipient.last_event_detail;
        lastEventCell.appendChild(detail);
      }
    } else {
      const dash = document.createElement('span');
      dash.className = 'text-muted';
      dash.textContent = '—';
      lastEventCell.appendChild(dash);
    }
    tr.appendChild(lastEventCell);

    return tr;
  }

  function populateRecipients(modal, payload) {
    clearTable(modal);
    const recipients = (payload && payload.recipients) || [];
    const summaryEl = modal.querySelector('[data-email-recipients-summary]');
    if (summaryEl) {
      summaryEl.textContent = recipients.length === 1
        ? '1 recipient'
        : recipients.length + ' recipients';
    }
    if (!recipients.length) {
      const empty = modal.querySelector('[data-email-recipients-empty]');
      if (empty) empty.hidden = false;
      return;
    }
    const rowsEl = modal.querySelector('[data-email-recipients-rows]');
    const wrap = modal.querySelector('[data-email-recipients-table-wrap]');
    if (!rowsEl || !wrap) return;
    recipients.forEach((recipient) => {
      rowsEl.appendChild(renderRecipientRow(recipient));
    });
    wrap.hidden = false;
  }

  async function loadRecipients(replyId) {
    const url = '/api/email-tracking/replies/' + encodeURIComponent(replyId) + '/recipients';
    const response = await fetch(url, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    });
    if (!response.ok) {
      let detail = 'Failed to load delivery status (' + response.status + ')';
      try {
        const body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return response.json();
  }

  async function openModalForReply(replyId) {
    const modal = getModal();
    if (!modal) return;
    setError(modal, '');
    clearTable(modal);
    setLoading(modal, true);
    showModal(modal);
    try {
      const data = await loadRecipients(replyId);
      populateRecipients(modal, data);
    } catch (err) {
      setError(modal, err && err.message ? err.message : String(err));
    } finally {
      setLoading(modal, false);
    }
  }

  function attachCloseHandlers(modal) {
    modal.querySelectorAll('[data-modal-close]').forEach((btn) => {
      btn.addEventListener('click', () => hideModal(modal));
    });
    modal.addEventListener('click', (event) => {
      if (event.target === modal) hideModal(modal);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        hideModal(modal);
      }
    });
  }

  function init() {
    const modal = getModal();
    if (modal) {
      attachCloseHandlers(modal);
    }
    document.addEventListener('click', (event) => {
      const trigger = event.target && event.target.closest && event.target.closest('[data-email-recipients-trigger]');
      if (!trigger) return;
      event.preventDefault();
      const replyId = trigger.getAttribute('data-reply-id');
      if (!replyId) return;
      openModalForReply(replyId);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
