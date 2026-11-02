/*
 * Staff workflow execution history.
 *
 * Page: /staff/workflows/history (app/templates/staff/workflow_history.html)
 * Styles: app/static/css/staff_workflows.css (+ shared .scf-* editor rules)
 *
 * Lists recent onboarding/offboarding runs grouped by day and opens one run
 * at a time in a focused modal with its steps, the variables it produced
 * (including values returned by Pause For Webhook callbacks) and details.
 * Protected values never reach the browser: the server sends only their names.
 */
(() => {
  'use strict';

  const root = document.querySelector('[data-swh]');
  if (!root) {
    return;
  }

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = String(value);
      } else if (key === 'dataset') {
        Object.entries(value).forEach(([dataKey, dataValue]) => {
          node.dataset[dataKey] = String(dataValue);
        });
      } else if (value === true) {
        node.setAttribute(key, '');
      } else {
        node.setAttribute(key, String(value));
      }
    });
    (Array.isArray(children) ? children : [children]).forEach((child) => {
      if (child === null || child === undefined || child === false) {
        return;
      }
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  // --------------------------------------------------------------- states

  const STATES = {
    completed: ['Completed', 'success', 'completed'],
    offboarding_completed: ['Completed', 'success', 'completed'],
    failed: ['Failed', 'danger', 'failed'],
    offboarding_failed: ['Failed', 'danger', 'failed'],
    provisioning: ['In progress', 'info', 'running'],
    offboarding_in_progress: ['In progress', 'info', 'running'],
    waiting_external: ['Waiting', 'warning', 'waiting'],
    offboarding_waiting_external: ['Waiting', 'warning', 'waiting'],
    paused_license_unavailable: ['Paused: no licence free', 'warning', 'waiting'],
    approved: ['Scheduled', 'neutral', 'waiting'],
    offboarding_approved: ['Scheduled', 'neutral', 'waiting'],
    requested: ['Requested', 'neutral', 'waiting'],
    awaiting_approval: ['Awaiting approval', 'neutral', 'waiting'],
    offboarding_awaiting_approval: ['Awaiting approval', 'neutral', 'waiting'],
    denied: ['Denied', 'neutral', 'other'],
    offboarding_denied: ['Denied', 'neutral', 'other'],
  };

  function stateInfo(state) {
    const key = String(state || '').toLowerCase();
    const known = STATES[key];
    if (known) {
      return { label: known[0], variant: known[1], group: known[2] };
    }
    return { label: key.replace(/_/g, ' ') || 'Unknown', variant: 'neutral', group: 'other' };
  }

  const STEP_STATUS = {
    success: ['Done', 'success', '✓'],
    failed: ['Failed', 'danger', '✕'],
    skipped: ['Skipped', 'neutral', '↷'],
    paused: ['Paused', 'warning', 'Ⅱ'],
    progress: ['In progress', 'info', '…'],
  };

  function stepStatus(status) {
    const known = STEP_STATUS[String(status || '').toLowerCase()];
    return known
      ? { label: known[0], variant: known[1], icon: known[2] }
      : { label: String(status || 'Unknown'), variant: 'neutral', icon: '•' };
  }

  // ---------------------------------------------------------------- dates

  function parseDate(iso) {
    if (!iso) {
      return null;
    }
    const text = String(iso);
    const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function timeNode(iso, options = { dateStyle: 'medium', timeStyle: 'short' }) {
    const date = parseDate(iso);
    if (!date) {
      return el('span', { className: 'text-muted', text: '—' });
    }
    return el('time', { 'data-utc': date.toISOString(), datetime: date.toISOString(), title: date.toLocaleString(), text: date.toLocaleString(undefined, options) });
  }

  function dayLabel(date) {
    if (!date) {
      return 'Not started';
    }
    const today = new Date();
    const startOf = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
    const diff = Math.round((startOf(today) - startOf(date)) / 86400000);
    if (diff === 0) {
      return 'Today';
    }
    if (diff === 1) {
      return 'Yesterday';
    }
    return date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  function duration(startIso, endIso) {
    const start = parseDate(startIso);
    const end = parseDate(endIso);
    if (!start || !end) {
      return '';
    }
    const seconds = Math.max(0, Math.round((end - start) / 1000));
    if (seconds < 60) {
      return `${seconds}s`;
    }
    const minutes = Math.round(seconds / 60);
    if (minutes < 120) {
      return `${minutes} min`;
    }
    return `${Math.round(minutes / 60)} h`;
  }

  const plural = (count, word, pluralWord) => `${count} ${count === 1 ? word : (pluralWord || `${word}s`)}`;

  // ---------------------------------------------------------------- steps

  /*
   * Collapse raw step logs into one entry per step: retries become attempts,
   * "<step>:context" logs become the step's outputs and "<step>:export_state"
   * logs become progress notes.
   */
  function buildSteps(execution) {
    const steps = [];
    const byName = new Map();
    const logs = (execution.steps || []).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    logs.forEach((log) => {
      const name = String(log.stepName || '');
      const contextMatch = name.match(/^(.*):(context|export_state)$/);
      if (contextMatch) {
        const owner = byName.get(contextMatch[1]);
        if (owner && contextMatch[2] === 'export_state') {
          owner.progress = log;
        }
        return;
      }
      let step = byName.get(name);
      if (!step) {
        step = { name, attempts: [], progress: null };
        byName.set(name, step);
        steps.push(step);
      }
      step.attempts.push(log);
    });
    steps.forEach((step) => {
      step.last = step.attempts[step.attempts.length - 1];
      step.variables = (execution.variables || []).filter((variable) => variable.step === step.name);
      const response = step.last.responsePayload || {};
      step.webhook = Object.prototype.hasOwnProperty.call(response, 'webhook_public_id') ? response : null;
    });
    return steps;
  }

  function friendlyStepName(name) {
    const match = String(name).match(/^custom_field_group:(.+?):(.+)$/);
    if (match) {
      return `Add to group for “${match[1]}”`;
    }
    return name;
  }

  // ------------------------------------------------------------------ state

  const els = {
    search: $('[data-swh-search]'),
    direction: $('[data-swh-direction]'),
    status: $('[data-swh-status]'),
    limit: $('[data-swh-limit]'),
    info: $('[data-swh-info]'),
    statusText: $('[data-swh-status-text]', root),
    groups: $('[data-swh-groups]', root),
    empty: $('[data-swh-empty]', root),
    noResults: $('[data-swh-no-results]', root),
  };

  const state = {
    executions: [],
    current: null,
  };

  function setCount(name, value) {
    const tile = $(`[data-swh-count="${name}"] .stat-strip__stat-value`, root);
    if (tile) {
      tile.textContent = String(value);
    }
  }

  function searchText(execution) {
    const stepNames = (execution.steps || []).map((step) => step.stepName).join(' ');
    return [
      execution.staffName, execution.staffId, execution.workflowName, execution.workflowKey,
      execution.direction, stateInfo(execution.state).label, stepNames,
    ].join(' ').toLowerCase();
  }

  function filtered() {
    const needle = (els.search.value || '').trim().toLowerCase();
    const group = els.status.value;
    return state.executions.filter((execution) => (
      (!needle || searchText(execution).includes(needle))
      && (!group || stateInfo(execution.state).group === group)
    ));
  }

  function statusPill(label, variant) {
    return el('span', { className: `status status--${variant}`, text: label });
  }

  function chip(text, modifier, title) {
    return el('li', { className: `scf-chip${modifier ? ` ${modifier}` : ''}`, title: title || null, text });
  }

  function progressText(execution) {
    const steps = buildSteps(execution);
    const done = steps.filter((step) => step.last.status === 'success').length;
    const skipped = steps.filter((step) => step.last.status === 'skipped').length;
    if (!steps.length) {
      return 'No steps run yet';
    }
    return `${done} of ${steps.length} steps done${skipped ? ` · ${skipped} skipped` : ''}`;
  }

  function actionFor(execution) {
    const group = stateInfo(execution.state).group;
    const key = String(execution.state || '').toLowerCase();
    if (group === 'failed') {
      return { kind: 'retry', label: 'Retry' };
    }
    if (['waiting_external', 'offboarding_waiting_external', 'paused_license_unavailable'].includes(key)) {
      return { kind: 'resume', label: 'Resume' };
    }
    return null;
  }

  function renderList() {
    const items = filtered();
    els.groups.textContent = '';
    els.empty.hidden = state.executions.length > 0;
    els.noResults.hidden = !state.executions.length || items.length > 0;
    els.info.textContent = state.executions.length ? `Showing ${items.length} of ${state.executions.length}` : '';

    const groups = new Map();
    items.forEach((execution) => {
      const label = dayLabel(parseDate(execution.requestedAt || execution.startedAt));
      if (!groups.has(label)) {
        groups.set(label, []);
      }
      groups.get(label).push(execution);
    });

    groups.forEach((executions, label) => {
      const list = el('ul', { className: 'scf-list swh-list' });
      executions.forEach((execution) => {
        const info = stateInfo(execution.state);
        const variables = execution.variables || [];
        const callbackCount = variables.filter((variable) => variable.source === 'webhook').length;
        const chips = [
          chip(execution.direction === 'offboarding' ? 'Offboarding' : 'Onboarding'),
          chip(progressText(execution)),
        ];
        if (variables.length) {
          chips.push(chip(`${plural(variables.length, 'value')}${callbackCount ? ` · ${callbackCount} from callback` : ''}`, 'swf-chip--timing'));
        }
        if (execution.lastError && info.group !== 'completed') {
          chips.push(chip('Has an error', 'swf-chip--danger', execution.lastError));
        }
        const action = actionFor(execution);
        const actions = el('div', { className: 'scf-item__actions' }, [
          action ? el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { swhAction: action.kind, swhId: execution.executionId }, text: action.label }) : null,
          el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { swhOpen: execution.executionId }, 'aria-haspopup': 'dialog', 'aria-controls': 'swh-modal', text: 'View' }),
        ]);
        list.append(el('li', { className: `scf-item swh-item swh-item--${info.group}` }, [
          el('span', { className: `swf-cat-icon swh-dir-icon swh-dir-icon--${execution.direction === 'offboarding' ? 'off' : 'on'}`, 'aria-hidden': 'true', text: execution.direction === 'offboarding' ? '←' : '→' }),
          el('div', { className: 'scf-item__main' }, [
            el('div', { className: 'scf-item__heading' }, [
              el('button', { type: 'button', className: 'scf-item__label', dataset: { swhOpen: execution.executionId }, 'aria-haspopup': 'dialog', 'aria-controls': 'swh-modal', text: execution.staffName || `Staff #${execution.staffId}` }),
              statusPill(info.label, info.variant),
            ]),
            el('p', { className: 'scf-item__help' }, [
              execution.workflowName || execution.workflowKey || 'Workflow',
              ' · requested ',
              timeNode(execution.requestedAt, { timeStyle: 'short' }),
            ]),
            el('ul', { className: 'scf-item__chips', 'aria-label': 'Run summary' }, chips),
          ]),
          actions,
        ]));
      });
      els.groups.append(el('section', { className: 'swh-group' }, [
        el('h3', { className: 'scf-group__title', text: label }),
        list,
      ]));
    });
  }

  function renderCounts() {
    const counts = { completed: 0, running: 0, waiting: 0, failed: 0 };
    state.executions.forEach((execution) => {
      const group = stateInfo(execution.state).group;
      if (group in counts) {
        counts[group] += 1;
      }
    });
    setCount('total', state.executions.length);
    Object.entries(counts).forEach(([name, value]) => setCount(name, value));
  }

  // ---------------------------------------------------------------- modal

  const modal = document.getElementById('swh-modal');
  const run = {
    eyebrow: $('[data-swh-run-eyebrow]', modal),
    title: $('[data-swh-run-title]', modal),
    subtitle: $('[data-swh-run-subtitle]', modal),
    timeline: $('[data-swh-timeline]', modal),
    stepsCount: $('[data-swh-steps-count]', modal),
    variables: $('[data-swh-variables]', modal),
    variablesTable: $('[data-swh-variables-table]', modal),
    variablesEmpty: $('[data-swh-variables-empty]', modal),
    variablesCount: $('[data-swh-variables-count]', modal),
    webhookTip: $('[data-swh-webhook-tip]', modal),
    copyNames: $('[data-swh-copy-names]', modal),
    details: $('[data-swh-details]', modal),
    summary: $('[data-swh-summary]', modal),
    action: $('[data-swh-run-action]', modal),
    actionStatus: $('[data-swh-action-status]', modal),
  };
  let returnFocus = null;

  const tabs = $$('[role="tab"]', modal);
  function showTab(name, focus) {
    tabs.forEach((tab) => {
      const active = tab.dataset.swfTab === name;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) {
        tab.focus();
      }
    });
    $$('[data-swf-panel]', modal).forEach((panel) => {
      panel.hidden = panel.dataset.swfPanel !== name;
    });
  }
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => showTab(tab.dataset.swfTab));
    tab.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
        return;
      }
      event.preventDefault();
      const index = tabs.indexOf(tab);
      const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
      showTab(next.dataset.swfTab, true);
    });
  });

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      if (button) {
        const original = button.textContent;
        button.textContent = 'Copied';
        window.setTimeout(() => {
          button.textContent = original;
        }, 1200);
      }
    } catch (error) {
      run.actionStatus.textContent = `Copy ${text} manually; the clipboard isn't available.`;
    }
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === '') {
      return el('span', { className: 'text-muted', text: 'Empty' });
    }
    const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
    const short = text.length > 160 ? `${text.slice(0, 157)}…` : text;
    return el('span', { className: 'swh-value', title: text.length > 160 ? text : null, text: short });
  }

  function payloadDetails(label, payload) {
    if (!payload || (typeof payload === 'object' && !Object.keys(payload).length)) {
      return null;
    }
    return el('details', { className: 'swh-payload' }, [
      el('summary', { text: label }),
      el('pre', { className: 'swh-payload__body', text: JSON.stringify(payload, null, 2) }),
    ]);
  }

  function renderTimeline(execution) {
    const steps = buildSteps(execution);
    run.timeline.textContent = '';
    run.stepsCount.textContent = steps.length ? String(steps.length) : '';
    if (!steps.length) {
      run.timeline.append(el('li', { className: 'scf-empty scf-empty--inline', text: 'No steps have run yet. Scheduled runs start at their scheduled time.' }));
      return;
    }
    steps.forEach((step, index) => {
      const status = stepStatus(step.last.status);
      const attempts = step.attempts.length;
      const meta = [
        timeNode(step.last.startedAt || step.attempts[0].startedAt, { timeStyle: 'medium' }),
      ];
      const took = duration(step.attempts[0].startedAt, step.last.completedAt);
      if (took) {
        meta.push(` · took ${took}`);
      }
      if (attempts > 1) {
        meta.push(` · ${plural(attempts, 'attempt')}`);
      }
      const response = step.last.responsePayload || {};
      const skippedReason = step.last.status === 'skipped' && response.reason
        ? el('p', { className: 'swh-step__note', text: response.reason === 'conditions_not_met' ? 'Skipped because the staff member didn\'t match the step\'s conditions.' : `Skipped: ${response.reason}` })
        : null;
      const errors = step.attempts
        .filter((attempt) => attempt.errorMessage)
        .map((attempt) => el('li', { text: `${attempts > 1 ? `Attempt ${attempt.attempt}: ` : ''}${attempt.errorMessage}` }));
      let webhookNote = null;
      if (step.webhook) {
        const outcome = String(step.webhook.outcome || 'success');
        webhookNote = el('p', { className: 'swh-step__note' }, outcome === 'failed'
          ? 'The callback reported that the external script failed.'
          : `Callback received${step.variables.length ? ` with ${plural(step.variables.length, 'value')}` : ''}.`);
      }
      const provides = step.variables.length
        ? el('p', { className: 'swf-step__vars' }, [
          el('span', { className: 'swf-step__vars-label', text: 'Provides' }),
          ...step.variables.map((variable) => el('code', { className: `swf-token swf-token--output${variable.secret ? ' swh-token--secret' : ''}`, title: variable.secret ? 'Protected value' : null, text: variable.name })),
        ])
        : null;
      run.timeline.append(el('li', { className: `swh-step swh-step--${status.variant}` }, [
        el('span', { className: `swh-step__icon swh-step__icon--${status.variant}`, 'aria-hidden': 'true', text: status.icon }),
        el('div', { className: 'swh-step__main' }, [
          el('div', { className: 'swf-step__heading' }, [
            el('span', { className: 'swh-step__name', text: `${index + 1}. ${friendlyStepName(step.name)}` }),
            statusPill(status.label, status.variant),
          ]),
          el('p', { className: 'swh-step__meta text-muted' }, meta),
          skippedReason,
          webhookNote,
          errors.length ? el('ul', { className: 'swf-step__issues' }, errors) : null,
          provides,
          payloadDetails('What was sent', step.last.requestPayload),
          payloadDetails('What came back', step.last.responsePayload),
        ]),
      ]));
    });
  }

  function renderVariables(execution) {
    const variables = execution.variables || [];
    run.variables.textContent = '';
    run.variablesCount.textContent = variables.length ? String(variables.length) : '';
    run.variablesEmpty.hidden = variables.length > 0;
    run.variablesTable.hidden = variables.length === 0;
    const callbackNames = variables.filter((variable) => variable.source === 'webhook').map((variable) => variable.name);
    run.webhookTip.hidden = callbackNames.length === 0;
    run.copyNames.onclick = () => copyText(callbackNames.join(', '), run.copyNames);

    variables.forEach((variable) => {
      const token = `\${vars.${variable.name}}`;
      const copy = el('button', { type: 'button', className: 'button button--ghost button--small', 'aria-label': `Copy ${token}`, text: 'Copy' });
      copy.addEventListener('click', () => copyText(token, copy));
      const value = variable.secret
        ? el('span', { className: 'swh-protected' }, [el('span', { className: 'swf-var__badge', text: 'Protected' }), ' Hidden. Later steps still receive the real value.'])
        : formatValue(variable.value);
      run.variables.append(el('tr', {}, [
        el('td', {}, el('div', { className: 'swh-var' }, [el('code', { className: 'swf-var__name swh-var__token', text: token }), copy])),
        el('td', {}, value),
        el('td', {}, [
          friendlyStepName(variable.step),
          variable.source === 'webhook' ? el('span', { className: 'scf-chip swh-source', text: 'Callback' }) : null,
        ]),
      ]));
    });
  }

  function detailRow(label, value) {
    return [el('dt', { text: label }), el('dd', {}, value === null || value === undefined || value === '' ? '—' : value)];
  }

  function renderDetails(execution) {
    run.details.textContent = '';
    [
      detailRow('Staff member', `${execution.staffName || '—'} (staff #${execution.staffId})`),
      detailRow('Workflow', execution.workflowName ? `${execution.workflowName} (${execution.workflowKey})` : execution.workflowKey),
      detailRow('Direction', execution.direction === 'offboarding' ? 'Offboarding' : 'Onboarding'),
      detailRow('Run ID', String(execution.executionId)),
      detailRow('Requested', timeNode(execution.requestedAt)),
      detailRow('Started', timeNode(execution.startedAt)),
      detailRow('Finished', timeNode(execution.completedAt)),
      detailRow('Current step', execution.currentStep || ''),
      detailRow('Retries used', String(execution.retriesUsed || 0)),
      detailRow('Helpdesk ticket', execution.helpdeskTicketId ? el('a', { href: `/admin/tickets/${encodeURIComponent(execution.helpdeskTicketId)}`, text: `#${execution.helpdeskTicketId}` }) : ''),
      detailRow('Last error', execution.lastError || ''),
    ].flat().forEach((node) => run.details.append(node));
  }

  function renderSummary(execution) {
    const info = stateInfo(execution.state);
    const steps = buildSteps(execution);
    const rules = [];
    const sentences = {
      completed: 'Every step finished.',
      running: 'The workflow is running now.',
      waiting: String(execution.state || '').includes('waiting_external')
        ? 'Waiting for an external callback or a technician to resume it.'
        : 'Waiting to start or for a technician.',
      failed: 'The workflow stopped because a step failed. Fix the cause, then retry.',
      other: 'The request was not approved, so nothing ran.',
    };
    rules.push(['', sentences[info.group] || '']);
    rules.push(['', progressText(execution)]);
    const failedStep = steps.slice().reverse().find((step) => step.last.status === 'failed');
    if (failedStep && info.group !== 'completed') {
      rules.push(['logic', `Failed at “${friendlyStepName(failedStep.name)}”.`]);
    }
    const variables = execution.variables || [];
    if (variables.length) {
      const secret = variables.filter((variable) => variable.secret).length;
      rules.push(['visibility', `${plural(variables.length, 'value')} produced${secret ? `, ${secret} protected` : ''}.`]);
    }
    run.summary.textContent = '';
    rules.filter(([, text]) => text).forEach(([kind, text]) => {
      run.summary.append(el('li', { className: `scf-preview__rule${kind ? ` scf-preview__rule--${kind}` : ''}`, text }));
    });
  }

  function renderRun(execution) {
    const info = stateInfo(execution.state);
    run.eyebrow.textContent = `${execution.direction === 'offboarding' ? 'Offboarding' : 'Onboarding'} · ${execution.workflowName || execution.workflowKey || 'Workflow'}`;
    run.title.textContent = execution.staffName || `Staff #${execution.staffId}`;
    run.subtitle.textContent = '';
    run.subtitle.append(statusPill(info.label, info.variant), ' Requested ', timeNode(execution.requestedAt));
    if (execution.completedAt) {
      run.subtitle.append(' · finished ', timeNode(execution.completedAt));
    }
    renderTimeline(execution);
    renderVariables(execution);
    renderDetails(execution);
    renderSummary(execution);
    const action = actionFor(execution);
    run.action.hidden = !action;
    if (action) {
      run.action.textContent = action.label === 'Retry' ? 'Retry run' : 'Resume run';
      run.action.dataset.swhAction = action.kind;
      run.action.dataset.swhId = String(execution.executionId);
      run.action.disabled = false;
    }
  }

  function openRun(executionId, tab) {
    const execution = state.executions.find((item) => String(item.executionId) === String(executionId));
    if (!execution) {
      return;
    }
    state.current = execution;
    returnFocus = document.activeElement;
    run.actionStatus.textContent = '';
    renderRun(execution);
    showTab(tab || 'steps');
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
    const body = $('.modal__body', modal);
    if (body) {
      body.scrollTop = 0;
    }
    window.requestAnimationFrame(() => $('.scf-tab.is-active', modal).focus());
    try {
      window.history.replaceState(null, '', `#run-${execution.executionId}`);
    } catch (error) {
      // History API is optional.
    }
  }

  function closeRun() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('scf-modal-open');
    state.current = null;
    try {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    } catch (error) {
      // History API is optional.
    }
    if (returnFocus && document.contains(returnFocus)) {
      returnFocus.focus();
    }
  }

  $$('[data-modal-close]', modal).forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      closeRun();
    });
  });
  modal.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') {
      return;
    }
    const focusable = $$('a[href], button:not([disabled]), summary, [tabindex="0"]', modal).filter((node) => node.offsetParent !== null);
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

  // -------------------------------------------------------------- actions

  async function runAction(kind, executionId, button) {
    const execution = state.executions.find((item) => String(item.executionId) === String(executionId));
    if (!execution) {
      return;
    }
    const who = execution.staffName || `staff #${execution.staffId}`;
    const question = kind === 'retry'
      ? `Retry the failed workflow run for ${who}? Steps that already succeeded are skipped.`
      : `Resume the paused workflow run for ${who}? It continues from the step where it stopped.`;
    if (!window.confirm(question)) {
      return;
    }
    const original = button.textContent;
    button.disabled = true;
    button.classList.add('button--processing');
    const report = (message) => {
      if (state.current && String(state.current.executionId) === String(executionId)) {
        run.actionStatus.textContent = message;
      }
      els.statusText.textContent = message;
    };
    try {
      const response = await fetch(`/api/staff/workflows/executions/${encodeURIComponent(executionId)}/${kind}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload.detail === 'string' ? payload.detail : `error ${response.status}`;
        report(`${kind === 'retry' ? 'Retry' : 'Resume'} failed: ${detail}`);
        button.disabled = false;
        button.textContent = original;
        return;
      }
      report(kind === 'retry' ? `Retry started for ${who}.` : `Resumed the run for ${who}.`);
      await load({ keepOpen: executionId });
    } catch (error) {
      report(`${kind === 'retry' ? 'Retry' : 'Resume'} failed. Check your connection and try again.`);
      button.disabled = false;
    } finally {
      button.classList.remove('button--processing');
    }
  }

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    if (target.closest('[data-swh-refresh]')) {
      event.preventDefault();
      load();
      return;
    }
    const open = target.closest('[data-swh-open]');
    if (open) {
      openRun(open.dataset.swhOpen);
      return;
    }
    const action = target.closest('[data-swh-action]');
    if (action) {
      runAction(action.dataset.swhAction, action.dataset.swhId, action);
    }
  });

  els.search.addEventListener('input', renderList);
  els.status.addEventListener('change', renderList);
  els.direction.addEventListener('change', () => load());
  els.limit.addEventListener('change', () => load());

  // --------------------------------------------------------------- load

  async function load({ keepOpen } = {}) {
    root.setAttribute('aria-busy', 'true');
    els.statusText.hidden = false;
    els.statusText.textContent = 'Loading workflow runs…';
    const params = new URLSearchParams({ limit: els.limit.value || '100' });
    if (els.direction.value) {
      params.set('direction', els.direction.value);
    }
    try {
      const response = await fetch(`/staff/workflows/history/recent?${params}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      state.executions = Array.isArray(payload.executions) ? payload.executions : [];
      els.statusText.hidden = true;
      renderCounts();
      renderList();
      if (state.current) {
        // Refresh the run that is already open (after Retry/Resume or Refresh).
        const openId = keepOpen || state.current.executionId;
        const fresh = state.executions.find((item) => String(item.executionId) === String(openId));
        if (fresh) {
          state.current = fresh;
          renderRun(fresh);
        }
      } else {
        // A shared #run-<id> link opens that run on first load.
        const linked = (window.location.hash.match(/^#run-(\d+)$/) || [])[1];
        if (linked && state.executions.some((item) => String(item.executionId) === linked)) {
          openRun(linked);
        }
      }
    } catch (error) {
      els.statusText.textContent = 'Workflow runs could not be loaded. Use Refresh to try again.';
    } finally {
      root.setAttribute('aria-busy', 'false');
    }
  }

  load();
})();
