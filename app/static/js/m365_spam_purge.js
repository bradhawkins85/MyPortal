document.addEventListener('DOMContentLoaded', function () {
  var element = document.querySelector('[data-spam-purge-config]');
  if (!element) return;

  var config = {};
  try {
    config = JSON.parse(element.getAttribute('data-spam-purge-config') || '{}');
  } catch (error) {
    console.error('Failed to parse spam purge config.', error);
  }

  var focusButton = document.querySelector('[data-focus-search]');
  if (focusButton) {
    focusButton.addEventListener('click', function () {
      var sender = document.getElementById('sender');
      var searchSection = document.getElementById('new-spam-search');
      if (sender) sender.focus();
      if (searchSection) searchSection.scrollIntoView({ behavior: 'smooth' });
    });
  }

  var openModal = null;
  var activeTrigger = null;
  var refreshDue = false;

  function focusableIn(modal) {
    return modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), summary, [href]'
    );
  }

  function handleKeydown(event) {
    if (!openModal) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDetails();
    } else if (event.key === 'Tab') {
      var focusable = focusableIn(openModal);
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  function openDetails(trigger) {
    var modal = document.getElementById(trigger.getAttribute('data-purge-details-open'));
    if (!modal) return;
    if (openModal) closeDetails();
    openModal = modal;
    activeTrigger = trigger;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.addEventListener('keydown', handleKeydown);
    var focusTarget = trigger.getAttribute('data-purge-focus');
    var target = focusTarget ? document.getElementById(focusTarget) : null;
    if (!target) target = modal.querySelector('[data-modal-close]');
    if (target) target.focus();
  }

  function closeDetails() {
    if (!openModal) return;
    openModal.classList.remove('is-visible');
    openModal.hidden = true;
    openModal.setAttribute('aria-hidden', 'true');
    openModal = null;
    document.removeEventListener('keydown', handleKeydown);
    if (activeTrigger && typeof activeTrigger.focus === 'function') activeTrigger.focus();
    activeTrigger = null;
    if (refreshDue) window.location.reload();
  }

  document.querySelectorAll('[data-purge-details-open]').forEach(function (button) {
    button.addEventListener('click', function () {
      openDetails(button);
    });
  });

  document.querySelectorAll('.purge-modal').forEach(function (modal) {
    modal.querySelectorAll('[data-modal-close]').forEach(function (button) {
      button.addEventListener('click', closeDetails);
    });
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeDetails();
    });
  });

  if (config.refresh) {
    window.setTimeout(function () {
      // Never reload away an open popup (for example a half-typed PURGE
      // confirmation); refresh once it is closed instead.
      if (openModal) {
        refreshDue = true;
      } else {
        window.location.reload();
      }
    }, 10000);
  }
});
