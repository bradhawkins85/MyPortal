(function () {
  'use strict';
  // Conditional logic. Mirrors visible_field_ids() in app/services/custom_forms.py,
  // which re-checks every submission.
  const NUMERIC_TYPES = new Set(['number', 'rating']);
  const NATIVE_REQUIRED_TYPES = new Set(['text', 'textarea', 'email', 'number', 'time', 'url', 'select', 'radio', 'checkbox', 'date', 'password', 'rating']);
  function conditionAnswers(wrap) {
    const type = wrap.dataset.fieldType;
    if (type === 'checkbox') return [wrap.querySelector('input').checked ? 'Yes' : 'No'];
    let items;
    if (type === 'multiselect') items = Array.from(wrap.querySelectorAll('input:checked')).map((input) => input.value);
    else if (type === 'radio' || type === 'rating') items = [wrap.querySelector('input:checked')?.value || ''];
    else items = [wrap.querySelector('[data-cf-value]')?.value || ''];
    return items.map((item) => String(item).trim()).filter(Boolean);
  }
  function compare(type, answer, target) {
    const numeric = NUMERIC_TYPES.has(type);
    const left = numeric ? Number(answer) : answer.toLowerCase();
    const right = numeric ? Number(target) : target.toLowerCase();
    if (numeric && (!target || Number.isNaN(left) || Number.isNaN(right))) return null;
    return (left > right) - (left < right);
  }
  function ruleMatches(type, answers, operator, value) {
    const target = String(value || '').trim();
    if (operator === 'empty') return !answers.length;
    if (operator === 'not_empty') return answers.length > 0;
    if (operator === 'equals' || operator === 'not_equals') {
      const hit = answers.some((answer) => compare(type, answer, target) === 0);
      return operator === 'equals' ? hit : !hit;
    }
    if (operator === 'contains' || operator === 'not_contains') {
      const hit = answers.some((answer) => answer.toLowerCase().includes(target.toLowerCase()));
      return operator === 'contains' ? hit : !hit;
    }
    if (operator === 'greater_than' || operator === 'less_than') {
      const wanted = operator === 'greater_than' ? 1 : -1;
      return answers.some((answer) => compare(type, answer, target) === wanted);
    }
    return false;
  }
  function initConditions(form) {
    const wraps = new Map(Array.from(form.querySelectorAll('[data-cf-pub-field]')).map((wrap) => [wrap.dataset.fieldId, wrap]));
    const conditional = Array.from(wraps.values()).filter((wrap) => wrap.dataset.cfConditions);
    if (!conditional.length) return;
    const rulesFor = new Map();
    conditional.forEach((wrap) => {
      try { rulesFor.set(wrap, JSON.parse(wrap.dataset.cfConditions)); } catch (_) { /* show the field */ }
    });
    function apply() {
      const state = new Map();
      function visible(wrap) {
        if (state.has(wrap)) return !!state.get(wrap); // null: still being evaluated (a loop)
        state.set(wrap, null);
        const conditions = rulesFor.get(wrap) || {};
        const results = [];
        (conditions.rules || []).forEach((rule) => {
          const source = wraps.get(String(rule.field_id));
          if (!source || source === wrap) return;
          const answers = visible(source) ? conditionAnswers(source) : [];
          results.push(ruleMatches(source.dataset.fieldType, answers, rule.operator, rule.value));
        });
        const shown = !results.length || (conditions.match === 'any' ? results.some(Boolean) : results.every(Boolean));
        state.set(wrap, shown);
        return shown;
      }
      conditional.forEach((wrap) => {
        const shown = visible(wrap);
        if (wrap.hidden === !shown && wrap.dataset.cfConditionsApplied) return;
        wrap.dataset.cfConditionsApplied = 'true';
        wrap.hidden = !shown;
        // Disabled controls are skipped by browser validation and FormData.
        wrap.querySelectorAll('input, select, textarea').forEach((input) => {
          if (!shown && !input.disabled) { input.disabled = true; input.dataset.cfConditionDisabled = 'true'; }
          else if (shown && input.dataset.cfConditionDisabled) { input.disabled = false; delete input.dataset.cfConditionDisabled; }
        });
        if (wrap.dataset.required === 'true') {
          const type = wrap.dataset.fieldType;
          const selector = type === 'matrix' ? '[data-cf-matrix-row]' : NATIVE_REQUIRED_TYPES.has(type) ? '[data-cf-value]' : null;
          if (selector) wrap.querySelectorAll(selector).forEach((input) => { input.required = shown; });
        }
      });
    }
    form.addEventListener('input', apply);
    form.addEventListener('change', apply);
    form.addEventListener('reset', () => setTimeout(apply));
    apply();
  }
  function isHiddenByCondition(node) { return !!node.closest('[data-cf-pub-field]')?.hidden; }
  function collectValues(form) {
    const values = {};
    form.querySelectorAll('[data-cf-pub-field]').forEach((wrap) => {
      if (wrap.hidden) return;
      const id = wrap.dataset.fieldId;
      const type = wrap.dataset.fieldType;
      if (type === 'file' || type === 'audio') return;
      if (type === 'matrix') {
        values[id] = {};
        wrap.querySelectorAll('[data-cf-matrix-row]:checked').forEach((input) => { values[id][input.dataset.cfMatrixRow] = input.dataset.cfMatrixColumn; });
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
  function initCaptureField(input, form) {
    const wrap = input.closest('[data-cf-pub-field]');
    const controls = wrap.querySelector('[data-cf-capture-controls]');
    const preview = wrap.querySelector('[data-cf-capture-preview]');
    const status = wrap.querySelector('[data-cf-capture-status]');
    const photo = wrap.querySelector('[data-cf-photo-input]');
    const video = wrap.querySelector('[data-cf-video-input]');
    let file = null, url = null;
    input.required = false;
    controls.hidden = false;
    const say = (message, error = false) => {
      status.textContent = message;
      status.classList.toggle('cf-public__field-error', error);
    };
    const update = () => {
      const submitting = form.getAttribute('aria-busy') === 'true';
      [input, ...controls.querySelectorAll('input, button'), ...preview.querySelectorAll('button')].forEach((control) => { control.disabled = submitting; });
    };
    const clear = () => {
      if (url) URL.revokeObjectURL(url);
      url = null; file = null; preview.replaceChildren();
      [input, photo, video].forEach((control) => { control.value = ''; });
      say('');
    };
    controls.querySelector('[data-cf-capture-photo]').addEventListener('click', () => photo.click());
    controls.querySelector('[data-cf-capture-video]').addEventListener('click', () => video.click());
    [input, photo, video].forEach((control) => control.addEventListener('change', () => {
      const selected = control.files[0];
      control.value = '';
      // Cancelling the camera or file picker keeps the previous attachment.
      if (!selected) return;
      if (selected.size > 50 * 1024 * 1024) { say('The attachment must be 50 MB or smaller. Take a smaller photo, record a shorter video, or choose a smaller file.', true); return; }
      clear(); file = selected;
      const name = document.createElement('p'); name.textContent = file.name; preview.append(name);
      // HEIC/HEIF can be attached even when the browser cannot display them.
      const image = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(file.type);
      if (image || file.type.startsWith('video/')) {
        url = URL.createObjectURL(file);
        const media = document.createElement(image ? 'img' : 'video');
        if (image) media.alt = `Preview of ${file.name}`;
        else { media.controls = true; media.preload = 'metadata'; media.playsInline = true; media.setAttribute('aria-label', `Preview of ${file.name}`); }
        media.src = url; preview.append(media);
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button--ghost'; remove.textContent = 'Remove attachment';
      remove.addEventListener('click', () => { clear(); input.dispatchEvent(new Event('input', { bubbles: true })); input.focus(); });
      preview.append(remove); say('Attachment ready.'); update();
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    form.addEventListener('reset', clear);
    window.addEventListener('pagehide', clear);
    return { input, files: () => file ? [file] : [], update };
  }
  function initAudioField(input, form, canRecord) {
    const wrap = input.closest('[data-cf-pub-field]');
    const list = wrap.querySelector('[data-cf-audio-list]');
    const controls = wrap.querySelector('[data-cf-audio-controls]');
    const record = wrap.querySelector('[data-cf-audio-record]');
    const stop = wrap.querySelector('[data-cf-audio-stop]');
    const timer = wrap.querySelector('[data-cf-audio-timer]');
    const status = wrap.querySelector('[data-cf-audio-status]');
    const maxClips = Number(input.dataset.maxClips);
    const maxBytes = 50 * 1024 * 1024;
    const supported = window.isSecureContext && navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined';
    let clips = [], recorder = null, stream = null, interval = null;
    let busy = false, generation = 0, sequence = 0;
    // Recorded clips live in memory and join uploads in FormData. Preserve native
    // required validation for the no-JavaScript fallback in the template.
    input.required = false;
    controls.hidden = !supported;
    const say = (message, error = false) => {
      status.textContent = message;
      status.classList.toggle('cf-public__field-error', error);
    };
    const update = () => {
      const submitting = form.getAttribute('aria-busy') === 'true';
      input.disabled = busy || submitting;
      record.disabled = busy || submitting || clips.length >= maxClips;
      list.querySelectorAll('button').forEach((button) => { button.disabled = busy || submitting; });
    };
    const releaseMicrophone = () => {
      clearInterval(interval);
      interval = null;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
      stop.hidden = true;
      timer.hidden = true;
    };
    const render = () => {
      list.replaceChildren();
      clips.forEach((clip, index) => {
        const row = document.createElement('div'); row.className = 'cf-audio-clip';
        const name = document.createElement('p'); name.textContent = clip.file.name;
        const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'metadata'; audio.src = clip.url;
        audio.setAttribute('aria-label', `Listen to ${clip.file.name}`);
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button--ghost'; remove.textContent = 'Remove clip';
        remove.setAttribute('aria-label', `Remove ${clip.file.name}`);
        remove.addEventListener('click', () => {
          URL.revokeObjectURL(clip.url); clips.splice(index, 1); render();
          say(`${clips.length} of ${maxClips} clips attached.`);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          (list.querySelector('button') || (supported ? record : input)).focus();
        });
        row.append(name, audio, remove); list.append(row);
      });
      update();
    };
    const add = (files) => {
      if (clips.length + files.length > maxClips) { say(`You can attach up to ${maxClips} clips. Remove a clip before adding more.`, true); return; }
      if (files.some((file) => file.size > maxBytes)) { say('Each clip must be 50 MB or smaller. Choose a smaller file or record a shorter clip.', true); return; }
      files.forEach((file) => clips.push({ file, url: URL.createObjectURL(file) }));
      render(); say(`${clips.length} of ${maxClips} clips attached. Listen back or remove a clip to record again.`);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    input.addEventListener('change', () => { add(Array.from(input.files)); input.value = ''; });
    const stopRecording = () => {
      if (!recorder || recorder.state === 'inactive') return;
      stop.disabled = true;
      say('Finishing your recording…');
      recorder.stop();
      releaseMicrophone();
    };
    stop.addEventListener('click', stopRecording);
    record.addEventListener('click', async () => {
      if (busy || clips.length >= maxClips) return;
      if (!canRecord()) { say('Stop the other recording before starting a new clip.', true); return; }
      const token = ++generation;
      let chunks = [], bytes = 0, tooLarge = false;
      busy = true; update(); say('Allow microphone access to start recording.');
      try {
        const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (token !== generation) { acquired.getTracks().forEach((track) => track.stop()); return; }
        stream = acquired;
        const mimeType = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((type) => MediaRecorder.isTypeSupported(type));
        const current = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
        recorder = current;
        current.addEventListener('dataavailable', (event) => {
          if (token !== generation || !event.data.size) return;
          bytes += event.data.size;
          if (bytes > maxBytes) { tooLarge = true; chunks = []; stopRecording(); }
          else if (!tooLarge) chunks.push(event.data);
        });
        current.addEventListener('stop', () => {
          if (token !== generation) return;
          releaseMicrophone(); recorder = null; busy = false;
          if (tooLarge) say('Recording exceeded 50 MB. Record a shorter clip.', true);
          else if (chunks.length) {
            const type = current.mimeType || chunks[0].type;
            const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
            add([new File(chunks, `recording-${++sequence}.${extension}`, { type })]);
          } else say('No audio was captured. Try recording again or upload a clip.', true);
          update(); record.focus();
        });
        current.addEventListener('error', () => {
          // Invalidate the subsequent stop event so partial/error audio is discarded.
          reset(false); say('Recording failed. Try again or upload an audio file.', true);
        });
        current.start(1000);
        const started = Date.now();
        timer.textContent = '0:00'; timer.hidden = false;
        interval = setInterval(() => {
          const seconds = Math.floor((Date.now() - started) / 1000);
          timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
        }, 500);
        stream.getAudioTracks().forEach((track) => track.addEventListener('ended', stopRecording));
        stop.disabled = false; stop.hidden = false; stop.focus();
        say('Recording. Tap Stop recording when you are finished.');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      } catch (error) {
        if (token !== generation) return;
        generation++; releaseMicrophone(); recorder = null; busy = false; update();
        say(error.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in your browser settings and try again, or upload an audio file.' : 'The microphone could not be started. Check it is available and try again, or upload an audio file.', true);
      }
    });
    function reset(clearClips = true) {
      generation++;
      if (recorder && recorder.state !== 'inactive') recorder.stop();
      recorder = null; releaseMicrophone(); busy = false;
      if (clearClips) { clips.forEach((clip) => URL.revokeObjectURL(clip.url)); clips = []; input.value = ''; }
      render();
      say(supported ? '' : 'Direct recording is unavailable in this browser. You can upload an audio file instead.');
    }
    form.addEventListener('reset', () => reset());
    window.addEventListener('pagehide', () => reset());
    reset();
    return { input, files: () => clips.map((clip) => clip.file), busy: () => busy, update };
  }
  function init() {
    const form = document.querySelector('[data-cf-submit]');
    if (!form) return;
    const status = form.querySelector('[data-cf-status]');
    const button = form.querySelector('button[type="submit"]');
    const captureFields = Array.from(form.querySelectorAll('[data-field-type="file"] [data-cf-file]')).map((input) => initCaptureField(input, form));
    const audioFields = [];
    form.querySelectorAll('[data-max-clips]').forEach((input) => {
      audioFields.push(initAudioField(input, form, () => !audioFields.some((field) => field.busy())));
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
    initConditions(form);
    let dirty = false;
    form.addEventListener('input', () => { dirty = true; });
    window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (button.disabled) return;
      const multiselects = Array.from(form.querySelectorAll('[data-field-type="multiselect"]')).filter((wrap) => !wrap.hidden);
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
        if (!wrap.hidden && wrap.dataset.required === 'true' && !wrap.querySelector('[data-cf-value]').value && !wrap.querySelector('[data-cf-signature-upload]').files.length) errors.push('Draw or upload your signature.');
      });
      audioFields.forEach((field) => {
        const wrap = field.input.closest('[data-cf-pub-field]');
        if (wrap.hidden) return;
        const missing = wrap.dataset.required === 'true' && !field.files().length;
        const error = wrap.querySelector('[data-cf-field-error]');
        error.hidden = !missing;
        error.textContent = missing ? 'Record or upload at least one audio clip.' : '';
        field.input.setAttribute('aria-invalid', String(missing));
        if (missing) errors.push('Record or upload an audio clip for ' + wrap.querySelector('label').textContent.trim() + '.');
        if (field.busy()) errors.push('Stop recording and wait for the clip to finish before submitting.');
      });
      captureFields.forEach((field) => {
        const wrap = field.input.closest('[data-cf-pub-field]');
        if (wrap.hidden) return;
        const missing = wrap.dataset.required === 'true' && !field.files().length;
        const error = wrap.querySelector('[data-cf-field-error]');
        error.hidden = !missing;
        error.textContent = missing ? 'Upload a file, take a photo, or record a video.' : '';
        field.input.setAttribute('aria-invalid', String(missing));
        if (missing) errors.push('Attach a file for ' + wrap.querySelector('label').textContent.trim() + '.');
      });
      if (errors.length) { showStatus(status, errors, true); return; }
      const data = new FormData(form);
      [...audioFields, ...captureFields].forEach((field) => {
        data.delete(field.input.name);
        if (isHiddenByCondition(field.input)) return;
        field.files().forEach((file) => data.append(field.input.name, file, file.name));
      });
      data.set('form_data', JSON.stringify(collectValues(form)));
      const headers = { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
      const csrf = document.querySelector('meta[name="csrf-token"]');
      if (csrf?.content) headers['X-CSRF-Token'] = csrf.content;
      button.disabled = true;
      button.textContent = 'Submitting…';
      form.setAttribute('aria-busy', 'true');
      [...audioFields, ...captureFields].forEach((field) => field.update());
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
        [...audioFields, ...captureFields].forEach((field) => field.update());
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
