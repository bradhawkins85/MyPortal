/*
 * Tag picker
 * ----------
 * Enhances tags/_picker.html: shows a record's tags as chips, and lets editors
 * search the shared tag list, pick a tag, or create a new one by typing a name
 * that does not exist yet. Every change is saved straight away with
 * PUT <data-endpoint> {tag_ids}. Automatic tags (source "auto") are shown but
 * cannot be removed here; their rules add and remove them.
 */
(function () {
  'use strict';

  const MAX_OPTIONS = 50;

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  async function api(url, options) {
    const init = Object.assign({ credentials: 'same-origin', headers: {} }, options || {});
    init.headers = Object.assign({ Accept: 'application/json' }, init.headers);
    if (init.body && typeof init.body !== 'string') {
      init.body = JSON.stringify(init.body);
      init.headers['Content-Type'] = 'application/json';
    }
    if (init.method && init.method !== 'GET') {
      init.headers['X-CSRF-Token'] = csrfToken();
    }
    const response = await fetch(url, init);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
    }
    return data;
  }

  function fold(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
  }

  // The whole tag list is small, so it is loaded once per page and filtered here.
  let catalogue = null;
  async function loadCatalogue(force) {
    if (!catalogue || force) {
      catalogue = api('/api/tags?limit=500').then((data) => data.tags || []).catch((error) => {
        catalogue = null;
        throw error;
      });
    }
    return catalogue;
  }

  function chip(tag, removable, onRemove) {
    const item = document.createElement('li');
    item.className = 'tag-chip';
    item.style.setProperty('--tag-colour', tag.colour || 'var(--hf-accent)');
    if (tag.description) {
      item.title = tag.description;
    }
    const name = document.createElement('span');
    name.className = 'tag-chip__name';
    name.textContent = tag.name;
    item.appendChild(name);
    if (tag.source === 'auto') {
      const auto = document.createElement('span');
      auto.className = 'tag-chip__auto';
      auto.textContent = 'auto';
      auto.title = 'Assigned automatically';
      item.appendChild(auto);
    } else if (removable) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'tag-chip__remove';
      remove.setAttribute('aria-label', 'Remove tag ' + tag.name);
      remove.textContent = '\u00d7';
      remove.addEventListener('click', () => onRemove(tag));
      item.appendChild(remove);
    }
    return item;
  }

  function init(root) {
    if (root.dataset.tagPickerReady) {
      return;
    }
    root.dataset.tagPickerReady = 'true';
    const endpoint = root.dataset.endpoint;
    const editable = root.hasAttribute('data-editable');
    const chips = root.querySelector('[data-tag-picker-chips]');
    const none = root.querySelector('[data-tag-picker-none]');
    const search = root.querySelector('[data-tag-picker-search]');
    const input = root.querySelector('[data-tag-picker-input]');
    const list = root.querySelector('[data-tag-picker-list]');
    const status = root.querySelector('[data-tag-picker-status]');
    let tags = [];
    try {
      tags = JSON.parse(root.querySelector('[data-tag-picker-initial]').textContent || '[]');
    } catch (error) {
      tags = [];
    }
    let options = [];
    let active = -1;
    let busy = false;

    function setStatus(message, isError) {
      if (!status) {
        return;
      }
      status.textContent = message || '';
      status.classList.toggle('tag-picker__status--error', Boolean(isError));
    }

    function render() {
      chips.replaceChildren(...tags.map((tag) => chip(tag, editable, removeTag)));
      if (none) {
        none.hidden = tags.length > 0;
      }
    }

    function manualIds() {
      return tags.filter((tag) => tag.source !== 'auto').map((tag) => tag.id);
    }

    async function save(ids, message) {
      busy = true;
      setStatus('Saving…');
      try {
        const data = await api(endpoint, { method: 'PUT', body: { tag_ids: ids } });
        tags = data.tags || [];
        render();
        setStatus(message);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        busy = false;
      }
    }

    function removeTag(tag) {
      if (busy) {
        return;
      }
      save(manualIds().filter((id) => id !== tag.id), 'Removed ' + tag.name + '.');
      input && input.focus();
    }

    async function addTag(option) {
      if (busy) {
        return;
      }
      let tag = option.tag;
      if (!tag) {
        try {
          const data = await api('/api/tags', { method: 'POST', body: { name: option.name } });
          tag = data.tag;
          loadCatalogue(true).catch(() => {});
        } catch (error) {
          setStatus(error.message, true);
          return;
        }
      }
      input.value = '';
      close();
      if (tags.some((existing) => existing.id === tag.id)) {
        setStatus(tag.name + ' is already on this record.');
        return;
      }
      await save(manualIds().concat(tag.id), 'Added ' + tag.name + '.');
      input.focus();
    }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    }

    function highlight(index) {
      active = index;
      Array.from(list.children).forEach((item, position) => {
        item.setAttribute('aria-selected', position === index ? 'true' : 'false');
        if (position === index) {
          input.setAttribute('aria-activedescendant', item.id);
          item.scrollIntoView({ block: 'nearest' });
        }
      });
    }

    async function refreshOptions() {
      const query = fold(input.value);
      let all = [];
      try {
        all = await loadCatalogue();
      } catch (error) {
        setStatus(error.message, true);
      }
      const assigned = new Set(tags.map((tag) => tag.id));
      const matches = all.filter((tag) => !assigned.has(tag.id) && (!query || fold(tag.name).includes(query)));
      matches.sort((a, b) => {
        const aStarts = fold(a.name).startsWith(query) ? 0 : 1;
        const bStarts = fold(b.name).startsWith(query) ? 0 : 1;
        return aStarts - bStarts || fold(a.name).localeCompare(fold(b.name));
      });
      options = matches.slice(0, MAX_OPTIONS).map((tag) => ({ tag, name: tag.name }));
      const exact = all.some((tag) => fold(tag.name) === query);
      if (query && !exact) {
        options.push({ tag: null, name: input.value.trim().replace(/\s+/g, ' ') });
      }
      list.replaceChildren();
      options.forEach((option, index) => {
        const item = document.createElement('li');
        item.id = root.id + '-option-' + index;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', 'false');
        item.className = 'tag-picker__option' + (option.tag ? '' : ' tag-picker__option--create');
        item.textContent = option.tag ? option.tag.name : 'Create tag "' + option.name + '"';
        if (option.tag && option.tag.is_auto) {
          const hint = document.createElement('span');
          hint.className = 'tag-chip__auto';
          hint.textContent = 'also automatic';
          item.appendChild(hint);
        }
        // mousedown keeps focus in the input so the list does not close first.
        item.addEventListener('mousedown', (event) => {
          event.preventDefault();
          addTag(option);
        });
        list.appendChild(item);
      });
      if (!options.length) {
        const empty = document.createElement('li');
        empty.className = 'tag-picker__empty';
        empty.textContent = query ? 'That tag is already on this record.' : 'No more tags. Type a name to create one.';
        list.appendChild(empty);
      }
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      highlight(options.length ? 0 : -1);
    }

    render();
    if (!editable || !input) {
      return;
    }
    search.hidden = false;
    input.addEventListener('focus', refreshOptions);
    input.addEventListener('input', refreshOptions);
    input.addEventListener('blur', () => window.setTimeout(close, 100));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (list.hidden) {
          refreshOptions();
          return;
        }
        if (options.length) {
          const step = event.key === 'ArrowDown' ? 1 : -1;
          highlight((active + step + options.length) % options.length);
        }
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (!list.hidden && active >= 0 && options[active]) {
          addTag(options[active]);
        }
      } else if (event.key === 'Escape') {
        if (!list.hidden) {
          event.preventDefault();
          close();
        }
      }
    });
  }

  function initAll(scope) {
    (scope || document).querySelectorAll('[data-tag-picker]').forEach(init);
  }

  window.TagPicker = { init: initAll };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initAll());
  } else {
    initAll();
  }
})();
