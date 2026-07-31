(function () {
  function getCellValue(row, index) {
    const cell = row.children[index];
    return cell ? cell.getAttribute('data-value') || cell.textContent.trim() : '';
  }

  function parseValue(value, type) {
    if (type === 'number' || type === 'int') {
      const parsed = type === 'int' ? Number.parseInt(value, 10) : parseFloat(value);
      return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    }
    if (type === 'date') {
      const timestamp = Date.parse(value);
      return Number.isNaN(timestamp) ? Number.NEGATIVE_INFINITY : timestamp;
    }
    return value.toLowerCase();
  }

  function sortTable(table, columnIndex, type, controller, forcedOrder) {
    const tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    const hadGroupedRows = tbody.querySelector('.ticket-group-header') !== null;
    const rows = Array.from(tbody.querySelectorAll('tr:not(.ticket-group-header)'));
    const current = table.getAttribute('data-sort-index') === String(columnIndex)
      ? table.getAttribute('data-sort-order')
      : null;
    const ascending = forcedOrder ? forcedOrder === 'asc' : current !== 'asc';

    rows.sort((a, b) => {
      const valueA = parseValue(getCellValue(a, columnIndex), type);
      const valueB = parseValue(getCellValue(b, columnIndex), type);
      if (valueA < valueB) {
        return ascending ? -1 : 1;
      }
      if (valueA > valueB) {
        return ascending ? 1 : -1;
      }
      return 0;
    });

    if (hadGroupedRows) {
      tbody.querySelectorAll('.ticket-group-header').forEach((row) => row.remove());
    }

    const fragment = document.createDocumentFragment();
    rows.forEach((row) => fragment.appendChild(row));
    tbody.appendChild(fragment);
    table.setAttribute('data-sort-index', String(columnIndex));
    table.setAttribute('data-sort-order', ascending ? 'asc' : 'desc');

    table.dispatchEvent(new CustomEvent('table:sorted', {
      bubbles: true,
      detail: {
        columnIndex,
        sortType: type,
        sortOrder: ascending ? 'asc' : 'desc',
        hadGroupedRows
      }
    }));

    if (controller) {
      controller.persistSortState(columnIndex, ascending ? 'asc' : 'desc', type);
      controller.refreshRows();
    }
  }

  function attachSorting(table, controller) {
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach((header) => {
      header.addEventListener('click', () => {
        const columnIndex = Array.prototype.indexOf.call(header.parentElement.children, header);
        sortTable(table, columnIndex, header.getAttribute('data-sort') || 'string', controller);
      });
    });
    if (controller) {
      controller.restorePersistedSort();
    }
  }

  class TableController {
    constructor(table) {
      this.table = table;
      this.tbody = table.tBodies[0] || null;
      this.rows = this.tbody ? Array.from(this.tbody.querySelectorAll('tr')) : [];
      this.filterInputs = new Set();
      this.columnFilterInputs = new Set();
      this.filterTerm = '';
      this.filterInputValue = '';
      this.columnFilters = {};
      this.persistedFilterState = this.loadPersistedFilterState();
      this.page = 0;
      this.pageSize = 0;
      const maxPageSizeAttr = table.getAttribute('data-page-size-max');
      const parsedMax = maxPageSizeAttr ? Number.parseInt(maxPageSizeAttr, 10) : NaN;
      this.maxPageSize = Number.isNaN(parsedMax) || parsedMax <= 0 ? null : parsedMax;
      this.rowHeight = 0;
      this.shouldPaginate = table.hasAttribute('data-table-paginate');
      this.paginationElement = this.shouldPaginate && table.id
        ? document.querySelector(`[data-pagination="${table.id}"]`)
        : null;
      this.infoElement = this.paginationElement
        ? this.paginationElement.querySelector('[data-page-info]')
        : null;
      this.prevButton = this.paginationElement
        ? this.paginationElement.querySelector('[data-page-prev]')
        : null;
      this.nextButton = this.paginationElement
        ? this.paginationElement.querySelector('[data-page-next]')
        : null;
      this.resizeObserver = null;
      this.resizeFrame = null;
      this.handleResize = this.handleResize.bind(this);
      this.externalRefreshListener = () => {
        this.refreshRows();
      };
      this.layoutRefreshListener = () => {
        this.handleResize();
      };

      this.mobileQuery = (typeof window !== 'undefined' && window.matchMedia)
        ? window.matchMedia('(max-width: 640px)')
        : null;
      this.mobileResizeListener = () => {
        this.applyMobileLayout();
      };
      this.mobileConfig = this.buildMobileConfig();

      if (this.table) {
        this.table.addEventListener('table:rows-updated', this.externalRefreshListener);
        this.table.addEventListener('table:layout-change', this.layoutRefreshListener);
      }

      if (typeof window !== 'undefined') {
        window.addEventListener('resize', this.mobileResizeListener);
      }
      if (this.mobileQuery) {
        if (typeof this.mobileQuery.addEventListener === 'function') {
          this.mobileQuery.addEventListener('change', this.mobileResizeListener);
        } else if (typeof this.mobileQuery.addListener === 'function') {
          this.mobileQuery.addListener(this.mobileResizeListener);
        }
      }

      this.restorePersistedFilters();
      this.setupColumnFilters();
      this.updateFilterState();
      if (this.paginationElement) {
        this.paginationElement.classList.add('table-pagination--active');
      }

      if (this.paginationElement) {
        this.initPagination();
      } else {
        this.render();
      }

      this.applyMobileLayout();
    }

    buildMobileConfig() {
      if (!this.table) {
        return [];
      }
      const headerRow = this.table.tHead ? this.table.tHead.rows[0] : null;
      if (!headerRow) {
        return [];
      }
      return Array.from(headerRow.children).map((header, index) => {
        const explicit = (header.getAttribute('data-mobile-priority') || '').toLowerCase();
        let priority;
        if (explicit === 'essential' || explicit === 'supporting') {
          priority = explicit;
        } else if (header.classList.contains('table__actions')) {
          priority = 'essential';
        } else if (index < 2) {
          priority = 'essential';
        } else {
          priority = 'supporting';
        }
        header.dataset.mobilePriority = priority;
        return { index, priority };
      });
    }

    applyMobileLayout() {
      if (!this.table || !this.mobileConfig) {
        return;
      }
      const portraitActive = this.isMobileView();
      const rows = [];
      if (this.table.tHead) {
        rows.push(...this.table.tHead.rows);
      }
      if (this.table.tBodies) {
        Array.from(this.table.tBodies).forEach((tbody) => {
          rows.push(...tbody.rows);
        });
      }
      if (this.table.tFoot) {
        rows.push(...this.table.tFoot.rows);
      }
      rows.forEach((row) => {
        this.mobileConfig.forEach(({ index, priority }) => {
          const cell = row.children[index];
          if (!cell) {
            return;
          }
          const override = (cell.getAttribute('data-mobile-priority') || '').toLowerCase();
          let effectivePriority = priority;
          if (override === 'essential' || override === 'supporting') {
            effectivePriority = override;
          } else if (cell.classList.contains('table__actions')) {
            effectivePriority = 'essential';
          }
          if (portraitActive && effectivePriority !== 'essential') {
            cell.setAttribute('data-mobile-hidden', 'true');
          } else {
            cell.removeAttribute('data-mobile-hidden');
          }
        });
      });
    }

    getStorageKey() {
      const tableId = this.table ? (this.table.getAttribute('data-table-id') || this.table.id) : '';
      return tableId ? `myportal.tableFilters.${tableId}` : '';
    }

    loadPersistedFilterState() {
      if (typeof window === 'undefined' || !window.localStorage) {
        return {};
      }
      const key = this.getStorageKey();
      if (!key) {
        return {};
      }
      try {
        const value = window.localStorage.getItem(key);
        return value ? JSON.parse(value) : {};
      } catch (error) {
        return {};
      }
    }

    persistFilterState() {
      if (typeof window === 'undefined' || !window.localStorage) {
        return;
      }
      const key = this.getStorageKey();
      if (!key) {
        return;
      }
      const currentSortIndex = this.table.getAttribute('data-sort-index');
      const currentSortOrder = this.table.getAttribute('data-sort-order');
      const currentSortType = this.table.getAttribute('data-sort-type');
      const state = {
        global: this.filterInputValue || '',
        columns: this.columnFilters || {},
        sort: currentSortIndex && currentSortOrder ? {
          index: Number.parseInt(currentSortIndex, 10),
          order: currentSortOrder,
          type: currentSortType || undefined,
        } : null,
      };
      const hasColumns = Object.values(state.columns).some((value) => Boolean(value));
      const hasSort = state.sort && Number.isInteger(state.sort.index) && ['asc', 'desc'].includes(state.sort.order);
      try {
        if (state.global || hasColumns || hasSort) {
          window.localStorage.setItem(key, JSON.stringify(state));
        } else {
          window.localStorage.removeItem(key);
        }
      } catch (error) {
        /* Ignore storage failures so table filtering remains usable. */
      }
    }


    persistSortState(columnIndex, order, type) {
      if (!this.table) {
        return;
      }
      this.table.setAttribute('data-sort-type', type || 'string');
      this.persistFilterState();
    }

    restorePersistedSort() {
      const sort = this.persistedFilterState?.sort;
      if (!sort || !Number.isInteger(sort.index) || !['asc', 'desc'].includes(sort.order)) {
        return;
      }
      const header = this.table?.tHead?.rows?.[0]?.children?.[sort.index];
      if (!header || !header.hasAttribute('data-sort')) {
        return;
      }
      sortTable(this.table, sort.index, sort.type || header.getAttribute('data-sort') || 'string', this, sort.order);
    }

    restorePersistedFilters() {
      const state = this.persistedFilterState || {};
      if (typeof state.global === 'string' && state.global) {
        this.filterInputValue = state.global;
        this.filterTerm = state.global.trim().toLowerCase();
      }
      if (state.columns && typeof state.columns === 'object') {
        this.columnFilters = Object.entries(state.columns).reduce((filters, [key, value]) => {
          if (typeof value === 'string' && value) {
            filters[key] = value.trim().toLowerCase();
          } else if (value && typeof value === 'object' && value.value !== '') {
            filters[key] = value;
          }
          return filters;
        }, {});
      }
    }

    /** Add the ticket-list filter popover to each usable column. */
    setupColumnFilters() {
      if (!this.table || !this.table.tHead || this.table.hasAttribute('data-table-column-filters-disabled')) {
        return;
      }
      // The ticket workspace owns richer saved-view filters and builds these menus itself.
      if (this.table.hasAttribute('data-ticket-status-options')) {
        return;
      }
      const headerRow = this.table.tHead.rows[0];
      if (!headerRow) {
        return;
      }
      Array.from(headerRow.cells).forEach((header, index) => {
        if (header.matches('.table__actions, .table__select, [data-column-filter-disabled]') || header.querySelector('input, button')) {
          return;
        }
        const label = (header.textContent || '').trim();
        if (!label) {
          return;
        }
        const key = header.getAttribute('data-column-key') || `column-${index}`;
        const sortType = (header.getAttribute('data-filter-type') || header.getAttribute('data-sort') || 'text').toLowerCase();
        const type = sortType === 'number' || sortType === 'int' ? 'number' : sortType === 'date' ? 'date' : 'text';
        header.setAttribute('data-column-key', key);
        Array.from(this.table.tBodies).forEach((tbody) => {
          Array.from(tbody.rows).forEach((row) => {
            if (row.cells[index]) row.cells[index].setAttribute('data-column-key', key);
          });
        });

        const wrapper = document.createElement('span');
        wrapper.className = 'table-column-filter';
        wrapper.dataset.tableColumnFilterMenu = key;
        const labelNode = document.createElement('span');
        labelNode.className = 'table-column-filter__label';
        labelNode.textContent = label;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'table-column-filter__toggle';
        toggle.setAttribute('aria-label', `Filter ${label}`);
        toggle.setAttribute('aria-haspopup', 'true');
        toggle.setAttribute('aria-expanded', 'false');
        toggle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 5.25A1.25 1.25 0 0 1 4.75 4h14.5a1.25 1.25 0 0 1 .96 2.05L14.5 12.9v5.35a1.25 1.25 0 0 1-.69 1.12l-2.5 1.25a1.25 1.25 0 0 1-1.81-1.12v-6.6L3.79 6.05a1.25 1.25 0 0 1-.29-.8Z"/></svg>';
        const panel = document.createElement('span');
        panel.className = 'table-column-filter__panel';
        panel.hidden = true;
        const operators = type === 'text'
          ? [['contains', 'Contains'], ['not_contains', 'Does not contain'], ['equals', 'Equals'], ['not_equals', 'Does not equal'], ['starts_with', 'Starts with'], ['ends_with', 'Ends with']]
          : type === 'number'
            ? [['equals', 'Equals'], ['not_equals', 'Does not equal'], ['greater', 'Greater than'], ['greater_equal', 'At least'], ['less', 'Less than'], ['less_equal', 'At most']]
            : [['on', 'On'], ['before', 'Before'], ['after', 'After'], ['on_or_before', 'On or before'], ['on_or_after', 'On or after']];
        const title = document.createElement('span');
        title.className = 'table-column-filter__title';
        title.textContent = `Filter ${label}`;
        const operator = document.createElement('select');
        operator.className = 'form-input';
        operator.setAttribute('aria-label', `Filter operation for ${label}`);
        operators.forEach(([value, text]) => operator.add(new Option(text, value)));
        const valueInput = document.createElement('input');
        valueInput.className = 'form-input';
        valueInput.type = type === 'number' ? 'number' : type === 'date' ? 'date' : 'text';
        valueInput.setAttribute('aria-label', `Filter value for ${label}`);
        const actions = document.createElement('span');
        actions.className = 'table-column-filter__actions';
        actions.innerHTML = '<button type="button" class="button button--primary button--compact" data-table-column-filter-apply>Apply</button><button type="button" class="button button--ghost button--compact" data-table-column-filter-clear>Clear</button>';
        panel.append(title, operator, valueInput, actions);
        wrapper.append(labelNode, toggle, panel);
        header.textContent = '';
        header.appendChild(wrapper);

        const current = this.columnFilters[key];
        if (current && typeof current === 'object') {
          operator.value = current.operator;
          valueInput.value = current.value;
          wrapper.classList.add('table-column-filter--active');
        }
        const close = () => { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
        toggle.addEventListener('click', (event) => {
          event.stopPropagation();
          document.querySelectorAll('[data-table-column-filter-menu]').forEach((menu) => {
            if (menu !== wrapper) menu.querySelector('.table-column-filter__panel')?.setAttribute('hidden', '');
          });
          panel.hidden = !panel.hidden;
          toggle.setAttribute('aria-expanded', String(!panel.hidden));
        });
        panel.addEventListener('click', (event) => event.stopPropagation());
        actions.querySelector('[data-table-column-filter-apply]').addEventListener('click', () => {
          const value = valueInput.value.trim();
          if (value) this.columnFilters[key] = { type, operator: operator.value, value };
          else delete this.columnFilters[key];
          wrapper.classList.toggle('table-column-filter--active', Boolean(value));
          this.page = 0;
          this.updateFilterState();
          this.persistFilterState();
          this.render();
          close();
        });
        actions.querySelector('[data-table-column-filter-clear]').addEventListener('click', () => {
          valueInput.value = '';
          delete this.columnFilters[key];
          wrapper.classList.remove('table-column-filter--active');
          this.page = 0;
          this.updateFilterState();
          this.persistFilterState();
          this.render();
          close();
        });
      });
      document.addEventListener('click', () => {
        this.table.querySelectorAll('[data-table-column-filter-menu]').forEach((menu) => {
          menu.querySelector('.table-column-filter__panel').hidden = true;
          menu.querySelector('.table-column-filter__toggle').setAttribute('aria-expanded', 'false');
        });
      });
    }

    getColumnCellValue(row, columnKey) {
      if (!row || !columnKey) {
        return '';
      }
      const escapedKey = window.CSS && typeof window.CSS.escape === 'function'
        ? window.CSS.escape(columnKey)
        : columnKey.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      let cell = row.querySelector(`[data-column-key="${escapedKey}"]`);
      if (!cell && this.table?.tHead?.rows?.[0]) {
        const headers = Array.from(this.table.tHead.rows[0].cells);
        const index = headers.findIndex((header) => header.getAttribute('data-column-key') === columnKey);
        cell = index >= 0 ? row.cells[index] : null;
      }
      return cell ? (cell.getAttribute('data-value') || cell.textContent || '').trim().toLowerCase() : '';
    }

    rowMatchesColumnFilters(row) {
      return Object.entries(this.columnFilters).every(([columnKey, filter]) => {
        if (!filter) {
          return true;
        }
        const cellValue = this.getColumnCellValue(row, columnKey);
        if (typeof filter === 'string') return cellValue.includes(filter);
        const expected = String(filter.value || '').toLowerCase();
        if (filter.type === 'number') {
          const actualNumber = Number.parseFloat(cellValue);
          const expectedNumber = Number.parseFloat(expected);
          if (Number.isNaN(actualNumber) || Number.isNaN(expectedNumber)) return false;
          return filter.operator === 'greater' ? actualNumber > expectedNumber
            : filter.operator === 'greater_equal' ? actualNumber >= expectedNumber
              : filter.operator === 'less' ? actualNumber < expectedNumber
                : filter.operator === 'less_equal' ? actualNumber <= expectedNumber
                  : filter.operator === 'not_equals' ? actualNumber !== expectedNumber
                    : actualNumber === expectedNumber;
        }
        if (filter.type === 'date') {
          const actualDate = Date.parse(cellValue);
          const expectedDate = Date.parse(expected);
          if (Number.isNaN(actualDate) || Number.isNaN(expectedDate)) return false;
          return filter.operator === 'before' ? actualDate < expectedDate
            : filter.operator === 'after' ? actualDate > expectedDate
              : filter.operator === 'on_or_before' ? actualDate <= expectedDate
                : filter.operator === 'on_or_after' ? actualDate >= expectedDate
                  : new Date(actualDate).toISOString().slice(0, 10) === expected;
        }
        return filter.operator === 'not_contains' ? !cellValue.includes(expected)
          : filter.operator === 'equals' ? cellValue === expected
            : filter.operator === 'not_equals' ? cellValue !== expected
              : filter.operator === 'starts_with' ? cellValue.startsWith(expected)
                : filter.operator === 'ends_with' ? cellValue.endsWith(expected)
                  : cellValue.includes(expected);
      });
    }

    updateFilterState() {
      if (!this.rows.length) {
        return;
      }
      const term = this.filterTerm;
      this.rows.forEach((row) => {
        if (!row) {
          return;
        }
        const text = (row.textContent || '').toLowerCase();
        const matchesGlobal = !term || text.includes(term);
        const matchesColumns = this.rowMatchesColumnFilters(row);
        if (matchesGlobal && matchesColumns) {
          delete row.dataset.filterHidden;
        } else {
          row.dataset.filterHidden = 'true';
        }
      });
    }

    bindFilterInput(input) {
      if (!input) {
        return;
      }
      this.filterInputs.add(input);
      if (this.filterInputValue) {
        input.value = this.filterInputValue;
      }
      input.addEventListener('input', () => {
        this.handleFilterInput(input.value, input);
      });
      if (this.filterInputs.size === 1 && input.value) {
        this.handleFilterInput(input.value, input);
      } else if (this.filterInputs.size > 1 && this.filterInputValue) {
        input.value = this.filterInputValue;
      }
    }

    syncFilterInputs(source) {
      const value = source ? source.value : this.filterInputValue;
      this.filterInputs.forEach((input) => {
        if (input === source) {
          return;
        }
        if (input.value !== value) {
          input.value = value;
        }
      });
    }

    handleFilterInput(value, source) {
      const rawValue = value || '';
      const normalised = rawValue.trim().toLowerCase();
      if (normalised === this.filterTerm && rawValue === this.filterInputValue) {
        this.syncFilterInputs(source);
        return;
      }
      this.filterTerm = normalised;
      this.filterInputValue = rawValue;
      this.syncFilterInputs(source);
      this.page = 0;
      this.updateFilterState();
      this.persistFilterState();
      this.render();
    }

    bindColumnFilterInput(input) {
      if (!input) {
        return;
      }
      const columnKey = input.getAttribute('data-table-column-filter');
      if (!columnKey) {
        return;
      }
      this.columnFilterInputs.add(input);
      const persistedValue = this.persistedFilterState?.columns?.[columnKey];
      if (typeof persistedValue === 'string' && !input.value) {
        input.value = persistedValue;
      }
      const applyValue = () => {
        const rawValue = input.value || '';
        const normalised = rawValue.trim().toLowerCase();
        if (normalised) {
          this.columnFilters[columnKey] = normalised;
        } else {
          delete this.columnFilters[columnKey];
        }
        this.page = 0;
        this.updateFilterState();
        this.persistFilterState();
        this.render();
      };
      input.addEventListener('input', applyValue);
      input.addEventListener('change', applyValue);
      if (input.value) {
        applyValue();
      }
    }

    refreshRows() {
      if (!this.tbody) {
        return;
      }
      this.rows = Array.from(this.tbody.querySelectorAll('tr'));
      this.updateFilterState();
      this.render();
    }

    getFilteredRows() {
      return this.rows.filter((row) => row.dataset.filterHidden !== 'true');
    }

    isMobileView() {
      return this.mobileQuery
        ? this.mobileQuery.matches
        : (typeof window !== 'undefined' ? window.innerWidth <= 720 : false);
    }

    render() {
      if (!this.tbody) {
        return;
      }
      const filteredRows = this.getFilteredRows();
      const totalFiltered = filteredRows.length;
      const inMobileView = this.isMobileView();

      // Skip pagination if no pagination element or if in mobile view
      if (!this.paginationElement || inMobileView) {
        this.rows.forEach((row) => {
          const hidden = row.dataset.filterHidden === 'true';
          row.style.display = hidden ? 'none' : '';
          // Clear any pagination-related hiding when not paginating
          delete row.dataset.pageHidden;
        });
        const visibleCount = this.rows.reduce((count, row) => (
          row.dataset.filterHidden === 'true' ? count : count + 1
        ), 0);
        this.dispatchRenderEvent({
          filteredCount: totalFiltered,
          visibleCount,
          totalPages: 1,
          page: 0,
          pageSize: totalFiltered || 0,
          startDisplay: totalFiltered > 0 ? 1 : 0,
          endDisplay: totalFiltered,
        });
        // Hide pagination controls when in mobile view
        // Note: When switching back to desktop, updatePaginationControls will restore visibility
        if (this.paginationElement && inMobileView) {
          this.paginationElement.hidden = true;
        }
        this.applyMobileLayout();
        return;
      }

      // Normal pagination flow for desktop view
      if (!this.pageSize) {
        this.recalculatePageSize();
      }

      if (totalFiltered === 0) {
        this.rows.forEach((row) => {
          const hidden = row.dataset.filterHidden === 'true';
          row.style.display = hidden ? 'none' : '';
        });
        this.updatePaginationControls(0, 1, 0, 0);
        this.dispatchRenderEvent({
          filteredCount: 0,
          visibleCount: 0,
          totalPages: 1,
          page: 0,
          pageSize: this.pageSize,
          startDisplay: 0,
          endDisplay: 0,
        });
        this.applyMobileLayout();
        return;
      }

      const totalPages = Math.max(1, Math.ceil(totalFiltered / Math.max(this.pageSize, 1)));
      if (this.page >= totalPages) {
        this.page = totalPages - 1;
      }
      const startIndex = this.page * this.pageSize;
      const endIndex = startIndex + this.pageSize;

      filteredRows.forEach((row, index) => {
        if (index >= startIndex && index < endIndex) {
          delete row.dataset.pageHidden;
        } else {
          row.dataset.pageHidden = 'true';
        }
      });

      this.rows.forEach((row) => {
        const hidden = row.dataset.filterHidden === 'true' || row.dataset.pageHidden === 'true';
        row.style.display = hidden ? 'none' : '';
      });

      const displayStart = Math.min(totalFiltered, startIndex + 1);
      const displayEnd = Math.min(totalFiltered, endIndex);
      const visibleCount = Math.max(0, Math.min(this.pageSize, totalFiltered - startIndex));
      this.updatePaginationControls(totalFiltered, totalPages, displayStart, displayEnd);
      this.dispatchRenderEvent({
        filteredCount: totalFiltered,
        visibleCount,
        totalPages,
        page: this.page,
        pageSize: this.pageSize,
        startDisplay: displayStart,
        endDisplay: displayEnd,
      });
      this.applyMobileLayout();
    }

    dispatchRenderEvent(detail) {
      if (!this.table || typeof window.CustomEvent !== 'function') {
        return;
      }
      this.table.dispatchEvent(new CustomEvent('table:render', {
        detail,
      }));
    }

    updatePaginationControls(totalFiltered, totalPages, startDisplay, endDisplay) {
      if (!this.paginationElement) {
        return;
      }
      const hasResults = totalFiltered > 0;
      if (this.infoElement) {
        if (!hasResults) {
          this.infoElement.textContent = this.filterTerm ? 'No matching records' : 'No records available';
        } else {
          this.infoElement.textContent = `Showing ${startDisplay}–${endDisplay} of ${totalFiltered}`;
        }
      }
      if (this.prevButton) {
        this.prevButton.disabled = !hasResults || this.page <= 0;
      }
      if (this.nextButton) {
        this.nextButton.disabled = !hasResults || this.page >= totalPages - 1;
      }
      const shouldHide = hasResults && totalFiltered <= this.pageSize && !this.filterTerm;
      this.paginationElement.hidden = shouldHide;
    }

    initPagination() {
      if (this.prevButton) {
        this.prevButton.addEventListener('click', () => {
          if (this.page <= 0) {
            return;
          }
          this.page -= 1;
          this.render();
        });
      }
      if (this.nextButton) {
        this.nextButton.addEventListener('click', () => {
          const filteredRows = this.getFilteredRows();
          const totalPages = Math.max(1, Math.ceil(filteredRows.length / Math.max(this.pageSize, 1)));
          if (this.page >= totalPages - 1) {
            return;
          }
          this.page += 1;
          this.render();
        });
      }

      this.recalculatePageSize();
      this.render();

      window.addEventListener('resize', this.handleResize);
      const wrapper = this.table.closest('.table-wrapper');
      if (window.ResizeObserver && wrapper) {
        this.resizeObserver = new ResizeObserver(() => {
          this.handleResize();
        });
        this.resizeObserver.observe(wrapper);
      }
    }

    computeAvailableHeight() {
      const wrapper = this.table.closest('.table-wrapper') || this.table;
      const rect = wrapper.getBoundingClientRect ? wrapper.getBoundingClientRect() : null;
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      if (!rect || !viewportHeight) {
        return viewportHeight;
      }
      const bottomPadding = 32;
      const safeTop = Math.max(rect.top, 0);
      const maxAvailable = Math.max(0, viewportHeight - bottomPadding);
      const availableRaw = viewportHeight - safeTop - bottomPadding;
      if (availableRaw > 0) {
        return Math.min(availableRaw, maxAvailable || availableRaw);
      }
      const fallbackBase = Math.max(viewportHeight * 0.5, 320);
      const fallback = maxAvailable > 0 ? Math.min(fallbackBase, maxAvailable) : fallbackBase;
      return fallback;
    }

    measureRowHeight() {
      if (!this.rows.length) {
        return this.rowHeight;
      }
      const candidate = this.rows.find((row) => row.dataset.filterHidden !== 'true') || this.rows[0];
      if (!candidate) {
        return this.rowHeight;
      }
      const previousDisplay = candidate.style.display;
      if (previousDisplay === 'none') {
        candidate.style.display = '';
      }
      const height = candidate.getBoundingClientRect().height;
      if (previousDisplay === 'none') {
        candidate.style.display = previousDisplay;
      }
      if (height > 0) {
        this.rowHeight = height;
      }
      return this.rowHeight || height || 0;
    }

    recalculatePageSize() {
      if (!this.paginationElement) {
        return;
      }
      const availableHeight = this.computeAvailableHeight();
      const headerHeight = this.table.tHead ? this.table.tHead.getBoundingClientRect().height : 0;
      const paginationHeight = this.paginationElement.getBoundingClientRect().height || 0;
      const rowHeight = this.measureRowHeight();
      if (!rowHeight) {
        const fallback = this.pageSize || 10;
        this.pageSize = this.maxPageSize
          ? Math.min(fallback, this.maxPageSize)
          : fallback;
        return;
      }
      const extraSpacing = 24;
      const usable = availableHeight - headerHeight - paginationHeight - extraSpacing;
      const proposed = Math.floor(usable / rowHeight);
      const computed = Math.max(1, Number.isFinite(proposed) && proposed > 0 ? proposed : 1);
      this.pageSize = this.maxPageSize ? Math.min(computed, this.maxPageSize) : computed;
    }

    handleResize() {
      if (this.resizeFrame) {
        cancelAnimationFrame(this.resizeFrame);
      }
      this.resizeFrame = window.requestAnimationFrame(() => {
        const previousSize = this.pageSize;
        this.recalculatePageSize();
        if (this.pageSize !== previousSize) {
          this.page = 0;
        }
        this.render();
      });
    }
  }

  function attachFilters(controllers) {
    document.querySelectorAll('[data-table-filter]').forEach((input) => {
      const tableId = input.getAttribute('data-table-filter');
      if (!tableId) {
        return;
      }
      const controller = controllers.get(tableId);
      if (controller) {
        controller.bindFilterInput(input);
        if (input.value) {
          controller.handleFilterInput(input.value, input);
        }
        return;
      }
      const table = document.getElementById(tableId);
      if (!table) {
        return;
      }
      input.addEventListener('input', () => {
        const term = input.value.trim().toLowerCase();
        table.querySelectorAll('tbody tr:not(.ticket-group-header)').forEach((row) => {
          const text = row.textContent || '';
          if (!term || text.toLowerCase().includes(term)) {
            row.classList.remove('table-search-hidden');
          } else {
            row.classList.add('table-search-hidden');
          }
        });
      });
    });
    document.querySelectorAll('[data-table-column-filter]').forEach((input) => {
      const tableId = input.getAttribute('data-table-column-filter-table') || input.getAttribute('data-table-filter');
      if (!tableId) {
        return;
      }
      const controller = controllers.get(tableId);
      if (controller) {
        controller.bindColumnFilterInput(input);
      }
    });
  }

  function convertUtcElements() {
    document.querySelectorAll('[data-utc]').forEach((element) => {
      const iso = element.getAttribute('data-utc');
      if (!iso) {
        return;
      }
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      const formatted = date.toLocaleString();
      element.textContent = formatted;
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const controllers = new Map();
    document.querySelectorAll('table[data-table]').forEach((table) => {
      const controller = new TableController(table);
      if (table.id) {
        controllers.set(table.id, controller);
      }
      attachSorting(table, controller);
    });
    attachFilters(controllers);
    convertUtcElements();
  });
})();
