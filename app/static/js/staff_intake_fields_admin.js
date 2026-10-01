(function () {
  'use strict';

  const OPTION_TYPES = new Set(['select', 'multiselect']);

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = value;
      } else if (key === 'dataset') {
        Object.assign(node.dataset, value);
      } else if (value === true) {
        node.setAttribute(key, '');
      } else {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach((child) => {
      if (child === null || child === undefined || child === false) {
        return;
      }
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function parseJson(id) {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (error) {
      console.error('Unable to parse staff intake field data', error);
      return null;
    }
  }

  function presenceOf(field) {
    if (!field.visible) {
      return 'hidden';
    }
    return field.required ? 'required' : 'optional';
  }

  function ordinal(value) {
    const number = Number(value) || 0;
    const mod100 = Math.abs(number) % 100;
    const mod10 = Math.abs(number) % 10;
    let suffix = 'th';
    if (mod100 < 11 || mod100 > 13) {
      suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[mod10] || 'th';
    }
    return `${number}${suffix}`;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const pageForm = document.querySelector('[data-sif-form]');
    const modal = document.getElementById('staff-intake-field-modal');
    const data = parseJson('staff-intake-field-editor-data');
    if (!pageForm || !modal || !data) {
      return;
    }

    const coreKeys = new Set(data.coreKeys || []);
    const typeLockedKeys = new Set(data.typeLockedKeys || []);
    const fields = new Map();
    (data.fields || []).forEach((field) => {
      fields.set(field.key, {
        key: field.key,
        label: field.label || field.key,
        type: field.type || 'text',
        visible: Boolean(field.visible),
        required: Boolean(field.required),
        sort_order: Number(field.sort_order) || 0,
        options: (field.options || []).map((option) => ({
          value: String(option.value || ''),
          label: String(option.label || option.value || ''),
        })),
      });
    });

    const outputs = pageForm.querySelector('[data-sif-outputs]');
    const status = pageForm.querySelector('[data-sif-status]');
    const editor = modal.querySelector('[data-sif-editor]');
    const title = modal.querySelector('[data-sif-title]');
    const subtitle = modal.querySelector('[data-sif-subtitle]');
    const presenceInputs = Array.from(modal.querySelectorAll('[data-sif-presence]'));
    const typeInputs = Array.from(modal.querySelectorAll('[data-sif-type]'));
    const coreNote = modal.querySelector('[data-sif-core-note]');
    const typeNote = modal.querySelector('[data-sif-type-note]');
    const optionsSection = modal.querySelector('[data-sif-options-section]');
    const optionsList = modal.querySelector('[data-sif-options]');
    const optionsError = modal.querySelector('[data-sif-error="options"]');
    const bulk = modal.querySelector('[data-sif-bulk]');
    const bulkInput = modal.querySelector('[data-sif-bulk-input]');
    const bulkToggles = Array.from(modal.querySelectorAll('[data-sif-bulk-toggle]'));
    const orderInput = modal.querySelector('[data-sif-order]');
    const preview = modal.querySelector('[data-sif-preview]');
    const previewRules = modal.querySelector('[data-sif-preview-rules]');
    const submitButton = modal.querySelector('[data-sif-submit]');

    let draft = null;
    let optionUid = 0;
    let snapshot = '';
    let lastTrigger = null;

    // ----- draft state ---------------------------------------------------

    function currentPresence() {
      const checked = presenceInputs.find((input) => input.checked);
      return checked ? checked.value : 'optional';
    }

    function currentType() {
      const checked = typeInputs.find((input) => input.checked);
      return checked ? checked.value : 'text';
    }

    function optionValue(option) {
      return (option.valueTouched ? option.value : option.label).trim();
    }

    function addOption(label, value) {
      const option = {
        uid: ++optionUid,
        label: label || '',
        value: value || '',
        valueTouched: Boolean(value) && value !== label,
      };
      draft.options.push(option);
      return option;
    }

    function serializeDraft() {
      return JSON.stringify({
        presence: currentPresence(),
        type: currentType(),
        order: orderInput.value,
        options: draft ? draft.options.map((option) => [option.label, optionValue(option)]) : [],
      });
    }

    // ----- options builder -----------------------------------------------

    function renderOptions() {
      optionsList.textContent = '';
      draft.options.forEach((option, index) => {
        const row = el('li', { className: 'scf-option', dataset: { uid: String(option.uid) } }, [
          el('span', { className: 'scf-option__move' }, [
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option up',
              title: 'Move up',
              disabled: index === 0,
              dataset: { sifOptionMove: '-1' },
              text: '↑',
            }),
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option down',
              title: 'Move down',
              disabled: index === draft.options.length - 1,
              dataset: { sifOptionMove: '1' },
              text: '↓',
            }),
          ]),
          el('input', {
            className: 'form-input',
            value: option.label,
            placeholder: `Option ${index + 1}`,
            'aria-label': `Option ${index + 1} label`,
            dataset: { sifOptionField: 'label' },
          }),
          el('input', {
            className: 'form-input scf-mono',
            value: option.valueTouched ? option.value : '',
            placeholder: option.label || 'Same as label',
            'aria-label': `Option ${index + 1} stored value`,
            spellcheck: 'false',
            dataset: { sifOptionField: 'value' },
          }),
          el('button', {
            type: 'button',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove option ${option.label || index + 1}`,
            title: 'Remove option',
            dataset: { sifOptionRemove: '' },
            text: '×',
          }),
        ]);
        optionsList.append(row);
      });
      if (!draft.options.length) {
        optionsList.append(el('li', { className: 'scf-options__empty text-muted', text: 'No options yet. Add at least one.' }));
      }
    }

    function focusOption(uid) {
      const input = optionsList.querySelector(`[data-uid="${uid}"] [data-sif-option-field="label"]`);
      if (input) {
        input.focus();
      }
    }

    function findOptionIndex(row) {
      return draft.options.findIndex((item) => String(item.uid) === row.dataset.uid);
    }

    optionsList.addEventListener('input', (event) => {
      const input = event.target.closest('[data-sif-option-field]');
      if (!input) {
        return;
      }
      const row = input.closest('.scf-option');
      const option = draft.options[findOptionIndex(row)];
      if (!option) {
        return;
      }
      if (input.dataset.sifOptionField === 'label') {
        option.label = input.value;
        row.querySelector('[data-sif-option-field="value"]').placeholder = option.label || 'Same as label';
        if (!option.valueTouched) {
          option.value = '';
        }
      } else {
        option.value = input.value;
        option.valueTouched = input.value.trim() !== '';
      }
      refresh({ keepOptions: true });
    });

    optionsList.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('[data-sif-option-field]')) {
        return;
      }
      event.preventDefault();
      const index = findOptionIndex(event.target.closest('.scf-option'));
      const option = addOption('');
      draft.options.splice(draft.options.length - 1, 1);
      draft.options.splice(index + 1, 0, option);
      refresh();
      focusOption(option.uid);
    });

    optionsList.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) {
        return;
      }
      const index = findOptionIndex(button.closest('.scf-option'));
      if (index < 0) {
        return;
      }
      if (button.hasAttribute('data-sif-option-remove')) {
        draft.options.splice(index, 1);
        refresh();
        const next = draft.options[Math.min(index, draft.options.length - 1)];
        if (next) {
          focusOption(next.uid);
        }
        return;
      }
      const offset = Number(button.dataset.sifOptionMove || 0);
      const target = index + offset;
      if (offset && target >= 0 && target < draft.options.length) {
        const [moved] = draft.options.splice(index, 1);
        draft.options.splice(target, 0, moved);
        refresh();
        const movedButton = optionsList.querySelector(`[data-uid="${moved.uid}"] [data-sif-option-move="${offset}"]`);
        if (movedButton && !movedButton.disabled) {
          movedButton.focus();
        }
      }
    });

    modal.querySelector('[data-sif-option-add]').addEventListener('click', () => {
      const option = addOption('');
      refresh();
      focusOption(option.uid);
    });

    function setBulkOpen(open) {
      bulk.hidden = !open;
      bulkToggles.forEach((button) => {
        if (button.hasAttribute('aria-expanded')) {
          button.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
      });
      if (open) {
        bulkInput.focus();
      }
    }

    bulkToggles.forEach((button) => {
      button.addEventListener('click', () => setBulkOpen(bulk.hidden));
    });

    modal.querySelector('[data-sif-bulk-apply]').addEventListener('click', () => {
      const existing = new Set(draft.options.map((option) => normalize(optionValue(option))));
      bulkInput.value.split(/\r?\n/).forEach((line) => {
        const text = line.trim();
        if (!text) {
          return;
        }
        let label = text;
        let value = '';
        const split = text.indexOf('=');
        if (split > 0) {
          value = text.slice(0, split).trim();
          label = text.slice(split + 1).trim() || value;
        }
        const key = normalize(value || label);
        if (!key || existing.has(key)) {
          return;
        }
        existing.add(key);
        addOption(label, value);
      });
      // Drop the placeholder row left behind when the list started empty.
      draft.options = draft.options.filter((option) => optionValue(option));
      bulkInput.value = '';
      setBulkOpen(false);
      refresh();
    });

    // ----- validation ----------------------------------------------------

    function validate() {
      const type = currentType();
      if (!OPTION_TYPES.has(type) || currentPresence() === 'hidden') {
        return '';
      }
      const values = draft.options.map(optionValue).filter(Boolean);
      if (!values.length) {
        return 'Add at least one choice so people have something to pick.';
      }
      const seen = new Set();
      const duplicate = values.find((value) => {
        const key = normalize(value);
        if (seen.has(key)) {
          return true;
        }
        seen.add(key);
        return false;
      });
      if (duplicate) {
        return `“${duplicate}” is used more than once. Each choice needs a different stored value.`;
      }
      return '';
    }

    function showError(message) {
      optionsError.textContent = message;
      optionsError.hidden = !message;
    }

    // ----- preview -------------------------------------------------------

    function renderPreview() {
      const type = currentType();
      const presence = currentPresence();
      const label = draft.label;
      preview.textContent = '';
      const labelNode = el('span', { className: 'form-label' }, [
        label,
        presence === 'required' ? el('span', { className: 'scf-required', 'aria-hidden': 'true', text: ' *' }) : null,
        presence === 'optional' ? el('span', { className: 'scf-optional', text: ' optional' }) : null,
      ]);
      const labels = draft.options.map((option) => option.label.trim() || optionValue(option)).filter(Boolean);
      if (type === 'checkbox') {
        preview.append(el('label', { className: 'scf-preview__checkbox' }, [
          el('input', { type: 'checkbox', tabindex: '-1' }),
          label,
        ]));
      } else if (type === 'select') {
        preview.append(labelNode, el('select', { className: 'form-input', tabindex: '-1' }, [
          el('option', { text: 'Select…' }),
          ...labels.map((text) => el('option', { text })),
        ]));
      } else if (type === 'multiselect') {
        preview.append(labelNode, el('div', { className: 'scf-preview__multi' }, labels.length
          ? labels.map((text) => el('label', { className: 'scf-preview__checkbox' }, [
            el('input', { type: 'checkbox', tabindex: '-1' }),
            text,
          ]))
          : [el('span', { className: 'text-muted', text: 'Choices appear here.' })]));
      } else {
        preview.append(labelNode, el('input', {
          className: 'form-input',
          type: type === 'date' ? 'date' : 'text',
          tabindex: '-1',
        }));
      }
      if (presence === 'hidden') {
        preview.append(el('p', { className: 'scf-preview__inactive', text: 'Hidden — not asked on this company\'s forms.' }));
      }

      previewRules.textContent = '';
      const rules = [];
      if (presence === 'required') {
        rules.push(['logic', 'Must be answered before the form can be submitted.']);
      } else if (presence === 'optional') {
        rules.push(['visibility', 'Shown on the form; people can leave it blank.']);
      } else {
        rules.push(['', 'Not shown. Existing answers on staff records are kept.']);
      }
      if (presence !== 'hidden') {
        const position = [...fields.values()]
          .filter((field) => field.key === draft.key || field.visible)
          .map((field) => ({ key: field.key, order: field.key === draft.key ? Number(orderInput.value) || 0 : field.sort_order }))
          .sort((a, b) => a.order - b.order)
          .findIndex((field) => field.key === draft.key) + 1;
        rules.push(['', `Appears ${ordinal(position)} on the form.`]);
      }
      if (OPTION_TYPES.has(type)) {
        rules.push(['m365', `${labels.length} choice${labels.length === 1 ? '' : 's'}${type === 'multiselect' ? ', pick any' : ', pick one'}.`]);
      }
      rules.forEach(([variant, text]) => {
        previewRules.append(el('li', {
          className: `scf-preview__rule${variant ? ` scf-preview__rule--${variant}` : ''}`,
          text,
        }));
      });
    }

    // ----- refresh -------------------------------------------------------

    function refresh(opts) {
      const settings = opts || {};
      const type = currentType();
      presenceInputs.forEach((input) => {
        input.closest('.scf-choice').classList.toggle('is-selected', input.checked);
      });
      typeInputs.forEach((input) => {
        input.closest('.scf-type-card').classList.toggle('is-selected', input.checked);
      });
      optionsSection.hidden = !OPTION_TYPES.has(type);
      if (OPTION_TYPES.has(type) && !settings.keepOptions) {
        renderOptions();
      }
      if (editor.dataset.submitted === 'true') {
        showError(validate());
      }
      renderPreview();
    }

    presenceInputs.concat(typeInputs).forEach((input) => {
      input.addEventListener('change', () => {
        if (OPTION_TYPES.has(currentType()) && !draft.options.length) {
          addOption('');
        }
        refresh();
      });
    });
    orderInput.addEventListener('input', () => refresh({ keepOptions: true }));

    // ----- modal ---------------------------------------------------------

    function loadField(field) {
      const isCore = coreKeys.has(field.key);
      const typeLocked = typeLockedKeys.has(field.key);
      draft = { key: field.key, label: field.label, options: [] };
      field.options.forEach((option) => addOption(option.label, option.value));
      title.textContent = field.label;
      subtitle.textContent = `Key: ${field.key}`;
      const presence = presenceOf(field);
      presenceInputs.forEach((input) => {
        input.checked = input.value === presence;
        input.disabled = isCore && input.value !== 'required';
        if (isCore) {
          input.checked = input.value === 'required';
        }
      });
      coreNote.hidden = !isCore;
      typeInputs.forEach((input) => {
        input.checked = input.value === field.type;
        input.disabled = typeLocked && input.value !== field.type;
      });
      typeNote.hidden = !typeLocked;
      orderInput.value = String(field.sort_order);
      editor.dataset.submitted = 'false';
      showError('');
      setBulkOpen(false);
      submitButton.disabled = false;
      submitButton.textContent = 'Save field';
      refresh();
    }

    function openModal(trigger) {
      lastTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      const body = modal.querySelector('.modal__body');
      if (body) {
        body.scrollTop = 0;
      }
      snapshot = serializeDraft();
      window.requestAnimationFrame(() => {
        const first = presenceInputs.find((input) => input.checked && !input.disabled)
          || typeInputs.find((input) => input.checked);
        if (first) {
          first.focus();
        }
      });
    }

    function closeModal(force) {
      if (!force && serializeDraft() !== snapshot && !window.confirm('Discard your unsaved changes to this field?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal(false);
      });
    });

    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled])',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    document.querySelectorAll('[data-sif-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const field = fields.get(button.dataset.sifEdit);
        if (field) {
          loadField(field);
          openModal(button);
        }
      });
    });

    // ----- save ----------------------------------------------------------

    function writeOutputs() {
      outputs.textContent = '';
      const add = (name, value) => {
        outputs.append(el('input', { type: 'hidden', name, value: String(value) }));
      };
      fields.forEach((field) => {
        add(`field_${field.key}_type`, field.type);
        if (field.visible) {
          add(`field_${field.key}_visible`, '1');
        }
        if (field.visible && field.required) {
          add(`field_${field.key}_required`, '1');
        }
        add(`field_${field.key}_sort_order`, field.sort_order);
        if (OPTION_TYPES.has(field.type)) {
          add(`field_${field.key}_options_json`, JSON.stringify(field.options));
        }
      });
    }

    editor.addEventListener('submit', (event) => {
      event.preventDefault();
      editor.dataset.submitted = 'true';
      const message = validate();
      showError(message);
      if (message) {
        const firstOption = optionsList.querySelector('[data-sif-option-field="label"]');
        (firstOption || modal.querySelector('[data-sif-option-add]')).focus();
        return;
      }
      const field = fields.get(draft.key);
      const presence = currentPresence();
      field.type = currentType();
      field.visible = presence !== 'hidden';
      field.required = presence === 'required';
      field.sort_order = Number.parseInt(orderInput.value, 10) || 0;
      if (OPTION_TYPES.has(field.type)) {
        const seen = new Set();
        field.options = draft.options
          .map((option) => ({ value: optionValue(option), label: option.label.trim() || optionValue(option) }))
          .filter((option) => option.value && !seen.has(option.value) && seen.add(option.value));
      }
      writeOutputs();
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
      if (status) {
        status.textContent = `Saving ${field.label}…`;
      }
      pageForm.submit();
    });

    // ----- list search ---------------------------------------------------

    const search = pageForm.querySelector('[data-sif-search]');
    if (search) {
      const items = Array.from(pageForm.querySelectorAll('[data-sif-item]'));
      const noResults = pageForm.querySelector('[data-sif-no-results]');
      search.addEventListener('input', () => {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        items.forEach((item) => {
          const haystack = normalize(item.dataset.sifSearchText);
          const match = terms.every((term) => haystack.includes(term));
          item.hidden = !match;
          if (match) {
            visible += 1;
          }
        });
        if (noResults) {
          noResults.hidden = visible > 0;
        }
      });
    }
  });
})();
