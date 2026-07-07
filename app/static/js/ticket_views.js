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
        search: ''
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
        });
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
        search: ''
      };
      this.groupingFields = [];
      this.groupingField = null;
      this.updateGroupingUI();
      this.updateFilterUI();
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
