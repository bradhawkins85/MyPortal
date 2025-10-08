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

    const stock = Number(product.stock || 0);
    let stockText = 'Out of stock';
    if (stock > 5) {
      stockText = 'In stock';
    } else if (stock > 0) {
      stockText = 'Low stock';
    }
    container.appendChild(createDetailRow('Availability', stockText));

    if (product.vendor_sku) {
      container.appendChild(createDetailRow('Vendor SKU', product.vendor_sku));
    }

    if (product.description) {
      const descriptionTitle = document.createElement('h3');
      descriptionTitle.className = 'modal__subtitle';
      descriptionTitle.textContent = 'Description';
      container.appendChild(descriptionTitle);

      product.description.split(/\r?\n/).forEach((line) => {
        if (!line) {
          return;
        }
        const paragraph = document.createElement('p');
        paragraph.textContent = line;
        container.appendChild(paragraph);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    submitOnChange(container);

    const products = parseJson('shop-products-data');
    const productsById = new Map(products.map((product) => [product.id, product]));

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
      button.addEventListener('click', () => {
        const id = Number(button.getAttribute('data-product-details'));
        renderProductDetails(productsById.get(id));
        openModal(detailsModal);
      });
    });
  });
})();
