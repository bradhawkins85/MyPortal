/**
 * Ticket View Management
 * Handles saving, loading, and managing ticket filter and grouping views
 */
(function () {
  'use strict';

  const API_BASE = '/api/tickets';

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    return getCookie('myportal_session_csrf');
  }

  /**
   * TicketViewManager - Manages ticket views with filters, grouping, and sorting
   */
  class TicketViewManager {
    constructor(container) {
      this.container = container;
      this.currentView = null;
      this.views = [];
      this.filterState = {
        statuses: [],
        priorities: [],
        companies: [],
        assignedUsers: [],
        search: '',
        columnFilters: {}
      };
      this.groupingFields = [];
      this.groupingField = null;
      this.sortField = null;
      this.sortDirection = 'asc';
      
      this.init();
    }

    async init() {
      // Set up all event listeners synchronously first to avoid missing events
      // that fire while async initialization (loadViews) is in progress.
      this.setupEventListeners();
      this.setupStatusFilters();
      this.setupStatusFilterMenu();
      this.setupColumnFilters();
      this.setupGroupingControls();
      await this.loadViews();
      this.updateViewActions();
      this.applyDefaultView();
    }

    /**
     * Load all saved views from the API
     */
    async loadViews(selectedViewId = null) {
      try {
        const response = await fetch(`${API_BASE}/views`);
        if (response.ok) {
          const data = await response.json();
          this.views = data.items || [];
          this.renderViewSelector(selectedViewId);
        }
      } catch (error) {
        console.error('Failed to load ticket views:', error);
      }
    }

    /**
     * Setup event listeners for UI controls
     */
    setupEventListeners() {
      const table = this.container.querySelector('[data-table]');
      if (table) {
        table.addEventListener('table:sorted', () => {
          const fields = this.groupingFields.length ? this.groupingFields : (this.groupingField ? [this.groupingField] : []);
          if (fields.length) {
            this.applyGrouping();
          }
        });

        // After the API populates the table, apply client-side filters and grouping
        table.addEventListener('table:rows-updated', () => {
          this._applyPostLoadFilters();
        });
      }

      // View selector
      const viewSelect = this.container.querySelector('[data-view-select]');
      if (viewSelect) {
        viewSelect.addEventListener('change', (e) => {
          const viewId = parseInt(e.target.value);
          if (viewId) {
            this.applyView(viewId);
          } else {
            this.clearView();
          }
        });
      }

      // Save view button
      const saveViewBtn = this.container.querySelector('[data-save-view]');
      if (saveViewBtn) {
        saveViewBtn.addEventListener('click', () => this.showSaveViewModal());
      }

      // Update view button
      const updateViewBtn = this.container.querySelector('[data-update-view]');
      if (updateViewBtn) {
        updateViewBtn.addEventListener('click', () => this.updateCurrentView());
      }

      // Save view form
      const saveViewForm = document.querySelector('[data-save-view-form]');
      if (saveViewForm) {
        saveViewForm.addEventListener('submit', async (e) => {
          e.preventDefault();
          const formData = new FormData(saveViewForm);
          const name = formData.get('name');
          const description = formData.get('description');
          const isDefault = formData.get('is_default') === 'on';
          
          const saved = await this.saveView(name, description, isDefault);
          if (saved) {
            saveViewForm.reset();
            const modal = document.getElementById('save-view-modal');
            if (modal) {
              modal.setAttribute('hidden', '');
              modal.setAttribute('aria-hidden', 'true');
            }
          }
        });
      }

      // Modal close buttons
      document.querySelectorAll('[data-modal-close]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const modal = e.target.closest('.modal');
          if (modal) {
            modal.setAttribute('hidden', '');
            modal.setAttribute('aria-hidden', 'true');
          }
        });
      });

      // Delete view button
      const deleteViewBtn = this.container.querySelector('[data-delete-view]');
      if (deleteViewBtn) {
        deleteViewBtn.addEventListener('click', () => this.deleteCurrentView());
      }

    }

    /**
     * Setup status filter checkboxes for multi-select
     */
    setupStatusFilters() {
      const statusCheckboxes = this.container.querySelectorAll('[data-status-filter]');
      
      // Initialize filterState.statuses with currently checked checkboxes
      statusCheckboxes.forEach(checkbox => {
        if (checkbox.checked && !this.filterState.statuses.includes(checkbox.value)) {
          this.filterState.statuses.push(checkbox.value);
        }
      });
      
      statusCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', (e) => {
          const status = e.target.value;
          if (e.target.checked) {
            if (!this.filterState.statuses.includes(status)) {
              this.filterState.statuses.push(status);
            }
          } else {
            this.filterState.statuses = this.filterState.statuses.filter(s => s !== status);
          }
          this.applyFilters();
          this.updateActiveFilterHeaders();
        });
      });
    }

    /** Add a type-appropriate filter popover to every data column header. */
    setupColumnFilters() {
      const table = this.container.querySelector('[data-table]');
      if (!table) return;
      const dateColumns = new Set(['updated', 'review-date', 'created', 'closed']);
      const numberColumns = new Set([
        'id', 'billable-minutes', 'non-billable-minutes', 'company-id',
        'attachment-count', 'task-count', 'open-task-count', 'age-days',
        'updated-age-hours', 'in-status-hours', 'last-reply-age-hours'
      ]);
      const booleanColumns = new Set(['has-attachments', 'has-tasks', 'has-open-tasks', 'latest-reply-internal']);

      table.querySelectorAll('thead th[data-column]').forEach((header) => {
        const column = header.dataset.column;
        // Status already has its purpose-built multi-select filter.
        if (!column || column === 'status') return;
        const type = dateColumns.has(column) ? 'date' : numberColumns.has(column) ? 'number' : booleanColumns.has(column) ? 'boolean' : 'text';
        const label = header.textContent.trim();
        header.textContent = '';
        const wrapper = document.createElement('span');
        wrapper.className = 'ticket-column-filter';
        wrapper.dataset.columnFilterMenu = column;
        wrapper.innerHTML = `<span class="ticket-column-filter__label">${this.escapeHtml(label)}</span>`;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'ticket-column-filter__toggle';
        toggle.setAttribute('aria-label', `Filter ${label}`);
        toggle.setAttribute('aria-expanded', 'false');
        toggle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 5.25A1.25 1.25 0 0 1 4.75 4h14.5a1.25 1.25 0 0 1 .96 2.05L14.5 12.9v5.35a1.25 1.25 0 0 1-.69 1.12l-2.5 1.25a1.25 1.25 0 0 1-1.81-1.12v-6.6L3.79 6.05a1.25 1.25 0 0 1-.29-.8Z"/></svg>';
        const panel = document.createElement('span');
        panel.className = 'ticket-column-filter__panel';
        panel.hidden = true;
        panel.innerHTML = this.columnFilterControls(column, type, label);
        const dateReference = panel.querySelector('[data-column-filter-date-reference]');
        if (dateReference) {
          dateReference.addEventListener('change', () => this.updateDateFilterValueControl(panel));
          this.updateDateFilterValueControl(panel);
        }
        wrapper.append(toggle, panel);
        header.appendChild(wrapper);

        const stop = (event) => event.stopPropagation();
        panel.addEventListener('click', stop);
        toggle.addEventListener('click', (event) => {
          stop(event);
          this.container.querySelectorAll('[data-column-filter-menu]').forEach((menu) => {
            if (menu !== wrapper) {
              menu.querySelector('.ticket-column-filter__panel').hidden = true;
              menu.querySelector('.ticket-column-filter__toggle').setAttribute('aria-expanded', 'false');
            }
          });
          panel.hidden = !panel.hidden;
          toggle.setAttribute('aria-expanded', String(!panel.hidden));
        });
        panel.querySelector('[data-column-filter-apply]').addEventListener('click', () => {
          const operator = panel.querySelector('[data-column-filter-operator]').value;
          const input = panel.querySelector('[data-column-filter-value]');
          const value = dateReference && dateReference.value === 'today'
            ? 'today'
            : (input ? input.value.trim() : '');
          if (operator === 'relative' || value !== '') this.filterState.columnFilters[column] = { type, operator, value };
          else delete this.filterState.columnFilters[column];
          panel.hidden = true;
          toggle.setAttribute('aria-expanded', 'false');
          this.updateActiveFilterHeaders();
          this.applyFilters();
        });
        panel.querySelector('[data-column-filter-clear]').addEventListener('click', () => {
          delete this.filterState.columnFilters[column];
          this.populateColumnFilterPanel(column);
          panel.hidden = true;
          this.updateActiveFilterHeaders();
          this.applyFilters();
        });
      });
      document.addEventListener('click', () => this.closeColumnFilterMenus());
      this.updateActiveFilterHeaders();
    }

    escapeHtml(value) {
      const node = document.createElement('span');
      node.textContent = value;
      return node.innerHTML;
    }

    columnFilterControls(column, type, label) {
      const operators = type === 'text'
        ? [['contains', 'Contains'], ['not_contains', 'Does not contain'], ['equals', 'Equals'], ['not_equals', 'Does not equal'], ['starts_with', 'Starts with'], ['ends_with', 'Ends with']]
        : type === 'number'
          ? [['equals', 'Equals'], ['not_equals', 'Does not equal'], ['greater', 'Greater than'], ['greater_equal', 'At least'], ['less', 'Less than'], ['less_equal', 'At most']]
          : type === 'date'
            ? [['on', 'On'], ['before', 'Before'], ['after', 'After'], ['on_or_before', 'On or before'], ['on_or_after', 'On or after'], ['relative', 'In the last 30 days']]
            : [['equals', 'Is']];
      const options = operators.map(([value, text]) => `<option value="${value}">${text}</option>`).join('');
      const inputType = type === 'number' ? 'number' : type === 'date' ? 'date' : 'text';
      const valueControl = type === 'boolean'
        ? '<select class="form-input" data-column-filter-value><option value="true">True</option><option value="false">False</option></select>'
        : type === 'date'
          ? '<select class="form-input" data-column-filter-date-reference aria-label="Date reference"><option value="date">Specific date</option><option value="today">Today</option></select><input class="form-input" data-column-filter-value type="date" aria-label="Filter value for ' + this.escapeHtml(label) + '">'
        : `<input class="form-input" data-column-filter-value type="${inputType}" aria-label="Filter value for ${this.escapeHtml(label)}">`;
      return `<span class="ticket-column-filter__title">Filter ${this.escapeHtml(label)}</span><select class="form-input" data-column-filter-operator>${options}</select>${valueControl}<span class="ticket-column-filter__actions"><button type="button" class="button button--primary button--compact" data-column-filter-apply>Apply</button><button type="button" class="button button--ghost button--compact" data-column-filter-clear>Clear</button></span>`;
    }

    closeColumnFilterMenus() {
      this.container.querySelectorAll('[data-column-filter-menu]').forEach((menu) => {
        menu.querySelector('.ticket-column-filter__panel').hidden = true;
        menu.querySelector('.ticket-column-filter__toggle').setAttribute('aria-expanded', 'false');
      });
    }

    populateColumnFilterPanel(column) {
      const menu = this.container.querySelector(`[data-column-filter-menu="${column}"]`);
      if (!menu) return;
      const filter = this.filterState.columnFilters[column];
      const operator = menu.querySelector('[data-column-filter-operator]');
      const value = menu.querySelector('[data-column-filter-value]');
      const dateReference = menu.querySelector('[data-column-filter-date-reference]');
      if (operator) operator.value = filter ? filter.operator : operator.options[0].value;
      if (dateReference) dateReference.value = filter && filter.value === 'today' ? 'today' : 'date';
      if (value) value.value = filter && filter.value !== 'today' ? filter.value : (filter && filter.type === 'boolean' ? 'true' : '');
      this.updateDateFilterValueControl(menu);
    }

    updateDateFilterValueControl(container) {
      const dateReference = container.querySelector('[data-column-filter-date-reference]');
      const value = container.querySelector('[data-column-filter-value]');
      if (!dateReference || !value) return;
      const usesToday = dateReference.value === 'today';
      value.hidden = usesToday;
      value.disabled = usesToday;
    }

    updateActiveFilterHeaders() {
      this.container.querySelectorAll('[data-column-filter-menu]').forEach((menu) => {
        const active = Boolean(this.filterState.columnFilters[menu.dataset.columnFilterMenu]);
        menu.classList.toggle('ticket-column-filter--active', active);
      });
      const statusMenu = this.container.querySelector('[data-ticket-status-filter-menu]');
      if (statusMenu) {
        const total = this.container.querySelectorAll('[data-status-filter]').length;
        statusMenu.classList.toggle('ticket-status-filter--active', this.filterState.statuses.length > 0 && this.filterState.statuses.length < total);
      }
    }

    /**
     * Setup the status column's filter menu without triggering table sorting.
     */
    setupStatusFilterMenu() {
      const menu = this.container.querySelector('[data-ticket-status-filter-menu]');
      if (!menu) return;
      const toggle = menu.querySelector('[data-ticket-status-filter-toggle]');
      const panel = menu.querySelector('[data-ticket-status-filter-panel]');
      if (!toggle || !panel) return;

      const closeMenu = () => {
        panel.hidden = true;
        menu.classList.remove('ticket-status-filter--open');
        toggle.setAttribute('aria-expanded', 'false');
      };

      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const willOpen = panel.hidden;
        panel.hidden = !willOpen;
        menu.classList.toggle('ticket-status-filter--open', willOpen);
        toggle.setAttribute('aria-expanded', String(willOpen));
      });
      panel.addEventListener('click', (event) => event.stopPropagation());
      document.addEventListener('click', (event) => {
        if (!menu.contains(event.target)) closeMenu();
      });
      menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          closeMenu();
          toggle.focus();
        }
      });
    }

    /**
     * Setup grouping controls
     */
    setupGroupingControls() {
      const groupBy = document.querySelector('[data-ticket-group-by]');
      if (!groupBy) return;
      const button = groupBy.querySelector('[data-group-by-toggle]');
      const panel = groupBy.querySelector('[data-group-by-panel]');
      const clear = groupBy.querySelector('[data-group-by-clear]');
      if (button && panel) {
        button.addEventListener('click', () => {
          const isOpen = groupBy.classList.contains('ticket-columns--open');
          panel.hidden = isOpen;
          groupBy.classList.toggle('ticket-columns--open', !isOpen);
          button.setAttribute('aria-expanded', String(!isOpen));
        });
        document.addEventListener('click', (event) => {
          if (!groupBy.contains(event.target)) {
            panel.hidden = true;
            groupBy.classList.remove('ticket-columns--open');
            button.setAttribute('aria-expanded', 'false');
          }
        });
      }
      groupBy.querySelectorAll('[data-grouping-field]').forEach((checkbox) => {
        checkbox.addEventListener('change', (event) => {
          const field = event.target.getAttribute('data-grouping-field');
          if (!field) return;
          if (event.target.checked) {
            this.setGrouping([...this.groupingFields.filter((item) => item !== field), field]);
          } else {
            this.setGrouping(this.groupingFields.filter((item) => item !== field));
          }
        });
      });
      if (clear) {
        clear.addEventListener('click', () => this.setGrouping([]));
      }
      this.updateGroupingUI();
    }

    /**
     * Apply filters to the ticket table.
     * For tables with data-table-autoload, filters are applied server-side via the API.
     * For server-rendered tables (e.g. phone search results), CSS-based filtering is used.
     */
    applyFilters() {
      const table = this.container.querySelector('[data-table]');
      if (!table) return;

      if (table.hasAttribute('data-table-autoload')) {
        this._applyFiltersViaApi(table);
      } else {
        this._applyFiltersCss(table);
      }
    }

    /**
     * Apply filters by updating the API URL and triggering a table refresh.
     * Only status filters are applied server-side; priority filtering is deferred
     * to _applyPostLoadFilters() which runs after the API response is rendered.
     */
    _applyFiltersViaApi(table) {
      const currentUrl = table.getAttribute('data-table-refresh-url') || '/api/tickets/dashboard';
      const [baseUrl, queryString = ''] = currentUrl.split('?');
      const params = new URLSearchParams(queryString);

      // Replace any existing status params with the current filter state
      params.delete('status');

      const allStatusValues = Array.from(
        this.container.querySelectorAll('[data-status-filter]')
      ).map(cb => cb.value);

      // Only add status params for a partial selection; all-or-none means no filter
      const isPartialSelection = this.filterState.statuses.length > 0 &&
        this.filterState.statuses.length < allStatusValues.length;
      if (isPartialSelection) {
        this.filterState.statuses.forEach(s => params.append('status', s));
      }

      const newUrl = params.toString() ? `${baseUrl}?${params.toString()}` : baseUrl;
      table.setAttribute('data-table-refresh-url', newUrl);
      table.dispatchEvent(new CustomEvent('table:refresh-request'));
    }

    /**
     * Apply filters using CSS visibility (for server-rendered tables such as phone search results).
     */
    _applyFiltersCss(table) {
      const tbody = table.querySelector('tbody');
      if (!tbody) return;

      const rows = tbody.querySelectorAll('tr:not(.ticket-group-header)');
      let visibleCount = 0;

      rows.forEach(row => {
        let shouldShow = true;

        shouldShow = shouldShow && this.rowMatchesColumnFilters(row);

        // Status filter
        if (this.filterState.statuses.length > 0) {
          const statusCell = row.querySelector('[data-label="Status"]');
          const rowStatus = statusCell ? statusCell.getAttribute('data-value') : '';
          shouldShow = shouldShow && this.filterState.statuses.includes(rowStatus);
        }

        // Priority filter
        if (this.filterState.priorities.length > 0) {
          const priorityCell = row.querySelector('[data-label="Priority"]');
          const rowPriority = priorityCell ? priorityCell.textContent.trim().toLowerCase() : '';
          shouldShow = shouldShow && this.filterState.priorities.some(p => rowPriority.includes(p.toLowerCase()));
        }

        if (shouldShow) {
          row.classList.remove('ticket-filtered-hidden');
          visibleCount++;
        } else {
          row.classList.add('ticket-filtered-hidden');
        }
      });

      this.updateTableInfo(visibleCount, rows.length);

      if (this.groupingField || this.groupingFields.length) {
        this.applyGrouping();
      }
    }

    /**
     * Apply client-side filters (priority) and grouping after the API has populated the table.
     * Called in response to the table:rows-updated event.
     */
    _applyPostLoadFilters() {
      const table = this.container.querySelector('[data-table]');
      if (!table) return;
      const tbody = table.querySelector('tbody');
      if (!tbody) return;

      const rows = tbody.querySelectorAll('tr:not(.ticket-group-header)');
      let visibleCount = 0;

      rows.forEach(row => {
        let shouldShow = true;

        shouldShow = shouldShow && this.rowMatchesColumnFilters(row);

        // Priority filter is client-side only (API does not support priority filtering)
        if (this.filterState.priorities.length > 0) {
          const priorityCell = row.querySelector('[data-label="Priority"]');
          const rowPriority = priorityCell ? priorityCell.textContent.trim().toLowerCase() : '';
          shouldShow = shouldShow && this.filterState.priorities.some(p => rowPriority.includes(p.toLowerCase()));
        }

        if (shouldShow) {
          row.classList.remove('ticket-filtered-hidden');
          visibleCount++;
        } else {
          row.classList.add('ticket-filtered-hidden');
        }
      });

      this.updateTableInfo(visibleCount, rows.length);

      if (this.groupingField || this.groupingFields.length) {
        this.applyGrouping();
      }
    }

    rowMatchesColumnFilters(row) {
      return Object.entries(this.filterState.columnFilters).every(([column, filter]) => {
        const cell = row.querySelector(`[data-column="${column}"]`);
        if (!cell) return false;
        const raw = (cell.getAttribute('data-value') || cell.textContent || '').trim();
        if (filter.type === 'number') {
          const actual = Number(raw);
          const expected = Number(filter.value);
          if (!Number.isFinite(actual) || !Number.isFinite(expected)) return false;
          return { equals: actual === expected, not_equals: actual !== expected, greater: actual > expected,
            greater_equal: actual >= expected, less: actual < expected, less_equal: actual <= expected }[filter.operator] ?? true;
        }
        if (filter.type === 'date') {
          const actual = new Date(raw.replace(' ', 'T'));
          if (Number.isNaN(actual.getTime())) return false;
          if (filter.operator === 'relative') return actual >= new Date(Date.now() - (30 * 86400000)) && actual <= new Date();
          const expected = filter.value === 'today' ? new Date() : new Date(`${filter.value}T00:00:00`);
          if (Number.isNaN(expected.getTime())) return false;
          const actualDay = new Date(actual.getFullYear(), actual.getMonth(), actual.getDate()).getTime();
          const expectedDay = new Date(expected.getFullYear(), expected.getMonth(), expected.getDate()).getTime();
          return { on: actualDay === expectedDay, before: actualDay < expectedDay, after: actualDay > expectedDay,
            on_or_before: actualDay <= expectedDay, on_or_after: actualDay >= expectedDay }[filter.operator] ?? true;
        }
        const actual = raw.toLocaleLowerCase();
        const expected = String(filter.value).toLocaleLowerCase();
        if (filter.type === 'boolean') return actual === expected;
        return { contains: actual.includes(expected), not_contains: !actual.includes(expected), equals: actual === expected,
          not_equals: actual !== expected, starts_with: actual.startsWith(expected), ends_with: actual.endsWith(expected) }[filter.operator] ?? true;
      });
    }

    /**
     * Set grouping field and apply
     */
    setGrouping(fields) {
      const selected = Array.isArray(fields) ? fields : (fields ? [fields] : []);
      const allowed = ['status', 'priority', 'company', 'assigned'];
      this.groupingFields = selected.filter((field, index) => allowed.includes(field) && selected.indexOf(field) === index);
      this.groupingField = this.groupingFields[0] || null;
      this.updateGroupingUI();
      if (this.groupingFields.length) {
        this.applyGrouping();
      } else {
        this.removeGrouping();
      }
    }

    updateGroupingUI() {
      const groupBy = document.querySelector('[data-ticket-group-by]');
      if (!groupBy) return;
      const labels = { status: 'Status', priority: 'Priority', company: 'Company', assigned: 'Assigned' };
      groupBy.querySelectorAll('[data-grouping-field]').forEach((checkbox) => {
        const field = checkbox.getAttribute('data-grouping-field');
        checkbox.checked = this.groupingFields.includes(field);
      });
      const label = groupBy.querySelector('[data-group-by-label]');
      if (label) {
        label.textContent = this.groupingFields.length
          ? `Group By: ${this.groupingFields.map((field) => labels[field] || field).join(' › ')}`
          : 'Group By';
      }
    }

    /**
     * Apply grouping to the ticket table
     */
    applyGrouping() {
      const table = this.container.querySelector('[data-table]');
      if (!table) return;

      const tbody = table.querySelector('tbody');
      if (!tbody) return;

      this.removeGrouping();

      const allRows = Array.from(tbody.querySelectorAll('tr:not(.ticket-group-header)'));
      const fieldMap = {
        status: 'Status',
        priority: 'Priority',
        company: 'Company',
        assigned: 'Assigned'
      };
      const fields = this.groupingFields.length ? this.groupingFields : (this.groupingField ? [this.groupingField] : []);
      if (!fields.length) return;

      const getGroupValue = (row, field) => {
        const label = fieldMap[field];
        const cell = label ? row.querySelector(`[data-label="${label}"]`) : null;
        const value = cell ? (cell.getAttribute('data-value') || cell.textContent.trim()) : '';
        return value || 'Unspecified';
      };

      const buildLevel = (rows, depth, path, fragment) => {
        const field = fields[depth];
        const groups = new Map();
        rows.forEach((row) => {
          const key = getGroupValue(row, field);
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(row);
        });

        Array.from(groups.keys()).sort().forEach((groupKey) => {
          const groupRows = groups.get(groupKey);
          const visibleRowsInGroup = groupRows.filter((row) => !row.classList.contains('ticket-filtered-hidden'));
          const groupId = [...path, groupKey].join('¦');
          const headerRow = document.createElement('tr');
          headerRow.className = 'ticket-group-header';
          headerRow.setAttribute('data-group-key', groupId);
          headerRow.setAttribute('data-group-path', groupId);
          headerRow.setAttribute('data-group-depth', String(depth));
          if (visibleRowsInGroup.length === 0) headerRow.classList.add('ticket-filtered-hidden');

          const headerCell = document.createElement('td');
          headerCell.colSpan = table.querySelector('thead tr').children.length;
          const headerContent = document.createElement('div');
          headerContent.className = 'ticket-group-header__content';
          headerContent.style.paddingLeft = `${depth * 1.5}rem`;
          const toggle = document.createElement('button');
          toggle.type = 'button';
          toggle.className = 'ticket-group-header__toggle';
          toggle.setAttribute('data-group-toggle', groupId);
          toggle.setAttribute('aria-expanded', 'true');
          toggle.innerHTML = '<svg class="ticket-group-header__icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6.2 8.2a1 1 0 0 1 1.4 0L12 12.6l4.4-4.4a1 1 0 0 1 1.4 1.4l-5.1 5.1a1 1 0 0 1-1.4 0L6.2 9.6a1 1 0 0 1 0-1.4z" /></svg>';
          const fieldTitle = document.createElement('span');
          fieldTitle.className = 'ticket-group-header__title ticket-group-header__title--nested';
          fieldTitle.textContent = `${fieldMap[field]}:`;
          const groupTitle = document.createElement('span');
          groupTitle.className = 'ticket-group-header__title';
          groupTitle.textContent = groupKey;
          const count = document.createElement('span');
          count.className = 'ticket-group-header__count';
          count.textContent = `${visibleRowsInGroup.length} ticket${visibleRowsInGroup.length !== 1 ? 's' : ''}`;
          headerContent.append(toggle, fieldTitle, groupTitle, count);
          headerCell.appendChild(headerContent);
          headerRow.appendChild(headerCell);
          fragment.appendChild(headerRow);

          if (depth + 1 < fields.length) {
            buildLevel(groupRows, depth + 1, [...path, groupKey], fragment);
          } else {
            groupRows.forEach((row) => {
              row.setAttribute('data-group', groupId);
              row.setAttribute('data-group-path', groupId);
              fragment.appendChild(row);
            });
          }
        });
      };

      const fragment = document.createDocumentFragment();
      buildLevel(allRows, 0, [], fragment);
      tbody.innerHTML = '';
      tbody.appendChild(fragment);

      tbody.querySelectorAll('[data-group-toggle]').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
          const groupKey = e.currentTarget.getAttribute('data-group-toggle');
          this.toggleGroup(groupKey);
        });
      });
    }

    /**
     * Toggle group visibility
     */
    toggleGroup(groupKey) {
      const tbody = this.container.querySelector('tbody');
      if (!tbody) return;

      const headerRow = Array.from(tbody.querySelectorAll('[data-group-key]'))
        .find((row) => row.getAttribute('data-group-key') === groupKey);
      if (!headerRow) return;
      const toggle = headerRow.querySelector('[data-group-toggle]');
      if (!toggle) return;
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
      const descendantRows = Array.from(tbody.querySelectorAll('tr[data-group-path]'))
        .filter((row) => row !== headerRow && (row.getAttribute('data-group-path') || '').startsWith(groupKey));

      descendantRows.forEach(row => {
        if (isExpanded) {
          row.classList.add('ticket-group-hidden');
        } else {
          row.classList.remove('ticket-group-hidden');
        }
      });

      toggle.setAttribute('aria-expanded', String(!isExpanded));
      headerRow.classList.toggle('ticket-group-header--collapsed', isExpanded);
    }

    /**
     * Remove grouping from table
     */
    removeGrouping() {
      const tbody = this.container.querySelector('tbody');
      if (!tbody) return;

      // Remove group headers
      tbody.querySelectorAll('.ticket-group-header').forEach(el => el.remove());
      
      // Remove group attributes and classes
      tbody.querySelectorAll('tr[data-group], tr[data-group-path]').forEach(row => {
        row.removeAttribute('data-group');
        row.removeAttribute('data-group-path');
        row.classList.remove('ticket-group-hidden');
      });
    }

    /**
     * Apply a saved view
     */
    async applyView(viewId) {
      try {
        const response = await fetch(`${API_BASE}/views/${viewId}`);
        if (response.ok) {
          const view = await response.json();
          this.currentView = view;
          const viewSelect = this.container.querySelector('[data-view-select]');
          if (viewSelect) {
            viewSelect.value = String(view.id);
          }
          this.updateViewActions();
          
          // Apply filters
          if (view.filters) {
            this.filterState.statuses = view.filters.status || [];
            this.filterState.priorities = view.filters.priority || [];
            this.filterState.columnFilters = view.filters.column_filters || {};
            // Update UI checkboxes
            this.updateFilterUI();
          }
          
          // Apply grouping
          this.setGrouping(view.grouping_fields || view.grouping_field || []);
          
          this.applyFilters();
          this.updateViewActions();
        }
      } catch (error) {
        console.error('Failed to apply view:', error);
      }
    }

    /**
     * Apply default view if one exists
     */
    applyDefaultView() {
      const defaultView = this.views.find(v => v.is_default);
      if (defaultView) {
        this.applyView(defaultView.id);
      }
    }

    /**
     * Clear current view
     */
    clearView() {
      this.currentView = null;
      this.updateViewActions();
      this.filterState = {
        statuses: [],
        priorities: [],
        companies: [],
        assignedUsers: [],
        search: '',
        columnFilters: {}
      };
      this.groupingFields = [];
      this.groupingField = null;
      this.updateGroupingUI();
      this.updateFilterUI();
      this.updateActiveFilterHeaders();
      this.removeGrouping();
      this.applyFilters();
    }

    /**
     * Update filter UI to match current state
     */
    updateFilterUI() {
      // Update status checkboxes
      this.container.querySelectorAll('[data-status-filter]').forEach(checkbox => {
        checkbox.checked = this.filterState.statuses.includes(checkbox.value);
      });
      Object.keys(this.filterState.columnFilters).forEach((column) => this.populateColumnFilterPanel(column));
      this.updateActiveFilterHeaders();
    }

    /**
     * Show save view modal
     */
    showSaveViewModal() {
      const modal = document.getElementById('save-view-modal');
      if (modal) {
        modal.removeAttribute('hidden');
        modal.setAttribute('aria-hidden', 'false');
      }
    }

    /**
     * Build the payload used when creating or updating a view.
     */
    buildViewPayload(overrides = {}) {
      return {
        filters: {
          status: this.filterState.statuses,
          priority: this.filterState.priorities,
          column_filters: this.filterState.columnFilters,
        },
        grouping_field: this.groupingField,
        grouping_fields: this.groupingFields,
        sort_field: this.sortField,
        sort_direction: this.sortDirection,
        ...overrides
      };
    }

    /**
     * Save current view
     */
    async saveView(name, description, isDefault) {
      const payload = this.buildViewPayload({
        name,
        description,
        is_default: isDefault
      });

      try {
        const csrfToken = getCsrfToken();
        const response = await fetch(`${API_BASE}/views`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
          body: JSON.stringify(payload)
        });

        if (response.ok) {
          await this.loadViews();
          return true;
        }
      } catch (error) {
        console.error('Failed to save view:', error);
      }
      return false;
    }

    /**
     * Update the selected saved view with the current filters and grouping.
     */
    async updateCurrentView() {
      if (!this.currentView) return false;

      const payload = this.buildViewPayload({
        name: this.currentView.name,
        description: this.currentView.description,
        is_default: Boolean(this.currentView.is_default)
      });

      try {
        const csrfToken = getCsrfToken();
        const response = await fetch(`${API_BASE}/views/${this.currentView.id}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
          body: JSON.stringify(payload)
        });

        if (response.ok) {
          this.currentView = await response.json();
          await this.loadViews(this.currentView.id);
          this.updateViewActions();
          return true;
        }
      } catch (error) {
        console.error('Failed to update view:', error);
      }
      return false;
    }

    /**
     * Delete current view
     */
    async deleteCurrentView() {
      if (!this.currentView) return;

      if (!confirm(`Delete view "${this.currentView.name}"?`)) {
        return;
      }

      try {
        const csrfToken = getCsrfToken();
        const response = await fetch(`${API_BASE}/views/${this.currentView.id}`, {
          method: 'DELETE',
          headers: {
            'X-CSRF-Token': csrfToken,
          },
        });

        if (response.ok) {
          this.clearView();
          await this.loadViews();
        }
      } catch (error) {
        console.error('Failed to delete view:', error);
      }
    }

    /**
     * Render view selector
     */
    renderViewSelector(selectedViewId = null) {
      const viewSelect = this.container.querySelector('[data-view-select]');
      if (!viewSelect) return;

      const activeViewId = selectedViewId || (this.currentView && this.currentView.id);
      viewSelect.innerHTML = '<option value="">Select a view...</option>';
      this.views.forEach(view => {
        const option = document.createElement('option');
        option.value = view.id;
        option.textContent = view.name + (view.is_default ? ' (default)' : '');
        viewSelect.appendChild(option);
      });
      viewSelect.value = activeViewId ? String(activeViewId) : '';
    }

    /**
     * Toggle saved-view actions based on whether a view is loaded.
     */
    updateViewActions() {
      const hasCurrentView = Boolean(this.currentView);
      const saveViewBtn = this.container.querySelector('[data-save-view]');
      const updateViewBtn = this.container.querySelector('[data-update-view]');
      const deleteViewBtn = this.container.querySelector('[data-delete-view]');

      if (saveViewBtn) {
        saveViewBtn.hidden = hasCurrentView;
      }
      if (updateViewBtn) {
        updateViewBtn.hidden = !hasCurrentView;
        updateViewBtn.disabled = !hasCurrentView;
      }
      if (deleteViewBtn) {
        deleteViewBtn.disabled = !hasCurrentView;
      }
    }

    /**
     * Update table info
     */
    updateTableInfo(visible, total) {
      const infoElement = this.container.querySelector('[data-table-info]');
      if (infoElement) {
        infoElement.textContent = `Showing ${visible} of ${total} tickets`;
      }
    }
  }

  // Initialize on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeTicketViews);
  } else {
    initializeTicketViews();
  }

  function initializeTicketViews() {
    const ticketContainers = document.querySelectorAll('[data-ticket-view-manager]');
    ticketContainers.forEach(container => {
      new TicketViewManager(container);
    });
  }
})();
