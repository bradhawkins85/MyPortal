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
        ...(options && options.headers ? options.headers : {}),
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
        // ignore json parsing errors
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    try {
      return await response.json();
    } catch (error) {
      return null;
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

  function getField(id) {
    return document.getElementById(id);
  }

  function setValue(element, value) {
    if (!element) {
      return;
    }
    element.value = value ?? '';
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    const staffList = parseJson('staff-data', []);
    const flags = parseJson('staff-flags', {});
    const staffById = new Map(staffList.map((member) => [member.id, member]));

    submitOnChange(container);

    const editModal = document.getElementById('staff-edit-modal');
    const editForm = document.getElementById('staff-edit-form');
    const editIdField = getField('edit-staff-id');

    const editFields = {
      first_name: getField('edit-first-name'),
      last_name: getField('edit-last-name'),
      email: getField('edit-email'),
      mobile_phone: getField('edit-mobile'),
      date_onboarded: getField('edit-date-onboarded'),
      date_offboarded: getField('edit-date-offboarded'),
      enabled: getField('edit-enabled'),
      street: getField('edit-street'),
      city: getField('edit-city'),
      state: getField('edit-state'),
      postcode: getField('edit-postcode'),
      country: getField('edit-country'),
      department: getField('edit-department'),
      job_title: getField('edit-job-title'),
      org_company: getField('edit-company'),
      manager_name: getField('edit-manager-name'),
      account_action: getField('edit-account-action'),
    };

    bindModalDismissal(editModal);

    if (flags && flags.isAdmin && !flags.isSuperAdmin) {
      const offboardField = editFields.date_offboarded;
      if (offboardField) {
        offboardField.addEventListener('change', () => {
          if (offboardField.value) {
            alert(
              'The offboard will be scheduled immediately for the requested date and time. Contact IT to make changes after saving.'
            );
          }
        });
      }
    }

    container.querySelectorAll('[data-staff-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = Number(button.getAttribute('data-staff-edit'));
        const member = staffById.get(id);
        if (!member || !editForm || !editIdField) {
          return;
        }
        editIdField.value = String(id);
        setValue(editFields.first_name, member.first_name);
        setValue(editFields.last_name, member.last_name);
        setValue(editFields.email, member.email);
        setValue(editFields.mobile_phone, member.mobile_phone);
        setValue(editFields.date_onboarded, member.date_onboarded ? member.date_onboarded.slice(0, 10) : '');
        setValue(editFields.date_offboarded, member.date_offboarded ? member.date_offboarded.slice(0, 16) : '');
        if (editFields.enabled) {
          editFields.enabled.checked = Boolean(member.enabled);
        }
        setValue(editFields.street, member.street);
        setValue(editFields.city, member.city);
        setValue(editFields.state, member.state);
        setValue(editFields.postcode, member.postcode);
        setValue(editFields.country, member.country);
        setValue(editFields.department, member.department);
        setValue(editFields.job_title, member.job_title);
        setValue(editFields.org_company, member.org_company);
        setValue(editFields.manager_name, member.manager_name);
        if (editFields.account_action) {
          editFields.account_action.value = member.account_action || 'Onboard Requested';
        }
        openModal(editModal);
      });
    });

    if (editForm) {
      editForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const staffId = editIdField ? editIdField.value : '';
        if (!staffId) {
          return;
        }
        const payload = {
          firstName: editFields.first_name ? editFields.first_name.value : '',
          lastName: editFields.last_name ? editFields.last_name.value : '',
          email: editFields.email ? editFields.email.value : '',
          mobilePhone: editFields.mobile_phone ? editFields.mobile_phone.value : '',
          dateOnboarded: editFields.date_onboarded ? editFields.date_onboarded.value : '',
          dateOffboarded: editFields.date_offboarded ? editFields.date_offboarded.value : '',
          enabled: editFields.enabled ? editFields.enabled.checked : false,
          street: editFields.street ? editFields.street.value : '',
          city: editFields.city ? editFields.city.value : '',
          state: editFields.state ? editFields.state.value : '',
          postcode: editFields.postcode ? editFields.postcode.value : '',
          country: editFields.country ? editFields.country.value : '',
          department: editFields.department ? editFields.department.value : '',
          jobTitle: editFields.job_title ? editFields.job_title.value : '',
          company: editFields.org_company ? editFields.org_company.value : '',
          managerName: editFields.manager_name ? editFields.manager_name.value : '',
          accountAction: editFields.account_action ? editFields.account_action.value : '',
        };
        try {
          await requestJson(`/staff/${staffId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          });
          window.location.reload();
        } catch (error) {
          alert(`Unable to update staff member: ${error.message}`);
        }
      });
    }

    container.querySelectorAll('[data-staff-verify]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-verify');
        if (!id) {
          return;
        }
        try {
          const data = await requestJson(`/staff/${id}/verify`, { method: 'POST' });
          const row = button.closest('tr');
          const codeCell = row ? row.querySelector('.verification-code') : null;
          if (codeCell) {
            codeCell.textContent = data && data.code ? data.code : '';
            codeCell.classList.toggle('text-success', data && data.status === 202);
          }
          if (!data || data.status !== 202) {
            alert('Verification code dispatched, but upstream delivery may have failed.');
          }
        } catch (error) {
          alert(`Failed to send verification code: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-invite]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (!confirm('Send an invitation to this staff member?')) {
          return;
        }
        const id = button.getAttribute('data-staff-invite');
        if (!id) {
          return;
        }
        try {
          await requestJson(`/staff/${id}/invite`, { method: 'POST' });
          alert('Invitation sent successfully.');
        } catch (error) {
          alert(`Failed to send invitation: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (!confirm('Delete this staff record? This action cannot be undone.')) {
          return;
        }
        const id = button.getAttribute('data-staff-delete');
        if (!id) {
          return;
        }
        try {
          await requestJson(`/staff/${id}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          alert(`Failed to delete staff record: ${error.message}`);
        }
      });
    });

  });
})();
