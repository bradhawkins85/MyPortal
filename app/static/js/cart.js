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

  function appendDetail(container, label, value) {
    if (!container || value === undefined || value === null || value === '') {
      return;
    }
    const paragraph = document.createElement('p');
    paragraph.className = 'modal__text';
    const strong = document.createElement('strong');
    strong.textContent = `${label}: `;
    paragraph.append(strong);
    paragraph.append(document.createTextNode(String(value)));
    container.append(paragraph);
  }

  function renderProductDetails(container, product) {
    if (!container) {
      return;
    }
    container.innerHTML = '';

    if (!product) {
      const empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = 'Product details are unavailable.';
      container.append(empty);
      return;
    }

    if (product.image_url) {
      const image = document.createElement('img');
      image.src = product.image_url;
      image.alt = `${product.name || 'Product'} image`;
      image.className = 'modal__image';
      container.append(image);
    }

    appendDetail(container, 'SKU', product.sku);
    appendDetail(container, 'Vendor SKU', product.vendor_sku);
    appendDetail(container, 'Unit price', `$${product.unit_price}`);
    appendDetail(container, 'Quantity in cart', product.quantity);
    appendDetail(container, 'Line total', `$${product.line_total}`);

    if (product.description) {
      const descriptionTitle = document.createElement('h3');
      descriptionTitle.className = 'modal__subtitle';
      descriptionTitle.textContent = 'Description';
      container.append(descriptionTitle);

      String(product.description)
        .split(/\r?\n/)
        .filter((line) => line.trim().length > 0)
        .forEach((line) => {
          const paragraph = document.createElement('p');
          paragraph.textContent = line;
          container.append(paragraph);
        });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    bindStockLimitInputs(container);

    const modal = document.getElementById('cart-product-details-modal');
    const modalTitle = document.getElementById('cart-product-details-title');
    const modalBody = document.getElementById('cart-product-details-body');
    if (!modal || !modalBody) {
      return;
    }

    const products = parseJson('cart-items-data');
    if (!products.length) {
      return;
    }

    const itemsById = new Map();
    products.forEach((product) => {
      if (product && product.product_id !== undefined) {
        itemsById.set(Number(product.product_id), product);
      }
    });

    if (!itemsById.size) {
      return;
    }

    bindModalDismissal(modal);

    document.querySelectorAll('[data-cart-product-modal]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = Number(button.getAttribute('data-cart-product-modal'));
        const product = itemsById.get(id);
        if (modalTitle) {
          modalTitle.textContent = product?.name || 'Product details';
        }
        renderProductDetails(modalBody, product);
        openModal(modal);
      });
    });
  });
})();
