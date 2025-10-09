(function () {
  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    return getCookie('myportal_session_csrf');
  }

  async function requestJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
        ...(options.headers || {}),
      },
      ...options,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* ignore json parse errors */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  async function requestForm(url, formData) {
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
      headers: {
        'X-CSRF-Token': getCsrfToken(),
      },
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* ignore json parse errors */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  function parsePermissions(value) {
    return value
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  function bindRoleForm() {
    const form = document.getElementById('role-form');
    if (!form) {
      return;
    }
    const idField = form.querySelector('#role-id');
    const nameField = form.querySelector('#role-name');
    const descriptionField = form.querySelector('#role-description');
    const permissionsField = form.querySelector('#role-permissions');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const roleId = idField.value;
      const payload = {
        name: nameField.value.trim(),
        description: descriptionField.value.trim() || null,
        permissions: parsePermissions(permissionsField.value),
      };
      const method = roleId ? 'PATCH' : 'POST';
      const url = roleId ? `/roles/${roleId}` : '/roles';
      try {
        await requestJson(url, { method, body: JSON.stringify(payload) });
        window.location.reload();
      } catch (error) {
        alert(`Unable to save role: ${error.message}`);
      }
    });

    const resetButton = form.querySelector('[data-role-reset]');
    if (resetButton) {
      resetButton.addEventListener('click', () => {
        idField.value = '';
        nameField.value = '';
        descriptionField.value = '';
        permissionsField.value = '';
        nameField.focus();
      });
    }

    document.querySelectorAll('[data-role-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        idField.value = row.dataset.roleId || '';
        nameField.value = row.dataset.roleName || '';
        descriptionField.value = row.dataset.roleDescription || '';
        try {
          const permissions = JSON.parse(row.dataset.rolePermissions || '[]');
          permissionsField.value = Array.isArray(permissions) ? permissions.join(', ') : '';
        } catch (error) {
          permissionsField.value = '';
        }
        nameField.focus();
      });
    });

    document.querySelectorAll('[data-role-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        const roleId = row.dataset.roleId;
        if (!roleId) {
          return;
        }
        if (!confirm('Delete this role? This action cannot be undone.')) {
          return;
        }
        try {
          await requestJson(`/roles/${roleId}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          alert(`Unable to delete role: ${error.message}`);
        }
      });
    });
  }

  function bindMembershipForms() {
    const createForm = document.getElementById('membership-create-form');
    const updateForm = document.getElementById('membership-update-form');
    const companyField = createForm ? createForm.querySelector('input[name="company_id"]') : null;
    const updateCompanyField = updateForm ? updateForm.querySelector('input[name="company_id"]') : null;
    const membershipIdField = updateForm ? updateForm.querySelector('#membership-id') : null;
    const membershipUserDisplay = updateForm ? updateForm.querySelector('#membership-user-display') : null;
    const membershipRoleSelect = updateForm ? updateForm.querySelector('#membership-role-update') : null;
    const membershipStatusSelect = updateForm ? updateForm.querySelector('#membership-status-update') : null;
    const membershipSubmit = updateForm ? updateForm.querySelector('[data-membership-submit]') : null;

    if (createForm) {
      createForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const companyId = companyField ? companyField.value : '';
        if (!companyId) {
          alert('Choose a company before adding memberships.');
          return;
        }
        const formData = new FormData(createForm);
        const payload = {
          user_id: Number(formData.get('user_id')),
          role_id: Number(formData.get('role_id')),
          status: String(formData.get('status')),
        };
        try {
          await requestJson(`/companies/${companyId}/memberships`, {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          window.location.reload();
        } catch (error) {
          alert(`Unable to create membership: ${error.message}`);
        }
      });
    }

    function clearMembershipForm() {
      if (!updateForm) {
        return;
      }
      membershipIdField.value = '';
      membershipUserDisplay.value = '';
      if (membershipRoleSelect) {
        membershipRoleSelect.selectedIndex = 0;
      }
      if (membershipStatusSelect) {
        membershipStatusSelect.selectedIndex = 0;
      }
      if (membershipSubmit) {
        membershipSubmit.disabled = true;
      }
    }

    if (updateForm) {
      updateForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const companyId = updateCompanyField ? updateCompanyField.value : '';
        const membershipId = membershipIdField ? membershipIdField.value : '';
        if (!companyId || !membershipId) {
          return;
        }
        const payload = {
          role_id: Number(membershipRoleSelect.value),
          status: String(membershipStatusSelect.value),
        };
        try {
          await requestJson(`/companies/${companyId}/memberships/${membershipId}`, {
            method: 'PATCH',
            body: JSON.stringify(payload),
          });
          window.location.reload();
        } catch (error) {
          alert(`Unable to update membership: ${error.message}`);
        }
      });

      const clearButton = updateForm.querySelector('[data-membership-clear]');
      if (clearButton) {
        clearButton.addEventListener('click', () => {
          clearMembershipForm();
        });
      }
    }

    document.querySelectorAll('[data-membership-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        if (!updateForm) {
          return;
        }
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        membershipIdField.value = row.dataset.membershipId || '';
        membershipRoleSelect.value = row.dataset.membershipRoleId || '';
        membershipStatusSelect.value = row.dataset.membershipStatus || '';
        const userCell = row.querySelector('[data-label="User"]');
        membershipUserDisplay.value = userCell ? userCell.textContent.trim() : '';
        if (membershipSubmit) {
          membershipSubmit.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-membership-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        const companyId = updateCompanyField ? updateCompanyField.value : '';
        const membershipId = row.dataset.membershipId;
        if (!companyId || !membershipId) {
          return;
        }
        if (!confirm('Remove this membership? The user will immediately lose access.')) {
          return;
        }
        try {
          await requestJson(`/companies/${companyId}/memberships/${membershipId}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          alert(`Unable to delete membership: ${error.message}`);
        }
      });
    });
  }

  function bindCompanyAssignmentControls() {
    document.querySelectorAll('[data-company-permission]').forEach((input) => {
      input.addEventListener('change', async () => {
        const { companyId, userId, field } = input.dataset;
        if (!companyId || !userId || !field) {
          return;
        }
        const formData = new FormData();
        formData.append('field', field);
        formData.append('value', input.checked ? '1' : '0');
        input.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/permission`, formData);
        } catch (error) {
          input.checked = !input.checked;
          alert(`Unable to update permission: ${error.message}`);
        } finally {
          input.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-staff-permission]').forEach((select) => {
      select.addEventListener('change', async () => {
        const { companyId, userId } = select.dataset;
        if (!companyId || !userId) {
          return;
        }
        const formData = new FormData();
        formData.append('permission', select.value);
        select.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/staff-permission`, formData);
        } catch (error) {
          alert(`Unable to update staff permission: ${error.message}`);
        } finally {
          select.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-remove-assignment]').forEach((button) => {
      button.addEventListener('click', async () => {
        const { companyId, userId } = button.dataset;
        if (!companyId || !userId) {
          return;
        }
        if (!confirm('Remove this membership? The user will immediately lose access.')) {
          return;
        }
        const row = button.closest('tr');
        const formData = new FormData();
        button.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/remove`, formData);
          if (row) {
            row.remove();
          }
        } catch (error) {
          alert(`Unable to remove membership: ${error.message}`);
          button.disabled = false;
  function bindApiKeyCopyButtons() {
    document.querySelectorAll('[data-copy-api-key]').forEach((button) => {
      const value = button.getAttribute('data-copy-api-key');
      if (!value) {
        return;
      }
      button.addEventListener('click', async () => {
        const originalText = button.textContent;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(value);
          } else {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = value;
            input.setAttribute('aria-hidden', 'true');
            input.style.position = 'absolute';
            input.style.left = '-1000px';
            document.body.appendChild(input);
            input.select();
            document.execCommand('copy');
            document.body.removeChild(input);
          }
          button.textContent = 'Copied';
          setTimeout(() => {
            button.textContent = originalText;
          }, 2000);
        } catch (error) {
          alert('Unable to copy API key. Please copy it manually.');
        }
      });
    });
  }

  function bindConfirmationButtons() {
    document.querySelectorAll('[data-confirm]').forEach((element) => {
      element.addEventListener('click', (event) => {
        const message = element.getAttribute('data-confirm') || 'Are you sure?';
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });
  }

  function bindAddCompanyModal() {
    const modal = document.getElementById('add-company-modal');
    const openButton = document.querySelector('[data-add-company-modal-open]');
    if (!modal || !openButton) {
      return;
    }

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let previousActiveElement = null;

    function getFocusableElements() {
      return Array.from(modal.querySelectorAll(focusableSelector)).filter((element) => {
        if (element.hasAttribute('disabled')) {
          return false;
        }
        if (element.getAttribute('aria-hidden') === 'true') {
          return false;
        }
        return element.offsetParent !== null;
      });
    }

    function focusFirstElement() {
      const [firstFocusable] = getFocusableElements();
      if (firstFocusable && typeof firstFocusable.focus === 'function') {
        firstFocusable.focus();
      }
    }

    function handleKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }

      const focusable = getFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey) {
        if (activeElement === first) {
          event.preventDefault();
          last.focus();
        }
      } else if (activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openModal() {
      previousActiveElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      openButton.setAttribute('aria-expanded', 'true');
      document.addEventListener('keydown', handleKeydown);
      focusFirstElement();
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      openButton.setAttribute('aria-expanded', 'false');
      document.removeEventListener('keydown', handleKeydown);
      if (previousActiveElement && typeof previousActiveElement.focus === 'function') {
        previousActiveElement.focus();
      } else {
        openButton.focus();
      }
    }

    openButton.setAttribute('aria-expanded', 'false');

    openButton.addEventListener('click', (event) => {
      event.preventDefault();
      openModal();
    });

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindRoleForm();
    bindMembershipForms();
    bindCompanyAssignmentControls();
    bindApiKeyCopyButtons();
    bindConfirmationButtons();
    bindAddCompanyModal();
  });
})();
