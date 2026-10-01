(function () {
  'use strict';

  const DESCRIPTION_MAX = 500;
  const TECHNICIAN_KEY = 'menu.admin.technician';

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = value;
      } else {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach((child) => {
      if (child !== null && child !== undefined && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    });
    return node;
  }

  function parseJson(id) {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (error) {
      return null;
    }
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    const match = document.cookie.match(/(?:^|; )myportal_session_csrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function requestJson(url, options) {
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    };
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetch(url, { credentials: 'same-origin', headers, ...options });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail) ? data.detail.map((entry) => entry.msg || entry).join(', ') : data.detail;
        }
      } catch (error) {
        /* keep the status text */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  function plural(count, word) {
    return `${count} ${word}${count === 1 ? '' : 's'}`;
  }

  function bindCatalogue(root) {
    const search = document.querySelector('[data-role-search]');
    const filter = document.querySelector('[data-role-filter]');
    const items = Array.from(root.querySelectorAll('[data-role-item]'));
    const noResults = root.querySelector('[data-role-no-results]');
    if (!items.length) {
      return;
    }

    function apply() {
      const terms = normalize(search && search.value).split(/\s+/).filter(Boolean);
      const mode = filter ? filter.value : '';
      let visible = 0;
      items.forEach((item) => {
        const text = normalize(item.dataset.searchText);
        const matchesText = terms.every((term) => text.includes(term));
        const matchesMode = !mode
          || (mode === 'unassigned' ? item.dataset.roleMembers === '0' : item.dataset.roleKind === mode);
        item.hidden = !(matchesText && matchesMode);
        if (!item.hidden) {
          visible += 1;
        }
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
    }

    if (search) {
      search.addEventListener('input', apply);
    }
    if (filter) {
      filter.addEventListener('change', apply);
    }
  }

  function bindEditor(data) {
    const modal = document.getElementById('role-modal');
    const form = modal && modal.querySelector('[data-role-form]');
    if (!form) {
      return;
    }
    const roles = new Map((data.roles || []).map((role) => [String(role.id), role]));
    const catalogue = data.catalogue || [];
    const levelsByKey = new Map(catalogue.map((item) => [item.key, item.levels || ['none', 'read', 'write']]));
    const levelLabels = data.levelLabels || {};
    const labelFor = (key, level) => ((levelLabels[key] || levelLabels.default || {})[level] || level);

    const eyebrow = form.querySelector('[data-role-eyebrow]');
    const title = form.querySelector('[data-role-title]');
    const subtitle = form.querySelector('[data-role-subtitle]');
    const nameInput = form.querySelector('[data-role-input="name"]');
    const nameHelp = form.querySelector('[data-role-name-help]');
    const nameError = form.querySelector('[data-role-error="name"]');
    const descriptionInput = form.querySelector('[data-role-input="description"]');
    const descriptionCounter = form.querySelector('[data-role-description-counter]');
    const tabs = Array.from(form.querySelectorAll('[data-role-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-role-panel]'));
    const accessCount = form.querySelector('[data-role-access-count]');
    const permSearch = form.querySelector('[data-role-perm-search]');
    const grantedOnly = form.querySelector('[data-role-granted-only]');
    const permRows = Array.from(form.querySelectorAll('[data-role-perm]'));
    const groups = Array.from(form.querySelectorAll('[data-role-group]'));
    const permNoResults = form.querySelector('[data-role-perm-no-results]');
    const levelInputs = Array.from(form.querySelectorAll('[data-permission-level]'));
    const previewName = form.querySelector('[data-role-preview-name]');
    const previewMembers = form.querySelector('[data-role-preview-members]');
    const previewRules = form.querySelector('[data-role-preview-rules]');
    const previewCounts = {
      write: form.querySelector('[data-role-preview-count="write"]'),
      read: form.querySelector('[data-role-preview-count="read"]'),
      none: form.querySelector('[data-role-preview-count="none"]'),
    };
    const deleteButton = form.querySelector('[data-role-delete]');
    const submitButton = form.querySelector('[data-role-submit]');
    const status = form.querySelector('[data-role-status]');

    let current = { mode: 'create', role: null };
    let snapshot = '';
    let lastTrigger = null;
    let saving = false;

    function getPermissions() {
      const permissions = {};
      levelInputs.forEach((input) => {
        if (input.checked && input.value !== 'none') {
          permissions[input.dataset.permissionKey] = input.value;
        }
      });
      return permissions;
    }

    // Every key is submitted, so an explicit "none" wins over the compatibility
    // mapping that would otherwise copy access from an older owning permission.
    function getPayloadPermissions() {
      const permissions = {};
      levelInputs.forEach((input) => {
        if (input.checked) {
          permissions[input.dataset.permissionKey] = input.value;
        }
      });
      return permissions;
    }

    function setLevel(key, level) {
      const levels = levelsByKey.get(key) || [];
      // Yes/No permissions have no read level; stored "read" values mean Yes.
      const target = levels.includes(level) ? level : (level === 'read' && levels.includes('write') ? 'write' : 'none');
      levelInputs.forEach((input) => {
        if (input.dataset.permissionKey === key) {
          input.checked = input.value === target;
        }
      });
    }

    function setPermissions(permissions) {
      catalogue.forEach((item) => setLevel(item.key, (permissions || {})[item.key] || 'none'));
    }

    function serialize() {
      return JSON.stringify([nameInput.value.trim(), descriptionInput.value.trim(), getPermissions()]);
    }

    function selectTab(name, focus) {
      tabs.forEach((tab) => {
        const active = tab.dataset.roleTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.rolePanel !== name;
      });
    }

    function filterPermissions() {
      const terms = normalize(permSearch.value).split(/\s+/).filter(Boolean);
      const onlyGranted = grantedOnly.checked;
      const permissions = getPermissions();
      let visible = 0;
      permRows.forEach((row) => {
        const text = normalize(row.dataset.searchText);
        const show = terms.every((term) => text.includes(term)) && (!onlyGranted || permissions[row.dataset.rolePerm]);
        row.hidden = !show;
        if (show) {
          visible += 1;
        }
      });
      groups.forEach((group) => {
        group.hidden = !group.querySelector('[data-role-perm]:not([hidden])');
      });
      permNoResults.hidden = visible > 0;
    }

    function validateName() {
      const value = nameInput.value.trim();
      let message = '';
      if (value.length < 2) {
        message = 'Enter a role name of at least 2 characters.';
      } else if (value.length > 100) {
        message = 'Keep the role name to 100 characters or fewer.';
      } else {
        const clash = Array.from(roles.values()).find((role) => normalize(role.name) === normalize(value)
          && !(current.mode === 'edit' && current.role && role.id === current.role.id));
        if (clash) {
          message = `A role called ${clash.name} already exists.`;
        }
      }
      nameError.textContent = message;
      nameError.hidden = !message;
      nameInput.classList.toggle('is-invalid', Boolean(message));
      nameInput.setAttribute('aria-invalid', message ? 'true' : 'false');
      const detailsTab = tabs.find((tab) => tab.dataset.roleTab === 'details');
      if (detailsTab) {
        detailsTab.classList.toggle('has-error', Boolean(message));
      }
      return !message;
    }

    function refresh() {
      const permissions = getPermissions();
      const byLevel = { write: 0, read: 0 };
      const byGroup = new Map();
      catalogue.forEach((item) => {
        const level = permissions[item.key];
        if (!byGroup.has(item.group)) {
          byGroup.set(item.group, { write: [], read: [], total: 0 });
        }
        const entry = byGroup.get(item.group);
        entry.total += 1;
        if (level) {
          byLevel[level] += 1;
          entry[level].push(item);
        }
      });
      const granted = byLevel.write + byLevel.read;

      previewCounts.write.textContent = String(byLevel.write);
      previewCounts.read.textContent = String(byLevel.read);
      previewCounts.none.textContent = String(catalogue.length - granted);
      accessCount.textContent = granted ? String(granted) : '';
      const accessTab = tabs.find((tab) => tab.dataset.roleTab === 'access');
      if (accessTab) {
        accessTab.classList.toggle('is-configured', granted > 0);
      }
      const detailsTab = tabs.find((tab) => tab.dataset.roleTab === 'details');
      if (detailsTab) {
        detailsTab.classList.toggle('is-configured', nameInput.value.trim().length >= 2);
      }

      groups.forEach((group) => {
        const entry = byGroup.get(group.dataset.roleGroup);
        const count = group.querySelector('[data-role-group-count]');
        if (count && entry) {
          const on = entry.write.length + entry.read.length;
          count.textContent = on ? `${on} of ${entry.total}` : '';
        }
      });
      permRows.forEach((row) => {
        row.classList.toggle('is-granted', Boolean(permissions[row.dataset.rolePerm]));
      });

      previewName.textContent = nameInput.value.trim() || (current.mode === 'create' ? 'New role' : 'Untitled role');
      if (current.mode === 'edit' && current.role) {
        const members = current.role.members || 0;
        previewMembers.textContent = members
          ? `Saving updates ${plural(members, 'company member')} straight away.`
          : 'No company members hold this role yet.';
      } else {
        previewMembers.textContent = 'Assign it to company members after saving.';
      }

      const rules = [];
      if (!granted) {
        rules.push(['warning', 'No menus granted yet. Members holding this role won\'t see any portal menus.']);
      }
      if (permissions[TECHNICIAN_KEY]) {
        rules.push(['danger', 'Technician is Yes: members can switch between all companies while keeping this role\'s permissions.']);
      }
      byGroup.forEach((entry, group) => {
        const parts = [];
        if (entry.write.length) {
          parts.push(entry.write.length === entry.total && entry.total > 1 ? 'everything read & write' : `${entry.write.length} read & write`);
        }
        if (entry.read.length) {
          parts.push(entry.read.length === entry.total && entry.total > 1 ? 'everything read only' : `${entry.read.length} read only`);
        }
        if (parts.length) {
          const names = entry.write.concat(entry.read).slice(0, 3).map((item) => `${item.label} (${labelFor(item.key, permissions[item.key])})`);
          const more = entry.write.length + entry.read.length - names.length;
          rules.push(['info', `${group}: ${parts.join(', ')}`, names.join(', ') + (more > 0 ? `, +${more} more` : '')]);
        }
      });
      previewRules.replaceChildren(...rules.map(([tone, text, detail]) => el('li', {
        className: `scf-preview__rule rol-preview__rule rol-preview__rule--${tone}`,
      }, [el('strong', { text }), detail ? el('span', { className: 'rol-preview__detail', text: detail }) : null])));

      const length = descriptionInput.value.length;
      descriptionCounter.textContent = `${length} / ${DESCRIPTION_MAX}`;

      if (grantedOnly.checked) {
        filterPermissions();
      }
    }

    function setStatus(message) {
      status.textContent = message || '';
      status.hidden = !message;
    }

    function load(mode, role) {
      current = { mode, role: role || null };
      const isEdit = mode === 'edit' && role;
      const isSystemEdit = Boolean(isEdit && role.isSystem);
      nameInput.value = mode === 'clone' && role ? `Copy of ${role.name}`.slice(0, 100) : (isEdit ? role.name : '');
      descriptionInput.value = role ? role.description || '' : '';
      setPermissions(role ? role.permissions : {});

      nameInput.readOnly = isSystemEdit;
      nameInput.classList.toggle('is-readonly', isSystemEdit);
      nameHelp.textContent = isSystemEdit
        ? 'System role names are fixed to keep portal integrity. You can still change its description and access.'
        : 'Shown when assigning company members. 2 to 100 characters.';

      if (isEdit) {
        eyebrow.textContent = role.isSystem ? 'System role' : 'Custom role';
        title.textContent = `Edit ${role.name}`;
        subtitle.textContent = 'Changes apply to everyone holding this role as soon as you save.';
        submitButton.textContent = 'Save role';
      } else if (mode === 'clone') {
        eyebrow.textContent = 'New role';
        title.textContent = `Clone ${role.name}`;
        subtitle.textContent = `Starts with the access from ${role.name}. Review it before saving.`;
        submitButton.textContent = 'Create role';
      } else {
        eyebrow.textContent = 'New role';
        title.textContent = 'Add role';
        subtitle.textContent = 'Name the role, then grant only the menus its members need.';
        submitButton.textContent = 'Create role';
      }

      const canDelete = Boolean(isEdit && !role.isSystem);
      deleteButton.hidden = !canDelete;
      deleteButton.disabled = canDelete && role.members > 0;
      deleteButton.title = canDelete && role.members > 0
        ? `Reassign its ${plural(role.members, 'member')} before deleting this role.`
        : '';

      permSearch.value = '';
      grantedOnly.checked = false;
      filterPermissions();
      nameError.hidden = true;
      nameInput.classList.remove('is-invalid');
      nameInput.setAttribute('aria-invalid', 'false');
      tabs.forEach((tab) => tab.classList.remove('has-error'));
      setStatus('');
      submitButton.disabled = false;
      selectTab(mode === 'create' ? 'details' : 'access', false);
      refresh();
    }

    function open(trigger) {
      lastTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      snapshot = serialize();
      window.requestAnimationFrame(() => {
        if (current.mode === 'create') {
          nameInput.focus();
        } else if (current.mode === 'clone') {
          selectTab('details', false);
          nameInput.focus();
          nameInput.select();
        } else {
          const active = tabs.find((tab) => tab.classList.contains('is-active'));
          if (active) {
            active.focus();
          }
        }
      });
    }

    function close(force) {
      if (!force && !saving && serialize() !== snapshot && !window.confirm('Discard your changes to this role?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTab(tab.dataset.roleTab, false));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(next.dataset.roleTab, true);
      });
    });

    nameInput.addEventListener('input', () => {
      if (!nameError.hidden) {
        validateName();
      }
      refresh();
    });
    nameInput.addEventListener('blur', () => {
      if (nameInput.value.trim()) {
        validateName();
      }
    });
    descriptionInput.addEventListener('input', refresh);
    levelInputs.forEach((input) => input.addEventListener('change', refresh));
    permSearch.addEventListener('input', filterPermissions);
    grantedOnly.addEventListener('change', filterPermissions);

    // Bulk setters leave the Technician Yes/No switch alone unless clearing access,
    // so elevated access is always an explicit choice.
    function bulkSet(keys, level) {
      keys.forEach((key) => {
        const levels = levelsByKey.get(key) || [];
        if (level !== 'none' && (key === TECHNICIAN_KEY || !levels.includes(level))) {
          return;
        }
        setLevel(key, level);
      });
      refresh();
    }

    form.querySelectorAll('[data-role-preset]').forEach((button) => {
      button.addEventListener('click', () => {
        bulkSet(catalogue.map((item) => item.key), button.dataset.rolePreset);
      });
    });
    groups.forEach((group) => {
      const keys = Array.from(group.querySelectorAll('[data-role-perm]')).map((row) => row.dataset.rolePerm);
      group.querySelectorAll('[data-role-group-set]').forEach((button) => {
        button.addEventListener('click', () => bulkSet(keys, button.dataset.roleGroupSet));
      });
    });

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        close(false);
      });
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (saving) {
        return;
      }
      if (!validateName()) {
        selectTab('details', false);
        nameInput.focus();
        return;
      }
      const isEdit = current.mode === 'edit' && current.role;
      const payload = {
        name: nameInput.value.trim(),
        description: descriptionInput.value.trim() || null,
        permissions: getPayloadPermissions(),
      };
      saving = true;
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
      setStatus('');
      try {
        await requestJson(isEdit ? `/roles/${current.role.id}` : '/roles', {
          method: isEdit ? 'PATCH' : 'POST',
          body: JSON.stringify(payload),
        });
        window.location.reload();
      } catch (error) {
        saving = false;
        submitButton.disabled = false;
        submitButton.textContent = isEdit ? 'Save role' : 'Create role';
        setStatus(`Couldn't save: ${error.message}`);
      }
    });

    deleteButton.addEventListener('click', async () => {
      const role = current.role;
      if (!role || role.isSystem || role.members > 0) {
        return;
      }
      if (!window.confirm(`Delete ${role.name}? This can't be undone.`)) {
        return;
      }
      saving = true;
      deleteButton.disabled = true;
      setStatus('');
      try {
        await requestJson(`/roles/${role.id}`, { method: 'DELETE' });
        window.location.reload();
      } catch (error) {
        saving = false;
        deleteButton.disabled = false;
        setStatus(`Couldn't delete: ${error.message}`);
      }
    });

    document.querySelectorAll('[data-role-create]').forEach((button) => {
      button.addEventListener('click', () => {
        load('create', null);
        open(button);
      });
    });
    document.querySelectorAll('[data-role-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const role = roles.get(button.dataset.roleEdit);
        if (role) {
          load('edit', role);
          open(button);
        }
      });
    });
    document.querySelectorAll('[data-role-clone]').forEach((button) => {
      button.addEventListener('click', () => {
        const role = roles.get(button.dataset.roleClone);
        if (role) {
          load('clone', role);
          open(button);
        }
      });
    });

    window.addEventListener('beforeunload', (event) => {
      if (!modal.hidden && !saving && serialize() !== snapshot) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  function init() {
    const data = parseJson('role-editor-data') || {};
    const root = document.querySelector('[data-role-root]');
    if (root) {
      bindCatalogue(root);
    }
    bindEditor(data);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
