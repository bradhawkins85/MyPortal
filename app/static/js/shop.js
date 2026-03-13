(function () {
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

    if (product.image_url) {
      const image = document.createElement('img');
      image.src = product.image_url;
      image.alt = `${product.name} image`;
      image.className = 'modal__image';
      container.appendChild(image);
    }

    container.appendChild(createDetailRow('Price', `$${Number(product.price || 0).toFixed(2)}`));
    container.appendChild(createDetailRow('Availability', describeStockStatus(product.stock)));

    if (product.description) {
      const descriptionTitle = document.createElement('h3');
      descriptionTitle.className = 'modal__subtitle';
      descriptionTitle.textContent = 'Description';
      container.appendChild(descriptionTitle);

      const descriptionDiv = document.createElement('div');
      descriptionDiv.className = 'modal__description';
      descriptionDiv.innerHTML = product.description;
      container.appendChild(descriptionDiv);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    submitOnChange(container);
    bindStockLimitInputs(container);


    const imageModal = document.getElementById('product-image-modal');
    const detailsModal = document.getElementById('product-details-modal');
    const imagePreview = document.getElementById('product-image-preview');

    bindModalDismissal(imageModal);
    bindModalDismissal(detailsModal);

    container.querySelectorAll('[data-image-preview]').forEach((button) => {
      button.addEventListener('click', () => {
        if (!imageModal || !imagePreview) {
          return;
        }
        imagePreview.src = button.getAttribute('data-image-url') || '';
        openModal(imageModal);
      });
    });

    container.querySelectorAll('[data-product-details]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = Number(button.getAttribute('data-product-details'));
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
      });
    });
  });
})();
