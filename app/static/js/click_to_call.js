(function () {
  const PHONE_PATTERN = /(?:\+?\d[\d\s().-]{5,}\d)/g;
  const SKIPPED_TAGS = new Set(['A', 'BUTTON', 'INPUT', 'OPTION', 'SCRIPT', 'STYLE', 'TEXTAREA']);
  let enabled = false;

  function toast(message, variant) {
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(message, { variant });
    } else {
      window.alert(message);
    }
  }

  function validNumber(text) {
    const normalized = text.replace(/[^0-9+]/g, '');
    const digits = normalized.replace(/\D/g, '');
    return digits.length >= 7 && digits.length <= 15 ? normalized : null;
  }

  async function call(number) {
    if (!window.confirm(`Call ${number}?`)) {
      return;
    }
    try {
      const response = await fetch('/api/click-to-call/call', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: number }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || 'Unable to start the call.');
      }
      toast(`Calling ${number}.`, 'success');
    } catch (error) {
      toast(error.message || 'Unable to start the call.', 'error');
    }
  }

  function linkify(root) {
    if (!enabled || !root || root.nodeType !== Node.ELEMENT_NODE) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const parent = node.parentElement;
      if (!parent || SKIPPED_TAGS.has(parent.tagName) || parent.closest('[contenteditable], .click-to-call')) return;
      const text = node.nodeValue || '';
      PHONE_PATTERN.lastIndex = 0;
      let match;
      let offset = 0;
      let changed = false;
      const fragment = document.createDocumentFragment();
      while ((match = PHONE_PATTERN.exec(text))) {
        const number = validNumber(match[0]);
        if (!number) continue;
        fragment.append(document.createTextNode(text.slice(offset, match.index)));
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'click-to-call';
        button.textContent = match[0];
        button.title = `Call ${number}`;
        button.addEventListener('click', () => call(number));
        fragment.append(button);
        offset = match.index + match[0].length;
        changed = true;
      }
      if (changed) {
        fragment.append(document.createTextNode(text.slice(offset)));
        node.replaceWith(fragment);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const response = await fetch('/api/click-to-call/settings', { credentials: 'same-origin' });
      if (!response.ok) return;
      enabled = Boolean((await response.json()).enabled);
      if (!enabled) return;
      linkify(document.body);
      new MutationObserver((mutations) => mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) linkify(node);
        });
      })).observe(document.body, { childList: true, subtree: true });
    } catch (error) {
      // Click-to-call is an enhancement; pages remain usable if settings cannot load.
    }
  });
})();
