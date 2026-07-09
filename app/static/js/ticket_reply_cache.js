(function () {
  const DB_NAME = 'myportal-ticket-reply-cache';
  const DB_STORE = 'keys';
  const DB_VERSION = 1;
  const KEY_ID = 'ticket-reply-cookie-key-v1';
  const COOKIE_PREFIX = 'mp_ticket_reply_';
  const MAX_AGE_SECONDS = 7 * 24 * 60 * 60;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function base64UrlEncode(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  }

  function base64UrlDecode(value) {
    const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((value.length + 3) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function openKeyDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(DB_STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function getKey() {
    const database = await openKeyDatabase();
    const existingKey = await new Promise((resolve, reject) => {
      const transaction = database.transaction(DB_STORE, 'readonly');
      const request = transaction.objectStore(DB_STORE).get(KEY_ID);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
    if (existingKey) {
      database.close();
      return existingKey;
    }
    const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    await new Promise((resolve, reject) => {
      const transaction = database.transaction(DB_STORE, 'readwrite');
      transaction.objectStore(DB_STORE).put(key, KEY_ID);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    database.close();
    return key;
  }

  async function encryptReply(replyHtml, cookieName) {
    const key = await getKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: encoder.encode(cookieName) },
      key,
      encoder.encode(replyHtml),
    );
    return `v1.${base64UrlEncode(iv)}.${base64UrlEncode(ciphertext)}`;
  }

  async function decryptReply(payload, cookieName) {
    const parts = String(payload || '').split('.');
    if (parts.length !== 3 || parts[0] !== 'v1') {
      return '';
    }
    const key = await getKey();
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: base64UrlDecode(parts[1]), additionalData: encoder.encode(cookieName) },
      key,
      base64UrlDecode(parts[2]),
    );
    return decoder.decode(plaintext);
  }

  function cookieNameForForm(form) {
    const ticketId = form.getAttribute('data-ticket-reply-cache-ticket-id') || form.dataset.ticketId || 'unknown';
    const scope = form.action && form.action.includes('/admin/') ? 'admin' : 'portal';
    return `${COOKIE_PREFIX}${scope}_${encodeURIComponent(ticketId)}`.replace(/[^A-Za-z0-9_%.-]/g, '_');
  }

  function readCookie(name) {
    return document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))?.slice(name.length + 1) || '';
  }

  function writeCookie(name, value) {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${MAX_AGE_SECONDS}; Path=${window.location.pathname}; SameSite=Strict${secure}`;
  }

  function deleteCookie(name) {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${name}=; Max-Age=0; Path=${window.location.pathname}; SameSite=Strict${secure}`;
  }

  function getReplyHtml(surface, hidden) {
    const html = surface ? surface.innerHTML.replace(/\u200B/g, '').trim() : hidden.value.trim();
    return html || '';
  }

  function isReplyEmpty(surface, hidden) {
    const text = surface ? surface.textContent.replace(/\u200B/g, '').trim() : hidden.value.trim();
    return !text && !getReplyHtml(surface, hidden);
  }

  function applyReply(surface, hidden, html) {
    if (surface) {
      surface.innerHTML = html;
      surface.dispatchEvent(new Event('input', { bubbles: true }));
      surface.focus({ preventScroll: true });
    }
    hidden.value = html;
    hidden.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function debounce(callback, delay) {
    let timeoutId;
    return (...args) => {
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => callback(...args), delay);
    };
  }

  function initForm(form) {
    if (!window.crypto?.subtle || !window.indexedDB) {
      return;
    }
    const editor = form.querySelector('[data-rich-text-editor]');
    const surface = editor?.querySelector('[data-rich-text-content]') || null;
    const hidden = form.querySelector('[data-rich-text-value]');
    if (!(hidden instanceof HTMLTextAreaElement || hidden instanceof HTMLInputElement)) {
      return;
    }
    const cookieName = cookieNameForForm(form);
    const loadButton = document.querySelector(`[data-ticket-reply-cache-load][data-ticket-reply-cache-target="${form.id}"]`);

    const refreshButton = () => {
      if (loadButton) {
        loadButton.hidden = !readCookie(cookieName) || !isReplyEmpty(surface, hidden);
      }
    };

    const save = debounce(async () => {
      const html = getReplyHtml(surface, hidden);
      if (!html) {
        deleteCookie(cookieName);
        refreshButton();
        return;
      }
      try {
        writeCookie(cookieName, await encryptReply(html, cookieName));
      } catch (error) {
        console.warn('Unable to cache encrypted ticket reply.', error);
      }
      refreshButton();
    }, 500);

    ['input', 'blur'].forEach((eventName) => {
      (surface || hidden).addEventListener(eventName, save);
      hidden.addEventListener(eventName, save);
    });
    editor?.querySelectorAll('[data-rich-text-button]').forEach((button) => {
      button.addEventListener('click', () => window.setTimeout(save, 0));
    });

    loadButton?.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        const cached = await decryptReply(decodeURIComponent(readCookie(cookieName)), cookieName);
        if (cached && isReplyEmpty(surface, hidden)) {
          applyReply(surface, hidden, cached);
        }
      } catch (error) {
        console.warn('Unable to load encrypted ticket reply cache.', error);
        deleteCookie(cookieName);
      }
      refreshButton();
    });

    form.addEventListener('submit', () => {
      deleteCookie(cookieName);
    });

    refreshButton();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-ticket-reply-cache]').forEach(initForm);
  });
})();
