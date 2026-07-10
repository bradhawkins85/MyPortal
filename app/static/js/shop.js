(function () {
  const SHOP_SEARCH_REFOCUS_KEY = 'shop.search.refocus';

  function submitOnChange(container) {
    container.querySelectorAll('[data-submit-on-change]').forEach((input) => {
      input.addEventListener('change', () => {
        const form = input.closest('form');
        if (form) {
          form.submit();
        }
      });
    });
  }

  function getLowStockThreshold() {
    const root = document.body;
    if (!root) {
      return 5;
    }
    const value = Number(root.getAttribute('data-low-stock-threshold'));
    return Number.isFinite(value) && value > 0 ? value : 5;
  }

  function describeStockStatus(quantity) {
    const threshold = getLowStockThreshold();
    const value = Number(quantity);
    if (!Number.isFinite(value) || value <= 0) {
      return 'Out of stock';
    }
    if (value < threshold) {
      return 'Low stock';
    }
    return 'In stock';
  }

  function bindStockLimitInputs(container) {
    container.querySelectorAll('[data-stock-limit]').forEach((input) => {
      if (!(input instanceof HTMLInputElement)) {
        return;
      }

      const limitAttr = Number(input.getAttribute('data-stock-limit'));
      const currentValue = Number(input.value);
      const fallbackLimit = Number.isFinite(currentValue) ? currentValue : 0;
      const limit = Number.isFinite(limitAttr) ? limitAttr : fallbackLimit;
      const effectiveLimit = limit > 0 ? limit : fallbackLimit;
      const minAttr = Number(input.getAttribute('min'));
      const min = Number.isFinite(minAttr) ? minAttr : 0;
      let previous = input.value;

      input.addEventListener('focus', () => {
        previous = input.value;
        input.setCustomValidity('');
      });

      input.addEventListener('input', () => {
        const value = Number(input.value);
        if (!Number.isFinite(value)) {
          return;
        }

        if (value > effectiveLimit) {
          input.value = previous || String(effectiveLimit || '');
          input.setCustomValidity('Cannot exceed available stock.');
          input.reportValidity();
          return;
        }

        if (value < min) {
          input.setCustomValidity(min > 0 ? 'Quantity must be at least 1.' : 'Quantity cannot be negative.');
          input.reportValidity();
          input.value = previous || String(Math.max(min, 0));
          return;
        }

        previous = input.value;
        input.setCustomValidity('');
      });

      input.addEventListener('blur', () => {
        if (!input.value) {
          input.value = previous || String(Math.max(min, 0));
        }
      });

      input.addEventListener('invalid', (event) => {
        event.preventDefault();
        if (Number(input.value) > effectiveLimit) {
          input.setCustomValidity('Cannot exceed available stock.');
        } else {
          input.setCustomValidity('Enter a valid quantity.');
        }
        input.reportValidity();
      });
    });
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.classList.add('is-visible');
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove('is-visible');
    modal.hidden = true;
  }

  function bindModalDismissal(modal) {
    if (!modal) {
      return;
    }
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.hasAttribute('data-modal-close')) {
        closeModal(modal);
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        closeModal(modal);
      }
    });
  }

  function createDetailRow(label, value) {
    const row = document.createElement('p');
    row.className = 'modal__text';
    row.textContent = `${label}: ${value}`;
    return row;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    const input = document.querySelector('input[name="_csrf"]');
    return input ? input.value : '';
  }

  function formatPrice(value) {
    return `$${Number(value || 0).toFixed(2)}`;
  }

  async function fetchProductDetails(productId) {
    const response = await fetch(`/api/shop/products/${productId}`, {
      headers: {
        Accept: 'application/json',
      },
      credentials: 'same-origin',
    });
    if (!response.ok) {
      throw new Error(`Unable to load product details (${response.status})`);
    }
    return response.json();
  }

  function buildModalAddToCart(product) {
    const stock = Number(product && product.stock);
    const modal = document.getElementById('product-details-modal');
    const cartAllowed = modal && modal.getAttribute('data-cart-allowed') === 'true';
    if (!cartAllowed || !Number.isFinite(stock) || stock <= 0) {
      const status = document.createElement('span');
      status.className = 'badge badge--muted';
      status.textContent = stock <= 0 ? 'Out of stock' : 'Cart unavailable';
      return status;
    }

    const form = document.createElement('form');
    form.action = '/cart/add';
    form.method = 'post';
    form.className = 'inline-form product-details-modal__cart';

    const csrfToken = getCsrfToken();
    if (csrfToken) {
      const csrf = document.createElement('input');
      csrf.type = 'hidden';
      csrf.name = '_csrf';
      csrf.value = csrfToken;
      form.appendChild(csrf);
    }

    const productId = document.createElement('input');
    productId.type = 'hidden';
    productId.name = 'productId';
    productId.value = String(product.id);
    form.appendChild(productId);

    const label = document.createElement('label');
    label.className = 'visually-hidden';
    label.setAttribute('for', `modal-product-quantity-${product.id}`);
    label.textContent = `Quantity for ${product.name}`;
    form.appendChild(label);

    const quantity = document.createElement('input');
    quantity.className = 'form-input form-input--sm';
    quantity.id = `modal-product-quantity-${product.id}`;
    quantity.type = 'number';
    quantity.name = 'quantity';
    quantity.min = '1';
    quantity.max = String(stock);
    quantity.value = '1';
    quantity.setAttribute('data-stock-limit', String(stock));
    form.appendChild(quantity);

    const button = document.createElement('button');
    button.type = 'submit';
    button.className = 'button button--primary';
    button.textContent = 'Add to cart';
    form.appendChild(button);
    bindStockLimitInputs(form);
    return form;
  }

  function buildRecommendationCard(item) {
    const card = document.createElement('article');
    card.className = 'product-details-recommendation-card';

    if (item.image_url) {
      const image = document.createElement('img');
      image.src = item.image_url;
      image.alt = '';
      image.loading = 'lazy';
      image.className = 'product-details-recommendation-card__image';
      card.appendChild(image);
    }

    const body = document.createElement('div');
    body.className = 'product-details-recommendation-card__body';

    const name = document.createElement('strong');
    name.textContent = item.name || 'Recommended product';
    body.appendChild(name);

    if (item.sku) {
      const sku = document.createElement('span');
      sku.className = 'text-muted';
      sku.textContent = item.sku;
      body.appendChild(sku);
    }

    if (item.price !== undefined && item.price !== null) {
      const price = document.createElement('span');
      price.className = 'product-details-recommendation-card__price';
      price.textContent = formatPrice(item.price);
      body.appendChild(price);
    }

    const detailsButton = document.createElement('button');
    detailsButton.type = 'button';
    detailsButton.className = 'button button--ghost button--sm';
    detailsButton.setAttribute('data-product-details', String(item.id));
    detailsButton.textContent = 'View';
    body.appendChild(detailsButton);

    card.appendChild(body);
    return card;
  }

  function buildRecommendationSection(title, items) {
    if (!items || items.length === 0) {
      return null;
    }

    const section = document.createElement('section');
    section.className = 'product-details-modal__section product-details-modal__recommendations';

    const heading = document.createElement('h3');
    heading.className = 'modal__subtitle';
    heading.textContent = title;
    section.appendChild(heading);

    const list = document.createElement('div');
    list.className = 'product-details-modal__recommendation-list';
    items.forEach((item) => list.appendChild(buildRecommendationCard(item)));
    section.appendChild(list);
    return section;
  }

  function renderProductDetails(product) {
    const container = document.getElementById('product-details-body');
    if (!container) {
      return;
    }
    container.innerHTML = '';
    if (!product) {
      const empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = 'Product details are unavailable.';
      container.appendChild(empty);
      return;
    }

    const layout = document.createElement('div');
    layout.className = 'product-details-modal';

    const hero = document.createElement('section');
    hero.className = 'product-details-modal__hero product-details-modal__section';

    const media = document.createElement('div');
    media.className = 'product-details-modal__media';
    if (product.image_url) {
      const image = document.createElement('img');
      image.src = product.image_url;
      image.alt = `${product.name} image`;
      image.className = 'modal__image product-details-modal__image';
      media.appendChild(image);
    } else {
      const placeholder = document.createElement('span');
      placeholder.className = 'shop-product-card__placeholder product-details-modal__placeholder';
      placeholder.textContent = '🛍';
      media.appendChild(placeholder);
    }
    hero.appendChild(media);

    const summary = document.createElement('div');
    summary.className = 'product-details-modal__summary';
    const title = document.createElement('h2');
    title.className = 'modal__title product-details-modal__title';
    title.textContent = product.name || 'Product details';
    summary.appendChild(title);
    summary.appendChild(createDetailRow('Price', formatPrice(product.price)));
    summary.appendChild(createDetailRow('Availability', describeStockStatus(product.stock)));
    const actions = document.createElement('div');
    actions.className = 'product-details-modal__actions';
    actions.appendChild(buildModalAddToCart(product));
    summary.appendChild(actions);
    hero.appendChild(summary);
    layout.appendChild(hero);

    const details = document.createElement('section');
    details.className = 'product-details-modal__section product-details-modal__details';
    const detailsTitle = document.createElement('h3');
    detailsTitle.className = 'modal__subtitle';
    detailsTitle.textContent = 'Product details';
    details.appendChild(detailsTitle);
    const descriptionDiv = document.createElement('div');
    descriptionDiv.className = 'modal__description rich-text-viewer';
    const descriptionHtml = typeof product.description_html === 'string' ? product.description_html.trim() : '';
    const descriptionText = typeof product.description === 'string' ? product.description.trim() : '';
    if (descriptionHtml) {
      descriptionDiv.innerHTML = descriptionHtml;
    } else {
      descriptionDiv.textContent = descriptionText || 'No product details are available yet.';
    }
    details.appendChild(descriptionDiv);
    layout.appendChild(details);

    const recommendationSections = [
      buildRecommendationSection('Goes well with this', product.cross_sell_products || []),
      buildRecommendationSection('Need a little more?', product.upsell_products || []),
    ].filter(Boolean);
    if (recommendationSections.length > 0) {
      const related = document.createElement('div');
      related.className = 'product-details-modal__related-grid';
      if (recommendationSections.length === 1) {
        related.classList.add('product-details-modal__related-grid--single');
      }
      recommendationSections.forEach((section) => related.appendChild(section));
      layout.appendChild(related);
    }

    container.appendChild(layout);
  }

  function bindShopSearch(container) {
    const searchInput = container.querySelector('[data-shop-search]');
    if (!searchInput) {
      return;
    }
    const form = searchInput.closest('form');
    if (!form) {
      return;
    }
    let debounceTimer = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        try {
          sessionStorage.setItem(SHOP_SEARCH_REFOCUS_KEY, '1');
        } catch (_error) {
        }
        form.submit();
      }, 400);
    });
  }


  function findSafeTitleTruncation(value, limit, minVisibleChars) {
    if (typeof value !== 'string' || value.length <= limit) {
      return value;
    }
    const minimum = Number.isFinite(minVisibleChars) && minVisibleChars > 0 ? minVisibleChars : 1;
    const effectiveLimit = Math.max(limit, minimum + 1);
    const commaIndex = value.indexOf(',');
    const dashIndex = value.indexOf('-');
    const candidates = [commaIndex, dashIndex].filter((index) => index >= minimum);
    if (candidates.length > 0) {
      const safeIndex = Math.min(...candidates);
      return `${value.slice(0, safeIndex).trim()}…`;
    }
    return `${value.slice(0, Math.max(effectiveLimit - 1, minimum)).trim()}…`;
  }

  function truncateSafeProductTitles(container) {
    container.querySelectorAll('[data-shop-safe-title]').forEach((title) => {
      const fullTitle = title.getAttribute('title') || title.textContent || '';
      const minVisibleChars = Number(title.getAttribute('data-min-visible-chars'));
      title.textContent = findSafeTitleTruncation(fullTitle.trim(), 58, minVisibleChars);
    });
  }

  function restoreShopSearchFocus(container) {
    const searchInput = container.querySelector('[data-shop-search]');
    if (!searchInput) {
      return;
    }

    let shouldRefocus = false;
    try {
      shouldRefocus = sessionStorage.getItem(SHOP_SEARCH_REFOCUS_KEY) === '1';
      if (shouldRefocus) {
        sessionStorage.removeItem(SHOP_SEARCH_REFOCUS_KEY);
      }
    } catch (_error) {
      return;
    }

    if (!shouldRefocus) {
      return;
    }

    requestAnimationFrame(() => {
      searchInput.focus({ preventScroll: true });
      const cursorPosition = searchInput.value.length;
      searchInput.setSelectionRange(cursorPosition, cursorPosition);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    submitOnChange(container);
    bindStockLimitInputs(container);
    bindShopSearch(container);
    restoreShopSearchFocus(container);
    truncateSafeProductTitles(container);


    const detailsModal = document.getElementById('product-details-modal');
    const refreshForm = detailsModal
      ? detailsModal.querySelector('[data-product-refresh-form]')
      : null;

    bindModalDismissal(detailsModal);

    async function openProductDetails(id) {
      if (refreshForm) {
        if (Number.isFinite(id) && id > 0) {
          refreshForm.action = `/shop/admin/product/${id}/refresh-description`;
          refreshForm.hidden = false;
        } else {
          refreshForm.removeAttribute('action');
          refreshForm.hidden = true;
        }
      }
      renderProductDetails(null);
      openModal(detailsModal);
      if (!Number.isFinite(id) || id <= 0) {
        return;
      }
      try {
        const product = await fetchProductDetails(id);
        renderProductDetails(product);
      } catch (error) {
        console.error('Unable to load product details', error);
        renderProductDetails(null);
      }
    }

    container.addEventListener('click', (event) => {
      const button = event.target.closest('[data-product-details]');
      if (!button) {
        return;
      }
      event.preventDefault();
      openProductDetails(Number(button.getAttribute('data-product-details')));
    });
  });
})();
