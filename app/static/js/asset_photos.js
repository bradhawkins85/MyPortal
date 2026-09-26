(() => {
  const form = document.querySelector('[data-asset-photo-upload]');
  if (!form) return;
  const inputs = [...form.querySelectorAll('[data-photo-input]')];
  const preview = form.querySelector('[data-photo-preview]');
  const image = form.querySelector('[data-photo-preview-image]');
  const status = form.querySelector('[data-photo-status]');
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
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!file) { status.textContent = 'Choose or take a photo first.'; return; }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    status.textContent = 'Uploading… Keep this page open.';
    const body = new FormData(form);
    body.set('photo', file, file.name);
    try {
      const response = await fetch(form.action, { method: 'POST', body });
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.detail || 'Upload failed. Check your connection and try again.');
      }
      status.textContent = 'Photo attached successfully.';
      location.assign(`${location.pathname}#asset-photos`);
      location.reload();
    } catch (error) {
      status.textContent = `${error.message} Retry will not create a duplicate.`;
      button.disabled = false;
    }
  });
})();
