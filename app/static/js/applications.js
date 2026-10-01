(() => {
  // Destructive forms ask before submitting.
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.getAttribute('data-confirm') || 'Are you sure?')) event.preventDefault();
    });
  });

  // The product key is fetched only on request so it is never in the page
  // source; each reveal is recorded in the audit log by the API.
  const reveal = document.querySelector('[data-product-key-reveal]');
  if (!reveal) return;
  const value = document.querySelector('[data-product-key-value]');
  const copy = document.querySelector('[data-product-key-copy]');
  const status = document.querySelector('[data-product-key-status]');
  let revealed = null;
  const announce = (message) => { if (status) status.textContent = message; };

  reveal.addEventListener('click', async () => {
    if (revealed !== null) {
      revealed = null;
      value.textContent = '••••••••••••';
      value.classList.add('text-muted');
      reveal.textContent = 'Reveal';
      copy.hidden = true;
      announce('Product key hidden.');
      return;
    }
    reveal.disabled = true;
    try {
      const response = await fetch(reveal.dataset.url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'The product key could not be loaded.');
      revealed = body.product_key;
      value.textContent = revealed;
      value.classList.remove('text-muted');
      reveal.textContent = 'Hide';
      copy.hidden = false;
      announce('Product key shown.');
    } catch (error) {
      announce(error.message);
      window.alert(error.message);
    } finally {
      reveal.disabled = false;
    }
  });

  copy.addEventListener('click', async () => {
    if (revealed === null) return;
    try {
      await navigator.clipboard.writeText(revealed);
      announce('Product key copied.');
      copy.textContent = 'Copied';
      setTimeout(() => { copy.textContent = 'Copy'; }, 2000);
    } catch {
      announce('Copy failed; select the key and copy it manually.');
    }
  });
})();
