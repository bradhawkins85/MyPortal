(function () {
  'use strict';

  const ALERT_VARIANTS = ['alert--info', 'alert--success', 'alert--warning', 'alert--error'];

  function createToastController(root) {
    if (!root) {
      return {
        show() {},
        hide() {},
      };
    }

    const messageEl = root.querySelector('[data-notification-toast-message]');
    const dismissButton = root.querySelector('[data-notification-toast-dismiss]');
    let hideTimer = null;

    function applyVariant(variant) {
      const targetClass = variant && typeof variant === 'string' ? variant : 'info';
      const variantClass =
        targetClass === 'success'
          ? 'alert--success'
          : targetClass === 'warning'
          ? 'alert--warning'
          : targetClass === 'error'
          ? 'alert--error'
          : 'alert--info';

      ALERT_VARIANTS.forEach((className) => {
        if (className !== variantClass) {
          root.classList.remove(className);
        }
      });
      root.classList.add(variantClass);
    }

    function hide() {
      if (hideTimer) {
        window.clearTimeout(hideTimer);
        hideTimer = null;
      }
      root.setAttribute('aria-hidden', 'true');
      root.hidden = true;
    }

    function show(message, options) {
      if (!messageEl) {
        return;
      }

      const settings = options || {};
      applyVariant(settings.variant);

      messageEl.textContent = message || '';
      root.hidden = false;
      root.setAttribute('aria-hidden', 'false');

      if (hideTimer) {
        window.clearTimeout(hideTimer);
        hideTimer = null;
      }

      if (settings.autoHideMs && Number.isFinite(settings.autoHideMs)) {
        hideTimer = window.setTimeout(() => {
          hide();
        }, settings.autoHideMs);
      }
    }

    if (dismissButton) {
      dismissButton.addEventListener('click', () => {
        hide();
      });
    }

    return { show, hide };
  }

  function setupAutoRefresh() {
    const body = document.body;
    if (!body) {
      return;
    }

    if (body.dataset.enableAutoRefresh !== 'true') {
      return;
    }

    if (!('WebSocket' in window)) {
      return;
    }

    const toast = createToastController(document.querySelector('[data-global-toast]'));

    let socket = null;
    let reconnectAttempts = 0;
    let reconnectTimer = null;
    let reloadTimer = null;
    let stop = false;

    const baseDelay = 1000;
    const maxDelay = 30000;

    function resetReloadTimer() {
      if (reloadTimer) {
        window.clearTimeout(reloadTimer);
        reloadTimer = null;
      }
    }

    function handleRefreshMessage(payload) {
      const detail = {
        ...(payload && typeof payload === 'object' ? payload : {}),
        showToast(message, options) {
          toast.show(message, options || {});
        },
      };

      const event = new CustomEvent('realtime:refresh', {
        detail,
        cancelable: true,
      });
      const shouldReload = document.dispatchEvent(event);
      if (!shouldReload) {
        return;
      }

      const reason = typeof detail.reason === 'string' ? detail.reason.trim() : '';
      const message = reason
        ? `${reason} Refreshing to apply updates…`
        : 'Updates are available. Refreshing to apply changes…';

      toast.show(message, { variant: 'info' });

      resetReloadTimer();
      reloadTimer = window.setTimeout(() => {
        window.location.reload();
      }, 1500);
    }

    function scheduleReconnect() {
      if (stop) {
        return;
      }

      const delay = Math.min(baseDelay * Math.pow(2, reconnectAttempts), maxDelay);
      reconnectAttempts += 1;

      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }

      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    }

    function connect() {
      if (stop) {
        return;
      }

      try {
        if (socket && socket.readyState !== WebSocket.CLOSED && socket.readyState !== WebSocket.CLOSING) {
          return;
        }
      } catch (error) {
        // Ignore errors when inspecting the current socket state.
      }

      const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const target = `${scheme}//${window.location.host}/ws/refresh`;

      let instance;
      try {
        instance = new WebSocket(target);
      } catch (error) {
        scheduleReconnect();
        return;
      }

      socket = instance;

      instance.addEventListener('open', () => {
        reconnectAttempts = 0;
      });

      instance.addEventListener('message', (event) => {
        if (!event || typeof event.data !== 'string') {
          return;
        }

        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          return;
        }

        if (!payload || payload.type !== 'refresh') {
          return;
        }

        handleRefreshMessage(payload);
      });

      instance.addEventListener('close', () => {
        if (stop) {
          return;
        }
        scheduleReconnect();
      });

      instance.addEventListener('error', () => {
        try {
          if (instance.readyState !== WebSocket.CLOSED && instance.readyState !== WebSocket.CLOSING) {
            instance.close();
          }
        } catch (error) {
          // Ignore socket state errors during error handling.
        }
      });
    }

    window.addEventListener('beforeunload', () => {
      stop = true;
      resetReloadTimer();
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      if (socket) {
        try {
          socket.close();
        } catch (error) {
          // Ignore socket close failures.
        }
      }
    });

    window.addEventListener('online', () => {
      if (!socket || socket.readyState === WebSocket.CLOSED) {
        reconnectAttempts = 0;
        connect();
      }
    });

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && (!socket || socket.readyState === WebSocket.CLOSED)) {
        reconnectAttempts = 0;
        connect();
      }
    });

    connect();
  }

  function setupForceRefresh() {
    const trigger = document.querySelector('[data-force-refresh]');
    if (!trigger) {
      return;
    }

    const hasServiceWorker = typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
    const hasCacheAPI = typeof window !== 'undefined' && 'caches' in window;
    const supportsEnhancedRefresh = hasServiceWorker || hasCacheAPI;
    const shouldBroadcastRefresh = trigger.getAttribute('data-force-refresh-broadcast') === 'true';

    if (!supportsEnhancedRefresh) {
      trigger.title = 'Reloads the app to request the latest files';
    }

    const toast = createToastController(document.querySelector('[data-global-toast]'));
    let busy = false;

    async function broadcastRefreshNotice() {
      if (!shouldBroadcastRefresh || typeof fetch !== 'function') {
        return { attempted: false, success: false };
      }

      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      let timeoutId = null;

      if (controller && typeof window !== 'undefined' && typeof window.setTimeout === 'function') {
        timeoutId = window.setTimeout(() => {
          try {
            controller.abort();
          } catch (error) {
            // Ignore abort errors so the refresh flow can continue.
          }
        }, 6000);
      }

      try {
        const options = {
          method: 'POST',
          headers: {
            Accept: 'application/json',
          },
          credentials: 'same-origin',
        };
        if (controller) {
          options.signal = controller.signal;
        }

        const response = await fetch('/api/system/refresh', options);
        if (!response || !response.ok) {
          return {
            attempted: true,
            success: false,
            status: response ? response.status : null,
          };
        }

        return { attempted: true, success: true };
      } catch (error) {
        console.error('Failed to broadcast refresh request', error);
        return {
          attempted: true,
          success: false,
          error,
        };
      } finally {
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
        }
      }
    }

    function waitForServiceWorkerMessage(expectedType, timeoutMs) {
      if (!hasServiceWorker || !navigator.serviceWorker || typeof navigator.serviceWorker.addEventListener !== 'function') {
        return Promise.resolve(null);
      }

      const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 7000;

      return new Promise((resolve) => {
        let settled = false;
        let timeoutId = null;

        function cleanup() {
          if (timeoutId !== null) {
            window.clearTimeout(timeoutId);
            timeoutId = null;
          }
          navigator.serviceWorker.removeEventListener('message', handleMessage);
        }

        function handleMessage(event) {
          const data = event && event.data;
          if (!data || data.type !== expectedType) {
            return;
          }
          settled = true;
          cleanup();
          resolve(data);
        }

        navigator.serviceWorker.addEventListener('message', handleMessage);
        timeoutId = window.setTimeout(() => {
          if (!settled) {
            cleanup();
            resolve(null);
          }
        }, timeout);
      });
    }

    async function clearServiceWorkerCaches() {
      if (!hasServiceWorker || !navigator.serviceWorker) {
        return;
      }

      let registrations = [];
      try {
        registrations = await navigator.serviceWorker.getRegistrations();
      } catch (error) {
        throw error;
      }

      if (registrations.length) {
        await Promise.allSettled(
          registrations.map((registration) => {
            try {
              return registration.update();
            } catch (error) {
              return Promise.reject(error);
            }
          })
        );
      }

      const activeWorkers = registrations
        .map((registration) => registration.active)
        .filter((worker) => Boolean(worker));

      const controller = navigator.serviceWorker.controller;
      const acknowledgement = controller ? waitForServiceWorkerMessage('CACHE_CLEARED', 7000) : Promise.resolve(null);

      const recipients = controller ? [controller] : activeWorkers;
      if (recipients.length) {
        recipients.forEach((worker) => {
          try {
            worker.postMessage({ type: 'CLEAR_CACHE' });
          } catch (error) {
            // Ignore message delivery errors so the refresh flow can continue.
          }
        });
      }

      await acknowledgement;

      registrations.forEach((registration) => {
        if (registration.waiting) {
          try {
            registration.waiting.postMessage({ type: 'SKIP_WAITING' });
          } catch (error) {
            // Ignore failures when nudging waiting workers.
          }
        }
      });
    }

    async function clearWindowCaches() {
      if (!hasCacheAPI || !window.caches || typeof window.caches.keys !== 'function') {
        return;
      }

      const keys = await window.caches.keys();
      if (!Array.isArray(keys) || !keys.length) {
        return;
      }

      await Promise.allSettled(keys.map((key) => window.caches.delete(key)));
    }

    async function performForceRefresh() {
      let hadError = false;

      try {
        await clearServiceWorkerCaches();
      } catch (error) {
        console.error('Failed to clear service worker caches', error);
        hadError = true;
      }

      try {
        await clearWindowCaches();
      } catch (error) {
        console.error('Failed to clear Cache Storage entries', error);
        hadError = true;
      }

      return { hadError };
    }

    trigger.addEventListener('click', async () => {
      if (busy) {
        return;
      }

      busy = true;
      trigger.disabled = true;
      trigger.setAttribute('aria-busy', 'true');

      let broadcastResult = { attempted: false, success: false };

      if (shouldBroadcastRefresh) {
        toast.show('Notifying connected sessions to refresh…', { variant: 'info' });
        broadcastResult = await broadcastRefreshNotice();
        if (broadcastResult.success) {
          toast.show(
            supportsEnhancedRefresh
              ? 'Refresh notice sent. Clearing cached assets…'
              : 'Refresh notice sent. Reloading with a clean request…',
            { variant: 'info' },
          );
        } else {
          toast.show('Could not notify other sessions. Continuing with local refresh…', {
            variant: 'warning',
            autoHideMs: 6000,
          });
        }
      } else {
        toast.show(
          supportsEnhancedRefresh
            ? 'Refreshing the application and clearing cached assets…'
            : 'Refreshing the application with a clean request…',
          {
            variant: 'info',
          },
        );
      }

      let hadError = false;
      try {
        const result = await performForceRefresh();
        hadError = result.hadError;
      } catch (error) {
        console.error('Force refresh encountered an unexpected error', error);
        hadError = true;
      }

      if (hadError) {
        toast.show('Encountered issues clearing cached assets. Reloading to request the latest files…', {
          variant: 'warning',
          autoHideMs: 6000,
        });
      } else if (supportsEnhancedRefresh) {
        toast.show('Cached assets cleared. Reloading with the latest version…', {
          variant: 'success',
          autoHideMs: 4000,
        });
      } else {
        const message = broadcastResult.success
          ? 'Reloading the application with the latest files…'
          : 'Reloading the application to request the latest files…';
        toast.show(message, {
          variant: 'success',
          autoHideMs: 4000,
        });
      }

      window.setTimeout(() => {
        try {
          const currentUrl = new URL(window.location.href);
          currentUrl.searchParams.set('_refresh', Date.now().toString(36));
          window.location.replace(currentUrl.toString());
        } catch (error) {
          window.location.reload();
        }
      }, 700);
    });
  }

  function setupHeaderMenus() {
    const menus = Array.from(document.querySelectorAll('[data-header-menu]'));
    if (!menus.length) {
      return;
    }

    const toggleLookup = new Map();

    function getToggle(menu) {
      if (toggleLookup.has(menu)) {
        return toggleLookup.get(menu);
      }
      const toggle = menu.querySelector('[data-header-menu-toggle]');
      if (toggle) {
        toggleLookup.set(menu, toggle);
      }
      return toggle;
    }

    function updateToggle(menu) {
      const toggle = getToggle(menu);
      if (toggle) {
        toggle.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
      }
    }

    function closeMenu(menu, options) {
      const settings = options || {};
      if (!menu.open) {
        return;
      }
      menu.removeAttribute('open');
      updateToggle(menu);
      if (settings.focusToggle) {
        const toggle = getToggle(menu);
        if (toggle) {
          try {
            toggle.focus({ preventScroll: true });
          } catch (error) {
            toggle.focus();
          }
        }
      }
    }

    menus.forEach((menu) => {
      updateToggle(menu);

      menu.addEventListener('toggle', () => {
        if (menu.open) {
          menus.forEach((other) => {
            if (other !== menu) {
              closeMenu(other);
            }
          });
        }
        updateToggle(menu);
      });
    });

    document.addEventListener('click', (event) => {
      menus.forEach((menu) => {
        if (!menu.contains(event.target)) {
          closeMenu(menu);
        }
      });
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' || event.key === 'Esc') {
        menus.forEach((menu) => {
          closeMenu(menu, { focusToggle: true });
        });
      }
    });
  }

  function initialise() {
    setupAutoRefresh();
    setupForceRefresh();
    setupHeaderMenus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, { once: true });
  } else {
    initialise();
  }
})();
