(function () {
  'use strict';

  // Schedules and onboarding on /rmm/automation. The script editor itself
  // lives in rmm_scripts.js (window.MyPortalRmm), which saves through it.
  const page = document.querySelector('[data-rmm-automation-page]');
  const rmm = window.MyPortalRmm;
  if (!page || !rmm) {
    return;
  }
  const api = rmm.api;
  let canRun = false;
  try {
    canRun = Boolean(JSON.parse(document.getElementById('rmm-config').textContent || '{}').can_run);
  } catch (error) {
    canRun = false;
  }
  const message = page.querySelector('[data-rmm-automation-message]');

  const STEP_STATUS = {
    pending: ['neutral', 'Waiting'],
    queued: ['info', 'Queued'],
    completed: ['success', 'Completed'],
    failed: ['error', 'Failed'],
    timed_out: ['error', 'Timed out'],
    cancelled: ['neutral', 'Cancelled'],
    expired: ['neutral', 'Expired'],
    skipped: ['neutral', 'Skipped'],
    not_run: ['neutral', 'Not run'],
  };
  const ONBOARDING_STATUS = {
    running: ['warning', 'Running'],
    completed: ['success', 'Completed'],
    failed: ['error', 'Stopped'],
    cancelled: ['neutral', 'Cancelled'],
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function pill(map, status) {
    const item = map[status] || ['neutral', status];
    return el('span', 'status status--' + item[0], item[1]);
  }

  function show(text, isError) {
    if (!message) {
      window.alert(text);
      return;
    }
    message.textContent = text;
    message.className = 'alert ' + (isError ? 'alert--warning' : 'alert--info');
    message.hidden = false;
  }

  async function act(button, request, reload) {
    button.disabled = true;
    try {
      const result = await request();
      if (reload) {
        window.location.reload();
      }
      return result;
    } catch (error) {
      button.disabled = false;
      show(error.message, true);
      return null;
    }
  }

  // ------------------------------------------------------------------ //
  // Schedules
  // ------------------------------------------------------------------ //

  async function openSchedule(id) {
    let item = null;
    if (id) {
      try {
        item = (await api('/api/rmm/schedules/' + encodeURIComponent(id))).schedule;
      } catch (error) {
        show(error.message, true);
        return;
      }
    }
    rmm.openEditor(item ? String(item.script_id) : '', { mode: 'schedule', item: item });
  }

  async function openStep(id) {
    let item = null;
    if (id) {
      try {
        item = (await api('/api/rmm/onboarding/steps/' + encodeURIComponent(id))).step;
      } catch (error) {
        show(error.message, true);
        return;
      }
    }
    rmm.openEditor(item ? String(item.script_id) : '', { mode: 'onboarding', item: item });
  }

  async function moveStep(button) {
    const list = button.closest('[data-rmm-steps]');
    const row = button.closest('[data-rmm-step]');
    const ids = Array.from(list.querySelectorAll('[data-rmm-step]')).map((node) => Number(node.dataset.rmmStep));
    const index = ids.indexOf(Number(row.dataset.rmmStep));
    const target = index + Number(button.dataset.rmmStepMove);
    if (index < 0 || target < 0 || target >= ids.length) {
      return;
    }
    ids.splice(target, 0, ids.splice(index, 1)[0]);
    await act(button, () => api('/api/rmm/onboarding/steps/order', {
      method: 'POST', body: { step_ids: ids },
    }), true);
  }

  // ------------------------------------------------------------------ //
  // Onboarding runs
  // ------------------------------------------------------------------ //

  const modal = document.querySelector('[data-rmm-onboarding-modal]');
  let current = null;
  let timer = null;
  let reloadOnClose = false;

  function renderOnboarding(run) {
    current = run;
    modal.querySelector('[data-rmm-onboarding-title]').textContent = run.asset_name || run.agent_hostname || 'Device';
    const subtitle = modal.querySelector('[data-rmm-onboarding-subtitle]');
    subtitle.replaceChildren(pill(ONBOARDING_STATUS, run.status), document.createTextNode(
      ' Started ' + rmm.formatDate(run.started_at) + (run.started_by_email ? ' by ' + run.started_by_email : ' when the agent was installed')
    ));
    const error = modal.querySelector('[data-rmm-onboarding-error]');
    error.textContent = run.error_message || '';
    error.hidden = !run.error_message;
    const list = modal.querySelector('[data-rmm-onboarding-steps]');
    list.replaceChildren();
    (run.plan || []).forEach((step, index) => {
      const item = el('li', 'rmm-step');
      item.append(el('span', 'rmm-step__number', index + 1));
      const main = el('div', 'rmm-step__main');
      main.append(el('span', 'rmm-script__name', step.script_name));
      const meta = el('span', 'text-muted', 'Tags: ' + ((step.tags || []).join(', ') || 'none') + (step.continue_on_failure ? ' · carries on if it fails' : ''));
      main.append(meta);
      if (step.message) {
        main.append(el('span', 'text-muted', step.message));
      }
      item.append(main);
      const actions = el('div', 'rmm-step__actions');
      actions.append(pill(STEP_STATUS, step.status));
      if (step.run_id) {
        const view = el('button', 'button button--ghost button--small', 'View run');
        view.type = 'button';
        view.addEventListener('click', () => rmm.openResult(step.run_id));
        actions.append(view);
      }
      item.append(actions);
      list.append(item);
    });
    modal.querySelector('[data-rmm-onboarding-cancel]').hidden = !(canRun && run.status === 'running');
  }

  async function refresh(id) {
    window.clearTimeout(timer);
    try {
      const data = await api('/api/rmm/onboarding/runs/' + encodeURIComponent(id));
      renderOnboarding(data.onboarding);
      if (data.onboarding.status === 'running' && !modal.hidden) {
        timer = window.setTimeout(() => refresh(id), 4000);
      }
    } catch (error) {
      const box = modal.querySelector('[data-rmm-onboarding-error]');
      box.textContent = error.message;
      box.hidden = false;
    }
  }

  function openOnboarding(id, reload) {
    reloadOnClose = reloadOnClose || Boolean(reload);
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('.modal__close').focus();
    refresh(id);
  }

  modal.querySelectorAll('[data-rmm-onboarding-close]').forEach((button) => button.addEventListener('click', () => {
    window.clearTimeout(timer);
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (reloadOnClose) {
      window.location.reload();
    }
  }));

  modal.querySelector('[data-rmm-onboarding-cancel]').addEventListener('click', async (event) => {
    if (!current || !window.confirm('Stop onboarding on this device? Steps not yet run are skipped.')) {
      return;
    }
    const data = await act(event.currentTarget, () => api('/api/rmm/onboarding/runs/' + current.id + '/cancel', { method: 'POST' }));
    if (data) {
      reloadOnClose = true;
      renderOnboarding(data.onboarding);
    }
  });

  const startForm = page.querySelector('[data-rmm-onboarding-start]');
  if (startForm) {
    startForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const agentId = Number(startForm.querySelector('[data-rmm-onboarding-agent]').value);
      if (!agentId) {
        return;
      }
      const data = await act(startForm.querySelector('button'), () => api('/api/rmm/onboarding/runs', { method: 'POST', body: { agent_id: agentId } }));
      if (data) {
        startForm.querySelector('button').disabled = false;
        openOnboarding(data.onboarding.id, true);
      }
    });
  }

  // ------------------------------------------------------------------ //
  // Buttons
  // ------------------------------------------------------------------ //

  page.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || !page.contains(button)) {
      return;
    }
    const data = button.dataset;
    if (data.rmmScheduleEdit) {
      openSchedule(data.rmmScheduleEdit);
    } else if (data.rmmScheduleRun) {
      const result = await act(button, () => api('/api/rmm/schedules/' + data.rmmScheduleRun + '/run', { method: 'POST' }));
      if (result) {
        button.disabled = false;
        show(result.summary + ' Reload the page to see the latest run.');
      }
    } else if (data.rmmScheduleToggle) {
      await act(button, () => api('/api/rmm/schedules/' + data.rmmScheduleToggle + '/enabled', {
        method: 'POST', body: { enabled: data.enabled !== 'true' },
      }), true);
    } else if (data.rmmScheduleDelete) {
      if (window.confirm('Delete this schedule? Its past runs stay in the run history.')) {
        await act(button, () => api('/api/rmm/schedules/' + data.rmmScheduleDelete, { method: 'DELETE' }), true);
      }
    } else if ('rmmStepNew' in data) {
      openStep(null);
    } else if (data.rmmStepEdit) {
      openStep(data.rmmStepEdit);
    } else if (data.rmmStepDelete) {
      if (window.confirm('Remove this onboarding step? Devices already being onboarded skip it.')) {
        await act(button, () => api('/api/rmm/onboarding/steps/' + data.rmmStepDelete, { method: 'DELETE' }), true);
      }
    } else if (data.rmmStepMove) {
      moveStep(button);
    } else if (data.rmmOnboardingView) {
      openOnboarding(data.rmmOnboardingView);
    }
  });

  document.querySelectorAll('[data-rmm-schedule-new]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      openSchedule(null);
    });
  });
})();
