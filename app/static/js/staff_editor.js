(function () {
  'use strict';

  // Tabs, live preview, inline validation and unsaved-change protection for the
  // Add / Edit staff member editors. staff.js opens the modals and fills in the
  // values; it announces that with a 'modal:opened' event and asks permission to
  // close with a cancelable 'modal:beforeclose' event.

  const DIRTY_CONFIRM = 'Discard your unsaved changes to this staff member?';
  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  // Guards against a double-click on Next landing on Send request.
  const SUBMIT_ARM_DELAY_MS = 1000;

  function parseJson(id, fallback) {
    const element = document.getElementById(id);
    if (!element) {
      return fallback;
    }
    try {
      return JSON.parse(element.textContent || '');
    } catch (error) {
      return fallback;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function formatDate(value, withTime) {
    if (!value) {
      return '';
    }
    const parsed = new Date(withTime ? value : `${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    const options = { day: 'numeric', month: 'short', year: 'numeric' };
    if (withTime) {
      options.hour = 'numeric';
      options.minute = '2-digit';
    }
    return parsed.toLocaleString(undefined, options);
  }

  function daysUntil(value) {
    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((parsed - today) / 86400000);
  }

  function relativeDays(days) {
    if (days === null) {
      return '';
    }
    if (days === 0) {
      return 'today';
    }
    if (days === 1) {
      return 'tomorrow';
    }
    if (days === -1) {
      return 'yesterday';
    }
    return days > 0 ? `in ${days} days` : `${Math.abs(days)} days ago`;
  }

  function labelFor(control) {
    const holder = control.closest('[data-custom-field-wrapper], [data-stf-field]');
    const groupLabel = holder ? holder.querySelector('.form-label, legend') : null;
    // Multi-select options are labelled by their option text; name the field instead.
    let label = control.closest('.stf-chip-options') ? groupLabel : null;
    if (!label && control.id) {
      label = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
    }
    label = label || groupLabel;
    if (!label) {
      return control.name || control.id || 'This field';
    }
    const clone = label.cloneNode(true);
    clone.querySelectorAll('.stf-optional, .stf-switch__track').forEach((node) => node.remove());
    return clone.textContent.replace(/\s+/g, ' ').trim();
  }

  function initEditor(form) {
    const modal = form.closest('.modal');
    if (!modal) {
      return;
    }
    const mode = form.dataset.stfEditor;
    const flags = parseJson('staff-flags', {});
    const tabs = Array.from(form.querySelectorAll('[data-stf-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-stf-panel]'));
    const formError = form.querySelector('[data-stf-form-error], #edit-form-error');
    const preview = {
      initials: form.querySelector('[data-stf-preview-initials]'),
      name: form.querySelector('[data-stf-preview-name]'),
      role: form.querySelector('[data-stf-preview-role]'),
      chips: form.querySelector('[data-stf-preview-chips]'),
      facts: form.querySelector('[data-stf-preview-facts]'),
      rules: form.querySelector('[data-stf-preview-rules]'),
    };
    let snapshot = new Map();

    form.noValidate = true;

    // ----- values ----------------------------------------------------------

    function control(key) {
      return form.querySelector(`[data-stf-bind="${key}"]`) || form.querySelector(`[name="${key}"]:not([type="hidden"])`);
    }

    function value(key) {
      const input = control(key);
      if (!input) {
        return '';
      }
      if (input.type === 'checkbox') {
        return input.checked;
      }
      if (input.tagName === 'SELECT' && input.value && input.selectedOptions.length) {
        return input.selectedOptions[0].textContent.trim();
      }
      return String(input.value || '').trim();
    }

    // Inputs that describe the staff member (not action notes, hidden fields or buttons).
    function trackedControls() {
      return Array.from(form.querySelectorAll('input, select, textarea')).filter((input) => (
        input.type !== 'hidden'
        && !input.closest('[data-stf-panel="actions"]')
      ));
    }

    function controlKey(input, index) {
      if (input.id) {
        return input.id;
      }
      return `${input.name || 'field'}:${input.value}:${index}`;
    }

    function controlValue(input) {
      if (input.type === 'checkbox' || input.type === 'radio') {
        return input.checked ? '1' : '';
      }
      return String(input.value || '');
    }

    function readState() {
      const state = new Map();
      trackedControls().forEach((input, index) => {
        state.set(controlKey(input, index), { input, value: controlValue(input) });
      });
      return state;
    }

    function changedControls() {
      const changed = [];
      readState().forEach((entry, key) => {
        const before = snapshot.get(key);
        if (!before || before.value !== entry.value) {
          changed.push(entry.input);
        }
      });
      return changed;
    }

    function isDirty() {
      return changedControls().length > 0;
    }

    // ----- tabs ------------------------------------------------------------

    function visibleTabs() {
      return tabs.filter((tab) => !tab.hidden);
    }

    function showTab(name, focusTab) {
      tabs.forEach((tab) => {
        const active = tab.dataset.stfTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focusTab) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.stfPanel !== name;
      });
      syncStepButtons(name);
    }

    // ----- two-step add flow -----------------------------------------------
    // When the Additional details tab exists, the Person tab shows Next; Send request
    // appears on the last tab and only becomes clickable a moment after it is shown.

    const nextButton = form.querySelector('[data-stf-next]');
    const submitButton = form.querySelector('button[type="submit"]');
    let armTimer = null;

    function isLastStep(name) {
      const visible = visibleTabs();
      return !nextButton || !visible.length || visible[visible.length - 1].dataset.stfTab === name;
    }

    function syncStepButtons(name) {
      if (!nextButton || !submitButton) {
        return;
      }
      window.clearTimeout(armTimer);
      const last = isLastStep(name);
      nextButton.hidden = last;
      submitButton.hidden = !last;
      submitButton.disabled = true;
      if (last) {
        armTimer = window.setTimeout(() => {
          submitButton.disabled = false;
        }, SUBMIT_ARM_DELAY_MS);
      }
    }

    function activeTabName() {
      const active = tabs.find((tab) => tab.classList.contains('is-active'));
      return active ? active.dataset.stfTab : '';
    }

    function goNext() {
      if (!validate()) {
        return;
      }
      const visible = visibleTabs();
      const index = visible.findIndex((tab) => tab.dataset.stfTab === activeTabName());
      const next = visible[index + 1];
      if (next) {
        showTab(next.dataset.stfTab, true);
      }
    }

    if (nextButton) {
      nextButton.addEventListener('click', goNext);
      // Enter in a field before the last step moves on instead of submitting.
      form.addEventListener('keydown', (event) => {
        if (
          event.key === 'Enter'
          && !isLastStep(activeTabName())
          && event.target.matches('input:not([type="checkbox"]):not([type="radio"]), select')
        ) {
          event.preventDefault();
          goNext();
        }
      });
    }

    function panelOf(input) {
      const panel = input.closest('[data-stf-panel]');
      return panel ? panel.dataset.stfPanel : null;
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => showTab(tab.dataset.stfTab));
      tab.addEventListener('keydown', (event) => {
        const visible = visibleTabs();
        const index = visible.indexOf(tab);
        let next = null;
        if (event.key === 'ArrowRight') {
          next = visible[(index + 1) % visible.length];
        } else if (event.key === 'ArrowLeft') {
          next = visible[(index - 1 + visible.length) % visible.length];
        } else if (event.key === 'Home') {
          next = visible[0];
        } else if (event.key === 'End') {
          next = visible[visible.length - 1];
        }
        if (next) {
          event.preventDefault();
          showTab(next.dataset.stfTab, true);
        }
      });
    });

    function updateTabIndicators() {
      const marked = new Set();
      if (mode === 'edit') {
        changedControls().forEach((input) => marked.add(panelOf(input)));
      } else {
        trackedControls().forEach((input) => {
          if (!input.disabled && controlValue(input) && !(input.type === 'checkbox' && input.defaultChecked)) {
            marked.add(panelOf(input));
          }
        });
      }
      tabs.forEach((tab) => {
        tab.classList.toggle('is-configured', marked.has(tab.dataset.stfTab));
      });

      const detailsCount = form.querySelector('[data-stf-tab="details"] [data-stf-tab-count]');
      if (detailsCount) {
        const answered = new Set();
        form.querySelectorAll('[data-custom-field-wrapper]').forEach((wrapper) => {
          const inputs = Array.from(wrapper.querySelectorAll('input, select, textarea'));
          if (!wrapper.hidden && inputs.some((input) => controlValue(input))) {
            answered.add(wrapper.dataset.customFieldName);
          }
        });
        detailsCount.textContent = answered.size ? String(answered.size) : '';
      }
    }

    // ----- actions tab (edit) ----------------------------------------------

    function syncActions() {
      const actionsPanel = form.querySelector('[data-stf-panel="actions"]');
      if (!actionsPanel) {
        return;
      }
      let total = 0;
      actionsPanel.querySelectorAll('[data-stf-action-group]').forEach((group) => {
        const available = Array.from(group.querySelectorAll('[data-edit-action]')).filter((button) => !button.hidden);
        group.hidden = available.length === 0;
        total += available.length;
      });
      const forceComplete = actionsPanel.querySelector('[data-edit-action="workflow-force-complete"]');
      const stepField = actionsPanel.querySelector('[data-stf-step-field]');
      if (stepField) {
        stepField.hidden = !forceComplete || forceComplete.hidden;
      }
      const empty = actionsPanel.querySelector('[data-stf-actions-empty]');
      if (empty) {
        empty.hidden = total > 0;
      }
      const note = actionsPanel.querySelector('#edit-action-note');
      if (note) {
        note.closest('.form-field').hidden = total === 0;
      }
      const count = form.querySelector('[data-stf-tab="actions"] [data-stf-tab-count]');
      if (count) {
        count.textContent = total ? String(total) : '';
      }
    }

    // ----- lifecycle stages (edit) -----------------------------------------

    function syncStages() {
      const track = form.querySelector('[data-stf-stages]');
      if (!track) {
        return;
      }
      const current = value('account_action');
      const stages = Array.from(track.querySelectorAll('[data-stf-stage]'));
      const currentIndex = stages.findIndex((stage) => stage.dataset.stfStage === current);
      stages.forEach((stage, index) => {
        stage.classList.toggle('is-current', index === currentIndex);
        stage.classList.toggle('is-done', currentIndex >= 0 && index < currentIndex);
      });
    }

    // ----- preview ---------------------------------------------------------

    function addChip(text, tone) {
      const chip = el('li', `stf-chip${tone ? ` stf-chip--${tone}` : ''}`, text);
      preview.chips.appendChild(chip);
    }

    function addFact(term, detail) {
      if (!detail) {
        return;
      }
      const row = el('div', 'stf-preview__fact');
      row.appendChild(el('dt', null, term));
      row.appendChild(el('dd', null, detail));
      preview.facts.appendChild(row);
    }

    function addRule(text, tone) {
      preview.rules.appendChild(el('li', `stf-preview__rule${tone ? ` stf-preview__rule--${tone}` : ''}`, text));
    }

    function missingRequired() {
      return Array.from(form.querySelectorAll('[required]')).filter((input) => {
        // Inactive tabs are hidden too, so only skip fields hidden by a "show when" condition.
        if (input.disabled || input.closest('[data-custom-field-wrapper][hidden]')) {
          return false;
        }
        if (input.type === 'checkbox') {
          return !input.checked;
        }
        return !String(input.value || '').trim();
      });
    }

    function updatePreview() {
      if (!preview.name) {
        return;
      }
      const first = value('first_name');
      const last = value('last_name');
      const fullName = `${first} ${last}`.trim();
      const initials = `${first.charAt(0)}${last.charAt(0)}`.toUpperCase();
      preview.initials.textContent = initials || '?';
      preview.name.textContent = fullName || (mode === 'add' ? 'New staff member' : 'Unnamed staff member');
      preview.name.classList.toggle('is-placeholder', !fullName);
      const role = [value('job_title'), value('department')].filter(Boolean).join(' · ');
      preview.role.textContent = role || (mode === 'add' ? 'Role not set yet' : 'No job title or department');
      preview.role.classList.toggle('is-placeholder', !role);

      preview.chips.replaceChildren();
      preview.facts.replaceChildren();
      preview.rules.replaceChildren();

      const startDate = value('date_onboarded');
      addFact('Email', value('email'));
      addFact('Mobile', value('mobile_phone'));
      addFact('Company', value('org_company'));
      addFact('Manager', value('manager_name'));
      addFact('Location', [value('city'), value('state'), value('country')].filter(Boolean).join(', '));

      if (mode === 'add') {
        addChip('Awaiting approval', 'warning');
        if (startDate) {
          const when = relativeDays(daysUntil(startDate));
          addFact('Starts', `${formatDate(startDate)}${when ? ` (${when})` : ''}`);
        }
        const missing = missingRequired();
        if (missing.length) {
          addRule(`Still needed: ${missing.map(labelFor).join(', ')}.`, 'warning');
        } else {
          addRule('Ready to send.', 'success');
        }
        const customWrappers = Array.from(form.querySelectorAll('[data-custom-field-wrapper]')).filter((wrapper) => !wrapper.hidden);
        if (customWrappers.length) {
          const answered = customWrappers.filter((wrapper) => (
            Array.from(wrapper.querySelectorAll('input, select, textarea')).some((input) => controlValue(input))
          )).length;
          addRule(`${answered} of ${customWrappers.length} additional details answered.`);
        }
        addRule('Your approvers are notified when you send this request. Accounts are set up after it is approved.', 'info');
      } else {
        const enabledInput = control('enabled');
        if (enabledInput) {
          addChip(enabledInput.checked ? 'Enabled' : 'Disabled', enabledInput.checked ? 'success' : 'muted');
        }
        const stage = value('account_action');
        if (stage) {
          const tone = /offboard/i.test(stage) ? 'danger' : (stage === 'Onboarded' ? 'info' : 'warning');
          addChip(stage, tone);
        }
        addFact('Started', formatDate(startDate));
        const offboardAt = value('date_offboarded');
        addFact('Offboard at', formatDate(offboardAt, true));
        addFact('M365 sign-in', value('m365_last_sign_in'));

        if (flags.canEditStaff) {
          const changed = changedControls();
          if (changed.length) {
            const names = Array.from(new Set(changed.map(labelFor)));
            const listed = names.length > 3 ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} more` : names.join(', ');
            addRule(`Unsaved changes: ${listed}.`, 'warning');
          } else {
            addRule('No unsaved changes.');
          }
          if (!flags.isSuperAdmin) {
            addRule('Saving creates a helpdesk ticket for a technician to apply your changes.', 'info');
          }
        } else {
          addRule('You can view this person but not change their details.', 'info');
        }
        if (offboardAt) {
          addRule(`Access is scheduled to be removed ${formatDate(offboardAt, true)}.`, 'danger');
        }
      }
    }

    // ----- validation ------------------------------------------------------

    function clearErrors() {
      form.querySelectorAll('.stf-error[data-stf-error-for]').forEach((node) => {
        node.textContent = '';
        node.hidden = true;
      });
      form.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute('aria-invalid'));
      tabs.forEach((tab) => tab.classList.remove('has-error'));
      if (formError && mode === 'add') {
        formError.textContent = '';
        formError.hidden = true;
      }
    }

    function showError(input, message) {
      input.setAttribute('aria-invalid', 'true');
      const key = input.name || input.dataset.stfBind;
      const slot = key ? form.querySelector(`[data-stf-error-for="${key}"]`) : null;
      if (slot) {
        slot.textContent = message;
        slot.hidden = false;
      }
      const panel = panelOf(input);
      const tab = tabs.find((item) => item.dataset.stfTab === panel);
      if (tab) {
        tab.classList.add('has-error');
      }
    }

    let showingErrors = false;

    function validate({ reveal = true } = {}) {
      clearErrors();
      const invalid = [];
      missingRequired().forEach((input) => {
        showError(input, `${labelFor(input)} is required.`);
        invalid.push(input);
      });
      form.querySelectorAll('input[type="email"]').forEach((input) => {
        const text = String(input.value || '').trim();
        if (text && !input.disabled && !input.readOnly && !EMAIL_PATTERN.test(text)) {
          showError(input, 'Enter a valid email address, like name@example.com.');
          invalid.push(input);
        }
      });
      showingErrors = invalid.length > 0;
      if (!invalid.length) {
        return true;
      }
      if (formError && mode === 'add') {
        formError.textContent = invalid.length === 1 ? 'Check the highlighted field.' : `Check the ${invalid.length} highlighted fields.`;
        formError.hidden = false;
      }
      if (!reveal) {
        return false;
      }
      const first = invalid[0];
      const panel = panelOf(first);
      if (panel) {
        showTab(panel);
      }
      first.focus();
      return false;
    }

    // Runs before staff.js's own submit handler (capture phase) so invalid edits never reach the server.
    form.addEventListener('submit', (event) => {
      if (submitButton && submitButton.disabled) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (!validate()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (mode === 'add') {
        snapshot = readState();
        if (submitButton) {
          submitButton.classList.add('button--processing');
          submitButton.disabled = true;
        }
      }
    }, true);

    // ----- lifecycle -------------------------------------------------------

    function refresh() {
      // Once errors are showing, clear each one as soon as the field is fixed.
      if (showingErrors) {
        validate({ reveal: false });
      }
      updateTabIndicators();
      syncStages();
      updatePreview();
    }

    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);

    modal.addEventListener('modal:opened', () => {
      if (submitButton) {
        submitButton.classList.remove('button--processing');
        submitButton.disabled = false;
      }
      clearErrors();
      showingErrors = false;
      syncActions();
      showTab(visibleTabs()[0] ? visibleTabs()[0].dataset.stfTab : '');
      snapshot = readState();
      refresh();
      window.requestAnimationFrame(() => {
        const activePanel = panels.find((panel) => !panel.hidden);
        const target = activePanel
          ? activePanel.querySelector('input:not([type="hidden"]):not([disabled]):not([readonly]), select:not([disabled]), textarea:not([disabled])')
          : null;
        if (target) {
          target.focus();
        }
      });
    });

    modal.addEventListener('modal:beforeclose', (event) => {
      if (flags.canEditStaff && isDirty() && !window.confirm(DIRTY_CONFIRM)) {
        event.preventDefault();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form[data-stf-editor]').forEach(initEditor);
  });
})();
