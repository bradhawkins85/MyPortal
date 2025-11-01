(function () {
  function parseJson(elementId, fallback) {
    const element = document.getElementById(elementId);
    if (!element) {
      return fallback;
    }
    try {
      return JSON.parse(element.textContent || 'null') ?? fallback;
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return fallback;
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

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    submitOnChange(container);

    const categoriesCollapsible = document.getElementById('product-categories-collapsible');
    const CATEGORIES_STORAGE_KEY = 'shopAdminCategoriesExpanded';
    if (categoriesCollapsible) {
      const isEmpty = categoriesCollapsible.dataset.empty === 'true';
      try {
        const stored = window.localStorage.getItem(CATEGORIES_STORAGE_KEY);
        if (stored === 'true') {
          categoriesCollapsible.open = true;
        } else if (stored === 'false') {
          categoriesCollapsible.open = false;
        } else if (isEmpty) {
          categoriesCollapsible.open = true;
        }
      } catch (error) {
        console.warn('Unable to read saved category toggle state', error);
      }
      categoriesCollapsible.addEventListener('toggle', () => {
        try {
          window.localStorage.setItem(
            CATEGORIES_STORAGE_KEY,
            categoriesCollapsible.open ? 'true' : 'false',
          );
        } catch (error) {
          console.warn('Unable to persist category toggle state', error);
        }
      });
    }

    const products = parseJson('admin-products-data', []);
    const restrictions = parseJson('admin-product-restrictions', {});
    const productsById = new Map(products.map((product) => [product.id, product]));

    const stockFilter = document.getElementById('stock-filter');
    const showArchivedCheckbox = document.getElementById('show-archived');
    const productsTable = document.getElementById('admin-products-table');

    function applyFilters() {
      if (!productsTable) {
        return;
      }
      const rows = productsTable.querySelectorAll('tbody tr');
      const stockValue = stockFilter ? stockFilter.value : '';
      const showArchived = showArchivedCheckbox ? showArchivedCheckbox.checked : false;
      rows.forEach((row) => {
        const stock = Number(row.getAttribute('data-stock') || '0');
        const isArchived = row.getAttribute('data-archived') === 'true';
        const matchesStock =
          !stockValue ||
          (stockValue === 'in' && stock > 0) ||
          (stockValue === 'out' && stock === 0);
        const matchesArchived = showArchived || !isArchived;
        row.style.display = matchesStock && matchesArchived ? '' : 'none';
      });
    }

    if (stockFilter) {
      stockFilter.addEventListener('change', applyFilters);
    }
    if (showArchivedCheckbox) {
      showArchivedCheckbox.addEventListener('change', () => {
        const url = new URL(window.location.href);
        if (showArchivedCheckbox.checked) {
          url.searchParams.set('showArchived', '1');
        } else {
          url.searchParams.delete('showArchived');
        }
        window.history.replaceState({}, '', url.toString());
        applyFilters();
      });
    }
    applyFilters();

    const editModal = document.getElementById('product-edit-modal');
    const visibilityModal = document.getElementById('product-visibility-modal');
    const editForm = document.getElementById('product-edit-form');
    const visibilityForm = document.getElementById('product-visibility-form');
    const previewImage = document.getElementById('edit-product-preview');
    const editIdField = document.getElementById('edit-product-id');

    bindModalDismissal(editModal);
    bindModalDismissal(visibilityModal);

    container.querySelectorAll('[data-product-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = Number(button.getAttribute('data-product-edit'));
        const product = productsById.get(id);
        if (!product || !editForm || !editIdField) {
          return;
        }
        editIdField.value = String(id);
        editForm.action = `/shop/admin/product/${id}`;
        editForm.querySelector('#edit-product-name').value = product.name || '';
        editForm.querySelector('#edit-product-sku').value = product.sku || '';
        editForm.querySelector('#edit-product-vendor').value = product.vendor_sku || '';
        editForm.querySelector('#edit-product-cross-sell').value =
          product.cross_sell_product_sku || '';
        editForm.querySelector('#edit-product-upsell').value =
          product.upsell_product_sku || '';
        editForm.querySelector('#edit-product-description').value = product.description || '';
        editForm.querySelector('#edit-product-price').value = product.price != null ? product.price : '';
        editForm.querySelector('#edit-product-vip').value = product.vip_price != null ? product.vip_price : '';
        editForm.querySelector('#edit-product-stock').value = product.stock != null ? product.stock : '';
        const categorySelect = editForm.querySelector('#edit-product-category');
        if (categorySelect) {
          categorySelect.value = product.category_id || '';
        }
        if (previewImage) {
          if (product.image_url) {
            previewImage.src = product.image_url;
            previewImage.hidden = false;
          } else {
            previewImage.hidden = true;
          }
        }
        openModal(editModal);
      });
    });

    container.querySelectorAll('[data-product-visibility]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = Number(button.getAttribute('data-product-visibility'));
        const form = visibilityForm;
        if (!form) {
          return;
        }
        form.action = `/shop/admin/product/${id}/visibility`;
        const selected = (restrictions[id] || []).map((entry) => Number(entry.company_id));
        form.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
          const value = Number(checkbox.value);
          checkbox.checked = selected.includes(value);
        });
        openModal(visibilityModal);
      });
    });
  });
})();
