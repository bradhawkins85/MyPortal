(function () {
  'use strict';
  function collectValues(form) {
    const values = {};
    form.querySelectorAll('[data-cf-pub-field]').forEach((wrap) => {
      const id = wrap.dataset.fieldId;
      const type = wrap.dataset.fieldType;
      if (type === 'file' || type === 'audio') return;
      if (type === 'matrix') {
        values[id] = {};
        wrap.querySelectorAll('[data-cf-matrix-row]').forEach((select) => { if (select.value) values[id][select.dataset.cfMatrixRow] = select.value; });
        return;
      }
      if (type === 'multiselect') values[id] = Array.from(wrap.querySelectorAll('input:checked')).map((input) => input.value);
      else if (type === 'radio' || type === 'rating') values[id] = wrap.querySelector('input:checked')?.value || '';
      else if (type === 'checkbox') values[id] = wrap.querySelector('input').checked;
      else values[id] = wrap.querySelector('[data-cf-value]').value;
    });
    return values;
  }
  function showStatus(status, messages, error) {
    status.replaceChildren();
    status.hidden = false;
    status.classList.toggle('cf-message--error', error);
    status.setAttribute('role', error ? 'alert' : 'status');
    const title = document.createElement('p');
    title.textContent = error ? 'Please check your request and try again.' : 'Your request has been submitted.';
    status.append(title);
    messages.forEach((message) => { const line = document.createElement('p'); line.textContent = message; status.append(line); });
    status.focus();
  }
  function init() {
    const form = document.querySelector('[data-cf-submit]');
    if (!form) return;
    const status = form.querySelector('[data-cf-status]');
    const button = form.querySelector('button[type="submit"]');
    form.querySelectorAll('[data-max-clips]').forEach((input) => {
      const list = input.closest('[data-cf-pub-field]').querySelector('[data-cf-audio-list]');
      let urls = [];
      const clear = () => { urls.forEach((url) => URL.revokeObjectURL(url)); urls = []; list.replaceChildren(); };
      input.addEventListener('change', () => {
        clear();
        Array.from(input.files).forEach((file) => {
          const name = document.createElement('p'); name.textContent = file.name;
          const audio = document.createElement('audio'); audio.controls = true; audio.src = URL.createObjectURL(file);
          urls.push(audio.src); list.append(name, audio);
        });
      });
      form.addEventListener('reset', clear);
      window.addEventListener('pagehide', clear);
    });
    form.querySelectorAll('[data-cf-password-toggle]').forEach((toggle) => toggle.addEventListener('click', () => {
      const input = toggle.closest('[data-cf-pub-field]').querySelector('[data-cf-value]');
      const show = input.type === 'password'; input.type = show ? 'text' : 'password';
      toggle.textContent = show ? 'Hide' : 'Show'; toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); toggle.setAttribute('aria-pressed', String(show));
    }));
    form.querySelectorAll('[data-cf-signature]').forEach((canvas) => {
      const wrap = canvas.closest('[data-cf-signature-wrap]'); wrap.hidden = false;
      const input = wrap.querySelector('[data-cf-value]');
      const ctx = canvas.getContext('2d'); let drawing = false;
      function position(event) { const rect = canvas.getBoundingClientRect(); return [(event.clientX - rect.left) * canvas.width / rect.width, (event.clientY - rect.top) * canvas.height / rect.height]; }
      canvas.addEventListener('pointerdown', (event) => { event.preventDefault(); drawing = true; canvas.setPointerCapture(event.pointerId); ctx.beginPath(); ctx.moveTo(...position(event)); });
      canvas.addEventListener('pointermove', (event) => {
        if (!drawing) return;
        event.preventDefault(); ctx.lineWidth = 2; ctx.lineCap = 'round'; ctx.strokeStyle = getComputedStyle(canvas).color; ctx.lineTo(...position(event)); ctx.stroke();
        input.value = canvas.toDataURL('image/png'); input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      for (const event of ['pointerup', 'pointercancel']) canvas.addEventListener(event, () => { drawing = false; });
      wrap.querySelector('[data-cf-signature-clear]').addEventListener('click', () => { ctx.clearRect(0, 0, canvas.width, canvas.height); input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); });
      form.addEventListener('reset', () => { drawing = false; ctx.clearRect(0, 0, canvas.width, canvas.height); input.value = ''; });
    });
    let dirty = false;
    form.addEventListener('input', () => { dirty = true; });
    window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (button.disabled) return;
      const multiselects = Array.from(form.querySelectorAll('[data-field-type="multiselect"]'));
      const errors = [];
      multiselects.forEach((wrap) => {
        const required = wrap.dataset.required === 'true';
        const missing = required && !wrap.querySelector('input:checked');
        const error = wrap.querySelector('[data-cf-field-error]');
        error.hidden = !missing;
        error.textContent = missing ? 'Choose at least one option.' : '';
        wrap.querySelectorAll('input').forEach((input) => { input.setAttribute('aria-invalid', String(missing)); });
        if (missing) errors.push('Choose at least one option for ' + wrap.querySelector('legend').textContent.trim() + '.');
      });
      form.querySelectorAll('[data-field-type="signature"]').forEach((wrap) => {
        if (wrap.dataset.required === 'true' && !wrap.querySelector('[data-cf-value]').value && !wrap.querySelector('[data-cf-signature-upload]').files.length) errors.push('Draw or upload your signature.');
      });
      form.querySelectorAll('[data-max-clips]').forEach((input) => {
        if (input.files.length > Number(input.dataset.maxClips)) errors.push(`Choose at most ${input.dataset.maxClips} audio clips.`);
      });
      if (errors.length) { showStatus(status, errors, true); return; }
      const data = new FormData(form);
      data.set('form_data', JSON.stringify(collectValues(form)));
      const headers = { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
      const csrf = document.querySelector('meta[name="csrf-token"]');
      if (csrf?.content) headers['X-CSRF-Token'] = csrf.content;
      button.disabled = true;
      button.textContent = 'Submitting…';
      form.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(form.action, { method: 'POST', credentials: 'same-origin', headers, body: data });
        if (response.redirected || response.headers.get('content-type')?.includes('text/html')) throw new Error('Your session may have expired. Reload the page and sign in again.');
        const result = await response.json();
        if (!response.ok) {
          const detail = result.detail;
          const messages = typeof detail === 'string' ? [detail] : Array.isArray(detail) ? detail.map((entry) => entry.msg || String(entry)) : detail?.errors || ['We could not submit your request. Please try again.'];
          showStatus(status, messages, true);
          return;
        }
        dirty = false;
        form.reset();
        showStatus(status, ['A support ticket has been created with your answers.'], false);
        if (Number.isInteger(result.ticket_id)) {
          const link = document.createElement('a');
          link.href = `/tickets/${result.ticket_id}`;
          link.textContent = `View ticket #${result.ticket_id}`;
          status.append(link);
        }
      } catch (error) {
        showStatus(status, [error.message || 'Connection lost. Your answers are still here; please try again.'], true);
      } finally {
        button.disabled = false;
        button.textContent = 'Submit request';
        form.removeAttribute('aria-busy');
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
