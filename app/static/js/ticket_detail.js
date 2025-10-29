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

  function formatTimeSummary(minutes, isBillable) {
    if (typeof minutes !== 'number' || Number.isNaN(minutes) || minutes < 0) {
      return '';
    }
    const label = minutes === 1 ? 'minute' : 'minutes';
    const billing = isBillable ? 'Billable' : 'Non-billable';
    return `${minutes} ${label} · ${billing}`;
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
    const errorMessage = modal.querySelector('[data-reply-time-error]');
    const submitButton = modal.querySelector('[data-reply-time-submit]');
    const triggers = document.querySelectorAll('[data-reply-edit]');
    if (!form || !minutesInput || !billableCheckbox || !errorMessage || !submitButton || !triggers.length) {
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

    function updateReplyDisplay(replyArticle, minutes, billable, summary) {
      if (!replyArticle) {
        return;
      }
      replyArticle.dataset.replyMinutes = typeof minutes === 'number' ? String(minutes) : '';
      replyArticle.dataset.replyBillable = billable ? 'true' : 'false';
      const summaryElement = replyArticle.querySelector('[data-reply-time-summary]');
      if (summaryElement) {
        const text = summary || formatTimeSummary(minutes, billable);
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
        minutesInput.value = minutesValue;
        billableCheckbox.checked = billableValue;
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
          body: JSON.stringify({ minutes_spent: minutes, is_billable: isBillable }),
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

        updateReplyDisplay(activeReply, updatedMinutes, updatedBillable, summaryText);
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
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
