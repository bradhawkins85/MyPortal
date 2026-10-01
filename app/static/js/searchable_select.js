/*
 * Searchable selects
 * ------------------
 * Platform-wide progressive enhancement that replaces long scrolling
 * dropdowns and Ctrl/Cmd+click multi-selects with a type-to-search box,
 * modelled on the shop product up-sell picker (search, pick, show chips).
 *
 * - <select multiple> is always enhanced: chosen options appear as removable
 *   chips and new ones are added by searching.
 * - Single <select> elements are enhanced once they hold more than
 *   THRESHOLD options (or use size="N" listbox mode), i.e. whenever the user
 *   would otherwise have to scroll to find a value.
 * - Opt out with data-searchable="off"; force with data-searchable="on";
 *   tune per element with data-searchable-threshold="N".
 *
 * The native <select> stays in the DOM (visually hidden) and remains the
 * source of truth, so form submission, FormData, existing scripts reading
 * .value/.selectedOptions and "change" listeners keep working unchanged.
 * Programmatic changes (select.value = x, option.selected = true, replacing
 * options, toggling disabled/hidden) are picked up automatically.
 */
(function () {
  'use strict';

  if (window.SearchableSelect) {
    return;
  }

  const THRESHOLD = 10;
  const MAX_RENDERED = 300;
  const STYLE_CLASS_PATTERN = /^(form-input(--[\w-]+)?|input(--[\w-]+)?|form-select|form-control|[\w-]+__select)$/;
  const instances = new WeakMap();
  const pending = new Set();
  let flushScheduled = false;
  let uid = 0;

  // -------------------------------------------------------------------------
  // Keep enhanced controls in sync with programmatic changes to the select.
  // -------------------------------------------------------------------------

  function scheduleRefresh(select) {
    if (!select || !instances.has(select)) {
      return;
    }
    pending.add(select);
    if (!flushScheduled) {
      flushScheduled = true;
      queueMicrotask(() => {
        flushScheduled = false;
        const items = Array.from(pending);
        pending.clear();
        items.forEach((item) => {
          const instance = instances.get(item);
          if (instance) {
            instance.sync();
          }
        });
      });
    }
  }

  function wrapSetter(proto, prop, resolveSelect) {
    const descriptor = Object.getOwnPropertyDescriptor(proto, prop);
    if (!descriptor || !descriptor.set || !descriptor.configurable) {
      return;
    }
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: descriptor.enumerable,
      get: descriptor.get,
      set(value) {
        descriptor.set.call(this, value);
        scheduleRefresh(resolveSelect(this));
      },
    });
  }

  wrapSetter(HTMLSelectElement.prototype, 'value', (el) => el);
  wrapSetter(HTMLSelectElement.prototype, 'selectedIndex', (el) => el);
  wrapSetter(HTMLOptionElement.prototype, 'selected', (el) => el.closest('select'));

  document.addEventListener('reset', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    window.setTimeout(() => {
      form.querySelectorAll('select').forEach(scheduleRefresh);
    }, 0);
  }, true);

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  function normalise(text) {
    return String(text || '')
      .toLocaleLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function optionLabel(option) {
    return (option.label || option.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function countRealOptions(select) {
    let count = 0;
    for (const option of select.options) {
      if (option.value !== '') {
        count += 1;
      }
    }
    return count;
  }

  function findLabelText(select) {
    if (select.getAttribute('aria-label')) {
      return select.getAttribute('aria-label');
    }
    if (select.labels && select.labels.length) {
      return select.labels[0].textContent.replace(/\s+/g, ' ').trim();
    }
    return '';
  }

  function isHiddenBySelf(select) {
    if (select.hidden) {
      return true;
    }
    return select.style.display === 'none' || select.style.visibility === 'hidden';
  }

  function shouldEnhance(select) {
    if (!(select instanceof HTMLSelectElement) || instances.has(select)) {
      return false;
    }
    const mode = (select.dataset.searchable || '').toLowerCase();
    if (mode === 'off' || mode === 'false' || mode === 'native') {
      return false;
    }
    // Controls that already ship their own searchable UI.
    if (select.hasAttribute('data-kb-picklist') || select.closest('[data-searchable-scope="off"]')) {
      return false;
    }
    if (!select.isConnected || select.closest('template')) {
      return false;
    }
    if (mode === 'on' || mode === 'true') {
      return true;
    }
    if (select.multiple) {
      return true;
    }
    if (select.size > 1) {
      return true;
    }
    const threshold = Number.parseInt(select.dataset.searchableThreshold || '', 10);
    const limit = Number.isFinite(threshold) ? threshold : THRESHOLD;
    return countRealOptions(select) > limit;
  }

  // -------------------------------------------------------------------------
  // Component
  // -------------------------------------------------------------------------

  function enhance(select) {
    if (!shouldEnhance(select)) {
      return null;
    }

    const multiple = select.multiple;
    const id = ++uid;
    const listId = `ss-list-${id}`;

    const root = document.createElement('div');
    root.className = 'ss';
    root.classList.add(multiple ? 'ss--multiple' : 'ss--single');
    // Carry over presentational classes only; behavioural hooks stay on the
    // native <select> so existing querySelector() calls keep finding it.
    select.classList.forEach((cls) => {
      if (STYLE_CLASS_PATTERN.test(cls) && cls !== 'form-input--multiselect') {
        root.classList.add(cls);
      }
    });
    if (select.style.width) {
      root.style.width = select.style.width;
    }
    if (select.style.minWidth) {
      root.style.minWidth = select.style.minWidth;
    }
    if (select.style.maxWidth) {
      root.style.maxWidth = select.style.maxWidth;
    }

    const chips = document.createElement('ul');
    chips.className = 'ss__chips';
    chips.setAttribute('role', 'list');

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'ss__input';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);
    const labelText = findLabelText(select);
    if (labelText) {
      input.setAttribute('aria-label', labelText);
    }
    if (select.id) {
      // Clicking the original <label for> should focus the search box.
      input.id = `${select.id}__search`;
      if (select.labels) {
        Array.from(select.labels).forEach((label) => {
          label.addEventListener('click', (event) => {
            event.preventDefault();
            input.focus();
          });
        });
      }
    }

    const toggle = document.createElement('span');
    toggle.className = 'ss__chevron';
    toggle.setAttribute('aria-hidden', 'true');

    const clearButton = document.createElement('button');
    clearButton.type = 'button';
    clearButton.className = 'ss__clear';
    clearButton.setAttribute('aria-label', 'Clear selection');
    clearButton.textContent = '×';
    clearButton.tabIndex = -1;

    // The panel is portalled to <body> (or the enclosing open <dialog>, to
    // stay in the top layer) while open so overflow/transform on ancestors
    // such as modals and cards cannot clip it.
    const panel = document.createElement('div');
    panel.className = `ss__panel ${multiple ? 'ss__panel--multiple' : 'ss__panel--single'}`;
    panel.hidden = true;

    const list = document.createElement('ul');
    list.className = 'ss__list';
    list.id = listId;
    list.setAttribute('role', 'listbox');
    if (multiple) {
      list.setAttribute('aria-multiselectable', 'true');
    }
    if (labelText) {
      list.setAttribute('aria-label', labelText);
    }

    const status = document.createElement('div');
    status.className = 'ss__status';
    status.setAttribute('aria-live', 'polite');

    panel.append(list, status);

    if (multiple) {
      root.append(chips);
    }
    root.append(input, clearButton, toggle);

    select.classList.add('ss-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    select.insertAdjacentElement('afterend', root);

    let open = false;
    let activeIndex = -1;
    let visible = [];

    function placeholderText() {
      if (select.dataset.searchablePlaceholder) {
        return select.dataset.searchablePlaceholder;
      }
      if (select.getAttribute('placeholder')) {
        return select.getAttribute('placeholder');
      }
      if (multiple) {
        return 'Search to add…';
      }
      const empty = Array.from(select.options).find((option) => option.value === '');
      return empty ? optionLabel(empty) || 'Search…' : 'Search…';
    }

    function selectedLabel() {
      const option = select.options[select.selectedIndex];
      if (!option) {
        return '';
      }
      return option.value === '' ? '' : optionLabel(option);
    }

    function renderChips() {
      chips.innerHTML = '';
      Array.from(select.options).forEach((option) => {
        if (!option.selected) {
          return;
        }
        const chip = document.createElement('li');
        chip.className = 'tag ss__chip';
        const text = document.createElement('span');
        text.textContent = optionLabel(option);
        chip.append(text);
        if (!select.disabled && !option.disabled) {
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'tag__remove';
          remove.setAttribute('aria-label', `Remove ${optionLabel(option)}`);
          remove.textContent = '×';
          remove.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            setOptionSelected(option, false);
            input.focus();
          });
          chip.append(remove);
        }
        chips.append(chip);
      });
    }

    function renderClosedInput() {
      if (multiple) {
        input.placeholder = placeholderText();
        return;
      }
      if (document.activeElement === input && open) {
        return;
      }
      input.value = selectedLabel();
      input.placeholder = placeholderText();
    }

    function syncState() {
      const disabled = select.disabled || Boolean(select.closest('fieldset[disabled]'));
      root.classList.toggle('ss--disabled', disabled);
      input.disabled = disabled;
      root.hidden = isHiddenBySelf(select);
      const hasValue = multiple ? select.selectedOptions.length > 0 : select.value !== '';
      root.classList.toggle('ss--has-value', hasValue);
      const clearable = !disabled && hasValue && (multiple || Array.from(select.options).some((option) => option.value === '' && !option.disabled));
      clearButton.hidden = !clearable;
      if (select.title) {
        root.title = select.title;
      }
    }

    function sync() {
      syncState();
      if (multiple) {
        renderChips();
      }
      renderClosedInput();
      if (open) {
        renderList();
      }
    }

    function fireChange() {
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setOptionSelected(option, selected) {
      if (option.selected === selected) {
        return;
      }
      option.selected = selected;
      root.classList.remove('ss--invalid');
      sync();
      fireChange();
    }

    function chooseOption(option) {
      if (!option || option.disabled) {
        return;
      }
      if (multiple) {
        setOptionSelected(option, !option.selected);
        input.value = '';
        renderList();
        return;
      }
      const changed = select.value !== option.value || !option.selected;
      option.selected = true;
      root.classList.remove('ss--invalid');
      close();
      input.value = selectedLabel();
      syncState();
      if (changed) {
        fireChange();
      }
    }

    function collectMatches(query) {
      const term = normalise(query);
      const tokens = term ? term.split(' ') : [];
      const results = [];
      Array.from(select.children).forEach((child) => {
        if (child instanceof HTMLOptGroupElement) {
          const groupLabel = child.label || '';
          const groupMatches = Array.from(child.children).filter((option) => (
            option instanceof HTMLOptionElement && matches(option, tokens, groupLabel)
          ));
          if (groupMatches.length) {
            results.push({ group: groupLabel });
            groupMatches.forEach((option) => results.push({ option }));
          }
        } else if (child instanceof HTMLOptionElement && matches(child, tokens, '')) {
          results.push({ option: child });
        }
      });
      return results;
    }

    function matches(option, tokens, groupLabel) {
      if (option.hidden && !option.selected) {
        return false;
      }
      if (option.value === '' && (multiple || tokens.length)) {
        return false;
      }
      if (!tokens.length) {
        return true;
      }
      const haystack = normalise(`${optionLabel(option)} ${option.value} ${groupLabel} ${option.dataset.searchTerms || ''}`);
      return tokens.every((token) => haystack.includes(token));
    }

    function renderList() {
      const query = multiple || input.dataset.ssSearching === 'true' ? input.value : '';
      const results = collectMatches(query);
      list.innerHTML = '';
      visible = [];
      let rendered = 0;
      let truncated = false;
      for (const entry of results) {
        if (rendered >= MAX_RENDERED) {
          truncated = true;
          break;
        }
        if (entry.group !== undefined) {
          const heading = document.createElement('li');
          heading.className = 'ss__group';
          heading.setAttribute('role', 'presentation');
          heading.textContent = entry.group;
          list.append(heading);
          continue;
        }
        const option = entry.option;
        const item = document.createElement('li');
        item.className = 'ss__option';
        item.id = `${listId}-${visible.length}`;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', option.selected ? 'true' : 'false');
        if (option.disabled) {
          item.setAttribute('aria-disabled', 'true');
          item.classList.add('ss__option--disabled');
        }
        if (option.selected) {
          item.classList.add('ss__option--selected');
        }
        if (option.value === '') {
          item.classList.add('ss__option--placeholder');
        }
        item.textContent = optionLabel(option) || ' ';
        item.addEventListener('mousedown', (event) => {
          event.preventDefault();
        });
        item.addEventListener('click', () => {
          chooseOption(option);
        });
        const index = visible.length;
        item.addEventListener('mousemove', () => {
          if (activeIndex !== index) {
            setActive(index, false);
          }
        });
        visible.push({ option, item });
        list.append(item);
        rendered += 1;
      }
      if (!visible.length) {
        status.textContent = input.value.trim() ? `No matches for “${input.value.trim()}”` : 'No options available';
      } else if (truncated) {
        status.textContent = `Showing first ${MAX_RENDERED} of ${results.filter((entry) => entry.option).length} — keep typing to narrow down`;
      } else {
        status.textContent = '';
      }
      status.hidden = !status.textContent;

      if (!visible.length) {
        activeIndex = -1;
        input.removeAttribute('aria-activedescendant');
        return;
      }
      let start = visible.findIndex((entry) => !entry.option.disabled && (multiple ? true : entry.option.selected));
      if (input.value.trim() || start < 0) {
        start = visible.findIndex((entry) => !entry.option.disabled);
      }
      setActive(start, true);
    }

    function setActive(index, scroll) {
      visible.forEach((entry, position) => {
        entry.item.classList.toggle('ss__option--active', position === index);
      });
      activeIndex = index;
      const entry = visible[index];
      if (entry) {
        input.setAttribute('aria-activedescendant', entry.item.id);
        if (scroll) {
          const item = entry.item;
          if (item.offsetTop < list.scrollTop) {
            list.scrollTop = item.offsetTop;
          } else if (item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight) {
            list.scrollTop = item.offsetTop + item.offsetHeight - list.clientHeight;
          }
        }
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function moveActive(delta) {
      if (!visible.length) {
        return;
      }
      let index = activeIndex;
      for (let step = 0; step < visible.length; step += 1) {
        index = (index + delta + visible.length) % visible.length;
        if (!visible[index].option.disabled) {
          break;
        }
      }
      setActive(index, true);
    }

    function position(event) {
      if (!open || (event && event.target instanceof Node && panel.contains(event.target))) {
        return;
      }
      if (!root.isConnected || !root.getClientRects().length) {
        close();
        return;
      }
      const rect = root.getBoundingClientRect();
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
      const below = viewportHeight - rect.bottom - 8;
      const above = rect.top - 8;
      const preferred = 320;
      const placeAbove = below < Math.min(preferred, 180) && above > below;
      const maxHeight = Math.max(120, Math.min(preferred, placeAbove ? above : below));
      const width = Math.max(rect.width, 220);
      let left = rect.left;
      if (left + width > viewportWidth - 8) {
        left = Math.max(8, viewportWidth - width - 8);
      }
      panel.style.maxHeight = `${maxHeight}px`;
      panel.style.width = `${width}px`;
      panel.style.left = `${left}px`;
      panel.style.top = placeAbove ? 'auto' : `${rect.bottom + 4}px`;
      panel.style.bottom = placeAbove ? `${viewportHeight - rect.top + 4}px` : 'auto';
      panel.classList.toggle('ss__panel--above', placeAbove);
      // Correct for ancestors that create a containing block for fixed
      // elements (transforms, filters), e.g. animated modals.
      const actual = panel.getBoundingClientRect();
      const dx = actual.left - left;
      if (Math.abs(dx) > 1) {
        panel.style.left = `${left - dx}px`;
      }
      if (!placeAbove) {
        const dy = actual.top - (rect.bottom + 4);
        if (Math.abs(dy) > 1) {
          panel.style.top = `${rect.bottom + 4 - dy}px`;
        }
      }
    }

    function openPanel() {
      if (open || input.disabled) {
        return;
      }
      open = true;
      root.classList.add('ss--open');
      input.setAttribute('aria-expanded', 'true');
      const host = root.closest('dialog[open]') || document.body;
      if (panel.parentNode !== host) {
        host.append(panel);
      }
      panel.hidden = false;
      renderList();
      position();
      window.addEventListener('scroll', position, true);
      window.addEventListener('resize', position);
    }

    function close() {
      if (!open) {
        return;
      }
      open = false;
      root.classList.remove('ss--open');
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      input.dataset.ssSearching = 'false';
      panel.hidden = true;
      panel.remove();
      window.removeEventListener('scroll', position, true);
      window.removeEventListener('resize', position);
    }

    function restoreInput() {
      if (multiple) {
        input.value = '';
      } else {
        input.value = selectedLabel();
      }
    }

    // --- Events ------------------------------------------------------------

    root.addEventListener('mousedown', (event) => {
      if (input.disabled || event.target.closest('.tag__remove') || event.target === clearButton) {
        return;
      }
      if (event.target !== input) {
        event.preventDefault();
        input.focus();
      }
      if (open && event.target !== input) {
        close();
        return;
      }
      openPanel();
    });

    // Keep focus in the search box while interacting with the panel.
    panel.addEventListener('mousedown', (event) => {
      event.preventDefault();
    });

    input.addEventListener('focus', () => {
      root.classList.add('ss--focused');
      if (!multiple) {
        input.select();
      }
    });

    input.addEventListener('blur', () => {
      root.classList.remove('ss--focused');
      close();
      restoreInput();
    });

    input.addEventListener('input', () => {
      input.dataset.ssSearching = 'true';
      if (!open) {
        openPanel();
      } else {
        renderList();
      }
    });

    input.addEventListener('keydown', (event) => {
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          if (!open) {
            openPanel();
          } else {
            moveActive(1);
          }
          break;
        case 'ArrowUp':
          event.preventDefault();
          if (!open) {
            openPanel();
          } else {
            moveActive(-1);
          }
          break;
        case 'Home':
          if (open && !input.value) {
            event.preventDefault();
            setActive(0, true);
          }
          break;
        case 'End':
          if (open && !input.value) {
            event.preventDefault();
            setActive(visible.length - 1, true);
          }
          break;
        case 'Enter':
          if (open) {
            event.preventDefault();
            const entry = visible[activeIndex];
            if (entry) {
              chooseOption(entry.option);
            }
          } else if (multiple) {
            event.preventDefault();
            openPanel();
          }
          break;
        case 'Escape':
          if (open) {
            event.preventDefault();
            event.stopPropagation();
            close();
            restoreInput();
            if (!multiple) {
              input.select();
            }
          }
          break;
        case 'Tab':
          close();
          break;
        case 'Backspace':
          if (multiple && !input.value) {
            const selected = Array.from(select.selectedOptions).filter((option) => !option.disabled);
            const last = selected[selected.length - 1];
            if (last) {
              event.preventDefault();
              setOptionSelected(last, false);
            }
          }
          break;
        default:
          break;
      }
    });

    clearButton.addEventListener('mousedown', (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    clearButton.addEventListener('click', (event) => {
      event.preventDefault();
      let changed = false;
      if (multiple) {
        Array.from(select.options).forEach((option) => {
          if (option.selected && !option.disabled) {
            option.selected = false;
            changed = true;
          }
        });
      } else {
        const empty = Array.from(select.options).find((option) => option.value === '' && !option.disabled);
        if (empty && !empty.selected) {
          empty.selected = true;
          changed = true;
        }
      }
      input.value = '';
      sync();
      if (changed) {
        fireChange();
      }
      input.focus();
    });

    select.addEventListener('change', () => scheduleRefresh(select));
    select.addEventListener('invalid', () => {
      root.classList.add('ss--invalid');
      if (!document.querySelector('.ss--invalid .ss__input:focus')) {
        input.focus({ preventScroll: false });
      }
    });
    select.addEventListener('focus', () => {
      input.focus();
    });

    const observer = new MutationObserver(() => scheduleRefresh(select));
    observer.observe(select, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });

    const instance = {
      root,
      input,
      sync,
      close,
      open: openPanel,
      destroy() {
        close();
        observer.disconnect();
        root.remove();
        select.classList.remove('ss-native');
        select.removeAttribute('aria-hidden');
        select.removeAttribute('tabindex');
        instances.delete(select);
      },
    };
    instances.set(select, instance);
    sync();
    return instance;
  }

  // -------------------------------------------------------------------------
  // Discovery
  // -------------------------------------------------------------------------

  function scan(scope) {
    const base = scope && scope.querySelectorAll ? scope : document;
    if (base instanceof HTMLSelectElement) {
      const existing = instances.get(base);
      if (existing && base.nextElementSibling !== existing.root) {
        base.insertAdjacentElement('afterend', existing.root);
      }
      enhance(base);
      return;
    }
    base.querySelectorAll('select').forEach((select) => {
      const existing = instances.get(select);
      if (existing) {
        // Keep the control beside its select if the select was moved.
        if (select.nextElementSibling !== existing.root) {
          select.insertAdjacentElement('afterend', existing.root);
        }
        return;
      }
      enhance(select);
    });
  }

  const candidates = new Set();
  let scanScheduled = false;

  function queueCandidate(node) {
    candidates.add(node);
    if (scanScheduled) {
      return;
    }
    scanScheduled = true;
    window.requestAnimationFrame(() => {
      scanScheduled = false;
      const nodes = Array.from(candidates);
      candidates.clear();
      nodes.forEach((candidate) => {
        if (candidate.isConnected) {
          scan(candidate);
        }
      });
    });
  }

  function watchDocument() {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type !== 'childList') {
          return;
        }
        // New selects, or options added to a not-yet-enhanced select that
        // has now grown past the threshold.
        const target = mutation.target;
        if (target instanceof HTMLSelectElement || target instanceof HTMLOptGroupElement) {
          const select = target instanceof HTMLSelectElement ? target : target.parentElement;
          if (select instanceof HTMLSelectElement && !instances.has(select)) {
            queueCandidate(select);
          }
          return;
        }
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType !== Node.ELEMENT_NODE) {
            return;
          }
          if (node instanceof HTMLSelectElement || node.querySelector('select')) {
            queueCandidate(node);
          }
        });
        mutation.removedNodes.forEach((node) => {
          if (node.nodeType !== Node.ELEMENT_NODE) {
            return;
          }
          const selects = node instanceof HTMLSelectElement ? [node] : Array.from(node.querySelectorAll('select'));
          selects.forEach((select) => {
            const instance = instances.get(select);
            if (instance && !select.isConnected) {
              // Removed (or replaced) for good: drop the orphaned control.
              // A select that is merely moved is reconnected by now and is
              // re-enhanced by the added-node scan instead.
              instance.destroy();
            }
          });
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    scan(document);
    watchDocument();
  }

  window.SearchableSelect = {
    enhance(select) {
      if (select && select.dataset && !select.dataset.searchable) {
        select.dataset.searchable = 'on';
      }
      return enhance(select);
    },
    refresh(select) {
      const instance = instances.get(select);
      if (instance) {
        instance.sync();
      }
    },
    scan,
    get(select) {
      return instances.get(select) || null;
    },
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
