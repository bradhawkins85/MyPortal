(function () {
  const ALLOWED_LINK_PROTOCOLS = /^(https?:|mailto:|tel:)/i;

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

    if (hidden.value) {
      surface.innerHTML = hidden.value;
    } else {
      surface.innerHTML = '';
    }
    updateSurfaceState(surface, hidden);

    const form = editor.closest('form');

    surface.addEventListener('input', () => {
      updateSurfaceState(surface, hidden);
    });
    surface.addEventListener('blur', () => {
      updateSurfaceState(surface, hidden);
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
