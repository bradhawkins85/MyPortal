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
      if (type === 'file' || type === 'audio') return;
      if (type === 'radio') {
        const checked = wrap.querySelector('input[type="radio"]:checked');
        values[fid] = checked ? checked.value : '';
      } else if (type === 'rating') {
        const checked = wrap.querySelector('input[type="radio"][data-cf-value]:checked');
        values[fid] = checked ? Number(checked.value) : 0;
      } else if (type === 'matrix') {
        const data = {};
        wrap.querySelectorAll('input[type="radio"][data-cf-matrix-row]:checked').forEach((radio) => {
          data[radio.getAttribute('data-cf-matrix-row')] = radio.value;
        });
        values[fid] = data;
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

  function bindPasswordToggles(form) {
    form.querySelectorAll('[data-cf-password-toggle]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const wrap = btn.closest('[data-cf-pub-field]');
        const input = wrap ? wrap.querySelector('input[data-cf-value]') : null;
        if (!input) return;
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.textContent = show ? 'Hide' : 'Show';
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      });
    });
  }

  function bindRating(form) {
    form.querySelectorAll('[data-cf-rating]').forEach((group) => {
      const stars = Array.from(group.querySelectorAll('.cf-rating__star'));
      const valueEl = document.createElement('span');
      valueEl.className = 'cf-rating__value';
      const max = group.getAttribute('data-max') || '5';
      valueEl.textContent = `0 / ${max}`;
      group.appendChild(valueEl);
      const update = (selected) => {
        stars.forEach((star) => {
          const v = Number(star.querySelector('input').value);
          star.classList.toggle('is-filled', v <= selected);
        });
        valueEl.textContent = `${selected} / ${max}`;
      };
      stars.forEach((star) => {
        const input = star.querySelector('input');
        if (input.checked) update(Number(input.value));
        input.addEventListener('change', () => update(Number(input.value)));
      });
    });
  }

  function bindSignature(form) {
    form.querySelectorAll('[data-cf-signature]').forEach((canvas) => {
      const wrap = canvas.closest('.cf-signature');
      const hidden = wrap ? wrap.querySelector('input[data-cf-value]') : null;
      const clearBtn = wrap ? wrap.querySelector('[data-cf-signature-clear]') : null;
      const ctx = canvas.getContext('2d');
      let drawing = false;
      let hasInk = false;
      const getPos = (event) => {
        const rect = canvas.getBoundingClientRect();
        return {
          x: ((event.clientX - rect.left) * canvas.width) / rect.width,
          y: ((event.clientY - rect.top) * canvas.height) / rect.height,
        };
      };
      const start = (event) => {
        drawing = true;
        const pos = getPos(event);
        ctx.beginPath();
        ctx.moveTo(pos.x, pos.y);
      };
      const move = (event) => {
        if (!drawing) return;
        event.preventDefault();
        const pos = getPos(event);
        ctx.lineWidth = 2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = '#111827';
        ctx.lineTo(pos.x, pos.y);
        ctx.stroke();
        hasInk = true;
      };
      const end = () => {
        if (!drawing) return;
        drawing = false;
        if (hasInk && hidden) hidden.value = canvas.toDataURL('image/png');
      };
      const reset = () => {
        hasInk = false;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (hidden) hidden.value = '';
      };
      canvas.addEventListener('pointerdown', start);
      canvas.addEventListener('pointermove', move);
      canvas.addEventListener('pointerup', end);
      canvas.addEventListener('pointerleave', end);
      if (clearBtn) clearBtn.addEventListener('click', reset);
    });
  }

  function bindAudioClips(form) {
    form.querySelectorAll('[data-cf-file][data-max-clips]').forEach((input) => {
      const wrap = input.closest('[data-cf-pub-field]');
      const list = wrap ? wrap.querySelector('[data-cf-audio-list]') : null;
      const render = () => {
        if (!list) return;
        list.replaceChildren();
        const files = Array.from(input.files || []);
        if (!files.length) {
          const hint = document.createElement('span');
          hint.className = 'cf-audio__hint';
          hint.textContent = 'No clips yet.';
          list.appendChild(hint);
          return;
        }
        files.forEach((file, i) => {
          const item = document.createElement('div');
          item.className = 'cf-audio__clip';
          const name = document.createElement('span');
          name.className = 'cf-audio__name';
          name.textContent = `${i + 1}. ${file.name}`;
          const audio = document.createElement('audio');
          audio.controls = true;
          audio.src = URL.createObjectURL(file);
          item.append(name, audio);
          list.appendChild(item);
        });
      };
      input.addEventListener('change', render);
      render();
    });
  }

  function resetWidgets(form) {
    form.querySelectorAll('[data-cf-signature]').forEach((canvas) => {
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
      const wrap = canvas.closest('.cf-signature');
      const hidden = wrap ? wrap.querySelector('input[data-cf-value]') : null;
      if (hidden) hidden.value = '';
    });
    form.querySelectorAll('[data-cf-audio-list]').forEach((list) => {
      list.replaceChildren();
    });
    form.querySelectorAll('[data-cf-password-toggle]').forEach((btn) => {
      const wrap = btn.closest('[data-cf-pub-field]');
      const input = wrap ? wrap.querySelector('input[data-cf-value]') : null;
      if (input) input.type = 'password';
      btn.textContent = 'Show';
      btn.setAttribute('aria-label', 'Show password');
    });
  }

  function bind() {
    const form = document.querySelector('[data-cf-submit]');
    if (!form) return;
    const slug = form.getAttribute('data-slug');
    const status = form.querySelector('[data-cf-status]');

    bindPasswordToggles(form);
    bindRating(form);
    bindSignature(form);
    bindAudioClips(form);

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const values = collectValues(form);
      const formData = new FormData();
      formData.append('form_data', JSON.stringify(values));
      form.querySelectorAll('[data-cf-file]').forEach((fileInput) => {
        if (!fileInput.name) return;
        const files = Array.from(fileInput.files || []);
        if (!files.length) return;
        const max = parseInt(fileInput.getAttribute('data-max-clips'), 10);
        const toAppend = Number.isFinite(max) && max > 0 ? files.slice(0, max) : files;
        toAppend.forEach((file) => formData.append(fileInput.name, file));
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
          resetWidgets(form);
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
