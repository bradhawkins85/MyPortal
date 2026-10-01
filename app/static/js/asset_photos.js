(() => {
  const form = document.querySelector('[data-asset-photo-upload]');
  if (!form) return;
  const inputs = [...form.querySelectorAll('[data-photo-input]')];
  const preview = form.querySelector('[data-photo-preview]');
  const image = form.querySelector('[data-photo-preview-image]');
  const status = form.querySelector('[data-photo-status]');
  const progress = form.querySelector('[data-photo-progress]');
  const progressBar = progress.querySelector('progress');
  const progressLabel = progress.querySelector('[data-photo-progress-label]');
  const key = form.querySelector('[data-photo-key]');
  let file = null;
  let previewUrl = null;
  const newKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const clear = () => {
    file = null;
    inputs.forEach((input) => { input.value = ''; });
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    preview.hidden = true;
    progress.hidden = true;
    progressBar.value = 0;
    progressLabel.textContent = '0%';
    status.textContent = '';
  };
  inputs.forEach((input) => input.addEventListener('change', () => {
    if (!input.files?.[0]) return;
    file = input.files[0];
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    image.src = previewUrl;
    preview.hidden = false;
    key.value = newKey();
    status.textContent = 'Photo ready to upload.';
  }));
  form.querySelector('[data-photo-cancel]').addEventListener('click', clear);
  form.querySelector('[data-photo-retake]').addEventListener('click', () => inputs[0].click());
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!file) { status.textContent = 'Choose or take a photo first.'; return; }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    status.textContent = 'Uploading… Keep this page open.';
    progress.hidden = false;
    const body = new FormData(form);
    body.set('photo', file, file.name);
    const request = new XMLHttpRequest();
    request.open('POST', form.action);
    request.setRequestHeader('Accept', 'application/json');
    request.upload.addEventListener('progress', (uploadEvent) => {
      if (!uploadEvent.lengthComputable) return;
      const percent = Math.min(100, Math.round((uploadEvent.loaded / uploadEvent.total) * 100));
      progressBar.value = percent;
      progressLabel.textContent = `${percent}%`;
    });
    request.addEventListener('load', () => {
      if (request.status < 200 || request.status >= 300) {
        let message = 'Upload failed. Check your connection and try again.';
        try { message = JSON.parse(request.responseText).detail || message; } catch (_) { /* use safe fallback */ }
        status.textContent = `${message} Retry will not create a duplicate.`;
        button.disabled = false;
        return;
      }
      progressBar.value = 100;
      progressLabel.textContent = '100%';
      status.textContent = 'Photo attached successfully.';
      location.assign(`${location.pathname}#asset-photos`);
    });
    request.addEventListener('error', () => {
      status.textContent = 'Upload interrupted. Check your connection and retry; a duplicate will not be created.';
      button.disabled = false;
    });
    request.addEventListener('abort', () => {
      status.textContent = 'Upload cancelled. You can safely retry.';
      button.disabled = false;
    });
    request.send(body);
  });
})();
