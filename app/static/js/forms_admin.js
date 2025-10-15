(function () {
  function parseJson(elementId, fallback) {
    const element = document.getElementById(elementId);
    if (!element) {
      return fallback;
    }
    try {
      const text = element.textContent || element.value || '';
      if (!text.trim()) {
        return fallback;
      }
      return JSON.parse(text);
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return fallback;
    }
  }

  function buildAssignments(data) {
    const assignments = new Map();
    if (!data || typeof data !== 'object') {
      return assignments;
    }
    Object.entries(data).forEach(([formKey, companies]) => {
      const formId = Number(formKey);
      if (!Number.isFinite(formId)) {
        return;
      }
      const companyMap = new Map();
      if (companies && typeof companies === 'object') {
        Object.entries(companies).forEach(([companyKey, userIds]) => {
          const companyId = Number(companyKey);
          if (!Number.isFinite(companyId)) {
            return;
          }
          const userSet = new Set();
          if (Array.isArray(userIds)) {
            userIds.forEach((value) => {
              const userId = Number(value);
              if (Number.isFinite(userId)) {
                userSet.add(userId);
              }
            });
          }
          if (userSet.size > 0) {
            companyMap.set(companyId, userSet);
          }
        });
      }
      if (companyMap.size > 0) {
        assignments.set(formId, companyMap);
      }
    });
    return assignments;
  }

  function getCompanyMap(assignments, formId, create) {
    let companyMap = assignments.get(formId);
    if (!companyMap && create) {
      companyMap = new Map();
      assignments.set(formId, companyMap);
    }
    return companyMap || null;
  }

  function cloneUserSet(assignments, formId, companyId) {
    const companyMap = getCompanyMap(assignments, formId, false);
    if (!companyMap) {
      return new Set();
    }
    const set = companyMap.get(companyId);
    return set ? new Set(set) : new Set();
  }

  function storeAssignment(assignments, formId, companyId, userIds) {
    const companyMap = getCompanyMap(assignments, formId, true);
    if (!companyMap) {
      return;
    }
    if (userIds.length > 0) {
      companyMap.set(companyId, new Set(userIds));
    } else {
      companyMap.delete(companyId);
    }
    if (companyMap.size === 0) {
      assignments.delete(formId);
    }
  }

  function collectCheckedUserIds(companyElement) {
    const checkboxes = companyElement.querySelectorAll('input[type="checkbox"][data-user-checkbox]');
    const selected = new Set();
    checkboxes.forEach((checkbox) => {
      if (checkbox.checked) {
        const userId = Number(checkbox.value);
        if (Number.isFinite(userId)) {
          selected.add(userId);
        }
      }
    });
    return selected;
  }

  function applySelections(companyElement, selectedSet) {
    const checkboxes = companyElement.querySelectorAll('input[type="checkbox"][data-user-checkbox]');
    const lookup = new Set(Array.from(selectedSet || []));
    checkboxes.forEach((checkbox) => {
      const userId = Number(checkbox.value);
      checkbox.checked = lookup.has(userId);
    });
    updateCompanyCount(companyElement, lookup.size);
  }

  function updateCompanyCount(companyElement, count) {
    const target = companyElement.querySelector('[data-selected-count]');
    if (target) {
      target.textContent = String(count);
    }
  }

  function setStatus(companyElement, message, type) {
    const statusElement = companyElement.querySelector('[data-status]');
    if (!statusElement) {
      return;
    }
    statusElement.textContent = message || '';
    statusElement.classList.remove(
      'forms-admin__status--success',
      'forms-admin__status--error',
      'forms-admin__status--pending',
    );
    if (type === 'success') {
      statusElement.classList.add('forms-admin__status--success');
    } else if (type === 'error') {
      statusElement.classList.add('forms-admin__status--error');
    } else if (type === 'pending') {
      statusElement.classList.add('forms-admin__status--pending');
    }
  }

  function refreshFormSummary(assignments, formElement) {
    const formId = Number(formElement.getAttribute('data-form-id'));
    const companiesEl = formElement.querySelector('[data-assignment-companies]');
    const usersEl = formElement.querySelector('[data-assignment-users]');
    if (!Number.isFinite(formId)) {
      if (companiesEl) {
        companiesEl.textContent = '0';
      }
      if (usersEl) {
        usersEl.textContent = '0';
      }
      return;
    }
    const companyMap = assignments.get(formId);
    let companyCount = 0;
    let userCount = 0;
    if (companyMap) {
      companyMap.forEach((userSet) => {
        if (userSet && userSet.size > 0) {
          companyCount += 1;
          userCount += userSet.size;
        }
      });
    }
    if (companiesEl) {
      companiesEl.textContent = String(companyCount);
    }
    if (usersEl) {
      usersEl.textContent = String(userCount);
    }
  }

  async function submitPermissions({
    assignments,
    formId,
    companyId,
    requestedSet,
    previousSet,
    formElement,
    companyElement,
  }) {
    const buttons = companyElement.querySelectorAll('[data-save-permissions], [data-clear-permissions]');
    buttons.forEach((button) => {
      button.disabled = true;
    });
    setStatus(companyElement, 'Saving…', 'pending');

    const payload = { user_ids: Array.from(requestedSet) };
    try {
      const response = await fetch(`/api/forms/${formId}/companies/${companyId}/permissions`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const responseText = await response.text();
      if (!response.ok) {
        let message = 'Unable to update permissions.';
        if (responseText) {
          try {
            const parsed = JSON.parse(responseText);
            if (parsed && typeof parsed === 'object') {
              if (Array.isArray(parsed.detail)) {
                message = parsed.detail
                  .map((item) => (typeof item === 'string' ? item : item?.msg))
                  .filter(Boolean)
                  .join(', ');
              } else if (parsed.detail) {
                message = String(parsed.detail);
              } else {
                message = responseText;
              }
            } else {
              message = responseText;
            }
          } catch (error) {
            message = responseText;
          }
        }
        throw new Error(message || 'Unable to update permissions.');
      }

      let data;
      if (responseText) {
        try {
          data = JSON.parse(responseText);
        } catch (error) {
          data = [];
        }
      } else {
        data = [];
      }

      const nextUserIds = Array.isArray(data)
        ? data
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
        : [];

      storeAssignment(assignments, formId, companyId, nextUserIds);
      applySelections(companyElement, new Set(nextUserIds));
      setStatus(
        companyElement,
        nextUserIds.length === 0
          ? 'Access removed for this company.'
          : `Saved access for ${nextUserIds.length} ${nextUserIds.length === 1 ? 'user' : 'users'}.`,
        'success',
      );
      refreshFormSummary(assignments, formElement);
    } catch (error) {
      applySelections(companyElement, previousSet);
      setStatus(companyElement, error instanceof Error ? error.message : 'Unable to update permissions.', 'error');
    } finally {
      buttons.forEach((button) => {
        button.disabled = false;
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const assignments = buildAssignments(parseJson('forms-admin-permissions', {}));
    const formSections = document.querySelectorAll('[data-form-assignment]');

    formSections.forEach((formElement) => {
      const formId = Number(formElement.getAttribute('data-form-id'));
      if (!Number.isFinite(formId)) {
        return;
      }
      const companySections = formElement.querySelectorAll('[data-company-id]');
      companySections.forEach((companyElement) => {
        const companyId = Number(companyElement.getAttribute('data-company-id'));
        if (!Number.isFinite(companyId)) {
          return;
        }

        const savedSelection = cloneUserSet(assignments, formId, companyId);
        applySelections(companyElement, savedSelection);
        setStatus(companyElement, '', null);

        const checkboxes = companyElement.querySelectorAll('input[type="checkbox"][data-user-checkbox]');
        checkboxes.forEach((checkbox) => {
          checkbox.addEventListener('change', () => {
            const currentSelection = collectCheckedUserIds(companyElement);
            updateCompanyCount(companyElement, currentSelection.size);
            setStatus(companyElement, '', null);
          });
        });

        const saveButton = companyElement.querySelector('[data-save-permissions]');
        if (saveButton) {
          saveButton.addEventListener('click', () => {
            const previousSet = cloneUserSet(assignments, formId, companyId);
            const requestedSet = collectCheckedUserIds(companyElement);
            submitPermissions({
              assignments,
              formId,
              companyId,
              requestedSet,
              previousSet,
              formElement,
              companyElement,
            });
          });
        }

        const clearButton = companyElement.querySelector('[data-clear-permissions]');
        if (clearButton) {
          clearButton.addEventListener('click', () => {
            const previousSet = cloneUserSet(assignments, formId, companyId);
            const requestedSet = new Set();
            applySelections(companyElement, requestedSet);
            submitPermissions({
              assignments,
              formId,
              companyId,
              requestedSet,
              previousSet,
              formElement,
              companyElement,
            });
          });
        }
      });

      refreshFormSummary(assignments, formElement);
    });
  });
})();
