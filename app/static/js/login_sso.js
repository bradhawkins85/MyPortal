(function () {
  'use strict';

  var DEBOUNCE_MS = 300;
  var START_PATH = '/auth/microsoft/start';
  var DISCOVER_PATH = '/auth/microsoft/discover';
  var EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  var link = document.querySelector('[data-sso-continue]');
  var form = link ? link.closest('form') : null;
  var email = form ? form.querySelector('input[name="email"]') : null;
  if (!link || !form || !email) {
    return;
  }
  var hint = form.querySelector('[data-sso-hint]');

  var timer = null;
  var sequence = 0;
  var commitPending = false;
  var redirectedFor = '';
  var modes = {};

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

  function setVisible(visible, address) {
    if (visible) {
      link.setAttribute('href', startUrl(address));
      link.removeAttribute('hidden');
      if (hint) {
        hint.removeAttribute('hidden');
      }
      return;
    }
    link.setAttribute('hidden', '');
    if (hint) {
      hint.setAttribute('hidden', '');
    }
  }

  function apply(mode, address, allowRedirect) {
    if (mode !== 'optional' && mode !== 'required') {
      setVisible(false, address);
      return;
    }
    setVisible(true, address);
    if (mode === 'required' && allowRedirect && redirectedFor !== address.toLowerCase()) {
      redirectedFor = address.toLowerCase();
      window.location.assign(startUrl(address));
    }
  }

  function check(allowRedirect) {
    var address = email.value.trim();
    var ticket = ++sequence;
    if (!EMAIL_PATTERN.test(address)) {
      setVisible(false, address);
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
          setVisible(false, address);
        }
      });
  }

  function schedule(isCommit) {
    commitPending = commitPending || isCommit;
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

  if (email.value) {
    schedule(false);
  }
})();
