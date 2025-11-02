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

  function createDetailRow(label, value) {
    const row = document.createElement('p');
    row.className = 'modal__text';
    row.textContent = `${label}: ${value}`;
    return row;
  }

  function formatCurrency(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return '$0.00';
    }
    return `$${number.toFixed(2)}`;
  }

  function getLowStockThreshold() {
    const root = document.body;
    if (!root) {
      return 5;
    }
    const value = Number(root.getAttribute('data-low-stock-threshold'));
    return Number.isFinite(value) && value > 0 ? value : 5;
  }

  function describeStock(stock) {
    const threshold = getLowStockThreshold();
    const quantity = Number(stock);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      return 'Out of stock';
    }
    if (quantity < threshold) {
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

  function renderPackageProductDetails(item) {
    const title = document.getElementById('package-product-details-title');
    const container = document.getElementById('package-product-details-body');
    if (!container) {
      return;
    }

    container.innerHTML = '';

    if (title) {
      title.textContent = item && item.product_name ? item.product_name : 'Product details';
    }

    if (!item) {
      const empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = 'Product details are unavailable.';
      container.appendChild(empty);
      return;
    }

    if (item.product_image_url) {
      const image = document.createElement('img');
      image.src = item.product_image_url;
      image.alt = `${item.product_name || 'Product'} image`;
      image.className = 'modal__image';
      container.appendChild(image);
    }

    container.appendChild(createDetailRow('Package', item.package_name || 'Package'));
    container.appendChild(createDetailRow('Included quantity', item.quantity ?? 0));
    container.appendChild(createDetailRow('SKU', item.product_sku || 'Unavailable'));

    if (item.product_vendor_sku) {
      container.appendChild(createDetailRow('Vendor SKU', item.product_vendor_sku));
    }

    container.appendChild(createDetailRow('Unit price', formatCurrency(item.product_price)));

    if (item.product_vip_price !== null && item.product_vip_price !== undefined) {
      container.appendChild(createDetailRow('VIP price', formatCurrency(item.product_vip_price)));
    }

    container.appendChild(createDetailRow('Availability', describeStock(item.product_stock)));

    const selection = item.is_substituted ? 'Alternate product in use' : 'Primary product in use';
    container.appendChild(createDetailRow('Selection', selection));

    if (item.available_stock_for_quantity !== null && item.available_stock_for_quantity !== undefined) {
      container.appendChild(
        createDetailRow(
          'Packages supported by current stock',
          Number(item.available_stock_for_quantity),
        ),
      );
    }

    if (item.is_substituted && item.primary_product) {
      const original = item.primary_product;
      const originalParts = [];
      if (original.product_name) {
        originalParts.push(original.product_name);
      }
      if (original.product_sku) {
        originalParts.push(`(${original.product_sku})`);
      }
      if (originalParts.length) {
        container.appendChild(
          createDetailRow('Original product', originalParts.join(' ')),
        );
      }
    }

    if (item.product_description) {
      const descriptionTitle = document.createElement('h3');
      descriptionTitle.className = 'modal__subtitle';
      descriptionTitle.textContent = 'Description';
      container.appendChild(descriptionTitle);

      item.product_description.split(/\r?\n/).forEach((line) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return;
        }
        const paragraph = document.createElement('p');
        paragraph.textContent = trimmed;
        container.appendChild(paragraph);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    bindStockLimitInputs(container);

    const modal = document.getElementById('package-product-details-modal');
    bindModalDismissal(modal);

    const packages = parseJson('shop-package-items-data');
    const itemsByKey = new Map();

    packages.forEach((pkg) => {
      if (!pkg || !pkg.items) {
        return;
      }
      const packageId = pkg.id != null ? String(pkg.id) : '';
      pkg.items.forEach((item) => {
        if (!item) {
          return;
        }
        const itemId = item.id != null ? String(item.id) : '';
        const resolved = item.resolved_product || {};
        const resolvedProductId =
          resolved.product_id != null ? resolved.product_id : item.product_id;
        const entry = {
          package_id: pkg.id,
          package_name: pkg.name,
          ...item,
          product_id: resolvedProductId,
          product_name: resolved.product_name || item.product_name,
          product_sku: resolved.product_sku || item.product_sku,
          product_vendor_sku:
            resolved.product_vendor_sku || item.product_vendor_sku,
          product_price:
            resolved.product_price != null
              ? resolved.product_price
              : item.product_price,
          product_vip_price:
            resolved.product_vip_price != null
              ? resolved.product_vip_price
              : item.product_vip_price,
          product_stock:
            resolved.product_stock != null
              ? resolved.product_stock
              : item.product_stock,
          product_image_url:
            resolved.product_image_url || item.product_image_url,
          product_description:
            resolved.product_description || item.product_description,
        };
        const key = `${packageId}:${itemId}`;
        itemsByKey.set(key, {
          ...entry,
        });
      });
    });

    document.querySelectorAll('[data-package-product-details]').forEach((button) => {
      button.addEventListener('click', () => {
        const key = button.getAttribute('data-package-product-details');
        const item = key ? itemsByKey.get(key) : undefined;
        renderPackageProductDetails(item);
        openModal(modal);
      });
    });
  });
})();
