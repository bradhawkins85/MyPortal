(function () {
  'use strict';

  const choiceTypes = new Set(['dropdown', 'multi_select', 'radio']);
  let fieldCounter = 0;

  function initFormBuilder(form) {
    const builder = form.querySelector('[data-coq-form]');
    if (!builder) return function () {};
    const list = builder.querySelector('[data-coq-form-fields]');
    const template = builder.querySelector('[data-coq-form-template]');
    const addButton = builder.querySelector('[data-coq-form-add]');
    const entryLabel = builder.querySelector('[data-coq-entry-label]');
    const summary = builder.querySelector('[data-coq-form-summary]');
    const max = parseInt(addButton.dataset.max, 10) || 20;

    const rows = () => Array.from(list.querySelectorAll('[data-coq-form-field]'));

    function refresh() {
      const all = rows();
      all.forEach((row, index) => {
        const type = row.querySelector('[data-coq-form-type]');
        const options = row.querySelector('[data-coq-form-options-field]');
        options.hidden = type.value !== 'dropdown';
        const label = row.querySelector('[data-coq-form-label]').value.trim() || `question ${index + 1}`;
        row.querySelectorAll('[data-coq-form-name]').forEach((name) => { name.textContent = label; });
        row.querySelector('[data-coq-form-move="-1"]').disabled = index === 0;
        row.querySelector('[data-coq-form-move="1"]').disabled = index === all.length - 1;
        row.querySelector('[data-coq-form-remove]').disabled = all.length === 1;
      });
      addButton.disabled = all.length >= max;
      const name = (entryLabel.value.trim() || 'entry').toLowerCase();
      const labels = all.map((row) => {
        const text = row.querySelector('[data-coq-form-label]').value.trim();
        const required = row.querySelector('[data-coq-form-required]').value === '1';
        return text ? `${text}${required ? ' (required)' : ''}` : '';
      }).filter(Boolean);
      summary.textContent = labels.length
        ? `The client adds one ${name} at a time. Each ${name} asks for: ${labels.join(', ')}.`
        : 'Add at least one question to the form.';
    }

    addButton.addEventListener('click', () => {
      if (rows().length >= max) return;
      fieldCounter += 1;
      const holder = document.createElement('template');
      holder.innerHTML = template.innerHTML.split('__FIELD__').join(`new-${fieldCounter}`);
      const row = holder.content.querySelector('[data-coq-form-field]');
      list.append(row);
      refresh();
      row.querySelector('[data-coq-form-label]').focus();
    });

    list.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      const row = button.closest('[data-coq-form-field]');
      if (button.hasAttribute('data-coq-form-remove')) {
        if (rows().length === 1) return;
        const next = row.nextElementSibling || row.previousElementSibling;
        row.remove();
        refresh();
        if (next) next.querySelector('[data-coq-form-label]').focus();
      } else if (button.dataset.coqFormMove) {
        if (button.dataset.coqFormMove === '-1' && row.previousElementSibling) {
          list.insertBefore(row, row.previousElementSibling);
        } else if (button.dataset.coqFormMove === '1' && row.nextElementSibling) {
          list.insertBefore(row.nextElementSibling, row);
        }
        refresh();
        if (!button.disabled) button.focus();
        else row.querySelector('[data-coq-form-label]').focus();
      }
    });

    builder.addEventListener('input', refresh);
    builder.addEventListener('change', refresh);
    refresh();
    return refresh;
  }

  document.querySelectorAll('[data-coq-editor]').forEach((form) => {
    const type = form.querySelector('[data-coq-field-type]');
    const options = form.querySelector('[data-coq-options]');
    const optionsInput = form.querySelector('[data-coq-options-input]');
    const requiredHelp = form.querySelector('[data-coq-required-help]');
    const formRequiredHelp = form.querySelector('[data-coq-form-required-help]');
    const builder = form.querySelector('[data-coq-form]');
    if (!type || !options || !optionsInput) return;
    initFormBuilder(form);

    function updateAnswerType() {
      const hasOptions = choiceTypes.has(type.value);
      options.hidden = !hasOptions;
      optionsInput.required = hasOptions;
      if (requiredHelp) requiredHelp.hidden = type.value !== 'checkbox';
      if (formRequiredHelp) formRequiredHelp.hidden = type.value !== 'form';
      if (builder) builder.hidden = type.value !== 'form';
    }

    type.addEventListener('change', updateAnswerType);
    updateAnswerType();
  });

  const errorSummary = document.querySelector('[data-coq-errors]');
  if (errorSummary) errorSummary.focus();
})();
