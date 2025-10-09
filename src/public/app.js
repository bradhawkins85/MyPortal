// Global event listeners to avoid inline handlers per CSP

document.addEventListener('DOMContentLoaded', () => {
  // Navigate to URL stored in data-href
  document.querySelectorAll('[data-href]').forEach(el => {
    el.addEventListener('click', () => {
      const href = el.getAttribute('data-href');
      if (href) {
        window.location.href = href;
      }
    });
  });

  // Submit parent form on change
  document.querySelectorAll('[data-submit-on-change]').forEach(el => {
    el.addEventListener('change', function () {
      this.form?.submit();
    });
  });

  // Confirmation dialogs for form submissions
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', (e) => {
      const msg = form.getAttribute('data-confirm') || 'Are you sure?';
      if (!window.confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // Show forms in iframe
  document.querySelectorAll('[data-open-form]').forEach(btn => {
    btn.addEventListener('click', () => {
      const formUrl = btn.getAttribute('data-form-url');
      if (typeof showForm === 'function' && formUrl) {
        showForm(formUrl, btn);
      }
    });
  });

  // Modals and other actions
  document.querySelectorAll('[data-open-edit-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-open-edit-modal');
      if (typeof openEditModal === 'function') {
        openEditModal(id);
      }
    });
  });

  document.querySelectorAll('[data-open-visibility-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-open-visibility-modal');
      if (typeof openVisibilityModal === 'function') {
        openVisibilityModal(id);
      }
    });
  });

  document.querySelectorAll('[data-close-edit-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (typeof closeEditModal === 'function') {
        closeEditModal();
      }
    });
  });

  document.querySelectorAll('[data-close-visibility-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (typeof closeVisibilityModal === 'function') {
        closeVisibilityModal();
      }
    });
  });

  document.querySelectorAll('[data-open-group-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-open-group-modal');
      if (typeof openGroupModal === 'function') {
        openGroupModal(parseInt(id, 10));
      }
    });
  });

    document.querySelectorAll('[data-close-group-modal]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (typeof closeGroupModal === 'function') {
          closeGroupModal();
        }
      });
    });

    // Initialize DataTables for visible tables
    if (typeof DataTable !== 'undefined') {
      DataTable.defaults.pageLength = 5;
      DataTable.defaults.lengthMenu = [5, 10, 25, 50, 100];

      const viewportAwareTables = new Map();

      const parsePositiveInt = (value) => {
        if (!value) return null;
        const parsed = parseInt(value, 10);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
      };

      const getViewportConfig = (table) => {
        const minRows = parsePositiveInt(table.dataset.viewportMinRows) ?? 5;
        const maxRowsCandidate = parsePositiveInt(table.dataset.viewportMaxRows);
        const step = parsePositiveInt(table.dataset.viewportStep) ?? 5;
        const maxRows = maxRowsCandidate && maxRowsCandidate >= minRows ? maxRowsCandidate : null;
        return { minRows, maxRows, step };
      };

      const calculateViewportPageLength = (table, config) => {
        const { minRows, maxRows, step } = config;
        const container = table.closest('.page-body');
        const tableRect = table.getBoundingClientRect();
        let availableHeight = window.innerHeight - tableRect.top - 160;
        if (container) {
          const containerRect = container.getBoundingClientRect();
          availableHeight = containerRect.bottom - tableRect.top - 48;
        }

        if (!Number.isFinite(availableHeight) || availableHeight <= 0) {
          const fallbackRows = maxRows ?? minRows;
          availableHeight = fallbackRows * 48;
        }

        const sampleRow = table.querySelector('tbody tr') || table.querySelector('thead tr');
        const rowHeight = sampleRow?.getBoundingClientRect().height || 48;
        let computed = Math.floor(availableHeight / rowHeight);

        if (!Number.isFinite(computed) || computed <= 0) {
          computed = minRows;
        }

        computed = Math.max(minRows, computed);

        if (step > 1) {
          computed = Math.floor(computed / step) * step;
        }

        if (computed < minRows) {
          computed = minRows;
        }

        if (maxRows) {
          computed = Math.min(computed, maxRows);
        }

        return computed;
      };

      const buildLengthMenu = (config, baseLength) => {
        const { minRows, maxRows, step } = config;
        const values = new Set();
        const defaultUpper = minRows + step * 10;
        const upperBound = maxRows ?? Math.max(baseLength + step * 2, defaultUpper);

        for (let value = minRows; value <= upperBound; value += step) {
          values.add(value);
        }

        [5, 10, 25, 50, 100].forEach((preset) => {
          if (!maxRows || preset <= maxRows) {
            const adjusted = Math.max(minRows, Math.floor(preset / step) * step);
            values.add(adjusted);
          }
        });

        values.add(baseLength);
        if (maxRows) {
          values.add(maxRows);
        }

        return Array.from(values)
          .filter((val) => Number.isFinite(val) && val > 0)
          .sort((a, b) => a - b);
      };

      const updateViewportTable = (table) => {
        const entry = viewportAwareTables.get(table);
        if (!entry) return;
        const { dt, config } = entry;
        const desiredLength = calculateViewportPageLength(table, config);
        if (!Number.isFinite(desiredLength) || desiredLength <= 0) return;
        if (dt.page.len() !== desiredLength) {
          dt.page.len(desiredLength).draw(false);
        }
      };

      const initDataTable = (table) => {
        if (table.dataset.datatableInitialized) return;
        const isViewportAware = table.dataset.viewportPagination === 'true';
        if (isViewportAware) {
          const config = getViewportConfig(table);
          const initialLength = calculateViewportPageLength(table, config);
          const lengthMenu = buildLengthMenu(config, initialLength);
          const dt = new DataTable(table, {
            pageLength: initialLength,
            lengthMenu,
          });
          viewportAwareTables.set(table, { dt, config });
          table.dataset.datatableInitialized = 'true';
          queueMicrotask(() => updateViewportTable(table));
          dt.on('draw', () => updateViewportTable(table));
        } else {
          new DataTable(table);
          table.dataset.datatableInitialized = 'true';
        }
      };

      const adjustDataTables = () => {
        const api = DataTable.tables({ visible: true, api: true });
        api.columns.adjust();
        viewportAwareTables.forEach((_, table) => updateViewportTable(table));
      };

      const initVisibleTables = () => {
        document.querySelectorAll('table').forEach((table) => {
          if (
            !table.closest('#cron-generator-container') &&
            table.offsetParent !== null
          ) {
            initDataTable(table);
          }
        });
        adjustDataTables();
      };

      initVisibleTables();
      setTimeout(initVisibleTables, 0);
      window.addEventListener('resize', adjustDataTables);

      document.querySelectorAll('.tabs button').forEach((btn) => {
        btn.addEventListener('click', () => {
          const tab = document.getElementById(btn.dataset.tab);
          tab?.querySelectorAll('table').forEach((table) => {
            if (!table.closest('#cron-generator-container')) {
              initDataTable(table);
            }
          });
          adjustDataTables();
        });
      });
    }

    // CSRF token handling
    const tokenMeta = document.querySelector('meta[name="csrf-token"]');
    const token = tokenMeta?.getAttribute('content');
    if (token) {
      document.querySelectorAll('form[method="post"]').forEach((form) => {
        if (!form.querySelector('input[name="_csrf"]')) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = '_csrf';
          input.value = token;
          form.appendChild(input);
        }
      });

        const originalFetch = window.fetch.bind(window);
        window.fetch = (input, init = {}) => {
          init.headers = init.headers || {};
          if (init.headers instanceof Headers) {
            if (!init.headers.has('X-CSRF-Token')) {
              init.headers.set('X-CSRF-Token', token);
            }
          } else if (!('X-CSRF-Token' in init.headers)) {
            init.headers['X-CSRF-Token'] = token;
          }
          if (!init.credentials) {
            init.credentials = 'same-origin';
          }
            const url = typeof input === 'string' ? input : input.url;
            const method = init.method || (typeof input !== 'string' && input.method) || 'GET';
            return originalFetch(input, init);
          };
        }

    // Company switcher form submission
    const companySwitcher = document.getElementById('company-switcher');
    if (companySwitcher) {
      companySwitcher.addEventListener('change', () => {
        const form = companySwitcher.form;
        if (!form) return;
        const selected = companySwitcher.value;
        if (!selected) return;
        let hidden = form.querySelector('input[data-company-switcher-hidden="true"]');
        if (!hidden) {
          hidden = document.createElement('input');
          hidden.type = 'hidden';
          hidden.name = 'companyId';
          hidden.setAttribute('data-company-switcher-hidden', 'true');
          form.appendChild(hidden);
        }
        hidden.value = selected;
        form.submit();
      });
    }
  });

