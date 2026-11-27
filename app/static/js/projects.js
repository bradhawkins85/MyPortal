// Projects (/admin/projects and a project's page): filters the list, opens the
// project editor in place with a plain-English summary, and switches the add
// task form between a new ticket and an existing one. Without JavaScript the
// links load the editor and every task field shows.
(function () {
  'use strict';

  document.querySelectorAll('[data-submit-on-change]').forEach((input) => {
    input.addEventListener('change', () => { if (input.form) input.form.requestSubmit(); });
  });

  const search = document.querySelector('[data-prj-search]');
  if (search) {
    search.addEventListener('input', () => {
      const term = search.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll('[data-prj-item]').forEach((item) => {
        const match = !term || (item.getAttribute('data-prj-text') || '').includes(term);
        item.hidden = !match;
        if (match) shown += 1;
      });
      const empty = document.querySelector('[data-prj-no-results]');
      if (empty) empty.hidden = shown !== 0;
    });
  }

  document.querySelectorAll('[data-prj-task-form]').forEach((form) => {
    const sync = () => {
      const mode = (form.querySelector('[data-prj-task-mode]:checked') || {}).value || 'new';
      form.querySelectorAll('[data-prj-task-show]').forEach((row) => {
        row.hidden = row.getAttribute('data-prj-task-show') !== mode;
      });
    };
    form.addEventListener('change', sync);
    sync();
  });

  const modal = document.querySelector('[data-prj-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-prj-form]');
  const preview = form.querySelector('[data-prj-preview]');
  const value = (name) => (form.querySelector(`[name="${name}"]`) || {}).value || '';

  function sync() {
    const billing = (form.querySelector('[name="billing_type"]:checked') || {}).value || 'time_materials';
    form.querySelectorAll('[data-prj-show]').forEach((item) => {
      item.hidden = item.getAttribute('data-prj-show') !== billing;
    });
    const amountLabel = form.querySelector('[data-prj-amount-label]');
    if (amountLabel) amountLabel.firstChild.textContent = billing === 'fixed_fee' ? 'Fixed fee ' : 'Budget ';
    const optional = form.querySelector('[data-prj-amount-optional]');
    if (optional) optional.hidden = billing === 'fixed_fee';
    const company = form.querySelector('[name="company_id"]');
    const who = company && company.value ? company.options[company.selectedIndex].text : '';
    const name = value('name').trim();
    if (!name || !who) {
      preview.textContent = 'Name the project and choose the company.';
      return;
    }
    let text = name + ' for ' + who;
    text += billing === 'fixed_fee'
      ? ', billed as a fixed fee' + (value('budget_amount') ? ' of ' + value('budget_amount') : '') + ' by milestone'
      : ', billed for the time logged' + (value('budget_amount') ? ' against a budget of ' + value('budget_amount') : '');
    if (value('budget_hours')) text += ', with ' + value('budget_hours') + ' hours budgeted';
    if (value('due_date')) text += ', due ' + value('due_date');
    preview.textContent = text + '.';
  }

  let lastTrigger = null;
  function open(trigger) {
    lastTrigger = trigger || null;
    modal.hidden = false;
    sync();
    const first = form.querySelector('[name="name"]');
    if (first) first.focus();
  }

  form.addEventListener('input', sync);
  form.addEventListener('change', sync);
  document.querySelectorAll('[data-prj-open]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      if (form.getAttribute('action') !== '/admin/projects') return;
      event.preventDefault();
      open(trigger);
    });
  });
  modal.querySelectorAll('[data-modal-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      modal.hidden = true;
      if (/[?&](new|project)=/.test(window.location.search)) window.history.replaceState(null, '', '/admin/projects');
      if (lastTrigger) lastTrigger.focus();
    });
  });
  if (modal.hasAttribute('data-prj-open-on-load')) open(null); else sync();
})();
