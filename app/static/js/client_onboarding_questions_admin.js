(function () {
  'use strict';

  const choiceTypes = new Set(['dropdown', 'multi_select', 'radio']);

  document.querySelectorAll('[data-coq-editor]').forEach((form) => {
    const type = form.querySelector('[data-coq-field-type]');
    const options = form.querySelector('[data-coq-options]');
    const optionsInput = form.querySelector('[data-coq-options-input]');
    const requiredHelp = form.querySelector('[data-coq-required-help]');
    if (!type || !options || !optionsInput) return;

    function updateAnswerType() {
      const hasOptions = choiceTypes.has(type.value);
      options.hidden = !hasOptions;
      optionsInput.required = hasOptions;
      if (requiredHelp) requiredHelp.hidden = type.value !== 'checkbox';
    }

    type.addEventListener('change', updateAnswerType);
    updateAnswerType();
  });

  const errorSummary = document.querySelector('[data-coq-errors]');
  if (errorSummary) errorSummary.focus();
})();
