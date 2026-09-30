(function () {
  'use strict';

  const METRIC_LABELS = {
    click_rate: 'click rate',
    open_rate: 'open rate',
    bounce_rate: 'bounce rate',
  };
  const COUNT_FIELDS = ['sent', 'delivered', 'opened', 'clicked', 'bounced'];
  const DEFAULT_MINIMUM = 25;

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
      } else if (key === 'dataset') {
        Object.assign(node.dataset, value);
      } else if (value === true) {
        node.setAttribute(key, '');
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

  function toInt(value, fallback) {
    const number = Number.parseInt(value, 10);
    return Number.isFinite(number) && number >= 0 ? number : fallback;
  }

  function parseCampaigns(text) {
    const raw = String(text || '').trim();
    if (!raw) {
      return { ok: true, value: [] };
    }
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return { ok: false, message: 'A/B campaigns must be a JSON list, for example [{"name": …}].' };
      }
      return { ok: true, value: parsed.filter((item) => item && typeof item === 'object' && !Array.isArray(item)) };
    } catch (error) {
      return { ok: false, message: "The JSON can't be read, so the campaign list wasn't updated." };
    }
  }

  // Mirrors select_ab_test_winner in app/services/smtp2go.py.
  function evaluate(campaign) {
    const metric = String(campaign.winner_metric || 'click_rate').trim() || 'click_rate';
    const minimum = Math.max(1, toInt(campaign.minimum_sample_size, DEFAULT_MINIMUM));
    const variants = (Array.isArray(campaign.variants) ? campaign.variants : [])
      .filter((variant) => variant && typeof variant === 'object')
      .map((variant) => {
        const delivered = toInt(variant.delivered, 0);
        const sent = toInt(variant.sent, delivered);
        const sample = Math.max(delivered, sent);
        const rate = (count) => (sample ? Math.round((toInt(count, 0) / sample) * 10000) / 100 : 0);
        return {
          name: String(variant.name || '').trim(),
          sample,
          eligible: sample >= minimum,
          metrics: {
            open_rate: rate(variant.opened),
            click_rate: rate(variant.clicked),
            bounce_rate: rate(variant.bounced),
          },
        };
      });
    const eligible = variants.filter((variant) => variant.eligible);
    let winner = null;
    if (eligible.length >= 2) {
      winner = eligible.reduce((best, variant) => {
        const a = [variant.metrics[metric] || 0, variant.sample, -variant.metrics.bounce_rate];
        const b = [best.metrics[metric] || 0, best.sample, -best.metrics.bounce_rate];
        for (let i = 0; i < a.length; i += 1) {
          if (a[i] !== b[i]) {
            return a[i] > b[i] ? variant : best;
          }
        }
        return best;
      });
    }
    return { metric, minimum, variants, winner };
  }

  document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-ab-campaigns]');
    const modal = document.getElementById('abc-modal');
    if (!root || !modal) {
      return;
    }
    const pageForm = root.closest('form');
    const textarea = root.querySelector('textarea[name="abCampaignsRaw"]');
    const advanced = root.querySelector('[data-abc-advanced]');
    const list = root.querySelector('[data-abc-list]');
    const empty = root.querySelector('[data-abc-empty]');
    const status = root.querySelector('[data-abc-status]');
    const errorNode = root.querySelector('[data-abc-error]');
    const createButtons = Array.from(root.querySelectorAll('[data-abc-create]'));

    const editor = modal.querySelector('[data-abc-editor]');
    const modalTitle = modal.querySelector('[data-abc-modal-title]');
    const nameInput = modal.querySelector('[data-abc-input="name"]');
    const minimumInput = modal.querySelector('[data-abc-input="minimum_sample_size"]');
    const metricInputs = Array.from(modal.querySelectorAll('[data-abc-metric]'));
    const metricList = modal.querySelector('[data-abc-metrics]');
    const variantsWrap = modal.querySelector('[data-abc-variants]');
    const editorError = modal.querySelector('[data-abc-editor-error]');
    const preview = modal.querySelector('[data-abc-preview]');
    const previewRules = modal.querySelector('[data-abc-preview-rules]');
    const deleteButton = modal.querySelector('[data-abc-delete]');
    const submitButton = modal.querySelector('[data-abc-submit]');

    let campaigns = [];
    let jsonOk = true;
    let draft = null;
    let editingIndex = -1;
    let snapshot = '';
    let lastTrigger = null;

    function setError(message) {
      errorNode.textContent = message || '';
      errorNode.hidden = !message;
    }

    // ----- list ------------------------------------------------------------

    function renderList() {
      list.textContent = '';
      campaigns.forEach((campaign, index) => {
        const result = evaluate(campaign);
        const name = String(campaign.name || '').trim() || 'Untitled campaign';
        const chips = [
          el('li', { className: 'scf-chip', text: `By ${METRIC_LABELS[result.metric] || result.metric}` }),
          el('li', { className: 'scf-chip', text: `${result.variants.length} variant${result.variants.length === 1 ? '' : 's'}` }),
          el('li', { className: 'scf-chip', text: `Min ${result.minimum} sends` }),
          result.winner
            ? el('li', { className: 'scf-chip sif-chip--optional', text: `Winner: ${result.winner.name || 'Unnamed variant'}` })
            : el('li', { className: 'scf-chip sif-chip--warning', text: 'Collecting data' }),
        ];
        list.append(el('li', { className: 'scf-item abc__item' }, [
          el('span', { className: 'abc__icon', 'aria-hidden': 'true', text: 'A/B' }),
          el('div', { className: 'scf-item__main' }, [
            el('div', { className: 'scf-item__heading' }, [
              el('button', { type: 'button', className: 'scf-item__label', dataset: { abcEdit: String(index) }, 'aria-haspopup': 'dialog', 'aria-controls': 'abc-modal', text: name }),
            ]),
            el('ul', { className: 'scf-item__chips', 'aria-label': 'Campaign summary' }, chips),
          ]),
          el('div', { className: 'scf-item__actions' }, [
            el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { abcEdit: String(index) }, 'aria-haspopup': 'dialog', 'aria-controls': 'abc-modal' }, ['Edit', el('span', { className: 'visually-hidden', text: ` ${name}` })]),
            el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { abcDuplicate: String(index) }, 'aria-haspopup': 'dialog', 'aria-controls': 'abc-modal' }, ['Duplicate', el('span', { className: 'visually-hidden', text: ` ${name}` })]),
          ]),
        ]));
      });
      const hasCampaigns = campaigns.length > 0;
      list.hidden = !jsonOk || !hasCampaigns;
      empty.hidden = !jsonOk || hasCampaigns;
      createButtons.forEach((button) => {
        if (!empty.contains(button)) {
          button.hidden = !jsonOk || !hasCampaigns;
        }
      });
    }

    function readJson() {
      const parsed = parseCampaigns(textarea.value);
      jsonOk = parsed.ok;
      if (parsed.ok) {
        campaigns = parsed.value;
        setError('');
      } else {
        setError(parsed.message);
        if (advanced) {
          advanced.open = true;
        }
      }
      renderList();
    }

    list.addEventListener('click', (event) => {
      const edit = event.target.closest('[data-abc-edit]');
      const duplicate = event.target.closest('[data-abc-duplicate]');
      if (edit) {
        openEditor(Number(edit.dataset.abcEdit), 'edit', edit);
      } else if (duplicate) {
        openEditor(Number(duplicate.dataset.abcDuplicate), 'duplicate', duplicate);
      }
    });
    createButtons.forEach((button) => button.addEventListener('click', () => openEditor(-1, 'create', button)));
    textarea.addEventListener('input', readJson);

    // ----- editor ----------------------------------------------------------

    function currentMetric() {
      const checked = metricInputs.find((input) => input.checked);
      return checked ? checked.value : draft.metric;
    }

    function draftCampaign() {
      const campaign = { ...draft.extra };
      campaign.name = nameInput.value.trim();
      campaign.winner_metric = currentMetric();
      campaign.minimum_sample_size = Math.max(1, toInt(minimumInput.value, DEFAULT_MINIMUM));
      campaign.variants = draft.variants.map((variant) => {
        const out = { ...variant.extra, name: variant.name.trim() };
        COUNT_FIELDS.forEach((field) => {
          out[field] = toInt(variant[field], 0);
        });
        return out;
      });
      return campaign;
    }

    function renderVariants() {
      variantsWrap.textContent = '';
      draft.variants.forEach((variant, index) => {
        const label = variant.name.trim() || `Variant ${index + 1}`;
        variantsWrap.append(el('div', { className: 'abc-variant', role: 'row', dataset: { index: String(index) } }, [
          el('input', {
            className: 'form-input',
            role: 'cell',
            value: variant.name,
            maxlength: '255',
            placeholder: `Variant ${String.fromCharCode(65 + (index % 26))}`,
            'aria-label': `Variant ${index + 1} name`,
            dataset: { abcVariantField: 'name' },
          }),
          ...COUNT_FIELDS.map((field) => el('label', { className: 'abc-variant__cell', role: 'cell' }, [
            el('span', { className: 'abc-variant__cell-label', text: field.charAt(0).toUpperCase() + field.slice(1) }),
            el('input', {
              className: 'form-input',
              type: 'number',
              min: '0',
              step: '1',
              inputmode: 'numeric',
              value: String(variant[field]),
              'aria-label': `${label} ${field}`,
              dataset: { abcVariantField: field },
            }),
          ])),
          el('button', {
            type: 'button',
            role: 'cell',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove ${label}`,
            title: 'Remove variant',
            dataset: { abcVariantRemove: '' },
            text: '×',
          }),
        ]));
      });
      if (!draft.variants.length) {
        variantsWrap.append(el('p', { className: 'scf-options__empty text-muted', text: 'No variants yet. Add at least two to compare.' }));
      }
    }

    function renderPreview() {
      const result = evaluate(draftCampaign());
      preview.textContent = '';
      if (!result.variants.length) {
        preview.append(el('p', { className: 'text-muted', text: 'Variant results appear here.' }));
      }
      const top = Math.max(1, ...result.variants.map((variant) => variant.metrics[result.metric] || 0));
      result.variants.forEach((variant, index) => {
        const value = variant.metrics[result.metric] || 0;
        const isWinner = result.winner && result.winner === variant;
        preview.append(el('div', { className: `abc-bar${isWinner ? ' abc-bar--winner' : ''}${variant.eligible ? '' : ' abc-bar--waiting'}` }, [
          el('div', { className: 'abc-bar__label' }, [
            el('span', { text: variant.name || `Variant ${index + 1}` }),
            el('span', { className: 'abc-bar__value', text: `${value}%${isWinner ? ' · winner' : ''}` }),
          ]),
          el('div', { className: 'abc-bar__track', 'aria-hidden': 'true' }, [
            el('span', { className: 'abc-bar__fill', style: `width: ${Math.round((value / top) * 100)}%` }),
          ]),
          el('p', { className: 'abc-bar__meta text-muted', text: variant.eligible ? `${variant.sample} sends` : `${variant.sample} of ${result.minimum} sends needed` }),
        ]));
      });
      previewRules.textContent = '';
      const eligibleCount = result.variants.filter((variant) => variant.eligible).length;
      const rules = [
        ['logic', `Winner is the eligible variant with the highest ${METRIC_LABELS[result.metric] || result.metric}.`],
        result.winner
          ? ['visibility', `${result.winner.name || 'The top variant'} is winning.`]
          : ['', eligibleCount < 2 ? `Waiting for ${2 - eligibleCount} more variant${2 - eligibleCount === 1 ? '' : 's'} to reach ${result.minimum} sends.` : 'Collecting data.'],
      ];
      rules.forEach(([variant, text]) => {
        previewRules.append(el('li', { className: `scf-preview__rule${variant ? ` scf-preview__rule--${variant}` : ''}`, text }));
      });
    }

    function refresh(opts) {
      metricInputs.forEach((input) => input.closest('.scf-choice').classList.toggle('is-selected', input.checked));
      if (!(opts && opts.keepVariants)) {
        renderVariants();
      }
      if (editor.dataset.submitted === 'true') {
        showEditorError(validate());
      }
      renderPreview();
    }

    function addVariant(source) {
      const variant = { name: '', extra: {} };
      COUNT_FIELDS.forEach((field) => { variant[field] = 0; });
      if (source) {
        const { name, ...rest } = source;
        variant.name = String(name || '');
        COUNT_FIELDS.forEach((field) => {
          variant[field] = toInt(source[field], 0);
          delete rest[field];
        });
        variant.extra = rest;
      }
      draft.variants.push(variant);
      return variant;
    }

    variantsWrap.addEventListener('input', (event) => {
      const field = event.target.dataset.abcVariantField;
      const row = event.target.closest('.abc-variant');
      if (!field || !row) {
        return;
      }
      draft.variants[Number(row.dataset.index)][field] = event.target.value;
      refresh({ keepVariants: true });
    });

    variantsWrap.addEventListener('click', (event) => {
      const remove = event.target.closest('[data-abc-variant-remove]');
      if (!remove) {
        return;
      }
      const index = Number(remove.closest('.abc-variant').dataset.index);
      draft.variants.splice(index, 1);
      refresh();
      const next = variantsWrap.querySelector('[data-abc-variant-field="name"]') || modal.querySelector('[data-abc-variant-add]');
      next.focus();
    });

    modal.querySelector('[data-abc-variant-add]').addEventListener('click', () => {
      addVariant(null);
      refresh();
      const names = variantsWrap.querySelectorAll('[data-abc-variant-field="name"]');
      names[names.length - 1].focus();
    });

    [nameInput, minimumInput].forEach((input) => input.addEventListener('input', () => refresh({ keepVariants: true })));
    metricInputs.forEach((input) => input.addEventListener('change', () => refresh({ keepVariants: true })));

    function validate() {
      if (!nameInput.value.trim()) {
        return { message: 'Give the campaign a name.', focus: nameInput };
      }
      if (draft.variants.length < 2) {
        return { message: 'Add at least two variants to compare.', focus: modal.querySelector('[data-abc-variant-add]') };
      }
      const blank = draft.variants.findIndex((variant) => !variant.name.trim());
      if (blank >= 0) {
        return { message: 'Name every variant so you can tell them apart.', focus: variantsWrap.querySelectorAll('[data-abc-variant-field="name"]')[blank] };
      }
      return null;
    }

    function showEditorError(error) {
      editorError.textContent = error ? error.message : '';
      editorError.hidden = !error;
      nameInput.classList.toggle('is-invalid', Boolean(error && error.focus === nameInput));
    }

    function openEditor(index, mode, trigger) {
      const source = index >= 0 ? campaigns[index] : null;
      editingIndex = mode === 'edit' ? index : -1;
      const { name, winner_metric: metric, minimum_sample_size: minimum, variants, ...extra } = source || {};
      draft = { metric: String(metric || 'click_rate'), variants: [], extra };
      nameInput.value = mode === 'duplicate' ? `${name || 'Untitled campaign'} (copy)` : String(name || '');
      minimumInput.value = String(Math.max(1, toInt(minimum, DEFAULT_MINIMUM)));
      if (!metricInputs.some((input) => input.value === draft.metric)) {
        const custom = el('label', { className: 'scf-choice', dataset: { abcCustomMetric: '' } }, [
          el('input', { type: 'radio', name: 'abc_metric', value: draft.metric, dataset: { abcMetric: '' } }),
          el('span', {}, [el('strong', { text: METRIC_LABELS[draft.metric] || draft.metric }), el('span', { className: 'text-muted', text: 'Kept from the existing settings.' })]),
        ]);
        metricList.append(custom);
        const input = custom.querySelector('input');
        input.addEventListener('change', () => refresh({ keepVariants: true }));
        metricInputs.push(input);
      }
      metricInputs.forEach((input) => { input.checked = input.value === draft.metric; });
      (Array.isArray(variants) ? variants : []).forEach((variant) => {
        if (variant && typeof variant === 'object') {
          addVariant(variant);
        }
      });
      if (!source) {
        addVariant({ name: 'Variant A' });
        addVariant({ name: 'Variant B' });
      }
      modalTitle.textContent = mode === 'edit' ? 'Edit campaign' : (mode === 'duplicate' ? 'Duplicate campaign' : 'Add campaign');
      deleteButton.hidden = mode !== 'edit';
      editor.dataset.submitted = 'false';
      submitButton.disabled = false;
      submitButton.textContent = 'Save campaign';
      showEditorError(null);
      refresh();

      lastTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      snapshot = JSON.stringify(draftCampaign());
      window.requestAnimationFrame(() => nameInput.focus());
    }

    function closeEditor(force) {
      if (!force && JSON.stringify(draftCampaign()) !== snapshot && !window.confirm('Discard your unsaved changes to this campaign?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      modal.querySelectorAll('[data-abc-custom-metric]').forEach((node) => {
        metricInputs.splice(metricInputs.indexOf(node.querySelector('input')), 1);
        node.remove();
      });
      if (lastTrigger && typeof lastTrigger.focus === 'function' && lastTrigger.isConnected) {
        lastTrigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeEditor(false);
      });
    });

    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
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

    function saveAll(message) {
      textarea.value = JSON.stringify(campaigns, null, 2);
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
      if (status) {
        status.textContent = message;
      }
      if (typeof pageForm.requestSubmit === 'function') {
        pageForm.requestSubmit();
      } else {
        pageForm.submit();
      }
    }

    editor.addEventListener('submit', (event) => {
      event.preventDefault();
      editor.dataset.submitted = 'true';
      const error = validate();
      showEditorError(error);
      if (error) {
        if (error.focus) {
          error.focus.focus();
        }
        return;
      }
      const campaign = draftCampaign();
      if (editingIndex >= 0) {
        campaigns[editingIndex] = campaign;
      } else {
        campaigns.push(campaign);
      }
      saveAll(`Saving ${campaign.name}…`);
    });

    deleteButton.addEventListener('click', () => {
      if (editingIndex < 0) {
        return;
      }
      const name = String(campaigns[editingIndex].name || 'this campaign');
      if (!window.confirm(`Delete “${name}”? Its variant counts are removed from the settings.`)) {
        return;
      }
      campaigns.splice(editingIndex, 1);
      saveAll(`Deleting ${name}…`);
    });

    readJson();
    if (advanced && jsonOk) {
      advanced.open = false;
    }
  });
})();
