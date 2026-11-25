/*
 * Ticket automation step designer.
 * Markup: app/templates/admin/_automation_flow.html (+ _automation_flow_modals.html)
 * Styles: app/static/css/staff_workflows.css (shared with the staff workflow
 * designer) and app/static/css/automation_flow.css.
 *
 * Steps are kept as state and posted as {"actions": [...]} in the hidden
 * ``actionPayload`` input, the format the automation form handler validates.
 */
(function () {
  'use strict';

  const root = document.querySelector('[data-af]');
  if (!root) {
    return;
  }
  const payloadInput = document.getElementById('automation-actions-data');
  const primaryModuleInput = document.getElementById('automation-action-module-primary');
  const form = root.closest('form');
  const payloadEditor = window.MyPortalActionPayloadEditor || null;
  const snippetsBySlug = window.MyPortalAutomationSnippets || {};

  // ------------------------------------------------------------------ DOM

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

  const els = {
    flow: $('[data-af-flow]', root),
    error: $('[data-af-error]', root),
    announce: $('[data-af-announce]', root),
  };

  function announce(message) {
    els.announce.textContent = '';
    window.setTimeout(() => {
      els.announce.textContent = message;
    }, 30);
  }

  function readJson(text, fallback) {
    try {
      return text ? JSON.parse(text) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  const clone = (value) => JSON.parse(JSON.stringify(value === undefined ? null : value));

  // ------------------------------------------------------------- catalog

  const CATEGORIES = [
    { key: 'ai', label: 'AI', icon: 'AI' },
    { key: 'tickets', label: 'Tickets', icon: 'T' },
    { key: 'email', label: 'Messages', icon: '✉' },
    { key: 'devices', label: 'Devices', icon: '▣' },
    { key: 'passwords', label: 'Passwords', icon: '✱' },
    { key: 'integrations', label: 'Integrations', icon: '⇄' },
    { key: 'records', label: 'Other', icon: '•' },
  ];
  const CATEGORY_BY_KEY = new Map(CATEGORIES.map((item) => [item.key, item]));
  const AI_SLUGS = new Set(['run-troubleshooting-bot', 'reprocess-ai', 'whisperx', 'ollama']);
  const TICKET_SLUGS = new Set([
    'create-ticket', 'create-task', 'update-ticket', 'update-ticket-description', 'set-ticket-custom-field', 'add-ticket-reply',
    'assign-approval-configuration', 'suggest-assets', 'smart-attachment-removal', 'matrix-chat-assign',
  ]);
  const MESSAGE_SLUGS = new Set(['smtp', 'smtp2go', 'm365-mail', 'm365-direct-delivery', 'ntfy', 'sms-gateway', 'unifi-talk']);
  const DEVICE_SLUGS = new Set(['rmm-script', 'tacticalrmm', 'huntress', 'warranty-lookup', 'cpu-age-lookup']);

  function categoryFor(slug) {
    if (slug.startsWith('ai-') || AI_SLUGS.has(slug)) return 'ai';
    if (TICKET_SLUGS.has(slug)) return 'tickets';
    if (MESSAGE_SLUGS.has(slug)) return 'email';
    if (DEVICE_SLUGS.has(slug)) return 'devices';
    if (slug === 'password-pusher') return 'passwords';
    if (['trello', 'hudu', 'gitea', 'solidtime', 'xero', 'm365-admin'].includes(slug)) return 'integrations';
    return 'records';
  }

  // Values an action returns that later steps can use, beyond status/error.
  const MODULE_OUTPUTS = {
    'ai-custom-prompt': [
      ['choice', 'The answer picked from your list (choice format)'],
      ['text', 'The written answer, or the reason for a choice'],
      ['items', 'The list of findings (list format)'],
    ],
    'ai-classify-ticket': [
      ['category', 'Category the AI chose'],
      ['priority', 'Priority the AI chose'],
      ['duplicate_of', 'Ticket it looks like a duplicate of'],
    ],
    'ai-rename-ticket': [['subject', 'The new subject']],
    'create-ticket': [['ticket_id', 'ID of the new ticket'], ['ticket_number', 'Number of the new ticket']],
    'create-task': [['task_id', 'ID of the new task']],
  };
  const COMMON_OUTPUTS = [
    ['status', 'succeeded, failed or skipped'],
    ['error', 'Why it failed, when it did'],
  ];

  const TICKET_VARIABLES = [
    ['ticket.id', 'Ticket ID'],
    ['ticket.number', 'Ticket number'],
    ['ticket.subject', 'Subject'],
    ['ticket.status', 'Status'],
    ['ticket.priority', 'Priority'],
    ['ticket.category', 'Category'],
    ['ticket.body', 'Message that triggered the event'],
    ['ticket.initial_body', 'Original request'],
    ['ticket.requester.email', 'Requester email'],
    ['ticket.requester.display_name', 'Requester name'],
    ['ticket.assigned_user.email', 'Assigned technician email'],
    ['ticket.company.name', 'Company'],
    ['ticket.latest_customer_reply.body', 'Latest customer reply'],
  ];

  const OPERATORS = [
    ['equals', 'is'],
    ['not_equals', 'is not'],
    ['contains', 'contains'],
    ['not_contains', 'does not contain'],
    ['starts_with', 'starts with'],
    ['ends_with', 'ends with'],
    ['in', 'is one of'],
    ['not_in', 'is not one of'],
    ['greater_than', 'is greater than'],
    ['less_than', 'is less than'],
    ['is_empty', 'is empty'],
    ['is_not_empty', 'is not empty'],
  ];
  const OPERATOR_LABELS = new Map(OPERATORS);
  const VALUELESS = new Set(['is_empty', 'is_not_empty']);
  const RESULT_NAME = /^[a-z][a-z0-9_]{0,39}$/;
  const RESERVED_NAMES = new Set(['context', 'now', 'ticket', 'vars', 'company', 'user', 'requester']);
  const VAR_REF = /\{\{\s*vars\.([a-z][a-z0-9_]*)[^}]*\}\}/g;

  const catalog = readJson(root.dataset.modules, []).map((module) => {
    const slug = String(module.slug || '').trim();
    return {
      slug,
      name: String(module.name || slug),
      description: String(module.description || ''),
      schema: module.payload_schema || null,
      category: categoryFor(slug),
    };
  }).filter((item) => item.slug);
  const catalogBySlug = new Map(catalog.map((item) => [item.slug, item]));

  function entryFor(slug) {
    return catalogBySlug.get(slug) || { slug, name: slug || 'Unknown action', description: '', schema: null, category: categoryFor(slug || '') };
  }

  function iconFor(category) {
    const meta = CATEGORY_BY_KEY.get(category) || CATEGORY_BY_KEY.get('records');
    return el('span', { className: `swf-cat-icon af-cat-icon--${category}`, 'aria-hidden': 'true', text: meta.icon });
  }

  function outputsFor(slug) {
    return COMMON_OUTPUTS.concat(MODULE_OUTPUTS[slug] || []);
  }

  const isAiModule = (slug) => categoryFor(slug) === 'ai';

  // --------------------------------------------------------------- state

  let uidCounter = 0;
  const nextUid = () => {
    uidCounter += 1;
    return `af-${uidCounter}`;
  };

  function readAction(raw) {
    const runIf = raw && raw.run_if && Array.isArray(raw.run_if.conditions) && raw.run_if.conditions.length
      ? { match: raw.run_if.match === 'any' ? 'any' : 'all', conditions: raw.run_if.conditions.map((c) => ({ ...c })) }
      : null;
    return {
      uid: nextUid(),
      module: String(raw.module || ''),
      note: String(raw.note || ''),
      payload: raw.payload && typeof raw.payload === 'object' && !Array.isArray(raw.payload) ? clone(raw.payload) : {},
      output_var: String(raw.output_var || ''),
      run_if: runIf,
    };
  }

  const initial = readJson(payloadInput.value, {});
  const state = {
    trigger: readJson(($('[data-af-trigger]', root) || {}).textContent, {}),
    actions: (Array.isArray(initial && initial.actions) ? initial.actions : [])
      .map((action, index) => ({ action, index }))
      .sort((a, b) => {
        const oa = typeof a.action.order === 'number' ? a.action.order : a.index;
        const ob = typeof b.action.order === 'number' ? b.action.order : b.index;
        return oa - ob || a.index - b.index;
      })
      .map(({ action }) => readAction(action || {})),
  };

  function serializeActions(actions) {
    return actions.map((action, index) => {
      const entry = { order: index, module: action.module, payload: action.payload };
      if (action.note) entry.note = action.note;
      if (action.output_var) entry.output_var = action.output_var;
      if (action.run_if && action.run_if.conditions.length) entry.run_if = action.run_if;
      return entry;
    });
  }

  function syncHiddenInputs() {
    const actions = serializeActions(state.actions);
    payloadInput.value = actions.length ? JSON.stringify({ actions }) : '';
    primaryModuleInput.value = actions.length ? actions[0].module : '';
  }

  function savedNamesBefore(index, actions = state.actions) {
    return actions.slice(0, index).filter((action) => action.output_var);
  }

  // ------------------------------------------------------------- summaries

  function friendlyField(field) {
    const match = /^vars\.([a-z0-9_]+)\.(.+)$/.exec(field);
    if (match) {
      return `${match[1]}'s ${match[2].replace(/_/g, ' ')}`;
    }
    const ticket = TICKET_VARIABLES.find(([path]) => path === field);
    return ticket ? `the ticket's ${ticket[1].toLowerCase()}` : field;
  }

  function conditionSentence(runIf) {
    if (!runIf || !runIf.conditions.length) {
      return '';
    }
    const parts = runIf.conditions.map((condition) => {
      const operator = OPERATOR_LABELS.get(condition.operator) || condition.operator;
      const value = VALUELESS.has(condition.operator) ? '' : ` “${condition.value ?? ''}”`;
      return `${friendlyField(condition.field)} ${operator}${value}`;
    });
    return parts.join(runIf.match === 'any' ? ' or ' : ' and ');
  }

  function collectRefs(value, into = new Set()) {
    if (typeof value === 'string') {
      for (const match of value.matchAll(VAR_REF)) {
        into.add(match[1]);
      }
    } else if (Array.isArray(value)) {
      value.forEach((item) => collectRefs(item, into));
    } else if (value && typeof value === 'object') {
      Object.values(value).forEach((item) => collectRefs(item, into));
    }
    return into;
  }

  function stepIssues(index, action = state.actions[index], actions = state.actions) {
    const issues = [];
    const earlier = new Set(savedNamesBefore(index, actions).map((item) => item.output_var));
    const later = new Set(actions.slice(index + 1).map((item) => item.output_var).filter(Boolean));
    const describe = (name) => (later.has(name) || name === action.output_var
      ? `uses “${name}”, which isn't saved until a later step`
      : `uses “${name}”, which no earlier step saves`);
    collectRefs(action.payload).forEach((name) => {
      if (!earlier.has(name)) issues.push(`A field ${describe(name)}.`);
    });
    (action.run_if ? action.run_if.conditions : []).forEach((condition) => {
      const match = /^vars\.([^.]+)/.exec(condition.field || '');
      if (match && !earlier.has(match[1])) issues.push(`A run-when rule ${describe(match[1])}.`);
    });
    if (action.output_var && actions.some((other, otherIndex) => otherIndex !== index && other.output_var === action.output_var)) {
      issues.push(`Another step also saves its result as “${action.output_var}”.`);
    }
    if (!catalogBySlug.has(action.module)) {
      issues.push('This action isn\'t available in this deployment.');
    }
    return issues;
  }

  function chip(text, modifier, title) {
    return el('li', { className: `scf-chip${modifier ? ` ${modifier}` : ''}`, title: title || null, text });
  }

  // ----------------------------------------------------------------- flow

  function iconButton(label, symbol, dataset, disabled) {
    return el('button', {
      type: 'button',
      className: 'scf-icon-button',
      'aria-label': label,
      title: label,
      disabled: Boolean(disabled),
      dataset,
    }, el('span', { 'aria-hidden': 'true', text: symbol }));
  }

  function insertRow(index, isEnd) {
    return el('li', { className: `swf-insert${isEnd ? ' swf-insert--end' : ''}` }, el('button', {
      type: 'button',
      className: 'swf-insert__button',
      'aria-haspopup': 'dialog',
      'aria-controls': 'af-picker-modal',
      dataset: { afInsert: index },
    }, [el('span', { 'aria-hidden': 'true', text: '+' }), isEnd ? ' Add a step' : el('span', { className: 'visually-hidden', text: `Add a step before step ${index + 1}` })]));
  }

  function stepTitle(action, index) {
    return action.note || `${entryFor(action.module).name} (step ${index + 1})`;
  }

  function renderFlow() {
    els.flow.textContent = '';
    els.flow.append(el('li', { className: 'swf-node swf-node--trigger' }, [
      el('span', { className: 'swf-cat-icon swf-cat-icon--trigger', 'aria-hidden': 'true', text: '▶' }),
      el('div', { className: 'swf-node__main' }, [
        el('p', { className: 'swf-node__title', text: state.trigger.title || 'When the automation runs' }),
        state.trigger.text ? el('p', { className: 'swf-node__text text-muted', text: state.trigger.text }) : null,
        el('p', { className: 'swf-node__text text-muted', text: 'Every step can use the ticket variables.' }),
      ]),
      el('a', { className: 'button button--ghost button--small', href: '#automation-when', text: 'Change' }),
    ]));

    if (!state.actions.length) {
      els.flow.append(el('li', { className: 'swf-node swf-node--empty' }, [
        el('div', { className: 'swf-node__main' }, [
          el('p', { className: 'swf-node__title', text: 'No steps yet' }),
          el('p', { className: 'text-muted', text: 'Add the first thing MyPortal should do, such as running an AI prompt over the ticket, updating it or emailing someone.' }),
        ]),
      ]));
    }

    state.actions.forEach((action, index) => {
      els.flow.append(insertRow(index, false));
      const entry = entryFor(action.module);
      const category = CATEGORY_BY_KEY.get(entry.category) || CATEGORY_BY_KEY.get('records');
      const issues = stepIssues(index);
      const refs = Array.from(collectRefs(action.payload));
      const chips = [];
      const sentence = conditionSentence(action.run_if);
      if (sentence) {
        chips.push(chip(`Only when ${sentence}`, 'scf-chip--logic'));
      }
      if (isAiModule(action.module) && action.output_var) {
        chips.push(chip('AI text is escaped', 'scf-chip--muted', 'Free text from this step is HTML-escaped before later steps use it.'));
      }
      if (issues.length) {
        chips.push(chip(issues.length === 1 ? '1 problem' : `${issues.length} problems`, 'swf-chip--danger'));
      }
      const title = stepTitle(action, index);
      const main = el('div', { className: 'swf-node__main' }, [
        el('div', { className: 'swf-step__heading' }, [
          el('button', {
            type: 'button',
            className: 'swf-step__name',
            'aria-haspopup': 'dialog',
            'aria-controls': 'af-step-modal',
            dataset: { afEdit: index },
            text: title,
          }),
          el('span', { className: 'swf-step__type', text: `${entry.name} · ${category.label}` }),
        ]),
        chips.length ? el('ul', { className: 'scf-item__chips', 'aria-label': 'Step summary' }, chips) : null,
        refs.length ? el('p', { className: 'swf-step__vars' }, [
          el('span', { className: 'swf-step__vars-label', text: 'Uses' }),
          ...refs.map((name) => el('code', { className: 'swf-token', text: name })),
        ]) : null,
        action.output_var ? el('p', { className: 'swf-step__vars' }, [
          el('span', { className: 'swf-step__vars-label', text: 'Saves as' }),
          el('code', { className: 'swf-token swf-token--output', text: `vars.${action.output_var}` }),
        ]) : null,
        issues.length ? el('ul', { className: 'swf-step__issues' }, issues.map((issue) => el('li', { text: issue }))) : null,
      ]);
      const actions = el('div', { className: 'swf-node__actions' }, [
        iconButton(`Move ${title} up`, '↑', { afMove: index, afDirection: -1 }, index === 0),
        iconButton(`Move ${title} down`, '↓', { afMove: index, afDirection: 1 }, index === state.actions.length - 1),
        el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { afDuplicate: index }, text: 'Duplicate' }),
        el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { afEdit: index }, 'aria-haspopup': 'dialog', 'aria-controls': 'af-step-modal', text: 'Edit' }),
      ]);
      els.flow.append(el('li', {
        className: `swf-node swf-step af-step${issues.length ? ' has-issues' : ''}`,
        dataset: { afStepIndex: index },
        'aria-label': `Step ${index + 1}: ${title}`,
      }, [
        el('span', { className: 'swf-step__num', 'aria-hidden': 'true', text: index + 1 }),
        iconFor(entry.category),
        main,
        actions,
      ]));
    });

    els.flow.append(insertRow(state.actions.length, true));
    els.flow.append(el('li', { className: 'swf-node swf-node--end' }, [
      el('span', { className: 'swf-cat-icon swf-cat-icon--end', 'aria-hidden': 'true', text: '✓' }),
      el('div', { className: 'swf-node__main' }, [
        el('p', { className: 'swf-node__title', text: 'Automation complete' }),
        el('p', { className: 'swf-node__text text-muted', text: 'Each step\'s outcome, including skipped steps, is recorded in the automation history.' }),
      ]),
    ]));
  }

  function commit(message) {
    syncHiddenInputs();
    renderFlow();
    els.error.hidden = true;
    if (message) announce(message);
  }

  els.flow.addEventListener('click', (event) => {
    const target = event.target.closest('button');
    if (!target) return;
    const { afInsert, afEdit, afMove, afDirection, afDuplicate } = target.dataset;
    if (afInsert !== undefined) {
      openPicker(Number(afInsert));
    } else if (afEdit !== undefined) {
      openStepEditor(Number(afEdit));
    } else if (afMove !== undefined) {
      const from = Number(afMove);
      const to = from + Number(afDirection);
      if (to < 0 || to >= state.actions.length) return;
      const [moved] = state.actions.splice(from, 1);
      state.actions.splice(to, 0, moved);
      commit(`Moved ${stepTitle(moved, to)} to position ${to + 1}.`);
      const button = $(`[data-af-move="${to}"][data-af-direction="${afDirection}"]`, els.flow)
        || $(`[data-af-edit="${to}"]`, els.flow);
      if (button) button.focus();
    } else if (afDuplicate !== undefined) {
      const index = Number(afDuplicate);
      const copy = readAction(serializeActions([state.actions[index]])[0]);
      copy.note = copy.note ? `${copy.note} (copy)` : '';
      copy.output_var = '';
      state.actions.splice(index + 1, 0, copy);
      commit(`Duplicated step ${index + 1}.`);
    }
  });

  // ---------------------------------------------------------------- modals

  const modalStack = [];

  function openModal(modal, { onRequestClose, focus } = {}) {
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
    modal._afRequestClose = onRequestClose || (() => closeModal(modal));
    modal._afReturnFocus = document.activeElement;
    modalStack.push(modal);
    const body = $('.modal__body', modal);
    if (body) body.scrollTop = 0;
    window.requestAnimationFrame(() => {
      const target = focus || $('input:not([type="hidden"]), button:not(.modal__close)', modal);
      if (target && typeof target.focus === 'function') target.focus();
    });
  }

  function closeModal(modal) {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    const position = modalStack.indexOf(modal);
    if (position >= 0) modalStack.splice(position, 1);
    if (!modalStack.length) document.body.classList.remove('scf-modal-open');
    const returnFocus = modal._afReturnFocus;
    if (returnFocus && document.contains(returnFocus) && typeof returnFocus.focus === 'function') {
      returnFocus.focus();
    }
  }

  const requestClose = (modal) => (typeof modal._afRequestClose === 'function' ? modal._afRequestClose() : closeModal(modal));

  ['af-picker-modal', 'af-step-modal', 'af-variables-modal'].forEach((id) => {
    const modal = document.getElementById(id);
    if (!modal) return;
    $$('[data-modal-close]', modal).forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        requestClose(modal);
      });
    });
    modal.addEventListener('click', (event) => {
      if (event.target === modal) requestClose(modal);
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        requestClose(modal);
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = $$('a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled])', modal)
        .filter((node) => node.offsetParent !== null);
      if (!focusable.length) return;
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
  });

  // ---------------------------------------------------------- variable list

  function variableGroups(index, actions = state.actions) {
    const groups = [{
      label: 'Ticket',
      variables: TICKET_VARIABLES.map(([path, label]) => ({ path, label })),
    }];
    savedNamesBefore(index, actions).forEach((action) => {
      groups.push({
        label: `${action.note || entryFor(action.module).name} (saved as ${action.output_var})`,
        ai: isAiModule(action.module),
        variables: outputsFor(action.module).map(([key, label]) => ({ path: `vars.${action.output_var}.${key}`, label })),
      });
    });
    return groups;
  }

  function renderVariableGroups(container, groups, { query = '', onPick } = {}) {
    container.textContent = '';
    const needle = query.trim().toLowerCase();
    let shown = 0;
    groups.forEach((group) => {
      const variables = group.variables.filter((variable) => !needle
        || variable.path.toLowerCase().includes(needle)
        || variable.label.toLowerCase().includes(needle)
        || group.label.toLowerCase().includes(needle));
      if (!variables.length) return;
      shown += variables.length;
      const list = el('ul', { className: 'swf-vars__list' });
      variables.forEach((variable) => {
        const item = el('button', { type: 'button', className: 'swf-var', title: `Insert {{${variable.path}}}` }, [
          el('span', { className: 'swf-var__label' }, variable.label),
          el('code', { className: 'swf-var__name', text: variable.path }),
        ]);
        item.addEventListener('click', () => onPick(variable));
        list.append(el('li', {}, item));
      });
      container.append(el('section', { className: 'swf-vars__group' }, [
        el('h4', { className: 'swf-vars__title', text: group.label }),
        group.ai ? el('p', { className: 'swf-vars__note', text: 'AI-written text is HTML-escaped.' }) : null,
        list,
      ]));
    });
    if (!shown) {
      container.append(el('p', { className: 'scf-empty scf-empty--inline', text: needle ? 'No variables match your search.' : 'No variables available.' }));
    }
  }

  // ------------------------------------------------------------ step picker

  const picker = { modal: document.getElementById('af-picker-modal'), insertAt: 0 };
  picker.search = $('[data-af-picker-search]', picker.modal);
  picker.groups = $('[data-af-picker-groups]', picker.modal);
  picker.empty = $('[data-af-picker-empty]', picker.modal);
  picker.eyebrow = $('[data-af-picker-eyebrow]', picker.modal);

  function renderPicker() {
    const needle = picker.search.value.trim().toLowerCase();
    picker.groups.textContent = '';
    let shown = 0;
    CATEGORIES.forEach((category) => {
      const items = catalog.filter((item) => item.category === category.key)
        .filter((item) => !needle || `${item.name} ${item.description} ${item.slug} ${category.label}`.toLowerCase().includes(needle))
        .sort((a, b) => a.name.localeCompare(b.name));
      if (!items.length) return;
      const cards = items.map((item) => {
        shown += 1;
        const outputs = (MODULE_OUTPUTS[item.slug] || []).map(([key]) => key);
        const card = el('button', { type: 'button', className: 'swf-pick', dataset: { afPick: item.slug } }, [
          iconFor(item.category),
          el('span', { className: 'swf-pick__text' }, [
            el('span', { className: 'swf-pick__name', text: item.name }),
            item.description ? el('span', { className: 'swf-pick__help', text: item.description }) : null,
            outputs.length ? el('span', { className: 'swf-pick__provides' }, ['Provides ', ...outputs.map((key) => el('code', { className: 'swf-token swf-token--output', text: key }))]) : null,
          ]),
        ]);
        card.addEventListener('click', () => {
          closeModal(picker.modal);
          openStepEditor(picker.insertAt, { draft: readAction({ module: item.slug, note: item.name }) });
        });
        return el('li', {}, card);
      });
      picker.groups.append(el('section', { className: 'swf-picker__group' }, [
        el('h3', { className: 'scf-group__title', text: category.label }),
        el('ul', { className: 'swf-picker__list' }, cards),
      ]));
    });
    picker.empty.hidden = shown > 0;
  }

  picker.search.addEventListener('input', renderPicker);

  function openPicker(index) {
    picker.insertAt = Math.max(0, Math.min(Number(index) || 0, state.actions.length));
    picker.eyebrow.textContent = picker.insertAt >= state.actions.length
      ? `Add step ${state.actions.length + 1}`
      : `Insert before step ${picker.insertAt + 1}`;
    picker.search.value = '';
    renderPicker();
    openModal(picker.modal, { focus: picker.search });
  }

  // ------------------------------------------------------------ step editor

  const editorModal = document.getElementById('af-step-modal');
  const editor = {
    index: 0,
    isNew: false,
    draft: null,
    snapshot: '',
    helperTarget: null,
    eyebrow: $('[data-af-step-eyebrow]', editorModal),
    title: $('[data-af-step-title]', editorModal),
    subtitle: $('[data-af-step-subtitle]', editorModal),
    note: $('[data-af-step-note]', editorModal),
    modeField: $('[data-af-mode-field]', editorModal),
    modes: $$('[data-af-payload-mode]', editorModal),
    schema: $('[data-af-schema]', editorModal),
    raw: $('[data-af-raw]', editorModal),
    payload: $('[data-af-step-payload]', editorModal),
    snippets: $('[data-af-snippets]', editorModal),
    snippetSelect: $('[data-af-snippet-select]', editorModal),
    snippetApply: $('[data-af-snippet-apply]', editorModal),
    output: $('[data-af-step-output]', editorModal),
    provides: $('[data-af-provides]', editorModal),
    aiNote: $('[data-af-ai-note]', editorModal),
    conditionModes: $$('[data-af-condition-mode]', editorModal),
    conditions: $('[data-af-conditions]', editorModal),
    rules: $('[data-af-rules]', editorModal),
    ruleAdd: $('[data-af-rule-add]', editorModal),
    sentence: $('[data-af-condition-sentence]', editorModal),
    helperGroups: $('[data-af-helper-groups]', editorModal),
    helperSearch: $('[data-af-helper-search]', editorModal),
    helperTargetText: $('[data-af-helper-target]', editorModal),
    deleteButton: $('[data-af-step-delete]', editorModal),
    applyButton: $('[data-af-step-apply]', editorModal),
  };

  const showTab = (() => {
    const tabs = $$('[role="tab"]', editorModal);
    const show = (name, focus) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.afTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) tab.focus();
      });
      $$('[data-af-panel]', editorModal).forEach((panel) => {
        panel.hidden = panel.dataset.afPanel !== name;
      });
    };
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => show(tab.dataset.afTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        event.preventDefault();
        const position = tabs.indexOf(tab);
        const next = tabs[(position + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        show(next.dataset.afTab, true);
      });
    });
    return show;
  })();

  function editorActions() {
    // The step list as it would be with the draft applied, for helpers and checks.
    const actions = state.actions.slice();
    if (editor.isNew) {
      actions.splice(editor.index, 0, editor.draft);
    } else {
      actions[editor.index] = editor.draft;
    }
    return actions;
  }

  function setFieldError(name, message) {
    const node = $(`[data-af-field-error="${name}"]`, editorModal);
    if (!node) return;
    node.textContent = message || '';
    node.hidden = !message;
  }

  function schemaFields(slug) {
    const entry = entryFor(slug);
    return payloadEditor ? payloadEditor.getSchemaFields(entry.schema) : [];
  }

  function currentMode() {
    const checked = editor.modes.find((input) => input.checked);
    return checked ? checked.value : 'raw';
  }

  function setMode(mode) {
    editor.modes.forEach((input) => {
      input.checked = input.value === mode;
    });
    editor.schema.hidden = mode !== 'schema';
    editor.raw.hidden = mode !== 'raw';
  }

  function buildSchema(payload) {
    editor.schema.textContent = '';
    if (!payloadEditor) return false;
    const result = payloadEditor.buildSchemaFields({
      container: editor.schema,
      schema: entryFor(editor.draft.module).schema,
      existingPayload: payload,
      idPrefix: 'af-step-field',
      onValueChange: onEditorChange,
    });
    return result.hasSchema;
  }

  // Reads the payload from whichever mode is showing; returns {ok, payload|error}.
  function readPayload() {
    if (currentMode() === 'schema' && payloadEditor) {
      return payloadEditor.parseSchemaFields({ container: editor.schema, rowIndex: editor.index, errorPrefix: 'Step' });
    }
    if (payloadEditor) {
      return payloadEditor.parseRawPayload({ text: editor.payload.value, rowIndex: editor.index, errorPrefix: 'Step' });
    }
    try {
      return { ok: true, payload: editor.payload.value.trim() ? JSON.parse(editor.payload.value) : {} };
    } catch (error) {
      return { ok: false, error: 'The payload must be valid JSON.' };
    }
  }

  function switchMode(mode) {
    const parsed = readPayload();
    if (!parsed.ok) {
      setMode(currentMode());
      setFieldError('payload', parsed.error);
      return;
    }
    setFieldError('payload', '');
    if (mode === 'schema') {
      buildSchema(parsed.payload);
    } else {
      editor.payload.value = Object.keys(parsed.payload).length ? JSON.stringify(parsed.payload, null, 2) : '';
    }
    setMode(mode);
  }

  editor.modes.forEach((input) => {
    input.addEventListener('change', () => {
      const previous = input.value === 'schema' ? 'raw' : 'schema';
      setMode(previous);
      switchMode(input.value);
    });
  });

  function renderSnippets() {
    const snippets = (snippetsBySlug[editor.draft.module] || []).filter((item) => item && item.value);
    editor.snippetSelect.textContent = '';
    editor.snippetSelect.append(el('option', { value: '', text: 'Start from an example…' }));
    snippets.forEach((item, index) => editor.snippetSelect.append(el('option', { value: index, text: item.label || `Example ${index + 1}` })));
    editor.snippets.hidden = !snippets.length;
    editor.snippetApply.onclick = () => {
      const chosen = snippets[Number(editor.snippetSelect.value)];
      if (!chosen) {
        editor.snippetSelect.focus();
        return;
      }
      editor.payload.value = chosen.value;
      editor.snippetSelect.value = '';
      onEditorChange();
    };
  }

  function renderProvides() {
    const name = editor.output.value.trim();
    editor.provides.textContent = '';
    outputsFor(editor.draft.module).forEach(([key, label]) => {
      editor.provides.append(el('li', { className: 'af-provides__item' }, [
        el('code', { className: 'swf-token swf-token--output', text: name ? `{{vars.${name}.${key}}}` : key }),
        el('span', { className: 'text-muted', text: label }),
      ]));
    });
    editor.aiNote.hidden = !isAiModule(editor.draft.module);
  }

  function ruleRow(condition = {}) {
    const fieldId = `af-rule-${nextUid()}`;
    const field = el('input', {
      type: 'text', className: 'form-input scf-mono', list: 'af-rule-fields', autocomplete: 'off', spellcheck: 'false',
      placeholder: 'vars.triage.choice', 'aria-label': 'Value to check', dataset: { afRuleField: '' }, id: fieldId,
    });
    field.value = condition.field || '';
    const operator = el('select', { className: 'form-input', 'aria-label': 'Comparison', dataset: { afRuleOperator: '' } },
      OPERATORS.map(([value, label]) => el('option', { value, text: label })));
    operator.value = condition.operator || 'equals';
    const value = el('input', { type: 'text', className: 'form-input', placeholder: 'Billing', 'aria-label': 'Compare with', dataset: { afRuleValue: '' } });
    value.value = condition.value === undefined || condition.value === null ? '' : String(condition.value);
    const remove = iconButton('Remove rule', '×', { afRuleRemove: '' }, false);
    const row = el('li', { className: 'af-rule' }, [field, operator, value, remove]);
    const syncValue = () => {
      value.hidden = VALUELESS.has(operator.value);
    };
    syncValue();
    operator.addEventListener('change', () => {
      syncValue();
      onEditorChange();
    });
    [field, value].forEach((input) => input.addEventListener('input', onEditorChange));
    remove.addEventListener('click', () => {
      row.remove();
      if (!editor.rules.children.length) {
        editor.conditionModes.forEach((input) => {
          input.checked = input.value === '';
        });
        syncConditionMode();
      }
      onEditorChange();
    });
    return row;
  }

  function renderRuleFieldOptions() {
    let datalist = document.getElementById('af-rule-fields');
    if (!datalist) {
      datalist = el('datalist', { id: 'af-rule-fields' });
      editorModal.append(datalist);
    }
    datalist.textContent = '';
    variableGroups(editor.index, editorActions()).forEach((group) => {
      group.variables.forEach((variable) => datalist.append(el('option', { value: variable.path, label: variable.label })));
    });
  }

  function conditionMode() {
    const checked = editor.conditionModes.find((input) => input.checked);
    return checked ? checked.value : '';
  }

  function syncConditionMode() {
    const mode = conditionMode();
    editor.conditions.hidden = !mode;
    if (mode && !editor.rules.children.length) {
      editor.rules.append(ruleRow());
    }
  }

  editor.conditionModes.forEach((input) => input.addEventListener('change', () => {
    syncConditionMode();
    onEditorChange();
  }));
  editor.ruleAdd.addEventListener('click', () => {
    const row = ruleRow();
    editor.rules.append(row);
    $('input', row).focus();
    onEditorChange();
  });

  function readRules() {
    const mode = conditionMode();
    if (!mode) return null;
    const conditions = $$('.af-rule', editor.rules).map((row) => {
      const condition = {
        field: $('[data-af-rule-field]', row).value.trim(),
        operator: $('[data-af-rule-operator]', row).value,
      };
      if (!VALUELESS.has(condition.operator)) condition.value = $('[data-af-rule-value]', row).value;
      return condition;
    });
    return conditions.length ? { match: mode, conditions } : null;
  }

  // Copies the editor's inputs into the draft (payload only when it parses).
  function readEditorIntoDraft() {
    editor.draft.note = editor.note.value.trim();
    editor.draft.output_var = editor.output.value.trim();
    editor.draft.run_if = readRules();
    const parsed = readPayload();
    if (parsed.ok) editor.draft.payload = parsed.payload;
    return parsed;
  }

  function renderEditorSummaries() {
    const sentence = conditionSentence(editor.draft.run_if);
    editor.sentence.textContent = sentence ? `Runs only when ${sentence}.` : '';
    $('[data-af-tab-dot="outputs"]', editorModal).hidden = !editor.draft.output_var;
    $('[data-af-tab-dot="conditions"]', editorModal).hidden = !editor.draft.run_if;
    editor.title.textContent = editor.draft.note || entryFor(editor.draft.module).name;
    renderProvides();
    renderHelper();
  }

  function onEditorChange() {
    readEditorIntoDraft();
    renderEditorSummaries();
  }

  [editor.note, editor.output, editor.payload].forEach((input) => input.addEventListener('input', onEditorChange));

  // Variable helper: inserts {{path}} into the last focused text field.
  function renderHelper() {
    renderVariableGroups(editor.helperGroups, variableGroups(editor.index, editorActions()), {
      query: editor.helperSearch.value,
      onPick: (variable) => {
        const target = editor.helperTarget && document.contains(editor.helperTarget) ? editor.helperTarget : null;
        if (!target) {
          announce('Click into a field first, then pick a value to insert.');
          editor.helperTargetText.textContent = 'Click into a field first, then pick a value to insert it.';
          return;
        }
        const token = target.matches('[data-af-rule-field]') ? variable.path : `{{${variable.path}}}`;
        if (target.matches('[data-af-rule-field]')) {
          target.value = token;
        } else {
          const start = target.selectionStart ?? target.value.length;
          const end = target.selectionEnd ?? target.value.length;
          target.value = target.value.slice(0, start) + token + target.value.slice(end);
          const caret = start + token.length;
          target.setSelectionRange(caret, caret);
        }
        target.focus();
        target.dispatchEvent(new Event('input', { bubbles: true }));
        announce(`Inserted ${token}`);
      },
    });
  }

  editor.helperSearch.addEventListener('input', renderHelper);
  editorModal.addEventListener('focusin', (event) => {
    const target = event.target;
    if (!target.matches('input[type="text"], input:not([type]), textarea') || target.closest('.swf-helper')) return;
    if (target === editor.output) return;
    editor.helperTarget = target;
    const label = target.id ? $(`label[for="${target.id}"]`, editorModal) : null;
    const name = target.getAttribute('aria-label') || (label ? label.textContent.trim() : 'this field');
    editor.helperTargetText.textContent = `Inserting into: ${name}`;
  });

  function openStepEditor(index, { draft } = {}) {
    editor.isNew = Boolean(draft);
    editor.index = index;
    editor.draft = draft || readAction(serializeActions([state.actions[index]])[0]);
    editor.helperTarget = null;
    editor.helperTargetText.textContent = 'Click into a field, then pick a value to insert it.';
    editor.helperSearch.value = '';
    const entry = entryFor(editor.draft.module);
    const category = CATEGORY_BY_KEY.get(entry.category) || CATEGORY_BY_KEY.get('records');
    editor.eyebrow.textContent = editor.isNew
      ? `New step ${index + 1} · ${category.label}`
      : `Step ${index + 1} · ${category.label}`;
    editor.subtitle.textContent = entry.description || entry.name;
    editor.deleteButton.hidden = editor.isNew;
    editor.applyButton.textContent = editor.isNew ? 'Add step' : 'Apply';
    editor.note.value = editor.draft.note;
    editor.output.value = editor.draft.output_var;
    ['note', 'payload', 'output', 'conditions'].forEach((name) => setFieldError(name, ''));

    const payload = editor.draft.payload;
    editor.payload.value = Object.keys(payload).length ? JSON.stringify(payload, null, 2) : '';
    const fields = schemaFields(editor.draft.module);
    const fieldNames = new Set(fields.map((field) => field.name));
    const hasSchema = buildSchema(payload);
    const unknown = payloadEditor ? payloadEditor.hasUnknownKeys(payload, fieldNames) : true;
    editor.modeField.hidden = !hasSchema;
    setMode(hasSchema && !unknown ? 'schema' : 'raw');
    renderSnippets();

    editor.rules.textContent = '';
    const runIf = editor.draft.run_if;
    editor.conditionModes.forEach((input) => {
      input.checked = input.value === (runIf ? runIf.match : '');
    });
    (runIf ? runIf.conditions : []).forEach((condition) => editor.rules.append(ruleRow(condition)));
    syncConditionMode();
    renderRuleFieldOptions();
    showTab('settings');
    renderEditorSummaries();
    editor.snapshot = JSON.stringify(serializeActions([editor.draft]));
    openModal(editorModal, { focus: editor.note, onRequestClose: requestCloseEditor });
  }

  function requestCloseEditor() {
    readEditorIntoDraft();
    const changed = editor.isNew || JSON.stringify(serializeActions([editor.draft])) !== editor.snapshot;
    if (changed && !window.confirm(editor.isNew ? 'Discard this new step?' : 'Discard your changes to this step?')) {
      return;
    }
    closeModal(editorModal);
  }

  function validateEditor() {
    const parsed = readEditorIntoDraft();
    let firstTab = null;
    const fail = (tab, field, message) => {
      setFieldError(field, message);
      if (!firstTab) firstTab = tab;
    };
    ['note', 'payload', 'output', 'conditions'].forEach((name) => setFieldError(name, ''));
    if (!editor.draft.note) fail('settings', 'note', 'Give the step a short name so it is easy to find in the history.');
    if (!parsed.ok) fail('settings', 'payload', parsed.error);
    const name = editor.draft.output_var;
    const actions = editorActions();
    if (name) {
      if (!RESULT_NAME.test(name) || RESERVED_NAMES.has(name)) {
        fail('outputs', 'output', 'Start with a lowercase letter and use only lowercase letters, numbers and underscores (not ticket, vars or context).');
      } else if (actions.some((other) => other !== editor.draft && other.output_var === name)) {
        fail('outputs', 'output', `Another step already saves its result as “${name}”.`);
      }
    }
    if (editor.draft.run_if) {
      const earlier = new Set(savedNamesBefore(editor.index, actions).map((item) => item.output_var));
      for (const condition of editor.draft.run_if.conditions) {
        if (!/^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$/.test(condition.field)) {
          fail('conditions', 'conditions', 'Choose what each rule checks, e.g. vars.triage.choice or ticket.priority.');
          break;
        }
        const match = /^vars\.([^.]+)/.exec(condition.field);
        if (match && !earlier.has(match[1])) {
          fail('conditions', 'conditions', `No earlier step saves a result named “${match[1]}”. Name an earlier step's result on its “What it provides” tab.`);
          break;
        }
      }
    }
    if (firstTab) {
      showTab(firstTab);
      const error = $('.scf-error:not([hidden])', editorModal);
      if (error) error.scrollIntoView({ block: 'nearest' });
      return false;
    }
    return true;
  }

  editor.applyButton.addEventListener('click', () => {
    if (!validateEditor()) return;
    const draft = editor.draft;
    if (editor.isNew) {
      state.actions.splice(editor.index, 0, draft);
    } else {
      state.actions[editor.index] = draft;
    }
    closeModal(editorModal);
    commit(editor.isNew ? `Added ${draft.note}.` : `Updated ${draft.note}.`);
    const button = $(`[data-af-edit="${editor.index}"]`, els.flow);
    if (button) button.focus();
  });

  editor.deleteButton.addEventListener('click', () => {
    const action = state.actions[editor.index];
    if (!window.confirm(`Remove “${stepTitle(action, editor.index)}”?`)) return;
    state.actions.splice(editor.index, 1);
    closeModal(editorModal);
    commit('Step removed.');
    const fallback = $('[data-af-insert]', els.flow);
    if (fallback) fallback.focus();
  });

  // ------------------------------------------------------- variables modal

  const reference = { modal: document.getElementById('af-variables-modal') };
  reference.search = $('[data-af-reference-search]', reference.modal);
  reference.groups = $('[data-af-reference-groups]', reference.modal);

  function renderReference() {
    renderVariableGroups(reference.groups, variableGroups(state.actions.length), {
      query: reference.search.value,
      onPick: async (variable) => {
        const token = `{{${variable.path}}}`;
        try {
          await navigator.clipboard.writeText(token);
          announce(`Copied ${token}`);
        } catch (error) {
          announce(`Copy ${token} manually; the clipboard isn't available.`);
        }
      },
    });
  }

  reference.search.addEventListener('input', renderReference);
  $('[data-af-variables-open]', root).addEventListener('click', () => {
    reference.search.value = '';
    renderReference();
    openModal(reference.modal, { focus: reference.search });
  });

  // ------------------------------------------------- submit + unsaved guard

  let initialFormState = '';
  let submitting = false;
  const formState = () => (form ? new URLSearchParams(new FormData(form)).toString() : '');

  if (form) {
    form.addEventListener('submit', (event) => {
      const problems = state.actions.map((action, index) => (action.note ? null : index + 1)).filter(Boolean);
      const issueSteps = state.actions.map((_, index) => (stepIssues(index).length ? index + 1 : null)).filter(Boolean);
      if (problems.length || issueSteps.length) {
        event.preventDefault();
        els.error.textContent = problems.length
          ? `Give step ${problems.join(', ')} a name before saving.`
          : `Fix the problems on step ${issueSteps.join(', ')} before saving.`;
        els.error.hidden = false;
        els.error.scrollIntoView({ block: 'center' });
        return;
      }
      syncHiddenInputs();
      submitting = true;
    });
    window.addEventListener('beforeunload', (event) => {
      if (submitting || formState() === initialFormState) return;
      event.preventDefault();
      event.returnValue = '';
    });
  }

  syncHiddenInputs();
  renderFlow();
  // Capture after other scripts have filled their hidden inputs.
  window.addEventListener('load', () => {
    initialFormState = formState();
  });
}());
