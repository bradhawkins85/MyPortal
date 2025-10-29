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
      const reason = typeof payload.reason === 'string' ? payload.reason.trim() : '';
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
    setupHeaderMenus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, { once: true });
  } else {
    initialise();
  }
})();
