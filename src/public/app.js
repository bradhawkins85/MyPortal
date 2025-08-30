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
});

