document.addEventListener('DOMContentLoaded', function () {
  function parseConfig() {
    var element = document.querySelector('[data-mailbox-page-config]');
    if (!element) return null;
    try {
      return JSON.parse(element.getAttribute('data-mailbox-page-config') || '{}');
    } catch (error) {
      console.error('Failed to parse mailbox page config.', error);
      return null;
    }
  }

  var config = parseConfig();
  if (!config) return;

  var csrfToken = config.csrfToken || '';
  var activeStaff = Array.isArray(config.activeStaff) ? config.activeStaff : [];

  document.querySelectorAll('[data-table-id="m365-user-mailboxes"] [data-header-menu], [data-table-id="m365-shared-mailboxes"] [data-header-menu]').forEach(function (menu) {
    var wrapper = menu.closest('.table-wrapper') || menu.closest('.panel__body');
    if (!wrapper) return;
    menu.addEventListener('toggle', function () {
      wrapper.style.overflow = menu.open ? 'visible' : '';
    });
  });

  function jsonHeaders() {
    var headers = { 'Content-Type': 'application/json' };
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    return headers;
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str == null ? '' : str)));
    return div.innerHTML;
  }

  function handleActionButton(button, pendingText, successText, request, onSuccess, errorText) {
    var originalText = button.textContent;
    button.disabled = true;
    button.textContent = pendingText;
    fetch(request.url, request.options)
      .then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
      })
      .then(function (result) {
        if (!result.ok || (result.data && result.data.error)) {
          button.disabled = false;
          button.textContent = originalText;
          window.alert((result.data && result.data.error) || errorText);
          return;
        }
        if (typeof onSuccess === 'function') {
          onSuccess(result.data || {}, originalText);
          return;
        }
        button.textContent = successText;
        window.setTimeout(function () {
          button.textContent = originalText;
          button.disabled = false;
        }, 3000);
      })
      .catch(function () {
        button.disabled = false;
        button.textContent = originalText;
        window.alert(errorText);
      });
  }

  var syncBtn = document.querySelector('[data-sync-mailboxes]');
  if (syncBtn) {
    syncBtn.addEventListener('click', function () {
      var originalText = syncBtn.textContent;
      syncBtn.disabled = true;
      fetch('/m365/mailboxes/sync', {
        method: 'POST',
        headers: jsonHeaders()
      })
        .then(function (resp) { return resp.json(); })
        .then(function () {
          syncBtn.textContent = '\u2713 Sync started';
          window.setTimeout(function () {
            syncBtn.textContent = originalText;
            syncBtn.disabled = false;
          }, 3000);
        })
        .catch(function () {
          syncBtn.textContent = originalText;
          syncBtn.disabled = false;
        });
    });
  }

  var runAllBtn = document.querySelector('[data-start-managed-folder-assistant-all]');
  if (runAllBtn) {
    runAllBtn.addEventListener('click', function () {
      if (!window.confirm('Start Managed Folder Assistant for all mailboxes?')) {
        return;
      }
      handleActionButton(
        runAllBtn,
        'Starting…',
        '\u2713 Started',
        {
          url: '/m365/mailboxes/start-managed-folder-assistant/all',
          options: { method: 'POST', headers: jsonHeaders() }
        },
        function (data, originalText) {
          runAllBtn.disabled = false;
          runAllBtn.textContent = '\u2713 Started (' + (data.started || 0) + ')';
          window.setTimeout(function () {
            runAllBtn.textContent = originalText;
          }, 3000);
        },
        'Failed to start Managed Folder Assistant. Please try again.'
      );
    });
  }

  var modal = document.getElementById('mailbox-perm-modal');
  if (modal) {
    var closeBtn = modal.querySelector('[data-modal-close]');
    var titleEl = document.getElementById('mailbox-perm-title');
    var loadingEl = document.getElementById('perm-loading');
    var errorEl = document.getElementById('perm-error');
    var contentEl = document.getElementById('perm-content');
    var canAccessList = document.getElementById('perm-can-access');
    var accessibleByList = document.getElementById('perm-accessible-by');
    var requestForm = document.getElementById('mailbox-permission-request-form');
    var requestUpn = document.getElementById('perm-request-upn');
    var requestDisplayName = document.getElementById('perm-request-display-name');
    var removalOptions = document.getElementById('perm-removal-options');
    var addOptions = document.getElementById('perm-add-options');
    var notesEl = document.getElementById('perm-request-notes');
    var requestStatus = document.getElementById('perm-request-status');
    var saveBtn = document.getElementById('perm-request-save');
    var activeTrigger = null;

    function handleKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
      }
    }

    function openModal(trigger) {
      activeTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.addEventListener('keydown', handleKeydown);
      if (closeBtn) {
        closeBtn.focus();
      }
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', handleKeydown);
      if (activeTrigger && typeof activeTrigger.focus === 'function') {
        activeTrigger.focus();
      }
      activeTrigger = null;
    }

    function buildList(listEl, items, nameKey, subKey) {
      listEl.innerHTML = '';
      if (items && items.length > 0) {
        items.forEach(function (item) {
          var li = document.createElement('li');
          li.innerHTML = escapeHtml(item[nameKey]) + ' <small class="text-muted">' + escapeHtml(item[subKey]) + '</small>';
          listEl.appendChild(li);
        });
      } else {
        var li = document.createElement('li');
        li.className = 'text-muted';
        li.style.listStyle = 'none';
        li.style.marginLeft = '-1.25rem';
        li.textContent = 'None found.';
        listEl.appendChild(li);
      }
    }

    function optionValue(item, keys) {
      for (var i = 0; i < keys.length; i += 1) {
        if (item && item[keys[i]]) return String(item[keys[i]]);
      }
      return '';
    }

    function uniqueItems(items, getValue) {
      var seen = {};
      return (items || []).filter(function (item) {
        var value = String(getValue(item) || '').trim().toLowerCase();
        if (!value || seen[value]) return false;
        seen[value] = true;
        return true;
      });
    }

    function buildCheckboxes(container, items, emptyText, getValue, getLabel) {
      container.innerHTML = '';
      if (!items || !items.length) {
        var empty = document.createElement('p');
        empty.className = 'text-muted';
        empty.style.margin = '0';
        empty.textContent = emptyText;
        container.appendChild(empty);
        return;
      }
      items.forEach(function (item, index) {
        var id = container.id + '-' + index;
        var label = document.createElement('label');
        label.className = 'checkbox';
        label.setAttribute('for', id);
        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = id;
        checkbox.value = getValue(item);
        checkbox.dataset.label = getLabel(item);
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(' ' + getLabel(item)));
        container.appendChild(label);
      });
    }

    function resetRequestForm(upn, displayName) {
      if (requestUpn) requestUpn.value = upn || '';
      if (requestDisplayName) requestDisplayName.value = displayName || upn || '';
      if (notesEl) notesEl.value = '';
      if (requestStatus) {
        requestStatus.style.display = 'none';
        requestStatus.textContent = '';
        requestStatus.className = 'panel__subtitle';
      }
      if (saveBtn) saveBtn.disabled = false;
    }

    if (closeBtn) {
      closeBtn.addEventListener('click', closeModal);
    }
    modal.addEventListener('click', function (event) {
      if (event.target === modal) {
        closeModal();
      }
    });

    document.querySelectorAll('[data-perm-btn]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var upn = btn.getAttribute('data-upn');
        var displayName = btn.getAttribute('data-display-name');

        titleEl.textContent = displayName;
        resetRequestForm(upn, displayName);
        loadingEl.style.display = 'block';
        errorEl.style.display = 'none';
        contentEl.style.display = 'none';
        openModal(btn);

        fetch('/m365/mailboxes/permissions?upn=' + encodeURIComponent(upn))
          .then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
          .then(function (result) {
            loadingEl.style.display = 'none';
            if (!result.ok || result.data.error) {
              errorEl.textContent = result.data.error || 'Failed to load permissions.';
              errorEl.style.display = 'block';
              return;
            }
            buildList(canAccessList, result.data.can_access, 'display_name', 'email');
            buildList(accessibleByList, result.data.accessible_by, 'display_name', 'upn');
            buildCheckboxes(removalOptions, result.data.accessible_by, 'No existing mailbox permissions found.', function (item) {
              return optionValue(item, ['upn', 'email', 'user_principal_name']);
            }, function (item) {
              var name = optionValue(item, ['display_name', 'name']);
              var upnValue = optionValue(item, ['upn', 'email', 'user_principal_name']);
              return name && upnValue ? name + ' (' + upnValue + ')' : (name || upnValue || 'Unknown user');
            });
            buildCheckboxes(addOptions, uniqueItems(activeStaff, function (item) {
              return optionValue(item, ['email']);
            }), 'No active staff members found.', function (item) {
              return optionValue(item, ['email']);
            }, function (item) {
              var name = [item.first_name, item.last_name].filter(Boolean).join(' ') || item.email;
              return name && item.email ? name + ' (' + item.email + ')' : (name || 'Unknown staff');
            });
            contentEl.style.display = 'block';
          })
          .catch(function () {
            loadingEl.style.display = 'none';
            errorEl.textContent = 'Failed to load permissions. Please try again.';
            errorEl.style.display = 'block';
          });
      });
    });

    if (requestForm) {
      requestForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var removals = Array.prototype.slice.call(removalOptions.querySelectorAll('input:checked')).map(function (input) {
          return { upn: input.value, label: input.dataset.label || input.value };
        });
        var additions = Array.prototype.slice.call(addOptions.querySelectorAll('input:checked')).map(function (input) {
          return { email: input.value, label: input.dataset.label || input.value };
        });
        if (!removals.length && !additions.length) {
          requestStatus.textContent = 'Select at least one permission change before saving.';
          requestStatus.className = 'alert alert--error';
          requestStatus.style.display = 'block';
          return;
        }
        saveBtn.disabled = true;
        requestStatus.textContent = 'Submitting request…';
        requestStatus.className = 'panel__subtitle';
        requestStatus.style.display = 'block';
        fetch('/m365/mailboxes/permissions/request', {
          method: 'POST',
          headers: jsonHeaders(),
          body: JSON.stringify({
            mailbox_upn: requestUpn.value,
            mailbox_display_name: requestDisplayName.value,
            removals: removals,
            additions: additions,
            notes: notesEl.value
          })
        })
          .then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
          .then(function (result) {
            if (!result.ok || (result.data && result.data.error)) {
              saveBtn.disabled = false;
              requestStatus.textContent = (result.data && result.data.error) || 'Failed to submit request.';
              requestStatus.className = 'alert alert--error';
              return;
            }
            requestStatus.textContent = 'Request saved as ticket #' + (result.data.ticket_number || result.data.ticket_id) + '.';
            requestStatus.className = 'alert alert--success';
          })
          .catch(function () {
            saveBtn.disabled = false;
            requestStatus.textContent = 'Failed to submit request. Please try again.';
            requestStatus.className = 'alert alert--error';
          });
      });
    }
  }

  document.querySelectorAll('[data-start-managed-folder-assistant-btn]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var upn = btn.getAttribute('data-upn');
      var displayName = btn.getAttribute('data-display-name') || upn;
      if (!upn) return;
      if (!window.confirm('Start Managed Folder Assistant for ' + displayName + '?')) {
        return;
      }
      handleActionButton(
        btn,
        'Starting…',
        '\u2713 Started',
        {
          url: '/m365/mailboxes/start-managed-folder-assistant',
          options: {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ upn: upn })
          }
        },
        null,
        'Failed to start Managed Folder Assistant. Please try again.'
      );
    });
  });

  document.querySelectorAll('[data-enable-archive-btn]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var upn = btn.getAttribute('data-upn');
      var displayName = btn.getAttribute('data-display-name') || upn;
      if (!upn) return;
      if (!window.confirm('Enable in-place archive for ' + displayName + '?')) {
        return;
      }
      handleActionButton(
        btn,
        'Enabling…',
        '\u2713 Archive enabled',
        {
          url: '/m365/mailboxes/enable-archive',
          options: {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ upn: upn })
          }
        },
        function () {
          btn.textContent = '\u2713 Archive enabled';
          window.setTimeout(function () {
            if (btn.parentNode) {
              btn.parentNode.removeChild(btn);
            }
          }, 1500);
        },
        'Failed to enable archive. Please try again.'
      );
    });
  });

  var rulesModal = document.getElementById('mailbox-rules-modal');
  if (!rulesModal) return;
  var closeRulesBtn = rulesModal.querySelector('[data-rules-modal-close]');
  var titleRulesEl = document.getElementById('mailbox-rules-title');
  var loadingRulesEl = document.getElementById('rules-loading');
  var errorRulesEl = document.getElementById('rules-error');
  var contentRulesEl = document.getElementById('rules-content');
  var activeRulesTrigger = null;

  function handleRulesKeydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeRulesModal();
    }
  }

  function openRulesModal(trigger) {
    activeRulesTrigger = trigger || null;
    rulesModal.hidden = false;
    rulesModal.classList.add('is-visible');
    rulesModal.setAttribute('aria-hidden', 'false');
    document.addEventListener('keydown', handleRulesKeydown);
    if (closeRulesBtn) closeRulesBtn.focus();
  }

  function closeRulesModal() {
    rulesModal.classList.remove('is-visible');
    rulesModal.hidden = true;
    rulesModal.setAttribute('aria-hidden', 'true');
    document.removeEventListener('keydown', handleRulesKeydown);
    if (activeRulesTrigger && typeof activeRulesTrigger.focus === 'function') activeRulesTrigger.focus();
    activeRulesTrigger = null;
  }

  function formatRawValue(value) {
    if (value === null || value === undefined || value === '') return '<span class="text-muted">—</span>';
    if (Array.isArray(value)) return escapeHtml(value.join(', '));
    if (typeof value === 'object') return '<pre style="white-space:pre-wrap;margin:0">' + escapeHtml(JSON.stringify(value, null, 2)) + '</pre>';
    return escapeHtml(value);
  }

  function buildRule(rule, index) {
    var details = document.createElement('details');
    details.className = 'panel';
    details.style.margin = '0 0 .75rem';
    var title = rule.title || ('Rule ' + (index + 1));
    var summary = document.createElement('summary');
    summary.style.cursor = 'pointer';
    summary.style.padding = '.75rem 1rem';
    summary.innerHTML = '<strong>' + escapeHtml(title) + '</strong>' +
      (rule.enabled === false ? ' <span class="badge badge--warning">Disabled</span>' : '') +
      (rule.priority !== null && rule.priority !== undefined ? ' <small class="text-muted">Priority ' + escapeHtml(rule.priority) + '</small>' : '');
    details.appendChild(summary);
    var body = document.createElement('div');
    body.style.padding = '0 1rem 1rem';
    var html = '';
    if (rule.description) html += '<p>' + escapeHtml(rule.description) + '</p>';
    var raw = rule.raw || {};
    var keys = Object.keys(raw).sort();
    if (keys.length) {
      html += '<dl class="definition-list">';
      keys.forEach(function (key) {
        html += '<dt>' + escapeHtml(key) + '</dt><dd>' + formatRawValue(raw[key]) + '</dd>';
      });
      html += '</dl>';
    }
    body.innerHTML = html || '<p class="text-muted">No rule details returned.</p>';
    details.appendChild(body);
    return details;
  }

  function renderRules(rules) {
    contentRulesEl.innerHTML = '';
    if (!rules || !rules.length) {
      var empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = 'No mailbox rules found.';
      contentRulesEl.appendChild(empty);
      return;
    }
    rules.forEach(function (rule, index) { contentRulesEl.appendChild(buildRule(rule, index)); });
  }

  if (closeRulesBtn) closeRulesBtn.addEventListener('click', closeRulesModal);
  rulesModal.addEventListener('click', function (event) { if (event.target === rulesModal) closeRulesModal(); });
  document.querySelectorAll('[data-rules-btn]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var upn = btn.getAttribute('data-upn');
      var displayName = btn.getAttribute('data-display-name') || upn;
      titleRulesEl.textContent = 'Mailbox Rules — ' + displayName;
      loadingRulesEl.style.display = 'block';
      errorRulesEl.style.display = 'none';
      contentRulesEl.style.display = 'none';
      contentRulesEl.innerHTML = '';
      openRulesModal(btn);
      fetch('/m365/mailboxes/rules?upn=' + encodeURIComponent(upn))
        .then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
        .then(function (result) {
          loadingRulesEl.style.display = 'none';
          if (!result.ok || (result.data && result.data.error)) {
            errorRulesEl.textContent = (result.data && result.data.error) || 'Failed to load mailbox rules.';
            errorRulesEl.style.display = 'block';
            return;
          }
          renderRules((result.data && result.data.rules) || []);
          contentRulesEl.style.display = 'block';
        })
        .catch(function () {
          loadingRulesEl.style.display = 'none';
          errorRulesEl.textContent = 'Failed to load mailbox rules. Please try again.';
          errorRulesEl.style.display = 'block';
        });
    });
  });
});
