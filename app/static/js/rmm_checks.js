// RMM checks form: show only the settings for the chosen type of check.
(function () {
  'use strict';

  const form = document.querySelector('[data-rmm-check-form]');
  if (!form) {
    return;
  }
  const select = form.querySelector('[data-rmm-check-type]');
  const durationLabel = form.querySelector('[data-rmm-check-duration-label]');

  function update() {
    const type = select.value;
    form.querySelectorAll('[data-rmm-check-fields]').forEach((fieldset) => {
      const active = fieldset.getAttribute('data-rmm-check-fields') === type;
      fieldset.hidden = !active;
      fieldset.disabled = !active;
    });
    if (durationLabel) {
      durationLabel.textContent = type === 'offline' ? 'Not checked in for' : 'For at least';
    }
  }

  select.addEventListener('change', update);
  update();
})();
