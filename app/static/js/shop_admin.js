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

  function toggleFieldsBySubscriptionCategory(subscriptionCategorySelect, formContext) {
    if (!subscriptionCategorySelect) {
      return;
    }

    const updateFieldVisibility = () => {
      const hasSubscriptionCategory = subscriptionCategorySelect.value && subscriptionCategorySelect.value !== '';
      
      // Get all standard price fields and subscription fields in the same form
      const form = subscriptionCategorySelect.closest('form');
      if (!form) {
        return;
      }

      const standardPriceFields = form.querySelectorAll('[data-field-type="standard-price"]');
      const subscriptionFields = form.querySelectorAll('[data-field-type="subscription"]');

      if (hasSubscriptionCategory) {
        // Hide standard price fields and clear their values
        standardPriceFields.forEach((field) => {
          field.style.display = 'none';
          const input = field.querySelector('input');
          if (input) {
            input.value = '';
            // Remove required attribute when hidden
            if (input.hasAttribute('required')) {
              input.removeAttribute('required');
              input.dataset.wasRequired = 'true';
            }
          }
        });

        // Show subscription fields
        subscriptionFields.forEach((field) => {
          field.style.display = '';
        });
      } else {
        // Show standard price fields
        standardPriceFields.forEach((field) => {
          field.style.display = '';
          const input = field.querySelector('input');
          if (input && input.dataset.wasRequired === 'true') {
            input.setAttribute('required', '');
            delete input.dataset.wasRequired;
          }
        });

        // Hide subscription fields and clear their values
        subscriptionFields.forEach((field) => {
          field.style.display = 'none';
          const input = field.querySelector('input, select');
          if (input) {
            input.value = '';
          }
        });
      }
    };

    // Update on change
    subscriptionCategorySelect.addEventListener('change', updateFieldVisibility);
    
    // Initial update
    updateFieldVisibility();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    submitOnChange(container);

    const products = parseJson('admin-products-data', []);
    const restrictions = parseJson('admin-product-restrictions', {});
    const productsById = new Map(products.map((product) => [product.id, product]));
    const productsBySku = new Map(products.map((product) => [String(product.sku).toLowerCase(), product]));

    function createSkuListManager(listId, errorId, formName, fieldName) {
      const list = document.getElementById(listId);
      const errorEl = document.getElementById(errorId);
      if (!list) {
        return null;
      }

      const selectedIds = new Set();

      function showError(msg) {
        if (errorEl) {
          errorEl.textContent = msg;
          errorEl.hidden = !msg;
        }
      }

      function renderItem(product) {
        const li = document.createElement('li');
        li.className = 'tag';
        li.dataset.productId = String(product.id);

        const label = document.createElement('span');
        label.textContent = `${product.name} (${product.sku})`;
        li.appendChild(label);

        // Hidden input so the product ID is submitted with the form
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = fieldName;
        input.value = String(product.id);
        li.appendChild(input);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'tag__remove';
        removeBtn.setAttribute('aria-label', `Remove ${product.name}`);
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => {
          selectedIds.delete(Number(product.id));
          li.remove();
        });
        li.appendChild(removeBtn);

        list.appendChild(li);
      }

      function addBySku(sku, excludeProductId) {
        showError('');
        const trimmed = sku.trim().toLowerCase();
        if (!trimmed) {
          return false;
        }
        const product = productsBySku.get(trimmed);
        if (!product) {
          showError(`No product found with SKU "${sku.trim()}"`);
          return false;
        }
        if (product.archived) {
          showError(`Product "${product.name}" is archived and cannot be added`);
          return false;
        }
        if (excludeProductId != null && Number(product.id) === Number(excludeProductId)) {
          showError('A product cannot be its own recommendation');
          return false;
        }
        const numericId = Number(product.id);
        if (selectedIds.has(numericId)) {
          showError(`Product "${product.name}" is already in the list`);
          return false;
        }
        selectedIds.add(numericId);
        renderItem(product);
        return true;
      }

      function initFromIds(ids, excludeProductId) {
        list.innerHTML = '';
        selectedIds.clear();
        showError('');
        (ids || []).forEach((id) => {
          const product = productsById.get(Number(id));
          if (product && !product.archived) {
            const numericId = Number(product.id);
            if (excludeProductId == null || numericId !== Number(excludeProductId)) {
              selectedIds.add(numericId);
              renderItem(product);
            }
          }
        });
      }

      return { addBySku, initFromIds, showError };
    }

    // Create form SKU list managers
    const createCrossManager = createSkuListManager(
      'product-cross-sell-list',
      'create-cross-sell-error',
      'create',
      'cross_sell_product_ids',
    );
    const createUpsellManager = createSkuListManager(
      'product-upsell-list',
      'create-upsell-error',
      'create',
      'upsell_product_ids',
    );

    document.querySelectorAll('[data-sku-add][data-form="create"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const type = btn.getAttribute('data-sku-add');
        const inputId = type === 'cross-sell' ? 'product-cross-sell-sku' : 'product-upsell-sku';
        const manager = type === 'cross-sell' ? createCrossManager : createUpsellManager;
        const input = document.getElementById(inputId);
        if (!input || !manager) {
          return;
        }
        if (manager.addBySku(input.value, null)) {
          input.value = '';
        }
      });
    });

    // Allow pressing Enter in the SKU input to add
    ['product-cross-sell-sku', 'product-upsell-sku'].forEach((inputId) => {
      const input = document.getElementById(inputId);
      if (!input) {
        return;
      }
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const addBtn = input.closest('.form-quick-add')
            ? input.closest('.form-quick-add').querySelector('[data-sku-add]')
            : null;
          if (addBtn) {
            addBtn.click();
          }
        }
      });
    });

    // Initialize field visibility toggle for create form
    const createSubscriptionCategorySelect = document.getElementById('product-subscription-category');
    if (createSubscriptionCategorySelect) {
      toggleFieldsBySubscriptionCategory(createSubscriptionCategorySelect, 'create');
    }

    const stockFilter = document.getElementById('stock-filter');
    const showArchivedCheckbox = document.getElementById('show-archived');
    const productsTable = document.getElementById('admin-products-table');

    // ── Column visibility ────────────────────────────────────────────────────
    const COLUMNS_STORAGE_KEY = 'shop_admin_columns';
    const COLUMN_KEYS = ['image', 'name', 'sku', 'vendor-sku', 'dbp', 'price', 'vip', 'profit', 'vip-profit', 'category', 'stock'];

    function loadColumnPrefs() {
      try {
        const raw = localStorage.getItem(COLUMNS_STORAGE_KEY);
        if (raw) {
          return JSON.parse(raw);
        }
      } catch (e) {
        console.warn('shop_admin: could not load column preferences', e);
      }
      return {};
    }

    function saveColumnPrefs(prefs) {
      try {
        localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(prefs));
      } catch (e) {
        console.warn('shop_admin: could not save column preferences', e);
      }
    }

    function applyColumnVisibility(columnKey, visible) {
      if (!productsTable) {
        return;
      }
      productsTable.querySelectorAll(`[data-column="${columnKey}"]`).forEach((cell) => {
        cell.style.display = visible ? '' : 'none';
      });
    }

    const columnsDropdown = document.getElementById('columns-dropdown');
    const columnsToggle = document.getElementById('columns-toggle');
    const columnsMenu = document.getElementById('columns-menu');

    if (columnsToggle && columnsMenu && columnsDropdown) {
      columnsToggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const isOpen = columnsMenu.classList.contains('dropdown__menu--open');
        columnsMenu.classList.toggle('dropdown__menu--open', !isOpen);
        columnsToggle.setAttribute('aria-expanded', String(!isOpen));
      });

      document.addEventListener('click', (event) => {
        if (!columnsDropdown.contains(event.target)) {
          columnsMenu.classList.remove('dropdown__menu--open');
          columnsToggle.setAttribute('aria-expanded', 'false');
        }
      });
    }

    const columnPrefs = loadColumnPrefs();

    COLUMN_KEYS.forEach((key) => {
      const visible = columnPrefs[key] !== false;
      applyColumnVisibility(key, visible);
      const checkbox = columnsMenu ? columnsMenu.querySelector(`[data-column-toggle="${key}"]`) : null;
      if (checkbox) {
        checkbox.checked = visible;
        checkbox.addEventListener('change', () => {
          const prefs = loadColumnPrefs();
          prefs[key] = checkbox.checked;
          saveColumnPrefs(prefs);
          applyColumnVisibility(key, checkbox.checked);
        });
      }
    });
    // ── End column visibility ─────────────────────────────────────────────────

    function applyFilters() {
      if (!productsTable) {
        return;
      }
      const rows = productsTable.querySelectorAll('tbody tr');
      const stockValue = stockFilter ? stockFilter.value : '';
      rows.forEach((row) => {
        const stock = Number(row.getAttribute('data-stock') || '0');
        const matchesStock =
          !stockValue ||
          (stockValue === 'in' && stock > 0) ||
          (stockValue === 'out' && stock === 0);
        row.style.display = matchesStock ? '' : 'none';
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
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    }
    applyFilters();

    const importModal = document.getElementById('import-product-modal');
    const editModal = document.getElementById('product-edit-modal');
    const visibilityModal = document.getElementById('product-visibility-modal');
    const descriptionEditorModal = document.getElementById('description-editor-modal');
    const editForm = document.getElementById('product-edit-form');
    const visibilityForm = document.getElementById('product-visibility-form');
    const imageFilenameDisplay = document.getElementById('edit-product-image-filename');
    const editIdField = document.getElementById('edit-product-id');
    const featuresTable = document.getElementById('edit-product-features-table');
    const featuresTableBody = featuresTable ? featuresTable.querySelector('tbody') : null;
    const featuresDataInput = document.getElementById('edit-product-features-data');
    const addFeatureButton = document.getElementById('add-product-feature');
    const expandDescriptionButton = document.getElementById('edit-description-expand');
    const descriptionEditorField = document.getElementById('description-editor-field');
    const descriptionEditorApply = document.getElementById('description-editor-apply');
    const descriptionEditorCancel = document.getElementById('description-editor-cancel');
    const descriptionEditorClose = document.getElementById('description-editor-close');

    let descriptionSunEditor = null;

    function getOrCreateDescriptionEditor() {
      if (descriptionSunEditor) {
        return descriptionSunEditor;
      }
      if (!descriptionEditorField || typeof SUNEDITOR === 'undefined') {
        return null;
      }
      descriptionSunEditor = SUNEDITOR.create(descriptionEditorField, {
        width: '100%',
        height: '400',
        buttonList: [
          ['undo', 'redo'],
          ['bold', 'underline', 'italic', 'strike'],
          ['fontColor', 'hiliteColor'],
          ['outdent', 'indent'],
          ['align', 'horizontalRule', 'list', 'lineHeight'],
          ['link'],
          ['removeFormat'],
          ['codeView'],
        ],
      });
      return descriptionSunEditor;
    }

    function openDescriptionEditor() {
      const descriptionTextarea = editForm ? editForm.querySelector('#edit-product-description') : null;
      if (!descriptionEditorModal || !descriptionTextarea) {
        return;
      }
      const currentValue = descriptionTextarea.value || '';
      const editor = getOrCreateDescriptionEditor();
      if (editor) {
        editor.setContents(currentValue);
      } else if (descriptionEditorField) {
        descriptionEditorField.value = currentValue;
      }
      descriptionEditorModal.hidden = false;
      descriptionEditorModal.classList.add('is-visible');
    }

    function applyDescriptionEditor() {
      const descriptionTextarea = editForm ? editForm.querySelector('#edit-product-description') : null;
      if (!descriptionTextarea) {
        return;
      }
      if (descriptionSunEditor) {
        descriptionTextarea.value = descriptionSunEditor.getContents();
      } else if (descriptionEditorField) {
        descriptionTextarea.value = descriptionEditorField.value;
      }
      closeDescriptionEditor();
    }

    function closeDescriptionEditor() {
      if (!descriptionEditorModal) {
        return;
      }
      descriptionEditorModal.classList.remove('is-visible');
      descriptionEditorModal.hidden = true;
    }

    if (expandDescriptionButton) {
      expandDescriptionButton.addEventListener('click', openDescriptionEditor);
    }
    if (descriptionEditorApply) {
      descriptionEditorApply.addEventListener('click', applyDescriptionEditor);
    }
    if (descriptionEditorCancel) {
      descriptionEditorCancel.addEventListener('click', closeDescriptionEditor);
    }
    if (descriptionEditorClose) {
      descriptionEditorClose.addEventListener('click', closeDescriptionEditor);
    }
    if (descriptionEditorModal) {
      descriptionEditorModal.addEventListener('click', (event) => {
        if (event.target === descriptionEditorModal) {
          closeDescriptionEditor();
        }
      });
    }

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

    bindModalDismissal(importModal);
    bindModalDismissal(editModal);
    bindModalDismissal(visibilityModal);

    // Edit form SKU list managers (modal)
    let currentEditProductId = null;
    const editCrossManager = createSkuListManager(
      'edit-product-cross-sell-list',
      'edit-cross-sell-error',
      'edit',
      'cross_sell_product_ids',
    );
    const editUpsellManager = createSkuListManager(
      'edit-product-upsell-list',
      'edit-upsell-error',
      'edit',
      'upsell_product_ids',
    );

    document.querySelectorAll('[data-sku-add][data-form="edit"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const type = btn.getAttribute('data-sku-add');
        const inputId = type === 'cross-sell' ? 'edit-product-cross-sell-sku' : 'edit-product-upsell-sku';
        const manager = type === 'cross-sell' ? editCrossManager : editUpsellManager;
        const input = document.getElementById(inputId);
        if (!input || !manager) {
          return;
        }
        if (manager.addBySku(input.value, currentEditProductId)) {
          input.value = '';
        }
      });
    });

    // Allow pressing Enter in the edit SKU inputs to add
    ['edit-product-cross-sell-sku', 'edit-product-upsell-sku'].forEach((inputId) => {
      const input = document.getElementById(inputId);
      if (!input) {
        return;
      }
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const addBtn = input.closest('.form-quick-add')
            ? input.closest('.form-quick-add').querySelector('[data-sku-add]')
            : null;
          if (addBtn) {
            addBtn.click();
          }
        }
      });
    });

    if (importModal) {
      const importSkuInput = importModal.querySelector('#import-vendor-sku');
      document.querySelectorAll('[data-import-product-modal-open]').forEach((button) => {
        button.addEventListener('click', (event) => {
          event.preventDefault();
          openModal(importModal);
          if (importSkuInput && typeof importSkuInput.focus === 'function') {
            importSkuInput.focus();
            importSkuInput.select();
          }
        });
      });
    }

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
        const subscriptionCategorySelect = editForm.querySelector('#edit-product-subscription-category');
        if (subscriptionCategorySelect) {
          subscriptionCategorySelect.value = product.subscription_category_id || '';
          // Initialize field visibility toggle for edit modal
          toggleFieldsBySubscriptionCategory(subscriptionCategorySelect, 'edit');
        }
        const commitmentTypeSelect = editForm.querySelector('#edit-product-commitment-type');
        if (commitmentTypeSelect) {
          commitmentTypeSelect.value = product.commitment_type || '';
        }
        const paymentFrequencySelect = editForm.querySelector('#edit-product-payment-frequency');
        if (paymentFrequencySelect) {
          paymentFrequencySelect.value = product.payment_frequency || '';
        }
        const priceMonthlyCommitment = editForm.querySelector('#edit-product-price-monthly-commitment');
        if (priceMonthlyCommitment) {
          priceMonthlyCommitment.value = product.price_monthly_commitment != null ? product.price_monthly_commitment : '';
        }
        const priceAnnualMonthly = editForm.querySelector('#edit-product-price-annual-monthly');
        if (priceAnnualMonthly) {
          priceAnnualMonthly.value = product.price_annual_monthly_payment != null ? product.price_annual_monthly_payment : '';
        }
        const priceAnnualAnnual = editForm.querySelector('#edit-product-price-annual-annual');
        if (priceAnnualAnnual) {
          priceAnnualAnnual.value = product.price_annual_annual_payment != null ? product.price_annual_annual_payment : '';
        }
        if (editCrossManager) {
          editCrossManager.initFromIds(product.cross_sell_product_ids || [], id);
        }
        if (editUpsellManager) {
          editUpsellManager.initFromIds(product.upsell_product_ids || [], id);
        }
        currentEditProductId = id;
        if (imageFilenameDisplay) {
          if (product.image_url) {
            const filename = product.image_url.split('/').pop();
            imageFilenameDisplay.textContent = `Current image: ${filename}`;
            imageFilenameDisplay.hidden = false;
          } else {
            imageFilenameDisplay.hidden = true;
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
