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
      const url = btn.getAttribute('data-url');
      if (typeof showForm === 'function' && url) {
        showForm(url, btn);
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
      function initDataTable(table) {
        if (table.dataset.datatableInitialized) return;
        new DataTable(table);
        table.dataset.datatableInitialized = 'true';
      }

      function adjustDataTables() {
        DataTable.tables({ visible: true, api: true }).columns.adjust();
      }

      function initVisibleTables() {
        document.querySelectorAll('table').forEach((table) => {
          if (
            !table.closest('#cron-generator-container') &&
            table.offsetParent !== null
          ) {
            initDataTable(table);
          }
        });
        adjustDataTables();
      }

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
        return originalFetch(input, init);
      };
    }

    // Company switcher form submission
    const companySwitcher = document.getElementById('company-switcher');
    if (companySwitcher) {
      companySwitcher.addEventListener('change', () => {
        companySwitcher.form?.submit();
      });
    }
  });

