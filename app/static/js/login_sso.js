(function () {
  'use strict';

  var DEBOUNCE_MS = 300;
  var START_PATH = '/auth/microsoft/start';
  var DISCOVER_PATH = '/auth/microsoft/discover';
  var EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  var REDIRECTED_KEY = 'myportal.sso.redirected';
  var PASSWORD_KEY = 'myportal.sso.usePassword';

  var link = document.querySelector('[data-sso-continue]');
  var form = link ? link.closest('form') : null;
  var email = form ? form.querySelector('input[name="email"]') : null;
  if (!link || !form || !email) {
    return;
  }
  var hint = form.querySelector('[data-sso-hint]');
  var usePassword = form.querySelector('[data-sso-use-password]');

  var timer = null;
  var sequence = 0;
  var commitPending = false;
  var redirected = {};
  var modes = {};

  function readStorage(key) {
    try {
      return window.sessionStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.sessionStorage.setItem(key, value);
    } catch (error) {
      /* Storage unavailable: the in-page state still applies. */
    }
  }

  try {
    var stored = JSON.parse(readStorage(REDIRECTED_KEY) || '[]');
    if (Array.isArray(stored)) {
      stored.forEach(function (key) {
        redirected[String(key)] = true;
      });
    }
  } catch (error) {
    redirected = {};
  }

  // The server turns this off after a failed Microsoft sign-in so nobody is
  // sent straight back to Microsoft in a loop.
  var passwordChosen = readStorage(PASSWORD_KEY) === '1';
  var autoRedirect = form.getAttribute('data-sso-auto') === '1' && !passwordChosen;

  function rememberRedirect(key) {
    redirected[key] = true;
    writeStorage(REDIRECTED_KEY, JSON.stringify(Object.keys(redirected)));
  }

  function nextTarget() {
    var target = form.getAttribute('data-success-redirect') || '/';
    return target === '/' ? '' : target;
  }

  function startUrl(address) {
    var url = START_PATH + '?email=' + encodeURIComponent(address);
    var next = nextTarget();
    if (next) {
      url += '&next=' + encodeURIComponent(next);
    }
    return url;
  }

  function setVisible(visible, address, required) {
    if (visible) {
      link.setAttribute('href', startUrl(address));
      link.removeAttribute('hidden');
      if (hint) {
        hint.removeAttribute('hidden');
      }
    } else {
      link.setAttribute('hidden', '');
      if (hint) {
        hint.setAttribute('hidden', '');
      }
    }
    if (usePassword) {
      if (visible && required && !passwordChosen) {
        usePassword.removeAttribute('hidden');
      } else {
        usePassword.setAttribute('hidden', '');
      }
    }
  }

  function apply(mode, address, allowRedirect) {
    if (mode !== 'optional' && mode !== 'required') {
      setVisible(false, address, false);
      return;
    }
    var key = address.toLowerCase();
    setVisible(true, address, mode === 'required');
    if (mode === 'required' && allowRedirect && autoRedirect && !redirected[key]) {
      rememberRedirect(key);
      window.location.assign(startUrl(address));
    }
  }

  function check(allowRedirect) {
    var address = email.value.trim();
    var ticket = ++sequence;
    if (!EMAIL_PATTERN.test(address)) {
      setVisible(false, address, false);
      return;
    }
    var key = address.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(modes, key)) {
      apply(modes[key], address, allowRedirect);
      return;
    }
    fetch(DISCOVER_PATH, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email: address }),
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('discover failed');
        }
        return response.json();
      })
      .then(function (body) {
        var mode = body && typeof body.sso === 'string' ? body.sso : 'off';
        modes[key] = mode;
        if (ticket === sequence) {
          apply(mode, address, allowRedirect);
        }
      })
      .catch(function () {
        if (ticket === sequence) {
          setVisible(false, address, false);
        }
      });
  }

  function schedule(isCommit) {
    // Typing after a commit cancels it: only a finished address may redirect.
    commitPending = isCommit;
    window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      var allowRedirect = commitPending;
      commitPending = false;
      check(allowRedirect);
    }, DEBOUNCE_MS);
  }

  email.addEventListener('input', function () {
    schedule(false);
  });
  email.addEventListener('change', function () {
    schedule(true);
  });
  email.addEventListener('blur', function () {
    schedule(true);
  });

  if (usePassword) {
    usePassword.addEventListener('click', function () {
      autoRedirect = false;
      passwordChosen = true;
      writeStorage(PASSWORD_KEY, '1');
      usePassword.setAttribute('hidden', '');
      var password = form.querySelector('input[name="password"]');
      if (password) {
        password.focus();
      }
    });
  }

  if (email.value) {
    schedule(false);
  }
})();
