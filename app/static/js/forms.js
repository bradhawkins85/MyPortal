(function () {
  'use strict';

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function collectValues(form) {
    const values = {};
    form.querySelectorAll('[data-cf-pub-field]').forEach((wrap) => {
      const fid = wrap.getAttribute('data-field-id');
      const type = wrap.getAttribute('data-field-type');
      if (type === 'file') return;
      if (type === 'radio') {
        const checked = wrap.querySelector('input[type="radio"]:checked');
        values[fid] = checked ? checked.value : '';
      } else if (type === 'multiselect') {
        const select = wrap.querySelector('select[data-cf-value]');
        values[fid] = select ? Array.from(select.selectedOptions).map((o) => o.value) : [];
      } else if (type === 'checkbox') {
        const box = wrap.querySelector('input[type="checkbox"]');
        values[fid] = !!(box && box.checked);
      } else {
        const input = wrap.querySelector('[data-cf-value]');
        values[fid] = input ? input.value : '';
      }
    });
    return values;
  }

  function showStatus(status, ok, message) {
    if (!status) return;
    status.hidden = false;
    status.className = 'cf-submit-status ' + (ok ? 'cf-submit-status--ok' : 'cf-submit-status--err');
    status.textContent = message;
  }

  function bind() {
    const form = document.querySelector('[data-cf-submit]');
    if (!form) return;
    const slug = form.getAttribute('data-slug');
    const status = form.querySelector('[data-cf-status]');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const values = collectValues(form);
      const formData = new FormData();
      formData.append('form_data', JSON.stringify(values));
      form.querySelectorAll('[data-cf-file]').forEach((fileInput) => {
        const file = fileInput.files && fileInput.files[0];
        if (file) formData.append(fileInput.name, file);
      });

      const headers = { Accept: 'application/json' };
      const csrf = getCsrfToken();
      if (csrf) headers['X-CSRF-Token'] = csrf;

      try {
        const response = await fetch(`/forms/${slug}/submit`, {
          method: 'POST',
          credentials: 'same-origin',
          headers,
          body: formData,
        });
        if (response.ok) {
          showStatus(status, true, 'Thank you — your request has been submitted and a ticket has been created.');
          form.reset();
        } else {
          let message = `Submission failed (${response.status}).`;
          try {
            const data = await response.json();
            if (data && data.detail) {
              message = Array.isArray(data.detail) ? data.detail.map((e) => e.msg || e).join(', ') : data.detail;
            }
          } catch (error) {
            /* keep default */
          }
          showStatus(status, false, message);
        }
      } catch (error) {
        showStatus(status, false, 'Network error. Please try again.');
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();