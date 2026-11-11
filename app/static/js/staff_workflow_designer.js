/*
 * Staff onboarding / offboarding workflow designer.
 *
 * Page: /staff/workflows/onboarding and /staff/workflows/offboarding
 * Markup: app/templates/staff/_workflow_designer.html
 * Styles: app/static/css/staff_workflows.css (+ shared .scf-* editor rules)
 *
 * The designer keeps every workflow in memory, draws its steps as a flow,
 * and edits one step at a time in a focused modal.  A variable helper shows
 * which ${vars.*} values exist at each point of the workflow: the request's
 * own values plus whatever earlier steps provide.
 */
(() => {
  'use strict';

  const root = document.querySelector('[data-swf]');
  const configNode = document.getElementById('swf-config');
  if (!root || !configNode) {
    return;
  }

  let pageConfig = {};
  try {
    pageConfig = JSON.parse(configNode.textContent || '{}') || {};
  } catch (error) {
    pageConfig = {};
  }

  const DIRECTION = String(pageConfig.direction || 'onboarding');
  const STEP_LIST_KEY = String(pageConfig.step_list_key || 'steps');
  const DEFAULT_KEY = String(pageConfig.default_key || `staff_${DIRECTION}`);
  const DEFAULT_STEP_TYPE = String(pageConfig.default_step_type || '');
  const API_BASE = `/staff/workflows/${DIRECTION}/policies`;
  const STORAGE_KEY = `myportal.staffWorkflows.${DIRECTION}.selected`;
  const VAR_PATTERN = /\$\{vars\.([A-Za-z0-9_.-]+)(\s*\|[^{}]*)?\}/g;
  const KEY_PATTERN = /[^a-z0-9_.-]+/g;
  // Engine aliases and namespaces that are always resolvable at run time.
  const ALWAYS_KNOWN = new Set([
    'staff_id', 'staff_email', 'staff_first_name', 'staff_last_name', 'staff_full_name',
    'staff_custom_fields', 'company_id', 'company_name', 'requestor_email',
  ]);
  const ALWAYS_KNOWN_PREFIXES = ['system.', 'custom_fields.', 'staff_custom_fields.'];

  const CATEGORY_ICONS = {
    m365: 'M',
    tickets: 'T',
    email: '@',
    passwords: '*',
    integrations: 'I',
    web: 'W',
    flow: 'P',
    records: 'R',
  };

  const FAILURE_LABELS = {
    fail_fast: 'stops the workflow',
    pause: 'pauses for a technician',
    continue: 'carries on',
    retry_then_fail: 'stops the workflow',
  };

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
      } else if (key in node && typeof value !== 'string') {
        node[key] = value;
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

  function appendAll(parent, ...nodes) {
    nodes.forEach((node) => {
      if (node !== null && node !== undefined && node !== false) {
        parent.append(node);
      }
    });
    return parent;
  }

  const els = {
    loading: $('[data-swf-loading]', root),
    empty: $('[data-swf-empty]', root),
    workspace: $('[data-swf-workspace]', root),
    list: $('[data-swf-list]', root),
    designerTitle: $('[data-swf-designer-title]', root),
    designerChips: $('[data-swf-designer-chips]', root),
    flow: $('[data-swf-flow]', root),
    savebar: $('[data-swf-savebar]', root),
    savebarCount: $('[data-swf-savebar-count]', root),
    savebarDetail: $('[data-swf-savebar-detail]', root),
    saveButton: $('[data-swf-save]', root),
    discardButton: $('[data-swf-discard]', root),
    announce: $('[data-swf-announce]', root),
  };

  function announce(message) {
    if (!els.announce) {
      return;
    }
    els.announce.textContent = '';
    window.setTimeout(() => {
      els.announce.textContent = message;
    }, 30);
  }

  // ---------------------------------------------------------------- utils

  const clone = (value) => JSON.parse(JSON.stringify(value === undefined ? null : value));
  let uidCounter = 0;
  const nextUid = (prefix) => {
    uidCounter += 1;
    return `${prefix}${uidCounter}`;
  };

  function getNested(source, path) {
    return String(path || '').split('.').filter(Boolean).reduce((acc, part) => (
      acc && typeof acc === 'object' ? acc[part] : undefined
    ), source);
  }

  function setNested(source, path, value) {
    const parts = String(path || '').split('.').filter(Boolean);
    if (!parts.length) {
      return;
    }
    let target = source;
    parts.slice(0, -1).forEach((part) => {
      if (!target[part] || typeof target[part] !== 'object' || Array.isArray(target[part])) {
        target[part] = {};
      }
      target = target[part];
    });
    target[parts[parts.length - 1]] = value;
  }

  function slugifyKey(value) {
    return String(value || '').trim().toLowerCase().replace(KEY_PATTERN, '_').replace(/^[._-]+|[._-]+$/g, '');
  }

  function splitList(value) {
    if (Array.isArray(value)) {
      return value.map((item) => String(item).trim()).filter(Boolean);
    }
    return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
  }

  function parseMapping(value) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return { ok: true, rows: Object.entries(value).map(([key, val]) => [key, typeof val === 'string' ? val : JSON.stringify(val)]) };
    }
    const text = String(value || '').trim();
    if (!text) {
      return { ok: true, rows: [] };
    }
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return { ok: true, rows: Object.entries(parsed).map(([key, val]) => [key, typeof val === 'string' ? val : JSON.stringify(val)]) };
      }
    } catch (error) {
      // fall through
    }
    return { ok: false, rows: [] };
  }

  // Mirrors app/services/workflow_variable_functions.py so the preview shows
  // the value the workflow will actually send.
  function splitOutsideQuotes(text, separator) {
    const parts = [];
    let current = '';
    let quote = null;
    for (const ch of text) {
      if (quote) {
        if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === separator) {
        parts.push(current);
        current = '';
        continue;
      }
      current += ch;
    }
    if (quote) throw new Error('unterminated quote');
    parts.push(current);
    return parts;
  }

  function parseFunctionArgs(raw) {
    if (!raw.trim()) return [];
    return splitOutsideQuotes(raw, ',').map((piece) => {
      const text = piece.trim();
      if (text.length >= 2 && text[0] === text[text.length - 1] && (text[0] === '"' || text[0] === "'")) {
        return text.slice(1, -1);
      }
      return text;
    });
  }

  function intArg(args, index, fallback) {
    if (index >= args.length) {
      if (fallback === undefined) throw new Error('needs a number');
      return fallback;
    }
    if (!/^\s*-?\d+\s*$/.test(args[index])) throw new Error('expects a whole number');
    return parseInt(args[index], 10);
  }

  function pickWord(value, index) {
    const words = value.split(/\s+/).filter(Boolean);
    if (index === 0 || Math.abs(index) > words.length) return '';
    return index > 0 ? words[index - 1] : words[words.length + index];
  }

  const VAR_FUNCTIONS = {
    left: (v, a) => v.slice(0, Math.max(intArg(a, 0), 0)),
    right: (v, a) => { const n = Math.max(intArg(a, 0), 0); return n ? v.slice(-n) : ''; },
    mid: (v, a) => {
      const start = Math.max(intArg(a, 0), 1) - 1;
      return a.length > 1 ? v.slice(start, start + Math.max(intArg(a, 1), 0)) : v.slice(start);
    },
    trim: (v) => v.trim(),
    ltrim: (v) => v.replace(/^\s+/, ''),
    rtrim: (v) => v.replace(/\s+$/, ''),
    len: (v) => String(v.length),
    upper: (v) => v.toUpperCase(),
    lower: (v) => v.toLowerCase(),
    proper: (v) => v.toLowerCase().replace(/(^|[^A-Za-z])([a-z])/g, (m, pre, ch) => pre + ch.toUpperCase()),
    replace: (v, a) => { if (!a.length) throw new Error('needs the text to find'); return v.split(a[0]).join(a[1] || ''); },
    word: (v, a) => pickWord(v, intArg(a, 0, 1)),
    first_word: (v) => pickWord(v, 1),
    last_word: (v) => pickWord(v, -1),
    initials: (v) => v.split(/\s+/).filter(Boolean).map((w) => w[0]).join(''),
    nospace: (v) => v.replace(/\s+/g, ''),
    alnum: (v) => v.replace(/[^\p{L}\p{N}]/gu, ''),
    pad_left: (v, a) => v.padStart(Math.min(Math.max(intArg(a, 0), 0), 200), (a[1] || '0')[0]),
    default: (v, a) => (v.trim() ? v : (a[0] || '')),
  };

  // Returns the shaped value, or throws with a plain-English reason.
  function applyVarFunctions(value, pipes) {
    let result = value === undefined || value === null ? '' : String(value);
    if (!pipes) return result;
    splitOutsideQuotes(pipes, '|').slice(1).forEach((segment) => {
      const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([\s\S]*)\))?\s*$/.exec(segment);
      if (!match) throw new Error(`cannot read "${segment.trim()}"`);
      const name = match[1].toLowerCase();
      const fn = VAR_FUNCTIONS[name];
      if (!fn) throw new Error(`unknown function "${name}"`);
      result = String(fn(result, parseFunctionArgs(match[2] || '')));
    });
    return result;
  }

  function collectRefs(value, into = new Set()) {
    if (typeof value === 'string') {
      value.replace(VAR_PATTERN, (match, name) => {
        into.add(name);
        return match;
      });
    } else if (Array.isArray(value)) {
      value.forEach((item) => collectRefs(item, into));
    } else if (value && typeof value === 'object') {
      Object.values(value).forEach((item) => collectRefs(item, into));
    }
    return into;
  }

  function plural(count, word, pluralWord) {
    return `${count} ${count === 1 ? word : (pluralWord || `${word}s`)}`;
  }

  function joinWords(items, conjunction = 'and') {
    if (items.length <= 1) {
      return items.join('');
    }
    return `${items.slice(0, -1).join(', ')} ${conjunction} ${items[items.length - 1]}`;
  }

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      // Preferences are optional.
    }
  }

  // ---------------------------------------------------------------- state

  const state = {
    catalog: [],
    catalogByType: new Map(),
    schema: {},
    categories: [],
    baseGroups: [],
    workflows: [],
    selectedUid: null,
    companyId: null,
    companies: [],
  };

  function catalogEntry(type) {
    return state.catalogByType.get(type) || { type, name: type, description: '', category: 'records', outputs: [] };
  }

  function categoryLabel(key) {
    const match = state.categories.find((item) => item.key === key);
    return match ? match.label : 'Other';
  }

  function schemaFor(type) {
    const base = state.schema['*'] && typeof state.schema['*'] === 'object' ? state.schema['*'] : {};
    const own = state.schema[type] && typeof state.schema[type] === 'object' ? state.schema[type] : {};
    return {
      retry_policy: own.retry_policy || base.retry_policy || { max_retries: 0 },
      failure_policy: own.failure_policy || base.failure_policy || { mode: 'fail_fast', create_ticket_on_failure: true },
      fields: [
        ...(Array.isArray(base.fields) ? base.fields : []),
        ...(Array.isArray(own.fields) ? own.fields : []),
      ],
    };
  }

  function fieldPath(field) {
    return String(field.target || field.name || '');
  }

  function fieldSection(field) {
    const path = fieldPath(field);
    if (path.startsWith('conditions.')) {
      return 'conditions';
    }
    if (field.widget === 'variable_name') {
      return 'outputs';
    }
    return 'settings';
  }

  function readStep(raw, index) {
    const type = String(raw.type || raw.key || (raw.config && raw.config.type) || DEFAULT_STEP_TYPE || 'step');
    const config = raw.config && typeof raw.config === 'object' ? clone(raw.config) : {};
    const retrySource = (raw.retry_policy && typeof raw.retry_policy === 'object') ? raw.retry_policy
      : (config.retry_policy && typeof config.retry_policy === 'object' ? config.retry_policy : {});
    const failureSource = (raw.failure_policy && typeof raw.failure_policy === 'object') ? raw.failure_policy
      : (config.failure_policy && typeof config.failure_policy === 'object' ? config.failure_policy : {});
    delete config.retry_policy;
    delete config.failure_policy;
    config.type = type;
    return {
      uid: nextUid('s'),
      name: String(raw.name || catalogEntry(type).name || `Step ${index + 1}`),
      type,
      enabled: raw.enabled !== false,
      config,
      retry_policy: { max_retries: Number(retrySource.max_retries || 0) },
      failure_policy: {
        ...failureSource,
        mode: String(failureSource.mode || 'fail_fast'),
        create_ticket_on_failure: Boolean(failureSource.create_ticket_on_failure ?? failureSource.createTicketOnFailure ?? true),
      },
      webhook_preview: raw.webhook_preview && typeof raw.webhook_preview === 'object' ? { ...raw.webhook_preview } : null,
    };
  }

  function readWorkflow(policy) {
    const config = policy.config_json && typeof policy.config_json === 'object' ? clone(policy.config_json) : {};
    const rawSteps = Array.isArray(config[STEP_LIST_KEY]) ? config[STEP_LIST_KEY] : [];
    const wf = {
      uid: nextUid('w'),
      id: policy.id || null,
      workflow_key: String(policy.workflow_key || DEFAULT_KEY),
      workflow_name: String(policy.workflow_name || ''),
      delay_type: policy.delay_type === 'immediate' ? 'immediate' : 'scheduled',
      enabled: policy.enabled !== false,
      max_retries: Number(policy.max_retries ?? 2),
      existingConfig: config,
      steps: rawSteps.map((step, index) => readStep(step, index)),
      snapshot: '',
      error: '',
    };
    wf.snapshot = policy.id ? JSON.stringify(buildRequestBody(wf)) : '';
    return wf;
  }

  function buildRequestBody(wf) {
    const steps = wf.steps.map((step) => ({
      key: step.type,
      name: step.name,
      enabled: step.enabled !== false,
      config: {
        ...(step.config || {}),
        type: step.type,
        retry_policy: { max_retries: Number(step.retry_policy.max_retries || 0) },
        failure_policy: { ...step.failure_policy },
      },
    }));
    return {
      workflow_key: wf.workflow_key || DEFAULT_KEY,
      workflow_name: wf.workflow_name || null,
      delay_type: wf.delay_type || 'scheduled',
      enabled: wf.enabled,
      max_retries: Number(wf.max_retries || 0),
      config: {
        ...wf.existingConfig,
        version: Number((wf.existingConfig || {}).version || 1),
        [STEP_LIST_KEY]: steps,
      },
    };
  }

  const isDirty = (wf) => JSON.stringify(buildRequestBody(wf)) !== wf.snapshot;
  const selectedWorkflow = () => state.workflows.find((wf) => wf.uid === state.selectedUid) || null;
  const workflowTitle = (wf) => wf.workflow_name || wf.workflow_key || `${DIRECTION} workflow`;

  function uniqueStepName(wf, desired, ignoreUid) {
    const base = String(desired || 'Step').trim() || 'Step';
    const taken = new Set(wf.steps.filter((step) => step.uid !== ignoreUid).map((step) => step.name.toLowerCase()));
    if (!taken.has(base.toLowerCase())) {
      return base;
    }
    let counter = 2;
    while (taken.has(`${base} ${counter}`.toLowerCase())) {
      counter += 1;
    }
    return `${base} ${counter}`;
  }

  // ------------------------------------------------------------ variables

  function stepOutputs(step) {
    const entry = catalogEntry(step.type);
    const outputs = new Map();
    const add = (name, label, secret) => {
      const clean = String(name || '').trim();
      if (clean && !outputs.has(clean)) {
        outputs.set(clean, { name: clean, label: label || clean, secret: Boolean(secret) });
      }
    };
    (entry.outputs || []).forEach((output) => add(output.name, output.label, output.secret));
    const schemaFields = schemaFor(step.type).fields;
    (entry.output_fields || []).forEach((spec) => {
      const field = schemaFields.find((item) => fieldPath(item) === spec.field) || {};
      const configured = getNested(step.config, spec.field);
      const name = String(configured === undefined || configured === '' ? (field.default || '') : configured).trim();
      if (!name) {
        return;
      }
      if (Array.isArray(spec.suffixes) && spec.suffixes.length) {
        spec.suffixes.forEach((suffix) => add(`${name}.${suffix}`, `${spec.label} (${suffix.replace(/_/g, ' ')})`, spec.secret || suffix === 'url'));
      } else {
        add(name, spec.label, spec.secret);
      }
    });
    (entry.mapping_fields || []).forEach((path) => {
      parseMapping(getNested(step.config, path)).rows.forEach(([key]) => add(key, 'Saved from the response'));
    });
    (entry.list_fields || []).forEach((path) => {
      splitList(getNested(step.config, path)).forEach((name) => add(name, 'Sent by the callback'));
    });
    return Array.from(outputs.values());
  }

  function stepIsConditional(step) {
    const conditions = step.config && step.config.conditions;
    if (!conditions || typeof conditions !== 'object') {
      return false;
    }
    return Object.values(conditions).some((value) => splitList(value).length > 0);
  }

  function variableGroupsAt(wf, index, { includeLater = false } = {}) {
    const groups = state.baseGroups.map((group) => ({ ...group, variables: group.variables.map((item) => ({ ...item })) }));
    wf.steps.forEach((step, stepIndex) => {
      if (stepIndex >= index && !includeLater) {
        return;
      }
      if (stepIndex === index) {
        return;
      }
      const outputs = stepOutputs(step);
      if (!outputs.length) {
        return;
      }
      const notes = [];
      if (!step.enabled) {
        notes.push('step is turned off');
      } else {
        if (stepIsConditional(step)) {
          notes.push('empty if the step is skipped');
        }
        if (step.failure_policy.mode === 'continue') {
          notes.push('empty if the step fails');
        }
      }
      groups.push({
        key: `step-${step.uid}`,
        label: `Step ${stepIndex + 1} · ${step.name}`,
        note: notes.join('; '),
        later: stepIndex > index,
        disabled: !step.enabled,
        variables: outputs.map((output) => ({ ...output, fromStep: stepIndex + 1 })),
      });
    });
    return groups;
  }

  function isAlwaysKnown(name) {
    return ALWAYS_KNOWN.has(name) || ALWAYS_KNOWN_PREFIXES.some((prefix) => name.startsWith(prefix));
  }

  function variableLookup(groups) {
    const lookup = new Map();
    groups.forEach((group) => {
      if (group.disabled) {
        return;
      }
      group.variables.forEach((variable) => {
        if (!lookup.has(variable.name)) {
          lookup.set(variable.name, variable);
        }
      });
    });
    return lookup;
  }

  function refStatus(name, known) {
    if (known.has(name) || isAlwaysKnown(name)) {
      return 'ok';
    }
    // ${vars.output_object.child} where output_object is known.
    const parts = name.split('.');
    for (let length = parts.length - 1; length > 0; length -= 1) {
      if (known.has(parts.slice(0, length).join('.'))) {
        return 'ok';
      }
    }
    return 'unknown';
  }

  function laterProvider(wf, index, name) {
    for (let stepIndex = index + 1; stepIndex < wf.steps.length; stepIndex += 1) {
      if (stepOutputs(wf.steps[stepIndex]).some((output) => output.name === name)) {
        return stepIndex + 1;
      }
    }
    return 0;
  }

  function disabledProvider(wf, index, name) {
    for (let stepIndex = 0; stepIndex < index; stepIndex += 1) {
      const step = wf.steps[stepIndex];
      if (!step.enabled && stepOutputs(step).some((output) => output.name === name)) {
        return stepIndex + 1;
      }
    }
    return 0;
  }

  function describeRefIssue(wf, index, name) {
    const off = disabledProvider(wf, index, name);
    if (off) {
      return `\${vars.${name}} comes from step ${off}, which is turned off, so it will be blank.`;
    }
    const later = laterProvider(wf, index, name);
    if (later) {
      return `\${vars.${name}} is only provided by step ${later}, which runs later. Move this step below it.`;
    }
    return `\${vars.${name}} isn't provided before this step, so it will be blank.`;
  }

  function stepIssues(wf, index, step = wf.steps[index]) {
    const known = variableLookup(variableGroupsAt(wf, index));
    const refs = Array.from(collectRefs(step.config));
    return refs.filter((name) => refStatus(name, known) !== 'ok').map((name) => describeRefIssue(wf, index, name));
  }

  // ------------------------------------------------------------- summaries

  function conditionSummary(step) {
    const conditions = (step.config && step.config.conditions) || {};
    const parts = [];
    const departments = splitList(conditions.department_in);
    if (departments.length) {
      parts.push(`staff are in ${joinWords(departments, 'or')}`);
    }
    const truthy = splitList(conditions.custom_fields_truthy).map((name) => customFieldLabel(name));
    if (truthy.length) {
      parts.push(`${joinWords(truthy)} ${truthy.length === 1 ? 'is' : 'are'} ticked`);
    }
    return parts.length ? `Runs only when ${parts.join(' and ')}.` : '';
  }

  function customFieldLabel(name) {
    const group = state.baseGroups.find((item) => item.key === 'custom_fields');
    const match = group && group.variables.find((variable) => variable.field_name === name);
    return match ? match.label : name;
  }

  function failureSummary(step) {
    const retries = Number(step.retry_policy.max_retries || 0);
    const mode = step.failure_policy.mode;
    const retryText = retries > 0 ? `MyPortal retries it ${plural(retries, 'time')}, then ` : 'MyPortal ';
    let text = `If it fails, ${retryText}${FAILURE_LABELS[mode] || FAILURE_LABELS.fail_fast}`;
    if (step.failure_policy.create_ticket_on_failure && mode !== 'continue') {
      text += ' and raises a ticket';
    }
    return `${text}.`;
  }

  function timingSentence(wf) {
    return wf.delay_type === 'immediate' ? pageConfig.trigger_immediate : pageConfig.trigger_scheduled;
  }

  // ------------------------------------------------------- workflow list

  function chip(text, modifier, title) {
    return el('li', { className: `scf-chip${modifier ? ` ${modifier}` : ''}`, title: title || null, text });
  }

  function workflowChips(wf) {
    const chips = [
      chip(wf.delay_type === 'immediate' ? 'Immediate' : 'Scheduled', 'swf-chip--timing', timingSentence(wf)),
      chip(plural(wf.steps.length, 'step')),
    ];
    if (!wf.enabled) {
      chips.unshift(chip('Off', 'scf-chip--muted'));
    }
    if (!wf.id) {
      chips.push(chip('Not saved yet', 'swf-chip--warning'));
    } else if (isDirty(wf)) {
      chips.push(chip('Unsaved changes', 'swf-chip--warning'));
    }
    return chips;
  }

  function renderList() {
    els.list.textContent = '';
    state.workflows.forEach((wf) => {
      const active = wf.uid === state.selectedUid;
      const button = el('button', {
        type: 'button',
        className: `swf-list__item${active ? ' is-active' : ''}${wf.enabled ? '' : ' is-off'}`,
        'aria-current': active ? 'true' : null,
        dataset: { swfSelect: wf.uid },
      }, [
        el('span', { className: 'swf-list__name', text: workflowTitle(wf) }),
        el('code', { className: 'scf-item__key', text: wf.workflow_key }),
        el('ul', { className: 'scf-item__chips', 'aria-label': 'Workflow summary' }, workflowChips(wf)),
      ]);
      els.list.append(el('li', {}, button));
    });
  }

  // ---------------------------------------------------------------- flow

  function iconFor(category) {
    return el('span', { className: `swf-cat-icon swf-cat-icon--${category}`, 'aria-hidden': 'true', text: CATEGORY_ICONS[category] || '•' });
  }

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

  function varCodes(names, modifier) {
    return names.map((name) => el('code', { className: `swf-token${modifier ? ` ${modifier}` : ''}`, text: name }));
  }

  function insertRow(index, isEnd) {
    return el('li', { className: `swf-insert${isEnd ? ' swf-insert--end' : ''}` }, el('button', {
      type: 'button',
      className: 'swf-insert__button',
      'aria-haspopup': 'dialog',
      'aria-controls': 'swf-picker-modal',
      dataset: { swfInsert: index },
    }, [el('span', { 'aria-hidden': 'true', text: '+' }), isEnd ? ' Add a step' : el('span', { className: 'visually-hidden', text: `Add a step before step ${index + 1}` })]));
  }

  function renderFlow() {
    const wf = selectedWorkflow();
    els.flow.textContent = '';
    if (!wf) {
      return;
    }
    els.designerTitle.textContent = workflowTitle(wf);
    els.designerChips.textContent = '';
    workflowChips(wf).forEach((item) => els.designerChips.append(item));

    const starting = state.baseGroups.map((group) => group.label.toLowerCase());
    els.flow.append(el('li', { className: 'swf-node swf-node--trigger' }, [
      el('span', { className: 'swf-cat-icon swf-cat-icon--trigger', 'aria-hidden': 'true', text: '▶' }),
      el('div', { className: 'swf-node__main' }, [
        el('p', { className: 'swf-node__title', text: pageConfig.trigger_title || 'Request approved' }),
        el('p', { className: 'swf-node__text text-muted', text: timingSentence(wf) }),
        el('p', { className: 'swf-node__text text-muted', text: `Every step can use the ${joinWords(starting)} variables.` }),
      ]),
      el('button', {
        type: 'button',
        className: 'button button--ghost button--small',
        dataset: { swfSettingsOpen: '' },
        'aria-haspopup': 'dialog',
        'aria-controls': 'swf-settings-modal',
        text: 'Change',
      }),
    ]));

    if (!wf.steps.length) {
      els.flow.append(el('li', { className: 'swf-node swf-node--empty' }, [
        el('div', { className: 'swf-node__main' }, [
          el('p', { className: 'swf-node__title', text: 'No steps yet' }),
          el('p', { className: 'text-muted', text: 'Add the first thing MyPortal should do, such as creating the account or raising a ticket.' }),
        ]),
      ]));
    }

    wf.steps.forEach((step, index) => {
      els.flow.append(insertRow(index, false));
      const entry = catalogEntry(step.type);
      const outputs = stepOutputs(step).map((output) => output.name);
      const refs = Array.from(collectRefs(step.config));
      const issues = stepIssues(wf, index);
      const chips = [];
      if (!step.enabled) {
        chips.push(chip('Off', 'scf-chip--muted'));
      }
      const condition = conditionSummary(step);
      if (condition) {
        chips.push(chip(condition.replace(/^Runs only when /, 'Only when ').replace(/\.$/, ''), 'scf-chip--logic'));
      }
      if (step.failure_policy.mode !== 'fail_fast' || Number(step.retry_policy.max_retries || 0) > 0) {
        chips.push(chip(failureSummary(step).replace(/^If it fails, MyPortal /, 'On failure: ').replace(/\.$/, ''), 'swf-chip--failure'));
      }
      if (issues.length) {
        chips.push(chip(plural(issues.length, 'variable problem'), 'swf-chip--danger'));
      }

      const main = el('div', { className: 'swf-node__main' }, [
        el('div', { className: 'swf-step__heading' }, [
          el('button', {
            type: 'button',
            className: 'swf-step__name',
            'aria-haspopup': 'dialog',
            'aria-controls': 'swf-step-modal',
            dataset: { swfEdit: index },
            text: step.name,
          }),
          el('span', { className: 'swf-step__type', text: `${entry.name} · ${categoryLabel(entry.category)}` }),
        ]),
        chips.length ? el('ul', { className: 'scf-item__chips', 'aria-label': 'Step summary' }, chips) : null,
        refs.length ? el('p', { className: 'swf-step__vars' }, [el('span', { className: 'swf-step__vars-label', text: 'Uses' }), ...varCodes(refs, '')]) : null,
        outputs.length ? el('p', { className: 'swf-step__vars' }, [el('span', { className: 'swf-step__vars-label', text: 'Provides' }), ...varCodes(outputs, 'swf-token--output')]) : null,
        issues.length ? el('ul', { className: 'swf-step__issues' }, issues.map((issue) => el('li', { text: issue }))) : null,
      ]);

      const actions = el('div', { className: 'swf-node__actions' }, [
        iconButton(`Move ${step.name} up`, '↑', { swfMove: index, swfDirection: -1 }, index === 0),
        iconButton(`Move ${step.name} down`, '↓', { swfMove: index, swfDirection: 1 }, index === wf.steps.length - 1),
        el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { swfDuplicateStep: index }, text: 'Duplicate' }),
        el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { swfEdit: index }, 'aria-haspopup': 'dialog', 'aria-controls': 'swf-step-modal', text: 'Edit' }),
      ]);

      els.flow.append(el('li', {
        className: `swf-node swf-step${step.enabled ? '' : ' is-off'}${issues.length ? ' has-issues' : ''}`,
        draggable: 'true',
        dataset: { swfStepIndex: index },
        'aria-label': `Step ${index + 1}: ${step.name}`,
      }, [
        el('span', { className: 'swf-step__num', 'aria-hidden': 'true', text: index + 1 }),
        iconFor(entry.category),
        main,
        actions,
      ]));
    });

    els.flow.append(insertRow(wf.steps.length, true));
    els.flow.append(el('li', { className: 'swf-node swf-node--end' }, [
      el('span', { className: 'swf-cat-icon swf-cat-icon--end', 'aria-hidden': 'true', text: '✓' }),
      el('div', { className: 'swf-node__main' }, [
        el('p', { className: 'swf-node__title', text: 'Workflow complete' }),
        el('p', { className: 'swf-node__text text-muted', text: 'Progress and any failures are recorded in execution history.' }),
      ]),
    ]));
  }

  function renderSavebar() {
    const wf = selectedWorkflow();
    const dirty = state.workflows.filter((item) => !item.id || isDirty(item));
    const selectedDirty = wf && (!wf.id || isDirty(wf));
    els.savebar.hidden = !selectedDirty && !(wf && wf.error);
    if (!wf) {
      return;
    }
    if (wf.error) {
      els.savebarCount.textContent = 'Not saved';
      els.savebarDetail.textContent = wf.error;
    } else {
      els.savebarCount.textContent = wf.id ? 'Unsaved changes' : 'New workflow';
      const others = dirty.filter((item) => item.uid !== wf.uid).length;
      els.savebarDetail.textContent = `to ${workflowTitle(wf)}${others ? ` · ${plural(others, 'other workflow')} also changed` : ''}`;
    }
    els.saveButton.disabled = false;
  }

  function render() {
    const hasWorkflows = state.workflows.length > 0;
    els.empty.hidden = hasWorkflows;
    els.workspace.hidden = !hasWorkflows;
    if (!hasWorkflows) {
      els.savebar.hidden = true;
      return;
    }
    if (!selectedWorkflow()) {
      state.selectedUid = state.workflows[0].uid;
    }
    renderList();
    renderFlow();
    renderSavebar();
  }

  function selectWorkflow(uid) {
    state.selectedUid = uid;
    const wf = selectedWorkflow();
    if (wf && wf.id) {
      storageSet(STORAGE_KEY, String(wf.id));
    }
    render();
  }

  // --------------------------------------------------------------- modals

  const modalStack = [];

  function openModal(modal, { onRequestClose, focus } = {}) {
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    modal.style.display = '';
    document.body.classList.add('scf-modal-open');
    modal._swfRequestClose = onRequestClose || (() => closeModal(modal));
    modal._swfReturnFocus = document.activeElement;
    modalStack.push(modal);
    const body = $('.modal__body', modal);
    if (body) {
      body.scrollTop = 0;
    }
    window.requestAnimationFrame(() => {
      const target = focus || $('input:not([type="hidden"]), button:not(.modal__close)', modal);
      if (target && typeof target.focus === 'function') {
        target.focus();
      }
    });
  }

  function closeModal(modal) {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    const position = modalStack.indexOf(modal);
    if (position >= 0) {
      modalStack.splice(position, 1);
    }
    if (!modalStack.length) {
      document.body.classList.remove('scf-modal-open');
    }
    const returnFocus = modal._swfReturnFocus;
    if (returnFocus && document.contains(returnFocus) && typeof returnFocus.focus === 'function') {
      returnFocus.focus();
    }
  }

  $$('.swf-modal').forEach((modal) => {
    $$('[data-modal-close]', modal).forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (typeof modal._swfRequestClose === 'function') {
          modal._swfRequestClose();
        } else {
          closeModal(modal);
        }
      });
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = $$('a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex="0"]', modal)
        .filter((node) => node.offsetParent !== null);
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
  });

  function setupTabs(scope, onChange) {
    const tabs = $$('[role="tab"]', scope);
    const show = (name, focus) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.swfTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      $$('[data-swf-panel]', scope).forEach((panel) => {
        panel.hidden = panel.dataset.swfPanel !== name;
      });
      if (onChange) {
        onChange(name);
      }
    };
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => show(tab.dataset.swfTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const visible = tabs.filter((item) => !item.hidden);
        const position = visible.indexOf(tab);
        const next = visible[(position + (event.key === 'ArrowRight' ? 1 : -1) + visible.length) % visible.length];
        show(next.dataset.swfTab, true);
      });
    });
    return show;
  }

  // -------------------------------------------------------- variable list

  function renderVariableGroups(container, groups, { query = '', onPick, showLater = false } = {}) {
    container.textContent = '';
    const needle = query.trim().toLowerCase();
    let shown = 0;
    groups.forEach((group) => {
      if (group.later && !showLater) {
        return;
      }
      const variables = group.variables.filter((variable) => (
        !needle
        || variable.name.toLowerCase().includes(needle)
        || String(variable.label || '').toLowerCase().includes(needle)
        || group.label.toLowerCase().includes(needle)
      ));
      if (!variables.length) {
        return;
      }
      shown += variables.length;
      const list = el('ul', { className: 'swf-vars__list' });
      variables.forEach((variable) => {
        const item = el('button', {
          type: 'button',
          className: 'swf-var',
          dataset: { swfVar: variable.name },
          title: `Insert \${vars.${variable.name}}`,
        }, [
          el('span', { className: 'swf-var__label' }, [
            variable.label || variable.name,
            variable.secret ? el('span', { className: 'swf-var__badge', text: 'Protected' }) : null,
          ]),
          el('code', { className: 'swf-var__name', text: variable.name }),
        ]);
        item.addEventListener('click', () => onPick(variable));
        list.append(el('li', {}, item));
      });
      container.append(el('section', { className: `swf-vars__group${group.disabled ? ' is-off' : ''}` }, [
        el('h4', { className: 'swf-vars__title', text: group.label }),
        group.note ? el('p', { className: 'swf-vars__note', text: `May be blank: ${group.note}` }) : null,
        list,
      ]));
    });
    if (!shown) {
      container.append(el('p', { className: 'scf-empty scf-empty--inline', text: needle ? 'No variables match your search.' : 'No variables available.' }));
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      announce(`Copied ${text}`);
      return true;
    } catch (error) {
      announce(`Copy ${text} manually; the clipboard isn't available.`);
      return false;
    }
  }

  // ---------------------------------------------------------- step picker

  const picker = {
    modal: document.getElementById('swf-picker-modal'),
    insertAt: 0,
  };
  picker.search = $('[data-swf-picker-search]', picker.modal);
  picker.groups = $('[data-swf-picker-groups]', picker.modal);
  picker.empty = $('[data-swf-picker-empty]', picker.modal);
  picker.eyebrow = $('[data-swf-picker-eyebrow]', picker.modal);

  function renderPicker() {
    const needle = picker.search.value.trim().toLowerCase();
    picker.groups.textContent = '';
    let shown = 0;
    const order = state.categories.map((item) => item.key);
    const byCategory = new Map();
    state.catalog.forEach((item) => {
      const haystack = `${item.name} ${item.description} ${item.type} ${categoryLabel(item.category)}`.toLowerCase();
      if (needle && !haystack.includes(needle)) {
        return;
      }
      if (!byCategory.has(item.category)) {
        byCategory.set(item.category, []);
      }
      byCategory.get(item.category).push(item);
    });
    Array.from(byCategory.keys()).sort((a, b) => order.indexOf(a) - order.indexOf(b)).forEach((category) => {
      const cards = byCategory.get(category).map((item) => {
        shown += 1;
        const outputs = (item.outputs || []).map((output) => output.name);
        const card = el('button', { type: 'button', className: 'swf-pick', dataset: { swfPick: item.type } }, [
          iconFor(item.category),
          el('span', { className: 'swf-pick__text' }, [
            el('span', { className: 'swf-pick__name', text: item.name }),
            el('span', { className: 'swf-pick__help', text: item.description }),
            outputs.length ? el('span', { className: 'swf-pick__provides' }, ['Provides ', ...varCodes(outputs.slice(0, 4), 'swf-token--output'), outputs.length > 4 ? ` +${outputs.length - 4}` : null]) : null,
          ]),
        ]);
        card.addEventListener('click', () => {
          closeModal(picker.modal);
          addStep(item.type, picker.insertAt);
        });
        return el('li', {}, card);
      });
      picker.groups.append(el('section', { className: 'swf-picker__group' }, [
        el('h3', { className: 'scf-group__title', text: categoryLabel(category) }),
        el('ul', { className: 'swf-picker__list' }, cards),
      ]));
    });
    picker.empty.hidden = shown > 0;
  }

  picker.search.addEventListener('input', renderPicker);

  function openPicker(index) {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    picker.insertAt = Math.max(0, Math.min(Number(index) || 0, wf.steps.length));
    picker.eyebrow.textContent = picker.insertAt >= wf.steps.length
      ? `Add step ${wf.steps.length + 1}`
      : `Insert before step ${picker.insertAt + 1}`;
    picker.search.value = '';
    renderPicker();
    openModal(picker.modal, { focus: picker.search });
  }

  function addStep(type, index) {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    const entry = catalogEntry(type);
    const schema = schemaFor(type);
    const config = { type };
    schema.fields.forEach((field) => {
      if (field.default !== undefined) {
        setNested(config, fieldPath(field), clone(field.default));
      }
    });
    const step = {
      uid: nextUid('s'),
      name: uniqueStepName(wf, entry.name || type),
      type,
      enabled: true,
      config,
      retry_policy: { ...schema.retry_policy },
      failure_policy: { ...schema.failure_policy },
      webhook_preview: null,
    };
    openStepEditor(index, { draft: step });
  }

  // ---------------------------------------------------------- step editor

  const editor = {
    modal: document.getElementById('swf-step-modal'),
    index: 0,
    isNew: false,
    step: null,
    snapshot: '',
    target: null,
    controls: [],
  };
  editor.form = $('[data-swf-step-form]', editor.modal);
  editor.eyebrow = $('[data-swf-step-eyebrow]', editor.modal);
  editor.title = $('[data-swf-step-title]', editor.modal);
  editor.subtitle = $('[data-swf-step-subtitle]', editor.modal);
  editor.nameInput = $('[data-swf-step-name]', editor.modal);
  editor.enabledInput = $('[data-swf-step-enabled]', editor.modal);
  editor.fieldContainers = {
    settings: $('[data-swf-fields="settings"]', editor.modal),
    outputs: $('[data-swf-fields="outputs"]', editor.modal),
    conditions: $('[data-swf-fields="conditions"]', editor.modal),
  };
  editor.webhook = $('[data-swf-webhook]', editor.modal);
  editor.provides = $('[data-swf-provides]', editor.modal);
  editor.outputsCount = $('[data-swf-outputs-count]', editor.modal);
  editor.conditionsBox = $('[data-swf-conditions]', editor.modal);
  editor.conditionSentence = $('[data-swf-condition-sentence]', editor.modal);
  editor.failureSentence = $('[data-swf-failure-sentence]', editor.modal);
  editor.retriesInput = $('[data-swf-step-retries]', editor.modal);
  editor.ticketInput = $('[data-swf-step-ticket]', editor.modal);
  editor.ticketLabel = $('[data-swf-step-ticket-label]', editor.modal);
  editor.deleteButton = $('[data-swf-step-delete]', editor.modal);
  editor.helperTarget = $('[data-swf-helper-target]', editor.modal);
  editor.helperSearch = $('[data-swf-helper-search]', editor.modal);
  editor.helperGroups = $('[data-swf-helper-groups]', editor.modal);
  editor.preview = $('[data-swf-preview]', editor.modal);
  editor.previewText = $('[data-swf-preview-text]', editor.modal);
  editor.nameError = $('[data-swf-error="name"]', editor.modal);
  editor.showTab = setupTabs(editor.modal);

  function editorWorkflow() {
    return selectedWorkflow();
  }

  function editorGroups() {
    const wf = editorWorkflow();
    if (!wf) {
      return state.baseGroups;
    }
    // Evaluate against a workflow where the draft sits at its position.
    const steps = wf.steps.slice();
    if (editor.isNew) {
      steps.splice(editor.index, 0, editor.step);
    } else {
      steps[editor.index] = editor.step;
    }
    return variableGroupsAt({ ...wf, steps }, editor.index);
  }

  function editorWorkflowWithDraft() {
    const wf = editorWorkflow();
    const steps = wf.steps.slice();
    if (editor.isNew) {
      steps.splice(editor.index, 0, editor.step);
    } else {
      steps[editor.index] = editor.step;
    }
    return { ...wf, steps };
  }

  function fieldIssues(value) {
    const known = variableLookup(editorGroups());
    const draftWf = editorWorkflowWithDraft();
    return Array.from(collectRefs(value))
      .filter((name) => refStatus(name, known) !== 'ok')
      .map((name) => describeRefIssue(draftWf, editor.index, name));
  }

  function setHelperTarget(control) {
    editor.target = control;
    editor.helperTarget.textContent = control
      ? `Inserting into: ${control.label}`
      : 'Click into a field, then pick a value to insert it.';
    renderPreview();
  }

  function renderHelper() {
    renderVariableGroups(editor.helperGroups, editorGroups(), {
      query: editor.helperSearch.value,
      onPick: (variable) => {
        const token = `\${vars.${variable.name}}`;
        if (editor.target && typeof editor.target.insert === 'function') {
          editor.target.insert(token);
          announce(`Inserted ${token} into ${editor.target.label}`);
        } else {
          copyText(token);
        }
      },
    });
  }

  editor.helperSearch.addEventListener('input', renderHelper);

  function renderPreview() {
    const control = editor.target;
    const text = control && typeof control.previewText === 'function' ? control.previewText() : '';
    if (!control || !text || !collectRefs(text).size) {
      editor.preview.hidden = true;
      return;
    }
    const lookup = variableLookup(editorGroups());
    editor.previewText.textContent = '';
    let cursor = 0;
    text.replace(VAR_PATTERN, (match, name, pipes, offset) => {
      if (offset > cursor) {
        editor.previewText.append(document.createTextNode(text.slice(cursor, offset)));
      }
      const variable = lookup.get(name);
      const known = Boolean(variable) || isAlwaysKnown(name) || refStatus(name, lookup) === 'ok';
      if (!known) {
        editor.previewText.append(el('mark', { className: 'swf-preview__value swf-preview__value--missing', title: 'Not available at this step', text: match }));
      } else if (variable && variable.secret) {
        editor.previewText.append(el('mark', { className: 'swf-preview__value', title: match, text: '••••••••' }));
      } else if (variable && variable.sample !== undefined) {
        try {
          const shaped = applyVarFunctions(variable.sample, pipes);
          editor.previewText.append(el('mark', { className: 'swf-preview__value', title: match, text: shaped }));
        } catch (error) {
          editor.previewText.append(el('mark', { className: 'swf-preview__value swf-preview__value--missing', title: `Can't apply: ${error.message}`, text: match }));
        }
      } else {
        const label = variable ? variable.label : name;
        const suffix = pipes ? ` ${pipes.trim()}` : '';
        editor.previewText.append(el('mark', { className: 'swf-preview__value', title: match, text: `‹${label}${suffix}›` }));
      }
      cursor = offset + match.length;
      return match;
    });
    if (cursor < text.length) {
      editor.previewText.append(document.createTextNode(text.slice(cursor)));
    }
    editor.preview.hidden = false;
  }

  function insertAtCursor(input, token) {
    const start = typeof input.selectionStart === 'number' ? input.selectionStart : input.value.length;
    const end = typeof input.selectionEnd === 'number' ? input.selectionEnd : input.value.length;
    input.value = `${input.value.slice(0, start)}${token}${input.value.slice(end)}`;
    const caret = start + token.length;
    input.focus();
    if (typeof input.setSelectionRange === 'function') {
      input.setSelectionRange(caret, caret);
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  let fieldIdCounter = 0;

  function fieldShell(field, { full, templatable }) {
    fieldIdCounter += 1;
    const id = `swf-field-${fieldIdCounter}`;
    const optionalTypes = ['text', 'textarea', 'number', 'date'];
    const labelRow = el('div', { className: 'swf-field__label-row' }, [
      el('label', { className: 'form-label', for: id }, [
        field.label || field.name,
        !field.required && optionalTypes.includes(field.type) && field.widget !== 'variable_name' ? ' ' : null,
        !field.required && optionalTypes.includes(field.type) && field.widget !== 'variable_name' ? el('span', { className: 'scf-optional', text: 'optional' }) : null,
      ]),
    ]);
    const wrapper = el('div', { className: `form-field swf-field${full ? ' scf-grid__full' : ''}` }, labelRow);
    let insertButton = null;
    if (templatable) {
      insertButton = el('button', {
        type: 'button',
        className: 'swf-field__insert',
        title: 'Insert a variable',
        'aria-label': `Insert a variable into ${field.label || field.name}`,
        text: '{x}',
      });
      labelRow.append(insertButton);
    }
    const help = [field.description, field.example ? `Example: ${field.example}` : ''].filter(Boolean).join(' ');
    const helpNode = help ? el('p', { className: 'form-help', text: help }) : null;
    const issuesNode = el('ul', { className: 'swf-field__issues', 'data-swf-field-issues': true, hidden: true });
    const errorNode = el('p', { className: 'scf-error', hidden: true });
    return { id, wrapper, insertButton, helpNode, issuesNode, errorNode };
  }

  function showFieldIssues(node, value) {
    const issues = fieldIssues(value);
    node.textContent = '';
    issues.forEach((issue) => node.append(el('li', { text: issue })));
    node.hidden = issues.length === 0;
  }

  function buildTextControl(field, container, value) {
    const isTextarea = field.type === 'textarea' || field.widget === 'json';
    const isVarName = field.widget === 'variable_name';
    const shell = fieldShell(field, { full: true, templatable: !isVarName });
    const input = el(isTextarea ? 'textarea' : 'input', {
      className: `form-input${field.widget === 'json' || isVarName ? ' scf-mono' : ''}`,
      id: shell.id,
      rows: isTextarea ? (field.widget === 'json' ? 6 : 4) : null,
      type: isTextarea ? null : 'text',
      autocomplete: 'off',
      spellcheck: field.widget === 'json' || isVarName ? 'false' : null,
      placeholder: field.example && !isVarName ? String(field.example) : null,
    });
    input.value = value === undefined || value === null ? '' : (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value));
    const varNameHelp = isVarName ? el('p', { className: 'form-help swf-field__varname' }) : null;
    appendAll(shell.wrapper, input, shell.helpNode, varNameHelp, shell.issuesNode, shell.errorNode);
    container.append(shell.wrapper);

    const control = {
      field,
      label: field.label || field.name,
      read: () => input.value,
      insert: (token) => insertAtCursor(input, token),
      previewText: () => input.value,
      error: shell.errorNode,
      focus: () => input.focus(),
    };
    const refresh = () => {
      if (isVarName) {
        const name = slugifyKey(input.value).replace(/[^a-z0-9_.]/g, '_');
        varNameHelp.textContent = name ? `Later steps use \${vars.${name}}.` : 'Give the value a name so later steps can use it.';
      } else {
        showFieldIssues(shell.issuesNode, input.value);
      }
      if (field.widget === 'json') {
        const text = input.value.trim();
        let ok = true;
        if (text) {
          try {
            JSON.parse(text);
          } catch (error) {
            ok = false;
          }
        }
        shell.errorNode.hidden = ok;
        shell.errorNode.textContent = ok ? '' : 'This isn\'t valid JSON yet. Put variables inside quotes, e.g. "name": "${vars.staff.full_name}".';
      }
    };
    input.addEventListener('focus', () => setHelperTarget(isVarName ? null : control));
    input.addEventListener('input', () => {
      refresh();
      onEditorChange();
    });
    if (shell.insertButton) {
      shell.insertButton.addEventListener('click', () => {
        setHelperTarget(control);
        input.focus();
        editor.helperSearch.focus();
      });
    }
    refresh();
    return control;
  }

  function buildTagControl(field, container, value) {
    const isCondition = fieldPath(field).startsWith('conditions.');
    const shell = fieldShell(field, { full: true, templatable: !isCondition });
    const tags = splitList(value);
    const box = el('div', { className: 'scf-tags swf-tags' });
    const input = el('input', {
      className: 'scf-tags__input',
      id: shell.id,
      autocomplete: 'off',
      placeholder: field.example ? `e.g. ${field.example}` : 'Type and press Enter',
    });
    let datalist = null;
    if (fieldPath(field) === 'conditions.custom_fields_truthy') {
      const group = state.baseGroups.find((item) => item.key === 'custom_fields');
      const options = group ? group.variables.filter((variable) => variable.field_type === 'checkbox') : [];
      if (options.length) {
        datalist = el('datalist', { id: `${shell.id}-options` }, options.map((variable) => el('option', { value: variable.field_name, label: variable.label })));
        input.setAttribute('list', datalist.id);
      }
    }
    const draw = () => {
      $$('.scf-tag', box).forEach((node) => node.remove());
      tags.forEach((tag, index) => {
        const label = fieldPath(field) === 'conditions.custom_fields_truthy' ? customFieldLabel(tag) : tag;
        const remove = el('button', { type: 'button', className: 'scf-tag__remove', 'aria-label': `Remove ${tag}`, text: '×' });
        remove.addEventListener('click', () => {
          tags.splice(index, 1);
          draw();
          onEditorChange();
          input.focus();
        });
        box.insertBefore(el('span', { className: 'scf-tag', title: tag }, [el('span', { className: 'scf-tag__text', text: label }), remove]), input);
      });
      showFieldIssues(shell.issuesNode, tags.join(','));
    };
    const commit = (raw) => {
      splitList(raw).forEach((tag) => {
        if (!tags.includes(tag)) {
          tags.push(tag);
        }
      });
      input.value = '';
      draw();
      onEditorChange();
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        commit(input.value);
      } else if (event.key === 'Backspace' && !input.value && tags.length) {
        tags.pop();
        draw();
        onEditorChange();
      }
    });
    input.addEventListener('blur', () => {
      if (input.value.trim()) {
        commit(input.value);
      }
    });
    input.addEventListener('paste', (event) => {
      const text = (event.clipboardData || window.clipboardData).getData('text');
      if (text && text.includes(',')) {
        event.preventDefault();
        commit(text);
      }
    });
    box.addEventListener('click', (event) => {
      if (event.target === box) {
        input.focus();
      }
    });
    box.append(input);
    appendAll(shell.wrapper, box, datalist, shell.helpNode, el('p', { className: 'form-help', text: 'Press Enter or comma to add each entry.' }), shell.issuesNode, shell.errorNode);
    container.append(shell.wrapper);
    const control = {
      field,
      label: field.label || field.name,
      read: () => {
        if (input.value.trim()) {
          splitList(input.value).forEach((tag) => {
            if (!tags.includes(tag)) {
              tags.push(tag);
            }
          });
          input.value = '';
        }
        return tags.join(', ');
      },
      insert: (token) => {
        commit(token);
        input.focus();
      },
      previewText: () => tags.join(', '),
      error: shell.errorNode,
      focus: () => input.focus(),
      count: () => tags.length + (input.value.trim() ? 1 : 0),
    };
    input.addEventListener('focus', () => setHelperTarget(isCondition ? null : control));
    if (shell.insertButton) {
      shell.insertButton.addEventListener('click', () => {
        setHelperTarget(control);
        editor.helperSearch.focus();
      });
    }
    draw();
    return control;
  }

  const MAPPING_COPY = {
    headers_json: ['Header', 'Value', '+ Add header'],
    query_params_json: ['Parameter', 'Value', '+ Add parameter'],
    store_json: ['Save as variable', 'From response field', '+ Save a value'],
  };

  function buildMappingControl(field, container, value) {
    const parsed = parseMapping(value);
    if (!parsed.ok) {
      // Keep legacy, hand-written JSON editable as text rather than losing it.
      return buildTextControl({ ...field, widget: 'json' }, container, value);
    }
    const [keyLabel, valueLabel, addLabel] = MAPPING_COPY[fieldPath(field)] || ['Name', 'Value', '+ Add row'];
    const isStore = fieldPath(field) === 'store_json';
    const shell = fieldShell(field, { full: true, templatable: !isStore });
    const rows = parsed.rows.map(([key, val]) => ({ key, value: val }));
    const list = el('ol', { className: 'swf-map' });
    let lastValueInput = null;
    const control = {
      field,
      label: field.label || field.name,
      read: () => {
        const result = {};
        rows.forEach((row) => {
          const key = String(row.key || '').trim();
          if (key) {
            result[isStore ? key.replace(/\s+/g, '_') : key] = row.value;
          }
        });
        return Object.keys(result).length ? JSON.stringify(result) : '';
      },
      insert: (token) => {
        if (!lastValueInput) {
          rows.push({ key: '', value: '' });
          draw();
          lastValueInput = $$('.swf-map__value', list).pop();
        }
        insertAtCursor(lastValueInput, token);
      },
      previewText: () => rows.map((row) => row.value).join(' · '),
      error: shell.errorNode,
      focus: () => {
        const first = $('input', list);
        if (first) {
          first.focus();
        }
      },
    };
    const draw = () => {
      list.textContent = '';
      if (!rows.length) {
        list.append(el('li', { className: 'swf-map__empty text-muted', text: isStore ? 'Nothing saved yet. Response values are always available as status_code and body.' : 'None added.' }));
      }
      rows.forEach((row, index) => {
        const keyInput = el('input', { className: 'form-input scf-mono', 'aria-label': `${keyLabel} ${index + 1}`, placeholder: keyLabel, value: row.key, autocomplete: 'off', spellcheck: 'false' });
        const valueInput = el('input', { className: 'form-input swf-map__value', 'aria-label': `${valueLabel} ${index + 1}`, placeholder: isStore ? 'e.g. body' : valueLabel, value: row.value, autocomplete: 'off' });
        keyInput.addEventListener('input', () => {
          row.key = keyInput.value;
          onEditorChange();
        });
        valueInput.addEventListener('input', () => {
          row.value = valueInput.value;
          showFieldIssues(shell.issuesNode, rows.map((item) => item.value).join(' '));
          onEditorChange();
        });
        valueInput.addEventListener('focus', () => {
          lastValueInput = valueInput;
          setHelperTarget(isStore ? null : control);
        });
        keyInput.addEventListener('focus', () => setHelperTarget(null));
        const remove = el('button', { type: 'button', className: 'scf-icon-button scf-icon-button--danger', 'aria-label': `Remove row ${index + 1}`, title: 'Remove' }, el('span', { 'aria-hidden': 'true', text: '×' }));
        remove.addEventListener('click', () => {
          rows.splice(index, 1);
          lastValueInput = null;
          draw();
          onEditorChange();
        });
        list.append(el('li', { className: 'swf-map__row' }, [keyInput, valueInput, remove]));
      });
      showFieldIssues(shell.issuesNode, rows.map((item) => item.value).join(' '));
    };
    const addButton = el('button', { type: 'button', className: 'button button--ghost button--small', text: addLabel });
    addButton.addEventListener('click', () => {
      rows.push({ key: '', value: '' });
      draw();
      const inputs = $$('input', list);
      if (inputs.length >= 2) {
        inputs[inputs.length - 2].focus();
      }
    });
    if (shell.insertButton) {
      shell.insertButton.addEventListener('click', () => {
        setHelperTarget(control);
        editor.helperSearch.focus();
      });
    }
    const head = el('div', { className: 'swf-map__head', 'aria-hidden': 'true' }, [el('span', { text: keyLabel }), el('span', { text: valueLabel }), el('span')]);
    shell.wrapper.querySelector('label').removeAttribute('for');
    appendAll(shell.wrapper, head, list, addButton, shell.helpNode, shell.issuesNode, shell.errorNode);
    container.append(shell.wrapper);
    draw();
    return control;
  }

  function buildSimpleControl(field, container, value) {
    const shell = fieldShell(field, { full: false, templatable: false });
    let input;
    let read;
    if (field.type === 'checkbox' || field.type === 'boolean') {
      input = el('input', { type: 'checkbox', id: shell.id });
      input.checked = Boolean(value);
      const switchLabel = el('label', { className: 'scf-switch' }, [input, el('span', { className: 'scf-switch__track', 'aria-hidden': 'true' }), el('span', { text: field.label || field.name })]);
      shell.wrapper.querySelector('.swf-field__label-row').remove();
      appendAll(shell.wrapper, switchLabel);
      read = () => input.checked;
    } else if (field.type === 'select') {
      input = el('select', { className: 'form-input', id: shell.id });
      const options = Array.isArray(field.options) ? field.options : [];
      options.forEach((option) => {
        const optionValue = typeof option === 'object' ? String(option.value ?? '') : String(option);
        const optionLabel = typeof option === 'object' ? String(option.label ?? optionValue) : String(option);
        input.append(el('option', { value: optionValue, text: optionLabel || 'Leave unchanged' }));
      });
      input.value = value === undefined || value === null ? '' : String(value);
      if (input.value !== String(value ?? '') && options.length) {
        input.selectedIndex = 0;
      }
      appendAll(shell.wrapper, input);
      read = () => input.value;
    } else {
      const isNumber = field.type === 'number';
      input = el('input', {
        className: 'form-input',
        id: shell.id,
        type: isNumber ? 'number' : (field.type === 'date' ? 'date' : 'text'),
        min: field.min !== undefined ? field.min : null,
        max: field.max !== undefined ? field.max : null,
      });
      input.value = value === undefined || value === null ? '' : String(value);
      appendAll(shell.wrapper, input);
      read = () => {
        if (!isNumber) {
          return input.value;
        }
        return input.value === '' ? null : Number(input.value);
      };
    }
    appendAll(shell.wrapper, shell.helpNode, shell.errorNode);
    container.append(shell.wrapper);
    input.addEventListener('focus', () => setHelperTarget(null));
    input.addEventListener('input', onEditorChange);
    input.addEventListener('change', onEditorChange);
    return { field, label: field.label || field.name, read, error: shell.errorNode, focus: () => input.focus() };
  }

  function buildControl(field, container, value) {
    if (field.widget === 'tags') {
      return buildTagControl(field, container, value);
    }
    if (field.widget === 'mapping') {
      return buildMappingControl(field, container, value);
    }
    if (field.type === 'text' || field.type === 'textarea' || field.widget === 'json' || field.widget === 'variable_name') {
      return buildTextControl(field, container, value);
    }
    return buildSimpleControl(field, container, value);
  }

  function readEditorIntoDraft() {
    const step = editor.step;
    step.name = editor.nameInput.value.trim();
    step.enabled = editor.enabledInput.checked;
    const conditional = $('[data-swf-condition-mode]:checked', editor.modal).value === 'conditional';
    editor.controls.forEach((control) => {
      const path = fieldPath(control.field);
      let value = control.read();
      if (path.startsWith('conditions.') && !conditional) {
        value = '';
      }
      setNested(step.config, path, value);
    });
    const mode = ($('[data-swf-failure-mode]:checked', editor.modal) || {}).value || 'fail_fast';
    step.failure_policy = { ...step.failure_policy, mode, create_ticket_on_failure: editor.ticketInput.checked };
    step.retry_policy = { max_retries: Math.max(0, Math.min(20, Number(editor.retriesInput.value || 0))) };
  }

  function serializeEditor() {
    readEditorIntoDraft();
    return JSON.stringify(editor.step);
  }

  function onEditorChange() {
    readEditorIntoDraft();
    renderEditorSummaries();
  }

  function renderEditorSummaries() {
    const step = editor.step;
    const outputs = stepOutputs(step);
    editor.provides.textContent = '';
    if (!outputs.length) {
      editor.provides.append(el('li', { className: 'text-muted', text: 'This step doesn\'t provide any values for later steps.' }));
    }
    outputs.forEach((output) => {
      const token = `\${vars.${output.name}}`;
      const copy = el('button', { type: 'button', className: 'swf-var swf-var--wide', title: `Copy ${token}` }, [
        el('span', { className: 'swf-var__label' }, [output.label, output.secret ? el('span', { className: 'swf-var__badge', text: 'Protected' }) : null]),
        el('code', { className: 'swf-var__name', text: token }),
      ]);
      copy.addEventListener('click', () => copyText(token));
      editor.provides.append(el('li', {}, copy));
    });
    if (outputs.some((output) => output.secret)) {
      editor.provides.append(el('li', { className: 'form-help', text: 'Protected values are hidden in logs and execution history but can still be used by later steps.' }));
    }
    editor.outputsCount.textContent = outputs.length ? String(outputs.length) : '';

    const conditional = $('[data-swf-condition-mode]:checked', editor.modal).value === 'conditional';
    editor.conditionsBox.hidden = !conditional;
    editor.conditionSentence.textContent = conditional ? (conditionSummary(step) || 'Add a department or a tick-box field. With nothing set, the step always runs.') : '';
    const failureMode = step.failure_policy.mode;
    editor.ticketLabel.textContent = failureMode === 'pause' ? 'Create a ticket when it pauses' : 'Create a ticket when it fails';
    editor.ticketInput.disabled = failureMode === 'continue';
    editor.failureSentence.textContent = failureSummary(step);

    const tabState = {
      conditions: conditional && stepIsConditional(step),
      failure: failureMode !== 'fail_fast' || Number(step.retry_policy.max_retries || 0) > 0,
    };
    $$('[data-swf-tab]', editor.modal).forEach((tab) => {
      tab.classList.toggle('is-configured', Boolean(tabState[tab.dataset.swfTab]));
    });
    editor.title.textContent = step.name || catalogEntry(step.type).name;
    renderPreview();
  }

  function renderWebhookDetails(step) {
    editor.webhook.textContent = '';
    if (step.type !== 'wait_for_webhook' && step.type !== 'wait_external_checkpoint') {
      editor.webhook.hidden = true;
      return;
    }
    const preview = step.webhook_preview || {};
    const children = [
      el('h3', { className: 'scf-panel__title', text: 'Callback details' }),
    ];
    if (preview.webhook_url && preview.post_key) {
      children.push(el('p', { className: 'text-muted', text: 'Your automation sends a POST request to this URL with the POST key as postKey in the JSON body. Add any values it returns under "values".' }));
      [['Callback URL', preview.webhook_url], ['POST key', preview.post_key]].forEach(([label, value]) => {
        fieldIdCounter += 1;
        const id = `swf-field-${fieldIdCounter}`;
        const input = el('input', { className: 'form-input scf-mono is-readonly', id, readOnly: true, value });
        const copy = el('button', { type: 'button', className: 'button button--ghost button--small', text: 'Copy' });
        copy.addEventListener('click', () => copyText(value));
        children.push(el('div', { className: 'form-field' }, [el('label', { className: 'form-label', for: id, text: label }), el('div', { className: 'swf-copy-row' }, [input, copy])]));
      });
      children.push(el('p', { className: 'form-help', text: 'Renaming this step or the workflow key creates a new URL and key after saving.' }));
    } else {
      children.push(el('p', { className: 'text-muted', text: 'Save the workflow to generate this step\'s callback URL and POST key.' }));
    }
    editor.webhook.append(...children);
    editor.webhook.hidden = false;
  }

  function openStepEditor(index, { draft } = {}) {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    editor.isNew = Boolean(draft);
    editor.index = index;
    editor.step = clone(draft || wf.steps[index]);
    editor.step.uid = draft ? draft.uid : wf.steps[index].uid;
    editor.target = null;
    editor.controls = [];
    const entry = catalogEntry(editor.step.type);
    const total = wf.steps.length + (editor.isNew ? 1 : 0);
    editor.eyebrow.textContent = `${editor.isNew ? 'New step' : 'Step'} ${index + 1} of ${total} · ${categoryLabel(entry.category)} · ${entry.name}`;
    editor.subtitle.textContent = entry.description || '';
    editor.nameInput.value = editor.step.name;
    editor.enabledInput.checked = editor.step.enabled !== false;
    editor.nameError.hidden = true;
    editor.deleteButton.hidden = editor.isNew;
    editor.helperSearch.value = '';

    Object.values(editor.fieldContainers).forEach((container) => {
      container.textContent = '';
    });
    schemaFor(editor.step.type).fields.forEach((field) => {
      const current = getNested(editor.step.config, fieldPath(field));
      const value = current === undefined ? field.default : current;
      editor.controls.push(buildControl(field, editor.fieldContainers[fieldSection(field)], value));
    });
    if (!editor.fieldContainers.settings.children.length) {
      editor.fieldContainers.settings.append(el('p', { className: 'text-muted scf-grid__full', text: 'This step has nothing else to set up.' }));
    }
    renderWebhookDetails(editor.step);

    const conditional = stepIsConditional(editor.step);
    $$('[data-swf-condition-mode]', editor.modal).forEach((radio) => {
      radio.checked = radio.value === (conditional ? 'conditional' : 'always');
    });
    const mode = ['fail_fast', 'pause', 'continue'].includes(editor.step.failure_policy.mode) ? editor.step.failure_policy.mode : 'fail_fast';
    $$('[data-swf-failure-mode]', editor.modal).forEach((radio) => {
      radio.checked = radio.value === mode;
    });
    editor.retriesInput.value = String(Number(editor.step.retry_policy.max_retries || 0));
    editor.ticketInput.checked = editor.step.failure_policy.create_ticket_on_failure !== false;

    editor.showTab('settings');
    setHelperTarget(null);
    renderHelper();
    renderEditorSummaries();
    editor.snapshot = editor.isNew ? '' : serializeEditor();
    openModal(editor.modal, { onRequestClose: requestCloseEditor, focus: editor.nameInput });
  }

  function requestCloseEditor() {
    if (serializeEditor() !== editor.snapshot && !window.confirm(editor.isNew ? 'Discard this new step?' : 'Discard your changes to this step?')) {
      return;
    }
    closeModal(editor.modal);
  }

  $$('[data-swf-condition-mode], [data-swf-failure-mode]', editor.modal).forEach((radio) => {
    radio.addEventListener('change', onEditorChange);
  });
  [editor.nameInput, editor.enabledInput, editor.retriesInput, editor.ticketInput].forEach((input) => {
    input.addEventListener('input', onEditorChange);
    input.addEventListener('change', onEditorChange);
  });
  editor.nameInput.addEventListener('focus', () => setHelperTarget(null));

  function validateEditor() {
    const wf = selectedWorkflow();
    let firstInvalid = null;
    let firstTab = null;
    const name = editor.nameInput.value.trim();
    const clash = wf.steps.some((step) => step.uid !== editor.step.uid && step.name.toLowerCase() === name.toLowerCase());
    if (!name || clash) {
      editor.nameError.textContent = !name ? 'Give the step a name.' : 'Another step already has this name. Step names must be unique.';
      editor.nameError.hidden = false;
      firstInvalid = editor.nameInput;
      firstTab = 'settings';
    } else {
      editor.nameError.hidden = true;
    }
    const conditional = $('[data-swf-condition-mode]:checked', editor.modal).value === 'conditional';
    editor.controls.forEach((control) => {
      const section = fieldSection(control.field);
      const raw = control.read();
      const empty = raw === null || raw === undefined || String(raw).trim() === '';
      const skip = section === 'conditions' && !conditional;
      const invalid = control.field.required && empty && !skip && control.field.type !== 'checkbox';
      control.error.textContent = invalid ? `${control.label} is required.` : (control.error.textContent || '');
      if (invalid) {
        control.error.hidden = false;
      } else if (control.field.widget !== 'json') {
        control.error.hidden = true;
      }
      if (invalid && !firstInvalid) {
        firstInvalid = control;
        firstTab = section;
      }
    });
    if (firstInvalid) {
      editor.showTab(firstTab);
      window.requestAnimationFrame(() => (typeof firstInvalid.focus === 'function' ? firstInvalid.focus() : null));
      return false;
    }
    return true;
  }

  editor.form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!editor.isNew && serializeEditor() === editor.snapshot) {
      closeModal(editor.modal);
      return;
    }
    readEditorIntoDraft();
    if (!validateEditor()) {
      return;
    }
    const wf = selectedWorkflow();
    const step = clone(editor.step);
    step.uid = editor.step.uid;
    if (editor.isNew) {
      wf.steps.splice(editor.index, 0, step);
    } else {
      wf.steps[editor.index] = step;
    }
    wf.error = '';
    closeModal(editor.modal);
    render();
    announce(editor.isNew ? `Added ${step.name} as step ${editor.index + 1}` : `Updated ${step.name}`);
  });

  editor.deleteButton.addEventListener('click', () => {
    const wf = selectedWorkflow();
    const step = wf.steps[editor.index];
    if (!step || !window.confirm(`Remove “${step.name}” from this workflow? It's only removed when you save.`)) {
      return;
    }
    wf.steps.splice(editor.index, 1);
    closeModal(editor.modal);
    render();
    announce(`Removed ${step.name}`);
  });

  // ------------------------------------------------------ workflow settings

  const settings = {
    modal: document.getElementById('swf-settings-modal'),
    mode: 'edit',
    keyTouched: false,
    snapshot: '',
  };
  settings.form = $('[data-swf-settings-form]', settings.modal);
  settings.title = $('[data-swf-settings-title]', settings.modal);
  settings.name = $('[data-swf-settings-name]', settings.modal);
  settings.key = $('[data-swf-settings-key]', settings.modal);
  settings.keyHelp = $('[data-swf-settings-key-help]', settings.modal);
  settings.retries = $('[data-swf-settings-retries]', settings.modal);
  settings.enabled = $('[data-swf-settings-enabled]', settings.modal);
  settings.deleteButton = $('[data-swf-workflow-delete]', settings.modal);
  settings.submit = $('[data-swf-settings-submit]', settings.modal);
  settings.nameError = $('[data-swf-error="workflow_name"]', settings.modal);
  settings.keyError = $('[data-swf-error="workflow_key"]', settings.modal);
  settings.subtitle = $('[data-swf-settings-subtitle]', settings.modal);
  settings.targetField = $('[data-swf-settings-target-field]', settings.modal);
  settings.target = $('[data-swf-settings-target]', settings.modal);
  settings.targetHelp = $('[data-swf-settings-target-help]', settings.modal);
  settings.targetError = $('[data-swf-error="target_company"]', settings.modal);

  const TARGET_HELP_HERE = 'Keep this company to make an unsaved copy here, or pick another company to save the copy straight into it.';
  const TARGET_HELP_OTHER = 'The saved steps are copied into this company. It starts turned off so you can review company-specific settings such as groups, licences and mailboxes first.';

  // The company a duplicate goes to, or null when it stays in this company.
  function copyTargetCompany() {
    if (settings.mode !== 'duplicate' || settings.targetField.hidden) {
      return null;
    }
    const id = Number(settings.target.value);
    if (!id || id === state.companyId) {
      return null;
    }
    return state.companies.find((company) => company.id === id) || null;
  }

  function renderCopyTarget() {
    const target = copyTargetCompany();
    settings.targetHelp.textContent = target ? TARGET_HELP_OTHER : TARGET_HELP_HERE;
    settings.submit.textContent = target ? `Copy to ${target.name}` : 'Create workflow';
    settings.targetError.hidden = true;
  }

  settings.target.addEventListener('change', () => {
    const target = copyTargetCompany();
    settings.enabled.checked = target ? false : settings.enabledDefault;
    renderCopyTarget();
  });

  function settingsValues() {
    return {
      workflow_name: settings.name.value.trim(),
      workflow_key: slugifyKey(settings.key.value),
      delay_type: ($('[data-swf-settings-delay]:checked', settings.modal) || {}).value || 'scheduled',
      max_retries: Math.max(0, Math.min(20, Number(settings.retries.value || 0))),
      enabled: settings.enabled.checked,
    };
  }

  function openSettings(mode, source) {
    settings.mode = mode;
    const wf = mode === 'edit' ? selectedWorkflow() : null;
    const values = wf || source || {
      workflow_name: '',
      workflow_key: '',
      delay_type: 'scheduled',
      max_retries: 2,
      enabled: true,
    };
    settings.title.textContent = mode === 'edit' ? 'Workflow settings' : (mode === 'duplicate' ? 'Duplicate workflow' : 'New workflow');
    settings.subtitle.textContent = mode === 'duplicate'
      ? 'Choose where the copy goes, then name it.'
      : 'Name the workflow and choose when it runs.';
    settings.submit.textContent = mode === 'edit' ? 'Apply' : 'Create workflow';
    const showTarget = mode === 'duplicate' && state.companies.length > 1;
    settings.targetField.hidden = !showTarget;
    settings.targetError.hidden = true;
    settings.target.replaceChildren(...state.companies.map((company) => el('option', {
      value: String(company.id),
      text: company.id === state.companyId ? `${company.name} (this company)` : company.name,
    })));
    if (state.companyId !== null) {
      settings.target.value = String(state.companyId);
    }
    settings.name.value = values.workflow_name || '';
    settings.key.value = values.workflow_key || '';
    settings.keyTouched = mode === 'edit' && Boolean(wf && wf.id);
    settings.keyHelp.textContent = settings.keyTouched
      ? 'Webhook callback URLs are built from the key, so changing it issues new URLs after saving.'
      : 'Generated from the name. Webhook callback URLs are built from it.';
    $$('[data-swf-settings-delay]', settings.modal).forEach((radio) => {
      radio.checked = radio.value === (values.delay_type === 'immediate' ? 'immediate' : 'scheduled');
    });
    settings.retries.value = String(Number(values.max_retries ?? 2));
    settings.enabled.checked = values.enabled !== false;
    settings.enabledDefault = settings.enabled.checked;
    if (showTarget) {
      renderCopyTarget();
    }
    settings.deleteButton.hidden = mode !== 'edit';
    settings.nameError.hidden = true;
    settings.keyError.hidden = true;
    settings.snapshot = JSON.stringify(settingsValues());
    openModal(settings.modal, {
      focus: settings.name,
      onRequestClose: () => {
        if (JSON.stringify(settingsValues()) !== settings.snapshot && !window.confirm('Discard these workflow settings?')) {
          return;
        }
        closeModal(settings.modal);
      },
    });
  }

  settings.name.addEventListener('input', () => {
    if (!settings.keyTouched) {
      const slug = slugifyKey(settings.name.value);
      settings.key.value = slug ? `staff_${DIRECTION}_${slug}`.slice(0, 120) : '';
    }
  });
  settings.key.addEventListener('input', () => {
    settings.keyTouched = true;
  });

  settings.form.addEventListener('submit', (event) => {
    event.preventDefault();
    const values = settingsValues();
    let valid = true;
    settings.nameError.hidden = Boolean(values.workflow_name);
    settings.nameError.textContent = 'Give the workflow a name.';
    if (!values.workflow_name) {
      valid = false;
    }
    const current = settings.mode === 'edit' ? selectedWorkflow() : null;
    const target = copyTargetCompany();
    const clash = !target && state.workflows.some((wf) => wf !== current && wf.workflow_key === values.workflow_key);
    if (!values.workflow_key || clash) {
      settings.keyError.textContent = !values.workflow_key ? 'Add a key using letters, numbers, _ , - or .' : 'Another workflow already uses this key.';
      settings.keyError.hidden = false;
      valid = false;
    } else {
      settings.keyError.hidden = true;
    }
    if (!valid) {
      (values.workflow_name ? settings.key : settings.name).focus();
      return;
    }
    if (target) {
      copyToCompany(target, values);
      return;
    }
    if (current) {
      Object.assign(current, values);
      current.error = '';
    } else {
      const base = settings.mode === 'duplicate' ? selectedWorkflow() : null;
      const wf = {
        uid: nextUid('w'),
        id: null,
        ...values,
        existingConfig: base ? clone(base.existingConfig) : {},
        steps: base ? base.steps.map((step) => ({ ...clone(step), uid: nextUid('s'), webhook_preview: null })) : [],
        snapshot: '',
        error: '',
      };
      state.workflows.push(wf);
      state.selectedUid = wf.uid;
    }
    closeModal(settings.modal);
    render();
    announce(current ? 'Workflow settings updated' : 'Workflow created. Add steps, then save.');
  });

  async function copyToCompany(target, values) {
    const source = selectedWorkflow();
    if (!source || !source.id || isDirty(source)) {
      settings.targetError.textContent = 'Save this workflow first. Only saved steps are copied to another company.';
      settings.targetError.hidden = false;
      settings.target.focus();
      return;
    }
    settings.submit.disabled = true;
    settings.submit.classList.add('button--processing');
    try {
      const response = await fetch(`${API_BASE}/${source.id}/copy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ ...values, target_company_id: target.id }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = errorMessage(payload).replace('The workflow could not be saved.', 'The workflow could not be copied.');
        const errorNode = response.status === 409 ? settings.keyError : settings.targetError;
        errorNode.textContent = message;
        errorNode.hidden = false;
        (response.status === 409 ? settings.key : settings.target).focus();
        return;
      }
      closeModal(settings.modal);
      const name = (payload.company && payload.company.name) || target.name;
      const message = `Copied ${values.workflow_name} to ${name}. Switch to ${name} to review it and turn it on.`;
      if (window.__portalToast) {
        window.__portalToast.show(message, { variant: 'success' });
      }
      announce(message);
    } catch (error) {
      settings.targetError.textContent = 'The workflow could not be copied. Check your connection and try again.';
      settings.targetError.hidden = false;
    } finally {
      settings.submit.disabled = false;
      settings.submit.classList.remove('button--processing');
    }
  }

  settings.deleteButton.addEventListener('click', async () => {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    if (!window.confirm(`Delete “${workflowTitle(wf)}”? It will stop running for new ${DIRECTION} requests. This can't be undone.`)) {
      return;
    }
    if (wf.id) {
      try {
        const response = await fetch(`${API_BASE}/${wf.id}`, { method: 'DELETE', headers: { Accept: 'application/json' } });
        if (!response.ok) {
          settings.keyError.textContent = 'The workflow could not be deleted. Try again.';
          settings.keyError.hidden = false;
          return;
        }
      } catch (error) {
        settings.keyError.textContent = 'The workflow could not be deleted. Check your connection and try again.';
        settings.keyError.hidden = false;
        return;
      }
    }
    state.workflows = state.workflows.filter((item) => item !== wf);
    state.selectedUid = state.workflows.length ? state.workflows[0].uid : null;
    closeModal(settings.modal);
    render();
    announce(`Deleted ${workflowTitle(wf)}`);
  });

  // ---------------------------------------------------- variables reference

  const reference = {
    modal: document.getElementById('swf-variables-modal'),
  };
  reference.search = $('[data-swf-reference-search]', reference.modal);
  reference.groups = $('[data-swf-reference-groups]', reference.modal);

  function renderReference() {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    renderVariableGroups(reference.groups, variableGroupsAt(wf, -1, { includeLater: true }).map((group) => ({ ...group, later: false })), {
      query: reference.search.value,
      onPick: (variable) => copyText(`\${vars.${variable.name}}`),
    });
  }

  reference.search.addEventListener('input', renderReference);

  // ----------------------------------------------------------------- save

  function errorMessage(payload) {
    const detail = payload && payload.detail;
    if (!detail) {
      return 'The workflow could not be saved.';
    }
    if (typeof detail === 'string') {
      return detail;
    }
    const errors = Array.isArray(detail.errors) ? detail.errors.join(' ') : '';
    return [detail.message, errors].filter(Boolean).join(' ') || 'The workflow could not be saved.';
  }

  async function saveWorkflow() {
    const wf = selectedWorkflow();
    if (!wf) {
      return;
    }
    els.saveButton.disabled = true;
    els.saveButton.classList.add('button--processing');
    const body = buildRequestBody(wf);
    try {
      const response = await fetch(wf.id ? `${API_BASE}/${wf.id}` : API_BASE, {
        method: wf.id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        wf.error = errorMessage(payload);
        renderSavebar();
        announce(wf.error);
        return;
      }
      const saved = readWorkflow(payload.policy || {});
      saved.uid = wf.uid;
      Object.assign(wf, saved, { error: '' });
      wf.snapshot = JSON.stringify(buildRequestBody(wf));
      if (wf.id) {
        storageSet(STORAGE_KEY, String(wf.id));
      }
      render();
      announce(`${workflowTitle(wf)} saved`);
    } catch (error) {
      wf.error = 'The workflow could not be saved. Check your connection and try again.';
      renderSavebar();
    } finally {
      els.saveButton.disabled = false;
      els.saveButton.classList.remove('button--processing');
    }
  }

  els.saveButton.addEventListener('click', saveWorkflow);
  els.discardButton.addEventListener('click', () => {
    const wf = selectedWorkflow();
    if (!wf || !window.confirm(wf.id ? 'Discard your unsaved changes to this workflow?' : 'Discard this new workflow?')) {
      return;
    }
    if (!wf.id) {
      state.workflows = state.workflows.filter((item) => item !== wf);
      state.selectedUid = state.workflows.length ? state.workflows[0].uid : null;
    } else {
      const restored = readWorkflow({ ...JSON.parse(wf.snapshot), id: wf.id, config_json: JSON.parse(wf.snapshot).config });
      restored.uid = wf.uid;
      // Keep webhook previews from the last load.
      restored.steps.forEach((step) => {
        const original = wf.steps.find((item) => item.name === step.name);
        step.webhook_preview = original ? original.webhook_preview : null;
      });
      Object.assign(wf, restored);
    }
    render();
    announce('Changes discarded');
  });

  window.addEventListener('beforeunload', (event) => {
    if (state.workflows.some((wf) => !wf.id || isDirty(wf))) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  // ------------------------------------------------------------- actions

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    const addWorkflow = target.closest('[data-swf-add-workflow]');
    if (addWorkflow) {
      event.preventDefault();
      if (!state.catalog.length) {
        announce('Please wait while workflows load.');
        return;
      }
      openSettings('create');
      return;
    }
    if (!root.contains(target)) {
      return;
    }
    const select = target.closest('[data-swf-select]');
    if (select) {
      selectWorkflow(select.dataset.swfSelect);
      return;
    }
    const insert = target.closest('[data-swf-insert]');
    if (insert) {
      openPicker(Number(insert.dataset.swfInsert));
      return;
    }
    const edit = target.closest('[data-swf-edit]');
    if (edit) {
      openStepEditor(Number(edit.dataset.swfEdit));
      return;
    }
    const move = target.closest('[data-swf-move]');
    if (move) {
      moveStep(Number(move.dataset.swfMove), Number(move.dataset.swfDirection));
      return;
    }
    const duplicateStep = target.closest('[data-swf-duplicate-step]');
    if (duplicateStep) {
      const wf = selectedWorkflow();
      const index = Number(duplicateStep.dataset.swfDuplicateStep);
      const copy = { ...clone(wf.steps[index]), uid: nextUid('s'), webhook_preview: null };
      copy.name = uniqueStepName(wf, `${wf.steps[index].name} (copy)`);
      wf.steps.splice(index + 1, 0, copy);
      render();
      announce(`Duplicated as ${copy.name}`);
      return;
    }
    if (target.closest('[data-swf-settings-open]')) {
      openSettings('edit');
      return;
    }
    if (target.closest('[data-swf-duplicate-workflow]')) {
      const wf = selectedWorkflow();
      if (wf) {
        openSettings('duplicate', {
          ...wf,
          workflow_name: `${workflowTitle(wf)} (copy)`,
          workflow_key: `${wf.workflow_key}_copy`.slice(0, 120),
        });
        settings.keyTouched = true;
      }
      return;
    }
    if (target.closest('[data-swf-variables-open]')) {
      reference.search.value = '';
      renderReference();
      openModal(reference.modal, { focus: reference.search });
    }
  });

  function moveStep(index, direction) {
    const wf = selectedWorkflow();
    const next = index + direction;
    if (!wf || next < 0 || next >= wf.steps.length) {
      return;
    }
    const [step] = wf.steps.splice(index, 1);
    wf.steps.splice(next, 0, step);
    render();
    const button = $(`[data-swf-move="${next}"][data-swf-direction="${direction}"]`, els.flow);
    if (button && !button.disabled) {
      button.focus();
    } else {
      const edit = $(`.swf-step__name[data-swf-edit="${next}"]`, els.flow);
      if (edit) {
        edit.focus();
      }
    }
    announce(`${step.name} is now step ${next + 1}`);
  }

  // Drag to reorder (buttons remain the keyboard path).
  let dragIndex = null;
  els.flow.addEventListener('dragstart', (event) => {
    const node = event.target instanceof Element ? event.target.closest('[data-swf-step-index]') : null;
    if (!node) {
      return;
    }
    dragIndex = Number(node.dataset.swfStepIndex);
    node.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(dragIndex));
  });
  els.flow.addEventListener('dragover', (event) => {
    if (dragIndex === null) {
      return;
    }
    const node = event.target instanceof Element ? event.target.closest('[data-swf-step-index]') : null;
    if (!node) {
      return;
    }
    event.preventDefault();
    $$('.is-drop-target', els.flow).forEach((item) => item.classList.remove('is-drop-target'));
    node.classList.add('is-drop-target');
  });
  els.flow.addEventListener('drop', (event) => {
    const node = event.target instanceof Element ? event.target.closest('[data-swf-step-index]') : null;
    if (dragIndex === null || !node) {
      return;
    }
    event.preventDefault();
    const wf = selectedWorkflow();
    const to = Number(node.dataset.swfStepIndex);
    if (wf && to !== dragIndex) {
      const [step] = wf.steps.splice(dragIndex, 1);
      wf.steps.splice(to, 0, step);
      announce(`${step.name} is now step ${to + 1}`);
    }
    dragIndex = null;
    render();
  });
  els.flow.addEventListener('dragend', () => {
    dragIndex = null;
    $$('.is-dragging, .is-drop-target', els.flow).forEach((item) => item.classList.remove('is-dragging', 'is-drop-target'));
  });

  // ----------------------------------------------------------------- load

  async function load() {
    try {
      const response = await fetch(API_BASE, { headers: { Accept: 'application/json' } });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      state.catalog = Array.isArray(payload.step_catalog) ? payload.step_catalog : [];
      state.catalogByType = new Map(state.catalog.map((item) => [item.type, item]));
      state.schema = payload.step_form_schema && typeof payload.step_form_schema === 'object' ? payload.step_form_schema : {};
      state.categories = Array.isArray(payload.step_categories) ? payload.step_categories : [];
      state.baseGroups = Array.isArray(payload.variables) ? payload.variables.map((group) => ({
        ...group,
        variables: Array.isArray(group.variables) ? group.variables : [],
      })) : [];
      state.workflows = (Array.isArray(payload.policies) ? payload.policies : []).map(readWorkflow);
      state.companyId = Number(payload.company_id) || null;
      state.companies = (Array.isArray(payload.companies) ? payload.companies : [])
        .map((company) => ({ id: Number(company.id), name: String(company.name || '') }))
        .filter((company) => company.id);
      const remembered = storageGet(STORAGE_KEY);
      const match = state.workflows.find((wf) => String(wf.id) === remembered);
      state.selectedUid = match ? match.uid : (state.workflows[0] || {}).uid || null;
      els.loading.hidden = true;
      root.setAttribute('aria-busy', 'false');
      render();
    } catch (error) {
      els.loading.textContent = `The ${DIRECTION} workflows could not be loaded. Refresh the page to try again.`;
      root.setAttribute('aria-busy', 'false');
    }
  }

  load();
})();
