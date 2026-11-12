// Ordered action lists (rmm/_action_steps.html) on the check and event log rule
// forms: add actions from their templates, remove and reorder them, show each
// scheduled script's own timing and say when a newly chosen script's values can
// be filled in. The list's order is kept in a hidden action_order field so the
// save bar sees added, removed and moved actions; Discard puts the saved list back.
(function () {
  'use strict';

  function randomKey(taken) {
    let key = '';
    do {
      const bytes = new Uint8Array(4);
      if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
      } else {
        bytes.forEach((_value, index) => { bytes[index] = Math.floor(Math.random() * 256); });
      }
      key = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    } while (taken.has(key));
    return key;
  }

  function setup(editor) {
    const list = editor.querySelector('[data-rmm-steps]');
    const empty = editor.querySelector('[data-rmm-steps-empty]');
    const status = editor.querySelector('[data-rmm-steps-status]');
    const order = editor.querySelector('[data-rmm-steps-order]');
    const add = editor.querySelector('[data-rmm-step-add]');
    const toggle = editor.querySelector('[data-rmm-step-add-toggle]');
    const picker = editor.querySelector('[data-rmm-step-picker]');
    const max = Number(editor.getAttribute('data-rmm-steps-max') || 10);
    const single = (editor.getAttribute('data-rmm-steps-single') || '').split(',').filter(Boolean);
    const form = editor.closest('form');
    if (!list || !add || !toggle || !picker) return;

    const cards = () => Array.from(list.children).filter((item) => item.hasAttribute('data-rmm-step'));
    const label = (card) => {
      const title = card.querySelector('.rmm-step__title');
      const hidden = title && title.querySelector('[data-rmm-step-position]');
      return title ? title.textContent.replace(hidden ? hidden.textContent : '', '').trim() : 'Action';
    };
    const saved = cards();
    add.hidden = false;

    function announce(text) {
      if (!status) return;
      status.textContent = '';
      window.setTimeout(() => { status.textContent = text; }, 50);
    }

    function enhance(card) {
      const tools = card.querySelector('[data-rmm-step-tools]');
      if (tools) tools.hidden = false;
      updateSchedule(card);
      const select = card.querySelector('[data-rmm-step-script]');
      if (select) updateScriptValues(select);
    }

    function updateSchedule(card) {
      const mode = card.querySelector('[data-rmm-step-schedule-mode]');
      if (!mode) return;
      card.querySelectorAll('[data-rmm-step-schedule-show]').forEach((field) => {
        field.hidden = field.getAttribute('data-rmm-step-schedule-show') !== mode.value;
      });
    }

    // A script chosen since the page loaded has its values filled in after saving.
    function updateScriptValues(select) {
      const card = select.closest('[data-rmm-step]');
      const values = card && card.querySelector('[data-rmm-step-values]');
      const pending = card && card.querySelector('[data-rmm-step-pending]');
      const savedScript = select.getAttribute('data-rmm-saved-script') || '';
      const same = select.value !== '' && select.value === savedScript && values && values.children.length > 0;
      if (values) values.hidden = !same;
      if (pending) {
        const option = select.selectedOptions[0];
        const name = option && option.value ? option.textContent.replace(/\s*\(.*\)\s*$/, '').trim() : '';
        pending.hidden = same;
        pending.textContent = name
          ? 'Once you save, ' + name + "'s parameters appear here to fill in."
          : 'Choose a script. Once you save, its parameters appear here to fill in.';
      }
    }

    function refresh() {
      const items = cards();
      items.forEach((card, index) => {
        const number = card.querySelector('[data-rmm-step-number]');
        if (number) number.textContent = String(index + 1);
        const position = card.querySelector('[data-rmm-step-position]');
        if (position) position.textContent = 'Action ' + (index + 1) + ' of ' + items.length + ': ';
        const up = card.querySelector('[data-rmm-step-move="up"]');
        const down = card.querySelector('[data-rmm-step-move="down"]');
        if (up) up.disabled = index === 0;
        if (down) down.disabled = index === items.length - 1;
      });
      if (empty) empty.hidden = items.length > 0;
      const types = items.map((card) => card.getAttribute('data-rmm-step-type'));
      picker.querySelectorAll('[data-rmm-step-add-type]').forEach((button) => {
        const kind = button.getAttribute('data-rmm-step-add-type');
        button.disabled = items.length >= max || (single.indexOf(kind) !== -1 && types.indexOf(kind) !== -1);
      });
      toggle.disabled = items.length >= max;
      toggle.title = items.length >= max ? 'A list can have up to ' + max + ' actions.' : '';
      const keys = items.map((card) => card.getAttribute('data-rmm-step-key')).join(',');
      if (order && order.value !== keys) {
        order.value = keys;
        order.dispatchEvent(new Event('change', { bubbles: true }));
      }
      editor.dispatchEvent(new CustomEvent('rmm:steps-change', { bubbles: true, detail: { types: types } }));
    }

    function closePicker(focusToggle) {
      picker.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      if (focusToggle) toggle.focus();
    }

    function addStep(kind) {
      const template = editor.querySelector('template[data-rmm-step-template="' + kind + '"]');
      if (!template) return;
      const taken = new Set(cards().map((card) => card.getAttribute('data-rmm-step-key')));
      const key = randomKey(taken);
      const holder = document.createElement('template');
      holder.innerHTML = template.innerHTML.split('__KEY__').join(key);
      const card = holder.content.firstElementChild;
      list.appendChild(card);
      enhance(card);
      closePicker(false);
      refresh();
      const first = card.querySelector('select:not([disabled]), input:not([type="hidden"]):not([disabled])');
      (first || card.querySelector('.rmm-step__title')).focus();
      announce('Added ' + label(card) + ' as action ' + cards().length + '.');
    }

    function move(card, direction) {
      const items = cards();
      const index = items.indexOf(card);
      const target = direction === 'up' ? items[index - 1] : items[index + 1];
      if (!target) return;
      if (direction === 'up') list.insertBefore(card, target);
      else list.insertBefore(target, card);
      refresh();
      const button = card.querySelector('[data-rmm-step-move="' + direction + '"]');
      const other = card.querySelector('[data-rmm-step-move="' + (direction === 'up' ? 'down' : 'up') + '"]');
      (button && !button.disabled ? button : other).focus();
      announce(label(card) + ' moved to action ' + (cards().indexOf(card) + 1) + ' of ' + cards().length + '.');
    }

    function remove(card) {
      const items = cards();
      const index = items.indexOf(card);
      const name = label(card);
      card.remove();
      refresh();
      const rest = cards();
      const next = rest[Math.min(index, rest.length - 1)];
      const focus = next ? next.querySelector('[data-rmm-step-remove]') : toggle;
      if (focus) focus.focus();
      announce('Removed ' + name + '.');
    }

    toggle.addEventListener('click', () => {
      const opening = picker.hidden;
      picker.hidden = !opening;
      toggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
      if (opening) {
        const first = picker.querySelector('[data-rmm-step-add-type]:not([disabled])');
        if (first) first.focus();
      }
    });
    picker.addEventListener('click', (event) => {
      const option = event.target.closest('[data-rmm-step-add-type]');
      if (option && !option.disabled) addStep(option.getAttribute('data-rmm-step-add-type'));
    });
    picker.addEventListener('keydown', (event) => {
      const options = Array.from(picker.querySelectorAll('[data-rmm-step-add-type]:not([disabled])'));
      const index = options.indexOf(document.activeElement);
      if (event.key === 'Escape') {
        event.preventDefault();
        closePicker(true);
      } else if ((event.key === 'ArrowDown' || event.key === 'ArrowRight') && options.length) {
        event.preventDefault();
        options[(index + 1) % options.length].focus();
      } else if ((event.key === 'ArrowUp' || event.key === 'ArrowLeft') && options.length) {
        event.preventDefault();
        options[(index - 1 + options.length) % options.length].focus();
      }
    });
    document.addEventListener('click', (event) => {
      if (!picker.hidden && !add.contains(event.target)) closePicker(false);
    });

    list.addEventListener('click', (event) => {
      const card = event.target.closest('[data-rmm-step]');
      if (!card) return;
      const moveButton = event.target.closest('[data-rmm-step-move]');
      if (moveButton) move(card, moveButton.getAttribute('data-rmm-step-move'));
      else if (event.target.closest('[data-rmm-step-remove]')) remove(card);
    });
    list.addEventListener('change', (event) => {
      const card = event.target.closest('[data-rmm-step]');
      if (!card) return;
      if (event.target.matches('[data-rmm-step-schedule-mode]')) updateSchedule(card);
      if (event.target.matches('[data-rmm-step-script]')) updateScriptValues(event.target);
    });

    // Discard restores the saved list, whose fields the save bar has already reset.
    if (form) {
      form.addEventListener('ae:discarded', () => {
        cards().forEach((card) => card.remove());
        saved.forEach((card) => {
          list.appendChild(card);
          updateSchedule(card);
          const select = card.querySelector('[data-rmm-step-script]');
          if (select) updateScriptValues(select);
        });
        refresh();
      });
    }

    cards().forEach(enhance);
    refresh();
  }

  document.querySelectorAll('[data-rmm-steps-editor]').forEach(setup);
})();
