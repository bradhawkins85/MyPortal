(function () {
  let attemptsModal = null;

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    return getCookie('myportal_session_csrf');
  }

  async function requestJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
        ...(options && options.headers ? options.headers : {}),
      },
      ...options,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* ignore */
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  function parseEvent(row) {
    if (!row) {
      return null;
    }
    try {
      const value = row.dataset.event || '{}';
      return JSON.parse(value);
    } catch (error) {
      return null;
    }
  }

  function query(id) {
    return document.getElementById(id);
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.__previousActive = document.activeElement;
    const focusTarget =
      modal.querySelector('[data-initial-focus]') ||
      modal.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
    if (focusTarget && typeof focusTarget.focus === 'function') {
      focusTarget.focus();
    }
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove('is-visible');
    modal.hidden = true;
    const previous = modal.__previousActive;
    if (previous && typeof previous.focus === 'function') {
      previous.focus();
    }
  }

  function bindModalDismissal(modal) {
    if (!modal) {
      return;
    }
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.hasAttribute('data-modal-close')) {
        closeModal(modal);
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        closeModal(modal);
      }
    });
  }

  function formatIso(iso) {
    if (!iso) {
      return { text: '—', value: '' };
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return { text: '—', value: '' };
    }
    return { text: date.toLocaleString(), value: iso };
  }

  function setAttemptsPlaceholder(message) {
    const tbody = query('webhook-attempts-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.className = 'table__empty';
    cell.textContent = message;
    row.appendChild(cell);
    tbody.appendChild(row);
  }

  function renderAttempts(attempts) {
    const tbody = query('webhook-attempts-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    if (!attempts || attempts.length === 0) {
      setAttemptsPlaceholder('No delivery attempts recorded for this event.');
      return;
    }
    attempts.forEach((attempt) => {
      const row = document.createElement('tr');

      const attemptedCell = document.createElement('td');
      attemptedCell.setAttribute('data-label', 'Attempted');
      const attempted = formatIso(
        attempt.attempted_at || attempt.attemptedAt || attempt.attemptedIso
      );
      attemptedCell.setAttribute('data-value', attempted.value);
      attemptedCell.textContent = attempted.text;
      row.appendChild(attemptedCell);

      const statusCell = document.createElement('td');
      statusCell.setAttribute('data-label', 'Status');
      statusCell.textContent = attempt.status || 'unknown';
      statusCell.setAttribute('data-value', attempt.status || '');
      row.appendChild(statusCell);

      const durationCell = document.createElement('td');
      durationCell.setAttribute('data-label', 'Duration (ms)');
      const duration = typeof attempt.duration_ms === 'number' ? attempt.duration_ms : attempt.durationMs;
      durationCell.setAttribute('data-value', String(duration ?? 0));
      durationCell.textContent = Number.isFinite(duration) ? String(duration) : '—';
      row.appendChild(durationCell);

      const responseCell = document.createElement('td');
      responseCell.setAttribute('data-label', 'Response');
      const statusCode =
        attempt.response_status ?? attempt.responseStatus ?? attempt.statusCode ?? null;
      responseCell.setAttribute('data-value', statusCode !== null ? String(statusCode) : '');
      responseCell.textContent = statusCode !== null ? String(statusCode) : '—';
      row.appendChild(responseCell);

      const errorCell = document.createElement('td');
      errorCell.setAttribute('data-label', 'Error');
      const errorMessage = attempt.error_message || attempt.errorMessage || attempt.error;
      errorCell.textContent = errorMessage || '—';
      row.appendChild(errorCell);

      tbody.appendChild(row);
    });
  }

  async function showAttemptsModal(eventData) {
    if (!attemptsModal || !eventData || !eventData.id) {
      return;
    }
    const title = query('webhook-attempts-title');
    if (title) {
      title.textContent = `Webhook attempts — ${eventData.name || `Event #${eventData.id}`}`;
    }
    const description = query('webhook-attempts-description');
    if (description) {
      const target = eventData.target_url || eventData.targetUrl || '';
      description.textContent = target
        ? `Review the latest delivery attempts for ${target}.`
        : 'Review the latest delivery attempts for the selected webhook event.';
    }
    setAttemptsPlaceholder('Loading attempts…');
    openModal(attemptsModal);
    try {
      const attempts = await requestJson(`/scheduler/webhooks/${eventData.id}/attempts?limit=50`);
      renderAttempts(Array.isArray(attempts) ? attempts : []);
    } catch (error) {
      setAttemptsPlaceholder(`Unable to load attempts: ${error.message}`);
    }
  }

  function bindRetryButtons() {
    document.querySelectorAll('[data-webhook-retry]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (button.disabled) {
          return;
        }
        const row = button.closest('tr');
        const eventData = parseEvent(row);
        if (!eventData || !eventData.id) {
          return;
        }
        try {
          await requestJson(`/scheduler/webhooks/${eventData.id}/retry`, { method: 'POST' });
          alert('Webhook retry has been queued. Refresh the page to see updates.');
          window.location.reload();
        } catch (error) {
          alert(`Unable to retry webhook: ${error.message}`);
        }
      });
    });
  }

  function bindAttemptsButtons() {
    document.querySelectorAll('[data-webhook-attempts]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        const eventData = parseEvent(row);
        if (eventData) {
          showAttemptsModal(eventData);
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    attemptsModal = query('webhook-attempts-modal');
    bindModalDismissal(attemptsModal);
    bindRetryButtons();
    bindAttemptsButtons();
  });
})();
