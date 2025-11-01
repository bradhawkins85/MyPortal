(function () {
  'use strict';

  function escapeHtml(value) {
    if (value == null) {
      return '';
    }
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderSimpleText(container, text) {
    if (!container) {
      return;
    }
    container.innerHTML = '';
    if (!text) {
      container.hidden = true;
      return;
    }
    const paragraph = document.createElement('p');
    paragraph.className = 'agent-answer__paragraph';
    paragraph.innerHTML = escapeHtml(text).replace(/\n{2,}/g, '</p><p class="agent-answer__paragraph">').replace(/\n/g, '<br />');
    container.appendChild(paragraph);
    container.hidden = false;
  }

  function createSourceList(title, items, formatter) {
    if (!items || items.length === 0) {
      return null;
    }
    const section = document.createElement('section');
    section.className = 'agent-sources__group';
    const heading = document.createElement('h4');
    heading.className = 'agent-sources__title';
    heading.textContent = title;
    section.appendChild(heading);

    const list = document.createElement('ul');
    list.className = 'agent-sources__list';
    items.forEach((item) => {
      const entry = document.createElement('li');
      entry.className = 'agent-sources__item';
      entry.innerHTML = formatter(item);
      list.appendChild(entry);
    });
    section.appendChild(list);
    return section;
  }

  function formatKnowledgeBaseSource(item) {
    const title = escapeHtml(item.title || item.slug);
    const slug = escapeHtml(item.slug);
    const summary = escapeHtml(item.summary || item.excerpt || '');
    const url = item.url ? escapeHtml(item.url) : null;
    const linkStart = url ? `<a href="${url}">` : '';
    const linkEnd = url ? '</a>' : '';
    return `${linkStart}[KB:${slug}] ${title}${linkEnd}${summary ? `<div class="agent-sources__meta">${summary}</div>` : ''}`;
  }

  function formatTicketSource(item) {
    const id = escapeHtml(item.id);
    const subject = escapeHtml(item.subject || `Ticket #${item.id}`);
    const status = escapeHtml(item.status || 'unknown');
    const priority = escapeHtml(item.priority || 'normal');
    const summary = escapeHtml(item.summary || '');
    return `[#${id}] ${subject}<div class="agent-sources__meta">Status: ${status} • Priority: ${priority}${summary ? `<br />${summary}` : ''}</div>`;
  }

  function formatProductSource(item) {
    const sku = item.sku ? escapeHtml(item.sku) : null;
    const name = escapeHtml(item.name || (sku ? sku : 'Product'));
    const price = item.price ? escapeHtml(item.price) : null;
    const description = item.description ? escapeHtml(item.description) : null;
    const recommendations = Array.isArray(item.recommendations) && item.recommendations.length
      ? item.recommendations.map(escapeHtml).join(', ')
      : null;
    const label = sku ? `[${sku}] ${name}` : name;
    const metaParts = [];
    if (price) {
      metaParts.push(`Price: ${price}`);
    }
    if (description) {
      metaParts.push(description);
    }
    if (recommendations) {
      metaParts.push(`Recommended with: ${recommendations}`);
    }
    const meta = metaParts.length ? `<div class="agent-sources__meta">${metaParts.join('<br />')}</div>` : '';
    return `${label}${meta}`;
  }

  function renderSources(container, sources) {
    if (!container) {
      return;
    }
    container.innerHTML = '';
    if (!sources) {
      container.hidden = true;
      return;
    }

    const groups = [];
    if (Array.isArray(sources.knowledge_base)) {
      groups.push(createSourceList('Knowledge base', sources.knowledge_base, formatKnowledgeBaseSource));
    }
    if (Array.isArray(sources.tickets)) {
      groups.push(createSourceList('Tickets', sources.tickets, formatTicketSource));
    }
    if (Array.isArray(sources.products)) {
      groups.push(createSourceList('Products', sources.products, formatProductSource));
    }

    const usable = groups.filter((group) => group);
    if (usable.length === 0) {
      container.hidden = true;
      return;
    }
    usable.forEach((group) => container.appendChild(group));
    container.hidden = false;
  }

  function formatStatus(result) {
    if (!result || typeof result !== 'object') {
      return '';
    }
    const parts = [];
    if (result.status) {
      const statusText = String(result.status).toLowerCase();
      if (statusText === 'succeeded') {
        parts.push('Answer generated successfully.');
      } else if (statusText === 'skipped') {
        parts.push('The Ollama module is disabled; showing recent context.');
      } else {
        parts.push('The agent could not generate a response.');
      }
    }
    if (result.model) {
      parts.push(`Model: ${result.model}`);
    }
    if (result.generated_at) {
      const generatedDate = new Date(result.generated_at);
      if (!Number.isNaN(generatedDate.getTime())) {
        parts.push(`Generated at ${generatedDate.toLocaleString()}`);
      }
    }
    if (result.event_id) {
      parts.push(`Webhook event #${result.event_id}`);
    }
    if (result.message) {
      parts.push(result.message);
    }
    return parts.join(' ');
  }

  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.querySelector('[data-agent-panel]');
    if (!panel) {
      return;
    }

    const form = panel.querySelector('[data-agent-form]');
    const input = panel.querySelector('[data-agent-input]');
    const submitButton = panel.querySelector('[data-agent-submit]');
    const status = panel.querySelector('[data-agent-status]');
    const results = panel.querySelector('[data-agent-results]');
    const answer = panel.querySelector('[data-agent-answer]');
    const answerBody = panel.querySelector('[data-agent-answer-body]');
    const sources = panel.querySelector('[data-agent-sources]');
    const sourcesLists = panel.querySelector('[data-agent-source-lists]');

    if (!form || !input) {
      return;
    }

    const defaultStatus = 'Enter a question to ask the agent.';
    if (status) {
      status.textContent = defaultStatus;
    }

    function setBusy(isBusy) {
      if (submitButton) {
        submitButton.disabled = isBusy;
      }
      if (input) {
        input.disabled = isBusy;
      }
      panel.classList.toggle('agent-panel--busy', Boolean(isBusy));
    }

    function resetResults() {
      if (answer) {
        answer.hidden = true;
      }
      if (answerBody) {
        answerBody.textContent = '';
      }
      if (sources) {
        sources.hidden = true;
      }
      if (sourcesLists) {
        sourcesLists.innerHTML = '';
      }
      if (results) {
        results.hidden = true;
      }
    }

    async function handleSubmit(event) {
      event.preventDefault();
      const query = input.value.trim();
      if (!query) {
        if (status) {
          status.textContent = 'Please enter a question for the agent.';
        }
        return;
      }

      setBusy(true);
      resetResults();
      if (status) {
        status.textContent = 'Contacting the agent…';
      }

      try {
        const response = await fetch('/api/agent/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query }),
        });

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `Request failed with status ${response.status}`);
        }

        const payload = await response.json();
        if (status) {
          status.textContent = formatStatus(payload) || defaultStatus;
        }

        if (payload && typeof payload === 'object') {
          if (payload.answer) {
            renderSimpleText(answerBody, payload.answer);
            if (answer) {
              answer.hidden = false;
            }
          } else if (answer) {
            answer.hidden = true;
          }

          if (payload.sources) {
            renderSources(sourcesLists, payload.sources);
            if (sources && sourcesLists && !sourcesLists.hidden && sourcesLists.children.length > 0) {
              sources.hidden = false;
            } else if (sources) {
              sources.hidden = true;
            }
          }

          if (results) {
            const answerVisible = answer && !answer.hidden;
            const sourcesVisible = sources && !sources.hidden;
            results.hidden = !(answerVisible || sourcesVisible);
          }
        }
      } catch (error) {
        if (status) {
          status.textContent = 'Unable to contact the agent. Please try again later.';
        }
        resetResults();
      } finally {
        setBusy(false);
      }
    }

    form.addEventListener('submit', handleSubmit);
  });
})();
