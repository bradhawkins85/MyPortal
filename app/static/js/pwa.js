(function releaseCoordinator() {
  'use strict';
  const MANIFEST_URL = '/release-manifest.json';
  const CHANNEL = 'myportal-release';
  const POLL_MS = 60000;
  const tabId = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel(CHANNEL) : null;
  let currentManifest = null;
  let registration = null;
  let explicitReloadRelease = null;

  function hasUnsavedWork() {
    if (document.querySelector('[data-dirty="true"], [data-unsaved="true"]')) return true;
    return Array.from(document.querySelectorAll('form')).some((form) => {
      if (form.dataset.releaseIgnore === 'true') return false;
      if (form.querySelector('input[type="file"]')?.files.length) return true;
      return form.dataset.releaseInitial && form.dataset.releaseInitial !== serialiseForm(form);
    }) || Boolean(document.querySelector('[contenteditable="true"][data-dirty="true"]'));
  }

  function serialiseForm(form) {
    return JSON.stringify(Array.from(new FormData(form).entries()).filter(([, value]) => typeof value === 'string'));
  }

  function trackWorkflows() {
    document.querySelectorAll('form').forEach((form) => {
      form.dataset.releaseInitial = serialiseForm(form);
      form.addEventListener('submit', () => { form.dataset.releaseInitial = serialiseForm(form); });
    });
    document.addEventListener('input', (event) => {
      if (event.target && event.target.isContentEditable) event.target.dataset.dirty = 'true';
    });
    window.MyPortalUpdates = { hasUnsavedWork, markClean(element) {
      if (element?.tagName === 'FORM') element.dataset.releaseInitial = serialiseForm(element);
      if (element?.dataset) delete element.dataset.dirty;
    } };
  }

  async function readinessCheck() {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      try {
        const upgrade = await fetch('/upgrade-status', { cache: 'no-store' });
        const state = upgrade.ok ? await upgrade.json() : { maintenance: true };
        const ready = await fetch('/readyz', { cache: 'no-store' });
        if (!state.maintenance && ready.ok) return true;
      } catch (_) { /* bounded retry */ }
      await new Promise((resolve) => setTimeout(resolve, Math.min(10000, 1000 * (attempt + 1))));
    }
    return false;
  }

  function refreshSoftAssets(manifest) {
    document.querySelectorAll('link[rel="stylesheet"][href*="/static/"]').forEach((link) => {
      const url = new URL(link.href);
      const revision = manifest.assets?.[url.pathname] || manifest.release;
      if (url.searchParams.get('v') !== revision) {
        url.searchParams.set('v', revision);
        link.href = url.toString();
      }
    });
    document.dispatchEvent(new CustomEvent('app:release-assets-updated', { detail: manifest }));
  }

  async function applyReload(manifest, options) {
    if (hasUnsavedWork() && !options?.discardUnsaved) {
      window.dispatchEvent(new CustomEvent('pwa:update-blocked', { detail: {
        release: manifest.release,
        message: 'Unsaved work is open. Finish or save it before reloading, or choose Refresh again to deliberately discard it.'
      } }));
      return false;
    }
    if (!(await readinessCheck())) {
      window.dispatchEvent(new CustomEvent('pwa:update-blocked', { detail: {
        release: manifest.release,
        message: 'The planned upgrade is not ready yet. Your page and work have been left open.'
      } }));
      return false;
    }
    sessionStorage.setItem('myportal-release-return', location.href);
    explicitReloadRelease = manifest.release;
    sessionStorage.setItem('myportal-release-reload', manifest.release);
    if (!options?.coordinated) channel?.postMessage({ type: 'applying', release: manifest.release, tabId });
    if (registration?.waiting) registration.waiting.postMessage({ type: 'SKIP_WAITING' });
    else location.replace(location.href);
    return true;
  }

  function announce(manifest) {
    if (manifest.compatibility === 'none') return;
    if (manifest.compatibility === 'soft') {
      refreshSoftAssets(manifest);
      return;
    }
    const promptKey = `myportal-release-prompt:${manifest.release}`;
    if (localStorage.getItem(promptKey)) return;
    localStorage.setItem(promptKey, tabId);
    channel?.postMessage({ type: 'offered', release: manifest.release, tabId });
    window.dispatchEvent(new CustomEvent('pwa:update-available', { detail: {
      release: manifest.release,
      compatibility: manifest.compatibility,
      mandatory: manifest.compatibility === 'mandatory',
      reason: manifest.message,
      dirty: hasUnsavedWork(),
      applyUpdate: (options) => applyReload(manifest, options)
    } }));
  }

  async function checkRelease() {
    try {
      const response = await fetch(MANIFEST_URL, { cache: 'no-store' });
      if (!response.ok) return;
      const manifest = await response.json();
      if (!currentManifest) {
        currentManifest = manifest;
        localStorage.setItem('myportal-release-current', manifest.release);
        return;
      }
      if (manifest.release !== currentManifest.release) {
        currentManifest = manifest;
        registration?.update();
        announce(manifest);
      }
    } catch (_) { /* Offline clients check again when connectivity returns. */ }
  }

  async function register() {
    if (!('serviceWorker' in navigator)) return;
    try {
      registration = await navigator.serviceWorker.register('/service-worker.js', { scope: '/' });
      registration.addEventListener('updatefound', () => {
        const worker = registration.installing;
        worker?.addEventListener('statechange', () => {
          if (worker.state === 'installed' && currentManifest) announce(currentManifest);
        });
      });
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        const expected = sessionStorage.getItem('myportal-release-reload');
        if (expected && expected === explicitReloadRelease && !hasUnsavedWork()) {
          sessionStorage.removeItem('myportal-release-reload');
          location.replace(sessionStorage.getItem('myportal-release-return') || location.href);
        }
      });
    } catch (error) { console.error('Service worker registration failed', error); }
  }

  channel?.addEventListener('message', (event) => {
    if (event.data?.type === 'applying' && event.data.release) {
      localStorage.setItem(`myportal-release-prompt:${event.data.release}`, event.data.tabId);
      if (currentManifest?.release === event.data.release) {
        if (hasUnsavedWork()) {
          window.dispatchEvent(new CustomEvent('pwa:update-blocked', { detail: {
            release: event.data.release,
            message: 'Another tab applied the required update. Save or finish this tab before refreshing it.'
          } }));
        } else {
          applyReload(currentManifest, { coordinated: true });
        }
      }
    }
  });
  window.addEventListener('online', checkRelease);
  window.addEventListener('load', () => { trackWorkflows(); register(); checkRelease(); setInterval(checkRelease, POLL_MS); });
})();
