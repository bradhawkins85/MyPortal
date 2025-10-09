(function () {
  function getCellValue(row, index) {
    const cell = row.children[index];
    return cell ? cell.getAttribute('data-value') || cell.textContent.trim() : '';
  }

  function parseValue(value, type) {
    if (type === 'number') {
      const parsed = parseFloat(value);
      return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    }
    if (type === 'date') {
      const timestamp = Date.parse(value);
      return Number.isNaN(timestamp) ? Number.NEGATIVE_INFINITY : timestamp;
    }
    return value.toLowerCase();
  }

  function sortTable(table, columnIndex, type, controller) {
    const tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const current = table.getAttribute('data-sort-index') === String(columnIndex)
      ? table.getAttribute('data-sort-order')
      : null;
    const ascending = current !== 'asc';

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

    const fragment = document.createDocumentFragment();
    rows.forEach((row) => fragment.appendChild(row));
    tbody.appendChild(fragment);
    table.setAttribute('data-sort-index', String(columnIndex));
    table.setAttribute('data-sort-order', ascending ? 'asc' : 'desc');

    if (controller) {
      controller.refreshRows();
    }
  }

  function attachSorting(table, controller) {
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach((header, index) => {
      header.addEventListener('click', () => {
        sortTable(table, index, header.getAttribute('data-sort') || 'string', controller);
      });
    });
  }

  class TableController {
    constructor(table) {
      this.table = table;
      this.tbody = table.tBodies[0] || null;
      this.rows = this.tbody ? Array.from(this.tbody.querySelectorAll('tr')) : [];
      this.filterInputs = new Set();
      this.filterTerm = '';
      this.filterInputValue = '';
      this.page = 0;
      this.pageSize = 0;
      this.rowHeight = 0;
      this.paginationElement = table.id
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

      this.updateFilterState();
      if (this.paginationElement) {
        this.initPagination();
      } else {
        this.render();
      }
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
        if (!term) {
          delete row.dataset.filterHidden;
          return;
        }
        const text = (row.textContent || '').toLowerCase();
        if (text.includes(term)) {
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
      this.render();
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

    render() {
      if (!this.tbody) {
        return;
      }
      if (!this.paginationElement) {
        this.rows.forEach((row) => {
          const hidden = row.dataset.filterHidden === 'true';
          row.style.display = hidden ? 'none' : '';
        });
        return;
      }

      if (!this.pageSize) {
        this.recalculatePageSize();
      }

      const filteredRows = this.getFilteredRows();
      const totalFiltered = filteredRows.length;

      if (totalFiltered === 0) {
        this.rows.forEach((row) => {
          const hidden = row.dataset.filterHidden === 'true';
          row.style.display = hidden ? 'none' : '';
        });
        this.updatePaginationControls(0, 1, 0, 0);
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
      this.updatePaginationControls(totalFiltered, totalPages, displayStart, displayEnd);
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
      const availableRaw = viewportHeight - rect.top - bottomPadding;
      const fallback = Math.max(viewportHeight * 0.5, 320);
      return availableRaw > 0 ? availableRaw : fallback;
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
        this.pageSize = this.pageSize || 10;
        return;
      }
      const extraSpacing = 24;
      const usable = availableHeight - headerHeight - paginationHeight - extraSpacing;
      const proposed = Math.floor(usable / rowHeight);
      this.pageSize = Math.max(1, Number.isFinite(proposed) && proposed > 0 ? proposed : 1);
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
        table.querySelectorAll('tbody tr').forEach((row) => {
          const text = row.textContent || '';
          row.style.display = !term || text.toLowerCase().includes(term) ? '' : 'none';
        });
      });
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
