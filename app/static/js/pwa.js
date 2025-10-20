(function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) {
    return;
  }

  const register = async () => {
    try {
      const registration = await navigator.serviceWorker.register('/service-worker.js', {
        scope: '/',
        type: 'classic'
      });

      if (registration.waiting) {
        notifyUpdateReady(registration.waiting);
      }

      registration.addEventListener('updatefound', () => {
        const newWorker = registration.installing;
        if (!newWorker) {
          return;
        }
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
            notifyUpdateReady(newWorker);
          }
        });
      });

      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshing) {
          return;
        }
        refreshing = true;
        window.location.reload();
      });
    } catch (error) {
      console.error('Service worker registration failed', error);
    }
  };

  const notifyUpdateReady = (worker) => {
    const event = new CustomEvent('pwa:update-available', {
      detail: {
        applyUpdate: () => worker.postMessage({ type: 'SKIP_WAITING' })
      }
    });
    window.dispatchEvent(event);
  };

  window.addEventListener('load', register);
})();

window.addEventListener('pwa:update-available', (event) => {
  if (!event || !event.detail || typeof event.detail.applyUpdate !== 'function') {
    return;
  }

  const confirmation = window.confirm('An update is available. Reload now to apply the latest version?');
  if (confirmation) {
    event.detail.applyUpdate();
  }
});

if (navigator.serviceWorker && typeof navigator.serviceWorker.addEventListener === 'function') {
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'CACHE_CLEARED') {
      console.info('Offline cache cleared');
    }
  });
}
