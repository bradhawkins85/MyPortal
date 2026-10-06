(function () {
  const modal = document.getElementById('feedback-modal');
  if (!modal) {
    return;
  }

  const form = modal.querySelector('[data-feedback-form]');
  const ratingOptions = Array.from(modal.querySelectorAll('[data-feedback-value]'));
  const reasonInput = modal.querySelector('[data-feedback-reason]');
  const reasonHelp = modal.querySelector('[data-feedback-reason-help]');
  const improvementsInput = modal.querySelector('[data-feedback-improvements]');
  const submitButton = modal.querySelector('[data-feedback-submit]');
  const closeButtons = Array.from(modal.querySelectorAll('[data-modal-close]'));
  const openTrigger = document.querySelector('[data-open-feedback]');

  let selectedRating = null;
  let lastFocused = null;

  function toast(message, variant) {
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(message, { variant });
    } else if (typeof window.alert === 'function') {
      window.alert(message);
    }
  }

  function isOpen() {
    return !modal.hidden;
  }

  function setReasonRequired(required) {
    if (reasonHelp) {
      reasonHelp.textContent = required
        ? 'Please tell us what went wrong so we can follow up.'
        : 'Optional — tell us what works well or could be better.';
    }
    if (reasonInput) {
      if (required) {
        reasonInput.setAttribute('aria-required', 'true');
      } else {
        reasonInput.removeAttribute('aria-required');
      }
    }
  }

  function resetForm() {
    selectedRating = null;
    ratingOptions.forEach((option) => {
      option.classList.remove('is-active');
      option.setAttribute('aria-checked', 'false');
    });
    if (reasonInput) reasonInput.value = '';
    if (improvementsInput) improvementsInput.value = '';
    setReasonRequired(false);
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = 'Submit feedback';
    }
  }

  function focusFirstControl() {
    const first = ratingOptions[0];
    if (first && typeof first.focus === 'function') {
      try {
        first.focus({ preventScroll: true });
      } catch (error) {
        first.focus();
      }
    }
  }

  function openModal() {
    lastFocused = document.activeElement;
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    focusFirstControl();
  }

  function closeModal() {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    resetForm();
    if (lastFocused && typeof lastFocused.focus === 'function') {
      try {
        lastFocused.focus({ preventScroll: true });
      } catch (error) {
        lastFocused.focus();
      }
    }
  }

  function selectRating(value) {
    selectedRating = value;
    ratingOptions.forEach((option) => {
      const active = option.getAttribute('data-feedback-value') === value;
      option.classList.toggle('is-active', active);
      option.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    setReasonRequired(value === 'down');
  }

  if (openTrigger) {
    openTrigger.addEventListener('click', () => {
      if (!isOpen()) openModal();
    });
  }

  closeButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (isOpen()) closeModal();
    });
  });

  ratingOptions.forEach((option) => {
    option.addEventListener('click', () => {
      selectRating(option.getAttribute('data-feedback-value'));
    });
  });

  if (form && submitButton) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();

      if (!selectedRating) {
        toast('Please choose 👍 or 👎 first.', 'error');
        return;
      }
      const reason = reasonInput ? reasonInput.value.trim() : '';
      const improvements = improvementsInput ? improvementsInput.value.trim() : '';
      if (selectedRating === 'down' && !reason && !improvements) {
        toast('Please add a reason or a suggested improvement.', 'error');
        return;
      }

      const body = {
        rating: selectedRating,
        reason: reason || null,
        suggested_improvements: improvements || null,
        page_url: window.location.pathname + window.location.search,
      };

      submitButton.disabled = true;
      submitButton.textContent = 'Submitting…';

      fetch('/api/feedback', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then((response) => {
          if (response.ok) {
            toast("Thanks for your feedback! We've created a ticket so the team can follow up.", 'success');
            closeModal();
            return null;
          }
          return response.json().catch(() => ({})).then((data) => {
            const detail = data && data.detail;
            const message = typeof detail === 'string' ? detail : 'Something went wrong. Please try again.';
            throw new Error(message);
          });
        })
        .catch((error) => {
          toast(error.message || 'Something went wrong. Please try again.', 'error');
          submitButton.disabled = false;
          submitButton.textContent = 'Submit feedback';
        });
    });
  }
})();