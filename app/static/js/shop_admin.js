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

    const createCategorySelect = document.getElementById('product-category');
    const createCrossSelect = document.getElementById('product-cross-sell');
    const createUpsellSelect = document.getElementById('product-upsell');

    function updateRecommendationSelect(select, categoryId, selectedIds, excludeProductId) {
      if (!select) {
        return;
      }
      const numericCategory = Number(categoryId || 0);
      const selectedSet = new Set((selectedIds || []).map((id) => Number(id)));
      select.innerHTML = '';

      if (!Number.isFinite(numericCategory) || numericCategory <= 0) {
        const option = document.createElement('option');
        option.textContent = 'Select a category to choose products';
        option.disabled = true;
        option.selected = true;
        select.appendChild(option);
        select.disabled = true;
        return;
      }

      const available = products
        .filter((product) => {
          if (Number(product.id) === Number(excludeProductId)) {
            return false;
          }
          if (product.archived) {
            return false;
          }
          return Number(product.category_id || 0) === numericCategory;
        })
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));

      if (!available.length) {
        const option = document.createElement('option');
        option.textContent = 'No other products in this category';
        option.disabled = true;
        option.selected = true;
        select.appendChild(option);
        select.disabled = true;
        return;
      }

      available.forEach((product) => {
        const option = document.createElement('option');
        option.value = String(product.id);
        option.textContent = `${product.name} (${product.sku})`;
        if (selectedSet.has(Number(product.id))) {
          option.selected = true;
        }
        select.appendChild(option);
      });

      select.disabled = false;
    }

    if (createCategorySelect) {
      const refreshCreateSelects = () => {
        updateRecommendationSelect(createCrossSelect, createCategorySelect.value, [], null);
        updateRecommendationSelect(createUpsellSelect, createCategorySelect.value, [], null);
      };
      createCategorySelect.addEventListener('change', () => {
        refreshCreateSelects();
      });
      refreshCreateSelects();
    }

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
    const featuresTable = document.getElementById('edit-product-features-table');
    const featuresTableBody = featuresTable ? featuresTable.querySelector('tbody') : null;
    const featuresDataInput = document.getElementById('edit-product-features-data');
    const addFeatureButton = document.getElementById('add-product-feature');

    function dispatchFeatureTableUpdate() {
      if (!featuresTable) {
        return;
      }
      featuresTable.dispatchEvent(new CustomEvent('table:rows-updated', { bubbles: true }));
    }

    function getFeatureRows() {
      if (!featuresTableBody) {
        return [];
      }
      return Array.from(featuresTableBody.querySelectorAll('tr[data-feature-row="true"]'));
    }

    function refreshFeatureInput() {
      if (!featuresDataInput) {
        dispatchFeatureTableUpdate();
        return;
      }
      const rows = getFeatureRows();
      const payload = rows.map((row, index) => {
        const nameInput = row.querySelector('input[data-feature-name]');
        const valueInput = row.querySelector('input[data-feature-value]');
        return {
          name: nameInput ? nameInput.value.trim() : '',
          value: valueInput ? valueInput.value.trim() : '',
          position: index,
        };
      });
      featuresDataInput.value = JSON.stringify(payload);
      dispatchFeatureTableUpdate();
    }

    function clearFeatureTable() {
      if (featuresTableBody) {
        featuresTableBody.innerHTML = '';
      }
    }

    function addEmptyFeatureRow() {
      if (!featuresTableBody) {
        return;
      }
      const row = document.createElement('tr');
      row.dataset.emptyRow = 'true';
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.className = 'table__empty';
      cell.textContent = 'No features added yet.';
      row.appendChild(cell);
      featuresTableBody.appendChild(row);
      dispatchFeatureTableUpdate();
    }

    function removeEmptyFeatureRow() {
      if (!featuresTableBody) {
        return;
      }
      const emptyRow = featuresTableBody.querySelector('tr[data-empty-row="true"]');
      if (emptyRow) {
        emptyRow.remove();
      }
    }

    function createFeatureRow(feature) {
      if (!featuresTableBody) {
        return null;
      }
      const safeFeature = feature && typeof feature === 'object' ? feature : {};
      const nameValue = typeof safeFeature.name === 'string' ? safeFeature.name : '';
      const valueValue = typeof safeFeature.value === 'string' ? safeFeature.value : '';

      const row = document.createElement('tr');
      row.dataset.featureRow = 'true';
      if (safeFeature.id != null) {
        row.dataset.featureId = String(safeFeature.id);
      }

      const nameCell = document.createElement('td');
      nameCell.setAttribute('data-label', 'Feature');
      nameCell.dataset.value = nameValue;
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.className = 'form-input';
      nameInput.required = true;
      nameInput.placeholder = 'Feature name';
      nameInput.value = nameValue;
      nameInput.setAttribute('data-feature-name', 'true');
      nameInput.addEventListener('input', () => {
        nameCell.dataset.value = nameInput.value.trim();
        refreshFeatureInput();
      });
      nameCell.appendChild(nameInput);

      const valueCell = document.createElement('td');
      valueCell.setAttribute('data-label', 'Value');
      valueCell.dataset.value = valueValue;
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.className = 'form-input';
      valueInput.placeholder = 'Feature value';
      valueInput.value = valueValue;
      valueInput.setAttribute('data-feature-value', 'true');
      valueInput.addEventListener('input', () => {
        valueCell.dataset.value = valueInput.value.trim();
        refreshFeatureInput();
      });
      valueCell.appendChild(valueInput);

      const actionsCell = document.createElement('td');
      actionsCell.className = 'table__actions';
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'button button--ghost button--small button--danger';
      removeButton.textContent = 'Remove';
      removeButton.setAttribute('aria-label', 'Remove feature');
      removeButton.addEventListener('click', () => {
        row.remove();
        if (!getFeatureRows().length) {
          addEmptyFeatureRow();
        }
        refreshFeatureInput();
      });
      actionsCell.appendChild(removeButton);

      row.appendChild(nameCell);
      row.appendChild(valueCell);
      row.appendChild(actionsCell);

      return row;
    }

    function addFeatureRow(feature) {
      if (!featuresTableBody) {
        return;
      }
      removeEmptyFeatureRow();
      const row = createFeatureRow(feature);
      if (!row) {
        return;
      }
      featuresTableBody.appendChild(row);
      refreshFeatureInput();
      const nameInput = row.querySelector('input[data-feature-name]');
      if (nameInput) {
        nameInput.focus();
        nameInput.select();
      }
    }

    function renderFeatureRows(features) {
      if (!featuresTableBody) {
        return;
      }
      clearFeatureTable();
      const items = Array.isArray(features) ? features : [];
      if (items.length) {
        items.forEach((item) => {
          const row = createFeatureRow(item);
          if (row) {
            featuresTableBody.appendChild(row);
          }
        });
      } else {
        addEmptyFeatureRow();
      }
      refreshFeatureInput();
    }

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
        editForm.querySelector('#edit-product-description').value = product.description || '';
        editForm.querySelector('#edit-product-price').value = product.price != null ? product.price : '';
        editForm.querySelector('#edit-product-vip').value = product.vip_price != null ? product.vip_price : '';
        editForm.querySelector('#edit-product-stock').value = product.stock != null ? product.stock : '';
        const categorySelect = editForm.querySelector('#edit-product-category');
        if (categorySelect) {
          categorySelect.value = product.category_id || '';
        }
        const editCrossSelect = editForm.querySelector('#edit-product-cross-sell');
        const editUpsellSelect = editForm.querySelector('#edit-product-upsell');
        updateRecommendationSelect(
          editCrossSelect,
          categorySelect ? categorySelect.value : '',
          product.cross_sell_product_ids || [],
          id,
        );
        updateRecommendationSelect(
          editUpsellSelect,
          categorySelect ? categorySelect.value : '',
          product.upsell_product_ids || [],
          id,
        );
        if (categorySelect) {
          categorySelect.onchange = () => {
            const selectedCross = Array.from(
              (editCrossSelect ? editCrossSelect.selectedOptions : []),
            ).map((option) => Number(option.value));
            const selectedUpsell = Array.from(
              (editUpsellSelect ? editUpsellSelect.selectedOptions : []),
            ).map((option) => Number(option.value));
            updateRecommendationSelect(editCrossSelect, categorySelect.value, selectedCross, id);
            updateRecommendationSelect(editUpsellSelect, categorySelect.value, selectedUpsell, id);
          };
        }
        if (previewImage) {
          if (product.image_url) {
            previewImage.src = product.image_url;
            previewImage.hidden = false;
          } else {
            previewImage.hidden = true;
          }
        }
        renderFeatureRows(product.features || []);
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

    if (addFeatureButton) {
      addFeatureButton.addEventListener('click', () => {
        addFeatureRow({ name: '', value: '' });
      });
    }

    if (editForm) {
      editForm.addEventListener('submit', () => {
        refreshFeatureInput();
      });
    }
  });
})();
