(() => {
  'use strict';
  const token = decodeURIComponent(window.location.hash.slice(1));
  history.replaceState(null, '', '/credential-share');
  const form = document.getElementById('verify-form');
  const entry = document.getElementById('entry-panel');
  const revealPanel = document.getElementById('reveal-panel');
  const secretPanel = document.getElementById('secret-panel');
  const status = document.getElementById('status-message');
  const reveal = document.getElementById('reveal-button');
  const unavailable = 'This share is unavailable. It may be expired, revoked, already used, or the code may be invalid. Contact the sender for a new link or code.';

  const request = async (path, body) => fetch(path, {
    method: 'POST', cache: 'no-store', credentials: 'omit',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    referrerPolicy: 'no-referrer'
  });
  const fail = (message = unavailable) => { status.textContent = message; status.className = 'status error'; };
  if (token.length < 32 || token.length > 256) {
    form.querySelectorAll('input,button').forEach(element => { element.disabled = true; });
    fail();
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.getElementById('verify-button');
    const code = document.getElementById('verification-code');
    if (!code.value.trim()) { fail('Enter the verification code supplied by the sender.'); code.focus(); return; }
    button.disabled = true; status.textContent = 'Verifying…'; status.className = 'status';
    try {
      const response = await request('/api/vault/shares/verify', {share_token: token, verification_code: code.value.trim()});
      code.value = '';
      if (response.status === 429) { fail('Too many attempts. Wait a few minutes, then try again or contact the sender.'); return; }
      if (!response.ok) { fail(); return; }
      entry.hidden = true; revealPanel.hidden = false; status.textContent = 'Code verified. Choose reveal when you are ready.'; reveal.focus();
    } catch (_) { fail('The share service is unavailable. Try again later or contact the sender.'); }
    finally { button.disabled = false; }
  });

  reveal.addEventListener('click', async () => {
    reveal.disabled = true; status.textContent = 'Revealing…'; status.className = 'status';
    try {
      const response = await request('/api/vault/shares/reveal', {share_token: token});
      if (response.status === 429) { fail('Too many attempts. Wait a few minutes, then try again or contact the sender.'); reveal.disabled = false; return; }
      if (!response.ok) { revealPanel.hidden = true; fail(); return; }
      const payload = await response.json();
      document.getElementById('secret').textContent = payload.secret;
      revealPanel.hidden = true; secretPanel.hidden = false; status.textContent = 'Credential revealed once.';
      document.getElementById('secret').focus();
    } catch (_) { fail('The share service is unavailable. Try again later or contact the sender.'); reveal.disabled = false; }
  });
})();
