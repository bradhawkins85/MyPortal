(function () {
  'use strict';

  const form = document.querySelector('[data-onboarding-form]');
  if (!form) {
    return;
  }
  const sitesContainer = form.querySelector('[data-sites]');
  const template = document.querySelector('[data-site-template]');
  const addButton = form.querySelector('[data-add-site]');
  const maxSites = parseInt(form.dataset.maxSites || '20', 10);
  const billingSame = form.querySelector('[data-billing-same]');
  const billingFields = form.querySelector('[data-billing-fields]');

  function sites() {
    return Array.from(sitesContainer.querySelectorAll('[data-site]'));
  }

  function nextIndex() {
    const used = sites().map((site) => {
      const input = site.querySelector('input[name="site_index"]');
      return input ? parseInt(input.value, 10) : -1;
    });
    return used.length ? Math.max(...used) + 1 : 0;
  }

  function refresh() {
    const all = sites();
    all.forEach((site, position) => {
      const number = site.querySelector('[data-site-number]');
      if (number) {
        number.textContent = String(position + 1);
      }
      const remove = site.querySelector('[data-remove-site]');
      if (remove) {
        remove.hidden = all.length <= 1;
      }
    });
    if (addButton) {
      addButton.disabled = all.length >= maxSites;
    }
  }

  function addSite() {
    if (!template || sites().length >= maxSites) {
      return;
    }
    const index = String(nextIndex());
    const markup = template.innerHTML
      .split('__INDEX__').join(index)
      .split('__NUMBER__').join(String(sites().length + 1));
    const wrapper = document.createElement('div');
    wrapper.innerHTML = markup.trim();
    const site = wrapper.firstElementChild;
    sitesContainer.appendChild(site);
    refresh();
    const first = site.querySelector('input:not([type="hidden"])');
    if (first) {
      first.focus();
    }
  }

  function toggleBilling() {
    if (!billingSame || !billingFields) {
      return;
    }
    const same = billingSame.checked;
    billingFields.hidden = same;
    billingFields.querySelectorAll('input').forEach((input) => {
      input.disabled = same;
    });
  }

  if (addButton) {
    addButton.addEventListener('click', addSite);
  }
  sitesContainer.addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-site]');
    if (!button || sites().length <= 1) {
      return;
    }
    button.closest('[data-site]').remove();
    refresh();
  });
  if (billingSame) {
    billingSame.addEventListener('change', toggleBilling);
  }
  refresh();
  toggleBilling();
})();
