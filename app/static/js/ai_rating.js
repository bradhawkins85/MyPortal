(function () {
  'use strict';

  const ENDPOINT = '/api/ai-quality/feedback';

  function csrfToken(form) {
    const input = form.querySelector('input[name="_csrf"]');
    if (input && input.value) return input.value;
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function currentRating(form) {
    const pressed = form.querySelector('[data-ai-rating-vote][aria-pressed="true"]');
    return pressed ? pressed.value : null;
  }

  function setPressed(form, rating) {
    form.querySelectorAll('[data-ai-rating-vote]').forEach((button) => {
      button.setAttribute('aria-pressed', button.value === rating ? 'true' : 'false');
    });
  }

  function selectedReason(form) {
    const checked = form.querySelector('[data-ai-rating-reason]:checked');
    return checked ? checked.value : null;
  }

  async function save(form, rating) {
    const status = form.querySelector('[data-ai-rating-status]');
    const comment = form.querySelector('[data-ai-rating-comment]');
    const previous = currentRating(form);
    setPressed(form, rating);
    if (status) status.textContent = 'Saving…';
    try {
      const response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken(form) },
        body: JSON.stringify({
          response_id: Number(form.querySelector('input[name="response_id"]').value),
          rating,
          reason: rating === 'down' ? selectedReason(form) : null,
          comment: rating === 'down' && comment ? comment.value.trim() || null : null,
        }),
      });
      if (!response.ok) throw new Error('Rating failed');
      if (status) status.textContent = rating ? 'Thanks — saved' : 'Rating removed';
    } catch (error) {
      setPressed(form, previous);
      if (status) status.textContent = "Couldn't save, try again";
    }
  }

  function enhance(form) {
    if (form.dataset.aiRatingReady) return;
    form.dataset.aiRatingReady = 'true';
    const why = form.querySelector('[data-ai-rating-why]');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      if (!submitter || submitter.name !== 'rating') return;
      const isVote = submitter.hasAttribute('data-ai-rating-vote');
      const alreadyPressed = isVote && submitter.getAttribute('aria-pressed') === 'true';
      const rating = alreadyPressed ? null : submitter.value;
      if (why) why.open = rating === 'down';
      save(form, rating);
    });
  }

  function init(root) {
    (root || document).querySelectorAll('form[data-ai-rating]').forEach(enhance);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init());
  } else {
    init();
  }
  document.addEventListener('htmx:afterSwap', (event) => init(event.target));
  window.MyPortalAiRating = { init };
})();
