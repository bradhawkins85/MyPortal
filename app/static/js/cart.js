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
    const products = parseJson('cart-items-data');
    if (!products.length) {
      return;
    }

    const modal = document.getElementById('cart-product-details-modal');
    const modalTitle = document.getElementById('cart-product-details-title');
    const modalBody = document.getElementById('cart-product-details-body');
    if (!modal || !modalBody) {
      return;
    }

    const itemsById = new Map();
    products.forEach((product) => {
      if (product && product.product_id !== undefined) {
        itemsById.set(Number(product.product_id), product);
      }
    });

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
