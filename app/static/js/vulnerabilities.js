// Vulnerability pages (/rmm/vulnerabilities and /rmm/vulnerabilities/settings).
// Filters the software list, opens the accept-risk and mapping editors, fills
// the mapping editor from NVD's product search and reads it back in plain
// English. Everything works without JavaScript except the editors, whose
// actions are plain forms.
(function () {
  'use strict';

  function openModal(modal, trigger) {
    modal.hidden = false;
    modal.lastTrigger = trigger || null;
    const first = modal.querySelector('textarea, input:not([type="hidden"]):not([type="radio"]), select');
    if (first) first.focus();
  }

  document.querySelectorAll('[data-vuln-accept-modal], [data-vuln-mapping-modal]').forEach((modal) => {
    modal.querySelectorAll('[data-modal-close]').forEach((control) => {
      control.addEventListener('click', () => {
        modal.hidden = true;
        if (modal.lastTrigger) modal.lastTrigger.focus();
      });
    });
  });

  // Filtering the software list.
  const search = document.querySelector('[data-vuln-search]');
  const filter = document.querySelector('[data-vuln-filter]');
  function applyFilter() {
    const term = search ? search.value.trim().toLowerCase() : '';
    const mode = filter ? filter.value : '';
    let shown = 0;
    document.querySelectorAll('[data-vuln-item]').forEach((item) => {
      const worst = item.getAttribute('data-vuln-worst');
      let match = !term || (item.getAttribute('data-vuln-text') || '').includes(term);
      if (mode === 'kev') match = match && item.getAttribute('data-vuln-kev') === '1';
      if (mode === 'critical') match = match && worst === 'critical';
      if (mode === 'high') match = match && (worst === 'critical' || worst === 'high');
      item.hidden = !match;
      if (match) shown += 1;
    });
    const empty = document.querySelector('[data-vuln-no-results]');
    if (empty) empty.hidden = shown !== 0;
  }
  if (search) search.addEventListener('input', applyFilter);
  if (filter) filter.addEventListener('change', applyFilter);

  // Accepting risk.
  const acceptModal = document.querySelector('[data-vuln-accept-modal]');
  if (acceptModal) {
    document.querySelectorAll('[data-vuln-accept]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
        const cve = trigger.getAttribute('data-cve');
        acceptModal.querySelector('[data-vuln-field="vendor"]').value = trigger.getAttribute('data-vendor');
        acceptModal.querySelector('[data-vuln-field="product"]').value = trigger.getAttribute('data-product');
        acceptModal.querySelector('[data-vuln-field="cve"]').value = cve || '';
        acceptModal.querySelector('[data-vuln-accept-heading]').textContent =
          'Accept ' + (cve || 'every CVE') + ' in ' + trigger.getAttribute('data-name');
        openModal(acceptModal, trigger);
      });
    });
  }

  // Software mappings.
  const mappingModal = document.querySelector('[data-vuln-mapping-modal]');
  if (!mappingModal) return;
  const form = mappingModal.querySelector('[data-vuln-mapping-form]');
  const field = (name) => form.querySelector(`[name="${name}"]`);
  const preview = form.querySelector('[data-vuln-mapping-preview]');
  const labels = { exact: 'named exactly', starts_with: 'whose names start with', contains: 'whose names contain' };

  function describe() {
    const type = (form.querySelector('[name="match_type"]:checked') || {}).value || 'starts_with';
    const pattern = field('pattern').value.trim();
    const vendor = field('vendor').value.trim();
    const product = field('product').value.trim();
    if (!pattern || !vendor || !product) {
      preview.textContent = 'Fill in the name and NVD product.';
      return;
    }
    preview.textContent = 'Programs ' + labels[type] + ' “' + pattern + '” are checked against NVD’s CVEs for ' + vendor + ':' + product + '.';
  }
  form.addEventListener('input', describe);
  form.addEventListener('change', describe);

  document.querySelectorAll('[data-vuln-mapping-open]').forEach((trigger) => {
    trigger.addEventListener('click', () => {
      let data = {};
      try { data = JSON.parse(trigger.getAttribute('data-mapping') || '{}'); } catch (error) { data = {}; }
      form.reset();
      form.setAttribute('action', data.id ? '/rmm/vulnerabilities/settings/mappings/' + data.id : '/rmm/vulnerabilities/settings/mappings');
      mappingModal.querySelector('[data-vuln-mapping-heading]').textContent = data.id ? 'Edit mapping' : 'Add mapping';
      const type = form.querySelector(`[name="match_type"][value="${data.match_type || 'starts_with'}"]`);
      if (type) type.checked = true;
      field('pattern').value = data.pattern || '';
      field('vendor').value = data.vendor || '';
      field('product').value = data.product || '';
      field('is_enabled').checked = data.is_enabled !== false;
      form.querySelector('[data-vuln-cpe-query]').value = data.search ? data.search.split(/\s+/).slice(0, 2).join(' ') : '';
      form.querySelector('[data-vuln-cpe-results]').innerHTML = '';
      form.querySelector('[data-vuln-cpe-status]').textContent = '';
      describe();
      openModal(mappingModal, trigger);
    });
  });

  const query = form.querySelector('[data-vuln-cpe-query]');
  const results = form.querySelector('[data-vuln-cpe-results]');
  const status = form.querySelector('[data-vuln-cpe-status]');

  async function searchCpe() {
    const term = query.value.trim();
    results.innerHTML = '';
    if (term.length < 2) {
      status.textContent = 'Type at least two letters.';
      return;
    }
    status.textContent = 'Searching NVD…';
    try {
      const response = await fetch('/api/rmm/vulnerabilities/cpe-search?q=' + encodeURIComponent(term), {
        credentials: 'same-origin', headers: { Accept: 'application/json' },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'NVD did not answer.');
      status.textContent = data.length ? 'Choose the matching product.' : 'No products found. Try a shorter or different word.';
      data.forEach((item) => {
        const li = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'vuln-cpe-results__item';
        const strong = document.createElement('strong');
        strong.textContent = item.vendor + ':' + item.product;
        const title = document.createElement('span');
        title.textContent = item.title || '';
        button.append(strong, title);
        button.addEventListener('click', () => {
          field('vendor').value = item.vendor;
          field('product').value = item.product;
          describe();
          status.textContent = 'Using ' + item.vendor + ':' + item.product + '.';
        });
        li.append(button);
        results.append(li);
      });
    } catch (error) {
      status.textContent = error.message;
    }
  }
  form.querySelector('[data-vuln-cpe-search]').addEventListener('click', searchCpe);
  query.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      searchCpe();
    }
  });
})();
