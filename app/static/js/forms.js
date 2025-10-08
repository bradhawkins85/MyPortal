(function () {
  function parseJson(elementId) {
    const element = document.getElementById(elementId);
    if (!element) {
      return [];
    }
    try {
      return JSON.parse(element.textContent || '[]');
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return [];
    }
  }

  function escapeHtml(value) {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function executeScripts(root) {
    const scripts = Array.from(root.querySelectorAll('script'));
    scripts.forEach((script) => {
      const replacement = document.createElement('script');
      Array.from(script.attributes).forEach((attr) => {
        replacement.setAttribute(attr.name, attr.value);
      });
      replacement.textContent = script.textContent || '';
      script.replaceWith(replacement);
    });
  }

  function renderForm(container, form) {
    if (!container || !form) {
      return;
    }
    if (form.embed_html) {
      container.innerHTML = form.embed_html;
      executeScripts(container);
      return;
    }
    if (form.iframe_url) {
      container.innerHTML = `
        <iframe
          class="form-frame"
          src="${escapeHtml(form.iframe_url)}"
          loading="lazy"
          title="Selected form"
          allow="publickey-credentials-get *; publickey-credentials-create *"
          referrerpolicy="strict-origin-when-cross-origin"
        ></iframe>
      `;
      return;
    }
    container.innerHTML = '<div class="empty-state"><p>Form unavailable.</p></div>';
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('forms-data');
    const container = document.querySelector('[data-form-container]');
    const buttons = Array.from(document.querySelectorAll('[data-form-switch]'));

    if (buttons.length === 0 || !container || data.length === 0) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        buttons.forEach((btn) => btn.classList.remove('is-active'));
        button.classList.add('is-active');
        const index = parseInt(button.getAttribute('data-form-index') || '-1', 10);
        if (!Number.isNaN(index) && data[index]) {
          renderForm(container, data[index]);
        } else {
          const url = button.getAttribute('data-form-url') || '';
          renderForm(container, { iframe_url: url });
        }
      });
    });
  });
})();
