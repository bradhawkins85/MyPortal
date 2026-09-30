(function () {
  'use strict';

  // Keys the structured fields own. Anything else in the payload is kept.
  const FIELDS = ['subject', 'description', 'priority', 'status', 'category'];
  const PRIORITIES = new Set(['urgent', 'high', 'normal', 'low']);

  function parsePayload(text) {
    const raw = String(text || '').trim();
    if (!raw) {
      return {};
    }
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch (error) {
      return null;
    }
  }

  function isShown(node) {
    return node.offsetParent !== null;
  }

  function enhance(container) {
    const textarea = container.querySelector('textarea[name="jsonPayload"]');
    const fieldsWrap = container.querySelector('[data-stp-fields]');
    const advanced = container.querySelector('[data-stp-advanced]');
    const inputs = {};
    container.querySelectorAll('[data-stp-field]').forEach((input) => {
      inputs[input.dataset.stpField] = input;
    });
    const errors = {};
    container.querySelectorAll('[data-stp-error]').forEach((node) => {
      errors[node.dataset.stpError] = node;
    });
    if (!textarea || !fieldsWrap) {
      return;
    }

    fieldsWrap.hidden = false;
    if (advanced) {
      advanced.open = false;
    }

    function setError(name, message) {
      const node = errors[name];
      if (node) {
        node.textContent = message || '';
        node.hidden = !message;
      }
      if (name === 'subject' && inputs.subject) {
        inputs.subject.classList.toggle('is-invalid', Boolean(message));
      }
    }

    function readFromJson() {
      const payload = parsePayload(textarea.value);
      if (payload === null) {
        setError('json', "The JSON can't be read, so the fields above weren't updated. Fix it, or clear it to start again.");
        if (advanced) {
          advanced.open = true;
        }
        return;
      }
      setError('json', '');
      FIELDS.forEach((key) => {
        const input = inputs[key];
        if (!input) {
          return;
        }
        const value = payload[key] == null ? '' : String(payload[key]);
        if (key === 'priority') {
          const priority = value.toLowerCase();
          if (priority && !PRIORITIES.has(priority)) {
            input.append(new Option(value, value));
          }
          input.value = value || 'normal';
        } else {
          input.value = value;
        }
      });
    }

    function writeToJson() {
      const payload = parsePayload(textarea.value) || {};
      FIELDS.forEach((key) => {
        const input = inputs[key];
        if (!input) {
          return;
        }
        const value = key === 'description' ? input.value : input.value.trim();
        if (value.trim()) {
          payload[key] = value;
        } else {
          delete payload[key];
        }
      });
      textarea.value = Object.keys(payload).length ? JSON.stringify(payload, null, 2) : '';
      setError('json', '');
      if (inputs.subject && inputs.subject.value.trim()) {
        setError('subject', '');
      }
    }

    Object.values(inputs).forEach((input) => {
      input.addEventListener('input', writeToJson);
      input.addEventListener('change', writeToJson);
    });
    // Programmatic loads (automation.js) dispatch "input" after setting the value.
    textarea.addEventListener('input', readFromJson);
    readFromJson();

    const form = container.closest('form');
    if (form) {
      // Capture on document so this runs before the form's own submit handlers.
      document.addEventListener('submit', (event) => {
        if (event.target !== form || !isShown(container)) {
          return;
        }
        if (parsePayload(textarea.value) === null) {
          return; // Let the existing JSON validation report it.
        }
        if (inputs.subject && !inputs.subject.value.trim()) {
          event.preventDefault();
          event.stopImmediatePropagation();
          setError('subject', 'Enter a subject for the ticket.');
          inputs.subject.focus();
        }
      }, true);
    }
  }

  function init() {
    document.querySelectorAll('[data-ticket-payload]').forEach(enhance);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
