// Event log rule form (rmm/event_log_rules.html): a rule either drops the events
// it matches or takes its listed actions. The actions, and the script cooldown
// once the list runs a script, only show while they apply. A drop rule's actions
// are disabled too, so the browser neither checks nor sends them.
(function () {
  'use strict';

  const form = document.querySelector('[data-rmm-event-rule-form]');
  if (!form) return;
  const actions = form.querySelector('[data-rmm-rule-actions]');
  const cooldown = form.querySelector('[data-rmm-rule-cooldown]');
  if (!actions) return;

  const mode = () => {
    const input = form.querySelector('[data-rmm-rule-mode]:checked');
    return input ? input.value : 'actions';
  };
  const types = () => Array.from(actions.querySelectorAll('[data-rmm-steps] > [data-rmm-step]'))
    .map((card) => card.getAttribute('data-rmm-step-type'));

  function update() {
    const dropping = mode() === 'drop';
    actions.hidden = dropping;
    actions.disabled = dropping;
    // Hidden, not disabled: the saved cooldown is kept for when a script is added back.
    if (cooldown) cooldown.hidden = types().indexOf('run_script') === -1;
  }

  form.addEventListener('change', (event) => {
    if (event.target.matches('[data-rmm-rule-mode]')) update();
  });
  actions.addEventListener('rmm:steps-change', update);
  update();
})();
