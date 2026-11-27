// Service agreements (/admin/agreements): filters, opens the editor and writes
// a plain-English summary of the agreement as it is filled in. Without
// JavaScript the "+ Add agreement" link opens the editor on its own page load.
(function () {
  'use strict';

  document.querySelectorAll('[data-submit-on-change]').forEach((input) => {
    input.addEventListener('change', () => { if (input.form) input.form.requestSubmit(); });
  });

  const search = document.querySelector('[data-agr-search]');
  if (search) {
    search.addEventListener('input', () => {
      const term = search.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll('[data-agr-group]').forEach((group) => {
        let groupShown = 0;
        group.querySelectorAll('[data-agr-item]').forEach((item) => {
          const match = !term || (item.getAttribute('data-agr-text') || '').includes(term);
          item.hidden = !match;
          if (match) groupShown += 1;
        });
        group.hidden = groupShown === 0;
        shown += groupShown;
      });
      const empty = document.querySelector('[data-agr-no-results]');
      if (empty) empty.hidden = shown !== 0;
    });
  }

  const modal = document.querySelector('[data-agr-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-agr-form]');
  const preview = form.querySelector('[data-agr-preview]');
  let labourNames = {};
  try { labourNames = JSON.parse(document.getElementById('agr-labour-names').textContent || '{}'); } catch (error) { labourNames = {}; }

  const checked = (name) => (form.querySelector(`[name="${name}"]:checked`) || {}).value || '';
  const value = (name) => (form.querySelector(`[name="${name}"]`) || {}).value || '';

  function describe() {
    const kind = checked('agreement_type') || 'hours';
    const labour = Array.from(form.querySelectorAll('[name="labour_type_ids"]:checked')).map((box) => labourNames[box.value] || box.value);
    const scope = labour.length ? ' for ' + labour.join(', ') + ' work' : '';
    const company = form.querySelector('[name="company_id"]');
    const who = company && company.value ? company.options[company.selectedIndex].text : 'The company';
    const hours = value('hours_allowance');
    let text;
    if (kind === 'unlimited') {
      text = who + ' has all support time' + scope + ' covered.';
    } else if (kind === 'block') {
      text = who + ' has ' + (hours || '?') + ' prepaid hours' + scope + ', used up over the agreement.';
    } else {
      const unit = { monthly: 'month', quarterly: 'quarter', annual: 'year' }[checked('period')] || 'month';
      text = who + ' has ' + (hours || '?') + ' hours' + scope + ' included each ' + unit + '.';
    }
    const start = value('start_date');
    const end = value('end_date');
    if (start) text += ' From ' + start + (end ? ' to ' + end : ', with no end date') + '.';
    if (kind !== 'unlimited' && value('alert_percent')) text += ' Alert at ' + value('alert_percent') + '% used.';
    preview.textContent = text;
  }

  function sync() {
    const kind = checked('agreement_type') || 'hours';
    form.querySelectorAll('[data-agr-show]').forEach((element) => {
      element.hidden = element.getAttribute('data-agr-show').split(',').indexOf(kind) === -1;
    });
    describe();
  }

  let lastTrigger = null;
  function open(trigger) {
    lastTrigger = trigger || null;
    modal.hidden = false;
    sync();
    const first = form.querySelector('[name="company_id"]');
    if (first) first.focus();
  }

  form.addEventListener('input', sync);
  form.addEventListener('change', sync);
  document.querySelectorAll('[data-agr-open]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      if (!form.getAttribute('action').endsWith('/admin/agreements')) return; // editing loads its own page
      event.preventDefault();
      open(trigger);
    });
  });
  modal.querySelectorAll('[data-modal-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      modal.hidden = true;
      if (window.location.search.includes('agreement=') || window.location.search.includes('new=')) {
        window.history.replaceState(null, '', '/admin/agreements');
      }
      if (lastTrigger) lastTrigger.focus();
    });
  });
  if (modal.hasAttribute('data-agr-open-on-load')) open(null); else sync();
})();
