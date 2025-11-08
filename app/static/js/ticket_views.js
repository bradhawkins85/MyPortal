/**
 * Ticket View Management
 * Handles saving, loading, and managing ticket filter and grouping views
 */
(function () {
  'use strict';

  const API_BASE = '/api/tickets';

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
      this.groupingField = null;
      this.sortField = null;
      this.sortDirection = 'asc';
      
      this.init();
    }

    async init() {
      await this.loadViews();
      this.setupEventListeners();
      this.setupStatusFilters();
      this.setupGroupingControls();
      this.applyDefaultView();
    }

    /**
     * Load all saved views from the API
     */
    async loadViews() {
      try {
        const response = await fetch(`${API_BASE}/views`);
        if (response.ok) {
          const data = await response.json();
          this.views = data.items || [];
          this.renderViewSelector();
        }
      } catch (error) {
        console.error('Failed to load ticket views:', error);
      }
    }

    /**
     * Setup event listeners for UI controls
     */
    setupEventListeners() {
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

      // Grouping selector
      const groupingSelect = this.container.querySelector('[data-grouping-select]');
      if (groupingSelect) {
        groupingSelect.addEventListener('change', (e) => {
          this.setGrouping(e.target.value);
        });
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
      // Grouping selector is already in the template, just verify it exists
      const groupingSelect = this.container.querySelector('[data-grouping-select]');
      if (groupingSelect) {
        groupingSelect.addEventListener('change', (e) => {
          this.setGrouping(e.target.value);
        });
      }
    }

    /**
     * Apply filters to the ticket table
     */
    applyFilters() {
      const table = this.container.querySelector('[data-table]');
      if (!table) return;

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

      // Update table info
      this.updateTableInfo(visibleCount, rows.length);

      // Apply grouping if set
      if (this.groupingField) {
        this.applyGrouping();
      }
    }

    /**
     * Set grouping field and apply
     */
    setGrouping(field) {
      this.groupingField = field || null;
      if (this.groupingField) {
        this.applyGrouping();
      } else {
        this.removeGrouping();
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

      // Remove existing grouping
      this.removeGrouping();

      // Get ALL rows (including filtered ones) to preserve them
      const allRows = Array.from(tbody.querySelectorAll('tr:not(.ticket-group-header)'));
      
      // Group ALL rows by the selected field (including hidden ones)
      const groups = {};
      const fieldMap = {
        'status': 'Status',
        'priority': 'Priority',
        'company': 'Company',
        'assigned': 'Assigned'
      };
      const fieldLabel = fieldMap[this.groupingField];

      allRows.forEach(row => {
        const cell = row.querySelector(`[data-label="${fieldLabel}"]`);
        let groupKey = cell ? (cell.getAttribute('data-value') || cell.textContent.trim()) : 'Unspecified';
        
        if (!groups[groupKey]) {
          groups[groupKey] = [];
        }
        groups[groupKey].push(row);
      });

      // Create grouped structure
      const fragment = document.createDocumentFragment();
      const groupKeys = Object.keys(groups).sort();

      groupKeys.forEach(groupKey => {
        // Count only visible rows for the header
        const visibleRowsInGroup = groups[groupKey].filter(row => !row.classList.contains('ticket-filtered-hidden'));
        
        // Create group header row
        const headerRow = document.createElement('tr');
        headerRow.className = 'ticket-group-header';
        headerRow.setAttribute('data-group-key', groupKey);
        
        const headerCell = document.createElement('td');
        headerCell.colSpan = table.querySelector('thead tr').children.length;
        headerCell.innerHTML = `
          <div class="ticket-group-header__content">
            <button type="button" class="ticket-group-header__toggle" data-group-toggle="${groupKey}" aria-expanded="true">
              <svg class="ticket-group-header__icon" viewBox="0 0 24 24" width="20" height="20">
                <path d="M6.2 8.2a1 1 0 0 1 1.4 0L12 12.6l4.4-4.4a1 1 0 0 1 1.4 1.4l-5.1 5.1a1 1 0 0 1-1.4 0L6.2 9.6a1 1 0 0 1 0-1.4z" />
              </svg>
            </button>
            <span class="ticket-group-header__title">${groupKey}</span>
            <span class="ticket-group-header__count">${visibleRowsInGroup.length} ticket${visibleRowsInGroup.length !== 1 ? 's' : ''}</span>
          </div>
        `;
        headerRow.appendChild(headerCell);
        fragment.appendChild(headerRow);

        // Add ALL rows for this group (including filtered ones)
        groups[groupKey].forEach(row => {
          row.setAttribute('data-group', groupKey);
          fragment.appendChild(row);
        });
      });

      // Clear and repopulate tbody
      tbody.innerHTML = '';
      tbody.appendChild(fragment);

      // Attach toggle listeners
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

      const headerRow = tbody.querySelector(`[data-group-key="${groupKey}"]`);
      const groupRows = tbody.querySelectorAll(`tr[data-group="${groupKey}"]`);
      const toggle = headerRow.querySelector('[data-group-toggle]');
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';

      groupRows.forEach(row => {
        if (isExpanded) {
          row.classList.add('ticket-group-hidden');
        } else {
          row.classList.remove('ticket-group-hidden');
        }
      });

      toggle.setAttribute('aria-expanded', !isExpanded);
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
      tbody.querySelectorAll('tr[data-group]').forEach(row => {
        row.removeAttribute('data-group');
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
          
          // Apply filters
          if (view.filters) {
            this.filterState.statuses = view.filters.status || [];
            this.filterState.priorities = view.filters.priority || [];
            // Update UI checkboxes
            this.updateFilterUI();
          }
          
          // Apply grouping
          if (view.grouping_field) {
            this.setGrouping(view.grouping_field);
            const groupingSelect = this.container.querySelector('[data-grouping-select]');
            if (groupingSelect) {
              groupingSelect.value = view.grouping_field;
            }
          }
          
          this.applyFilters();
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
      this.filterState = {
        statuses: [],
        priorities: [],
        companies: [],
        assignedUsers: [],
        search: ''
      };
      this.groupingField = null;
      
      // Remove all filter classes from rows
      const tbody = this.container.querySelector('tbody');
      if (tbody) {
        tbody.querySelectorAll('tr').forEach(row => {
          row.classList.remove('ticket-filtered-hidden', 'ticket-group-hidden');
        });
      }
      
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
     * Save current view
     */
    async saveView(name, description, isDefault) {
      const payload = {
        name,
        description,
        filters: {
          status: this.filterState.statuses,
          priority: this.filterState.priorities,
        },
        grouping_field: this.groupingField,
        sort_field: this.sortField,
        sort_direction: this.sortDirection,
        is_default: isDefault
      };

      try {
        const response = await fetch(`${API_BASE}/views`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
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
     * Delete current view
     */
    async deleteCurrentView() {
      if (!this.currentView) return;

      if (!confirm(`Delete view "${this.currentView.name}"?`)) {
        return;
      }

      try {
        const response = await fetch(`${API_BASE}/views/${this.currentView.id}`, {
          method: 'DELETE'
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
    renderViewSelector() {
      const viewSelect = this.container.querySelector('[data-view-select]');
      if (!viewSelect) return;

      viewSelect.innerHTML = '<option value="">Select a view...</option>';
      this.views.forEach(view => {
        const option = document.createElement('option');
        option.value = view.id;
        option.textContent = view.name + (view.is_default ? ' (default)' : '');
        viewSelect.appendChild(option);
      });
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
