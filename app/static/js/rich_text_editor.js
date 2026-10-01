(function () {
  const ALLOWED_LINK_PROTOCOLS = /^(https?:|mailto:|tel:)/i;
  const ALLOWED_IMAGE_PROTOCOLS = /^(https?:|data:image\/)/i;

  function sanitiseLinkUrl(url) {
    if (!url) {
      return null;
    }
    const trimmed = url.trim();
    if (!trimmed) {
      return null;
    }
    if (ALLOWED_LINK_PROTOCOLS.test(trimmed)) {
      return trimmed;
    }
    const normalised = trimmed.replace(/^\/*/, '');
    if (!normalised) {
      return null;
    }
    return `https://${normalised}`;
  }

  function setActiveLinkAttributes(selection) {
    if (!selection || selection.rangeCount === 0) {
      return;
    }
    const range = selection.getRangeAt(0);
    let container = range.commonAncestorContainer;
    if (container.nodeType === Node.TEXT_NODE) {
      container = container.parentElement;
    }
    if (!(container instanceof Element)) {
      return;
    }
    const anchor = container.closest('a');
    if (anchor instanceof HTMLAnchorElement) {
      if (!anchor.getAttribute('target')) {
        anchor.setAttribute('target', '_blank');
      }
      const rel = anchor.getAttribute('rel') || '';
      const relTokens = new Set(
        rel
          .split(/\s+/)
          .map((token) => token.trim().toLowerCase())
          .filter(Boolean),
      );
      relTokens.add('noopener');
      relTokens.add('noreferrer');
      anchor.setAttribute('rel', Array.from(relTokens).join(' '));
    }
  }

  function getEditorHtml(surface) {
    const html = surface.innerHTML.replace(/\u200B/g, '').trim();
    return html.length > 0 ? html : '';
  }

  function sanitizeEditorHtml(value) {
    const source = typeof value === 'string' ? value : '';
    if (!source) {
      return '';
    }
    if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
      return window.DOMPurify.sanitize(source, { USE_PROFILES: { html: true } });
    }
    return source
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function sanitizePastedHtml(value) {
    const source = typeof value === 'string' ? value : '';
    if (!source || !window.DOMPurify || typeof window.DOMPurify.sanitize !== 'function') {
      return '';
    }
    return window.DOMPurify.sanitize(source, { USE_PROFILES: { html: true } });
  }

  function showEditorMessage(editor, message, variant) {
    const feedback = editor.querySelector('[data-rich-text-feedback]');
    if (!(feedback instanceof HTMLElement)) {
      return;
    }
    feedback.hidden = !message;
    feedback.textContent = message || '';
    feedback.classList.toggle('text-danger', !!message && variant === 'error');
    feedback.classList.toggle('text-muted', !!message && variant !== 'error');
  }

  function isSupportedImageSource(src) {
    return ALLOWED_IMAGE_PROTOCOLS.test(String(src || '').trim());
  }

  function insertHtmlAtSelection(surface, html) {
    surface.focus({ preventScroll: true });
    const selection = window.getSelection();
    const range = selection && selection.rangeCount > 0
      ? selection.getRangeAt(0)
      : (() => {
          const fallbackRange = document.createRange();
          fallbackRange.selectNodeContents(surface);
          fallbackRange.collapse(false);
          return fallbackRange;
        })();
    range.deleteContents();
    const fragment = range.createContextualFragment(html);
    range.insertNode(fragment);
    if (selection) {
      selection.removeAllRanges();
      selection.addRange(range);
      selection.collapseToEnd();
    }
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
      reader.onerror = () => reject(new Error('Failed to read pasted image.'));
      reader.readAsDataURL(file);
    });
  }

  async function handlePaste(surface, hidden, editor, event) {
    const clipboard = event.clipboardData;
    if (!clipboard) {
      return;
    }
    const imageFiles = Array.from(clipboard.items || [])
      .filter((item) => item.kind === 'file' && item.type && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file) => file instanceof File);

    const html = clipboard.getData('text/html');
    if (html) {
      event.preventDefault();
      const sanitizedHtml = sanitizePastedHtml(html);
      const wrapper = document.createElement('div');
      wrapper.innerHTML = sanitizedHtml;
      let removedImages = 0;
      wrapper.querySelectorAll('img').forEach((image) => {
        const src = image.getAttribute('src') || '';
        if (isSupportedImageSource(src)) {
          return;
        }
        image.remove();
        removedImages += 1;
      });
      insertHtmlAtSelection(surface, wrapper.innerHTML);
      updateSurfaceState(surface, hidden);
      if (removedImages > 0) {
        showEditorMessage(editor, 'Some pasted images could not be imported automatically. Use an HTTPS image URL or paste the image by itself.', 'error');
      } else if (imageFiles.length > 0) {
        showEditorMessage(editor, 'Pasted signature formatting was imported. Inline clipboard images could not be matched automatically.', 'error');
      } else {
        showEditorMessage(editor, '', 'info');
      }
      return;
    }

    if (imageFiles.length > 0) {
      event.preventDefault();
      try {
        const dataUrls = await Promise.all(imageFiles.map((file) => readFileAsDataUrl(file)));
        insertHtmlAtSelection(surface, dataUrls.map((src) => `<img src="${src}" alt="Pasted image" />`).join(''));
        updateSurfaceState(surface, hidden);
        showEditorMessage(editor, 'Pasted images were imported into the signature.', 'info');
      } catch (error) {
        showEditorMessage(editor, 'A pasted image could not be imported. Paste the image again or use an HTTPS image URL.', 'error');
      }
    }
  }

  function updateSurfaceState(surface, hidden) {
    const html = getEditorHtml(surface);
    hidden.value = html;
    const text = surface.textContent ? surface.textContent.replace(/\u200B/g, '').trim() : '';
    if (text || surface.querySelector('img, table, code, pre, blockquote, ul, ol')) {
      surface.classList.remove('rich-text-editor__surface--empty');
    } else {
      surface.classList.add('rich-text-editor__surface--empty');
      if (!html) {
        surface.innerHTML = '';
      }
    }
  }

  function handleCommand(surface, command, value) {
    surface.focus({ preventScroll: true });
    if (command === 'link') {
      const selection = window.getSelection();
      const existing = selection && selection.rangeCount > 0 ? selection.toString() : '';
      const url = window.prompt('Enter link URL', existing ? 'https://' : '');
      const sanitised = sanitiseLinkUrl(url);
      if (!sanitised) {
        document.execCommand('unlink');
        return;
      }
      document.execCommand('createLink', false, sanitised);
      setActiveLinkAttributes(selection);
      return;
    }
    if (command === 'image') {
      const url = window.prompt('Enter image URL', 'https://');
      const sanitised = sanitiseLinkUrl(url);
      if (!sanitised) {
        return;
      }
      document.execCommand('insertImage', false, sanitised);
      return;
    }
    if (command === 'table') {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0) {
        return;
      }
      const range = selection.getRangeAt(0);
      const fragment = range.createContextualFragment(
        '<table role="presentation"><tbody><tr><td>Column 1</td><td>Column 2</td></tr><tr><td>Value 1</td><td>Value 2</td></tr></tbody></table><p></p>',
      );
      range.deleteContents();
      range.insertNode(fragment);
      return;
    }
    if (command === 'removeFormat') {
      document.execCommand('removeFormat');
      document.execCommand('unlink');
      return;
    }
    document.execCommand(command, false, value || null);
  }

  function initEditor(editor) {
    const surface = editor.querySelector('[data-rich-text-content]');
    const hidden = editor.querySelector('[data-rich-text-value]');
    if (!(surface instanceof HTMLElement) || !(hidden instanceof HTMLInputElement || hidden instanceof HTMLTextAreaElement)) {
      return;
    }

    editor.classList.add('rich-text-editor--enhanced');

    surface.innerHTML = sanitizeEditorHtml(hidden.value);

    updateSurfaceState(surface, hidden);

    const form = editor.closest('form');

    surface.addEventListener('input', () => {
      updateSurfaceState(surface, hidden);
    });
    surface.addEventListener('blur', () => {
      updateSurfaceState(surface, hidden);
    });
    surface.addEventListener('paste', (event) => {
      handlePaste(surface, hidden, editor, event).catch(() => {
        showEditorMessage(editor, 'Pasted content could not be imported safely.', 'error');
      });
    });

    editor.querySelectorAll('[data-rich-text-button]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        const command = button.getAttribute('data-command');
        if (!command) {
          return;
        }
        const value = button.getAttribute('data-command-value');
        handleCommand(surface, command, value || undefined);
        updateSurfaceState(surface, hidden);
      });
    });

    surface.addEventListener('keydown', (event) => {
      if (event.key === 'Tab') {
        event.preventDefault();
        document.execCommand('insertText', false, '\t');
        updateSurfaceState(surface, hidden);
      }
    });

    if (form instanceof HTMLFormElement) {
      form.addEventListener('submit', () => {
        updateSurfaceState(surface, hidden);
      });
      form.addEventListener('reset', () => {
        window.setTimeout(() => {
          surface.innerHTML = '';
          updateSurfaceState(surface, hidden);
        }, 0);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-rich-text-editor]').forEach((editor) => {
      initEditor(editor);
    });
  });
})();
