(function () {
  'use strict';

  const DEBOUNCE_MS = 600;
  const MIN_QUERY_LENGTH = 12;
  const TYPE_LABELS = {
    knowledge_base: 'Knowledge base article',
    ticket: 'Resolved ticket',
  };

  function isLocalPath(url) {
    return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//');
  }

  function init() {
    const form = document.querySelector('[data-user-ticket-form]');
    const panel = document.querySelector('[data-ticket-suggestions]');
    if (!form || !panel) {
      return;
    }
    const list = panel.querySelector('[data-ticket-suggestions-list]');
    const resolvedNote = panel.querySelector('[data-ticket-suggestions-resolved]');
    const subjectInput = form.querySelector('input[name="subject"]');
    const descriptionSurface = form.querySelector('[data-rich-text-content]');
    if (!list || !subjectInput) {
      return;
    }

    const dismissed = new Set();
    let timer = null;
    let lastQuery = '';
    let controller = null;

    function readDraft() {
      return {
        subject: subjectInput.value.trim(),
        description: descriptionSurface ? descriptionSurface.textContent.trim() : '',
      };
    }

    function sendFeedback(item, helpful) {
      fetch('/tickets/suggestions/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: item.type, id: item.id, helpful }),
      }).catch(() => {});
    }

    function render(items) {
      list.replaceChildren();
      const visible = items.filter((item) => isLocalPath(item.url) && !dismissed.has(`${item.type}:${item.id}`));
      visible.forEach((item) => {
        const entry = document.createElement('li');
        entry.className = 'ticket-suggestions__item';

        const link = document.createElement('a');
        link.href = item.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = item.title;
        entry.appendChild(link);

        const meta = document.createElement('p');
        meta.className = 'ticket-suggestions__meta';
        meta.textContent = TYPE_LABELS[item.type] || 'Suggestion';
        entry.appendChild(meta);

        if (item.summary && item.type === 'knowledge_base') {
          const summary = document.createElement('p');
          summary.className = 'ticket-suggestions__summary';
          summary.textContent = item.summary;
          entry.appendChild(summary);
        }

        const actions = document.createElement('div');
        actions.className = 'ticket-suggestions__actions';
        const prompt = document.createElement('span');
        prompt.textContent = 'Did this fix it?';
        actions.appendChild(prompt);

        const yes = document.createElement('button');
        yes.type = 'button';
        yes.className = 'button button--small button--primary';
        yes.textContent = 'Yes';
        yes.addEventListener('click', () => {
          sendFeedback(item, true);
          list.replaceChildren();
          if (resolvedNote) {
            resolvedNote.hidden = false;
          }
        });
        actions.appendChild(yes);

        const no = document.createElement('button');
        no.type = 'button';
        no.className = 'button button--small button--ghost';
        no.textContent = 'No';
        no.addEventListener('click', () => {
          sendFeedback(item, false);
          dismissed.add(`${item.type}:${item.id}`);
          entry.remove();
          if (!list.children.length) {
            panel.hidden = true;
          }
        });
        actions.appendChild(no);

        entry.appendChild(actions);
        list.appendChild(entry);
      });
      if (resolvedNote) {
        resolvedNote.hidden = true;
      }
      panel.hidden = visible.length === 0;
    }

    async function refresh() {
      const draft = readDraft();
      const query = `${draft.subject}\n${draft.description}`.trim();
      if (query.length < MIN_QUERY_LENGTH) {
        lastQuery = '';
        panel.hidden = true;
        return;
      }
      if (query === lastQuery) {
        return;
      }
      lastQuery = query;
      if (controller) {
        controller.abort();
      }
      controller = new AbortController();
      try {
        const response = await fetch('/tickets/suggestions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(draft),
          signal: controller.signal,
        });
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        render(Array.isArray(data.suggestions) ? data.suggestions : []);
      } catch (error) {
        // Suggestions are best effort; the ticket form keeps working without them.
      }
    }

    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, DEBOUNCE_MS);
    }

    subjectInput.addEventListener('input', schedule);
    if (descriptionSurface) {
      descriptionSurface.addEventListener('input', schedule);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
