(function () {
  'use strict';

  function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderSimpleText(container, text) {
    if (!container) return;
    container.innerHTML = '';
    if (!text) {
      container.hidden = true;
      return;
    }
    const paragraph = document.createElement('p');
    paragraph.className = 'agent-answer__paragraph';
    paragraph.innerHTML = escapeHtml(text)
      .replace(/\n{2,}/g, '</p><p class="agent-answer__paragraph">')
      .replace(/\n/g, '<br />');
    container.appendChild(paragraph);
    container.hidden = false;
  }

  function sourceUrl(sourceType, item) {
    if (item.url) return String(item.url);
    const id = encodeURIComponent(item.id == null ? item.source_id || '' : item.id);
    const destinations = {
      tickets: id ? `/tickets/${id}` : '/tickets',
      knowledge_base: '/knowledge-base',
      products: id ? `/shop?product=${id}` : '/shop',
      packages: id ? `/shop/packages?package=${id}` : '/shop/packages',
      chats: id ? `/chat/${id}` : '/chat',
      orders: '/orders',
      assets: id ? `/assets/${id}` : '/assets',
      companies: '/',
      staff: '/staff',
      issues: '/issues',
      service_status: '/service-status',
      backup_jobs: '/admin/backup-summary',
      reports: '/reports/company-overview',
      mailboxes: item.mailbox_type === 'shared' ? '/m365/mailboxes/shared' : '/m365/mailboxes/users',
      best_practices: '/m365/best-practices'
    };
    return destinations[sourceType] || '/search';
  }

  function renderDuplicateMeta(item) {
    const duplicateCount = Number.parseInt(item.duplicate_count, 10);
    const duplicates = Array.isArray(item.duplicates) ? item.duplicates : [];
    if ((!duplicateCount || duplicateCount < 1) && duplicates.length === 0) return '';
    const labels = duplicates.slice(0, 5).map((duplicate) => {
      const source = escapeHtml(duplicate.source_type || item.source_type || 'source');
      const sourceId = escapeHtml(duplicate.source_id || duplicate.id || '?');
      const title = duplicate.title ? ` ${escapeHtml(duplicate.title)}` : '';
      return `<li>[${source}:${sourceId}]${title}</li>`;
    }).join('');
    return `<details class="agent-sources__meta"><summary>Similar items: ${escapeHtml(duplicateCount || duplicates.length)}</summary>${labels ? `<ul>${labels}</ul>` : ''}</details>`;
  }

  function createSourceList(title, sourceType, items, formatter) {
    if (!items || items.length === 0) return null;
    const section = document.createElement('details');
    section.className = 'agent-sources__group';
    const heading = document.createElement('summary');
    heading.className = 'agent-sources__title';
    heading.textContent = `${title} (${items.length})`;
    section.appendChild(heading);

    const list = document.createElement('ul');
    list.className = 'agent-sources__list';
    items.forEach((item) => {
      const entry = document.createElement('li');
      entry.className = 'agent-sources__item';
      const link = document.createElement('a');
      link.className = 'agent-sources__link';
      link.href = sourceUrl(sourceType, item);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.innerHTML = formatter(item);
      entry.appendChild(link);
      const duplicateMeta = renderDuplicateMeta(item);
      if (duplicateMeta) {
        const extra = document.createElement('div');
        extra.innerHTML = duplicateMeta;
        entry.appendChild(extra);
      }
      list.appendChild(entry);
    });
    section.appendChild(list);
    return section;
  }

  function formatKnowledgeBaseSource(item) {
    const title = escapeHtml(item.title || item.slug);
    const slug = escapeHtml(item.slug || item.source_id || '');
    const summary = escapeHtml(item.summary || item.excerpt || '');
    return `[KB:${slug}] ${title}${summary ? `<div class="agent-sources__meta">${summary}</div>` : ''}`;
  }

  function formatTicketSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const subject = escapeHtml(item.subject || item.title || `Ticket #${id}`);
    const status = escapeHtml(item.status || 'unknown');
    const priority = escapeHtml(item.priority || 'normal');
    const summary = escapeHtml(item.summary || '');
    return `[#${id}] ${subject}<div class="agent-sources__meta">Status: ${status} • Priority: ${priority}${summary ? `<br />${summary}` : ''}</div>`;
  }

  function formatProductSource(item) {
    const sku = item.sku ? escapeHtml(item.sku) : null;
    const name = escapeHtml(item.name || item.title || (sku ? sku : 'Product'));
    const price = item.price ? escapeHtml(item.price) : null;
    const description = item.description ? escapeHtml(item.description) : null;
    const recommendations = Array.isArray(item.recommendations) && item.recommendations.length ? item.recommendations.map(escapeHtml).join(', ') : null;
    const metaParts = [];
    if (price) metaParts.push(`Price: ${price}`);
    if (description) metaParts.push(description);
    if (recommendations) metaParts.push(`Recommended with: ${recommendations}`);
    const label = sku ? `[${sku}] ${name}` : name;
    return `${label}${metaParts.length ? `<div class="agent-sources__meta">${metaParts.join('<br />')}</div>` : ''}`;
  }

  function formatPackageSource(item) {
    const sku = item.sku ? escapeHtml(item.sku) : null;
    const name = escapeHtml(item.name || (sku ? sku : 'Package'));
    const description = item.description ? escapeHtml(item.description) : null;
    const productCount = typeof item.product_count === 'number' ? item.product_count : Number.parseInt(item.product_count, 10);
    const label = sku ? `[${sku}] ${name}` : name;
    const metaParts = [];
    if (!Number.isNaN(productCount) && Number.isFinite(productCount)) {
      const count = Math.max(0, productCount);
      metaParts.push(`Includes ${count} ${count === 1 ? 'item' : 'items'}`);
    }
    if (description) metaParts.push(description);
    return `${label}${metaParts.length ? `<div class="agent-sources__meta">${metaParts.join('<br />')}</div>` : ''}`;
  }

  function formatChatSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const subject = escapeHtml(item.subject || item.title || `Chat #${id}`);
    const status = escapeHtml(item.status || 'unknown');
    const summary = escapeHtml(item.summary || item.excerpt || '');
    const ticket = item.linked_ticket_id ? ` • Ticket #${escapeHtml(item.linked_ticket_id)}` : '';
    return `[#${id}] ${subject}<div class="agent-sources__meta">Status: ${status}${ticket}${summary ? `<br />${summary}` : ''}</div>`;
  }

  function formatOrderSource(item) {
    const number = escapeHtml(item.order_number || item.source_id || 'Order');
    const status = escapeHtml(item.status || 'unknown');
    const shipping = item.shipping_status ? ` • Shipping: ${escapeHtml(item.shipping_status)}` : '';
    const po = item.po_number ? ` • PO: ${escapeHtml(item.po_number)}` : '';
    const summary = escapeHtml(item.summary || item.notes || '');
    return `[${number}]<div class="agent-sources__meta">Status: ${status}${shipping}${po}${summary ? `<br />${summary}` : ''}</div>`;
  }

  function formatAssetSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Asset #${id}`);
    const metaParts = [];
    if (item.type) metaParts.push(`Type: ${escapeHtml(item.type)}`);
    if (item.serial_number) metaParts.push(`Serial: ${escapeHtml(item.serial_number)}`);
    if (item.status) metaParts.push(`Status: ${escapeHtml(item.status)}`);
    if (item.os_name) metaParts.push(`OS: ${escapeHtml(item.os_name)}`);
    if (item.last_user) metaParts.push(`Last user: ${escapeHtml(item.last_user)}`);
    return `[#${id}] ${name}${metaParts.length ? `<div class="agent-sources__meta">${metaParts.join(' • ')}</div>` : ''}`;
  }

  function formatCompanySource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Company #${id}`);
    const syncro = item.syncro_company_id ? `<div class="agent-sources__meta">Syncro ID: ${escapeHtml(item.syncro_company_id)}</div>` : '';
    return `[#${id}] ${name}${syncro}`;
  }

  function formatStaffSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Staff #${id}`);
    const metaParts = [];
    if (item.email) metaParts.push(`Email: ${escapeHtml(item.email)}`);
    if (item.job_title) metaParts.push(`Title: ${escapeHtml(item.job_title)}`);
    if (item.department) metaParts.push(`Department: ${escapeHtml(item.department)}`);
    if (item.mobile_phone) metaParts.push(`Mobile: ${escapeHtml(item.mobile_phone)}`);
    if (item.onboarding_status) metaParts.push(`Status: ${escapeHtml(item.onboarding_status)}`);
    return `[#${id}] ${name}${metaParts.length ? `<div class="agent-sources__meta">${metaParts.join(' • ')}</div>` : ''}`;
  }

  function formatIssueSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Issue #${id}`);
    const description = item.description ? escapeHtml(item.description) : '';
    return `[#${id}] ${name}${description ? `<div class="agent-sources__meta">${description}</div>` : ''}`;
  }

  function formatServiceStatusSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Service #${id}`);
    const detail = item.status_message || item.description || '';
    return `[#${id}] ${name}${detail ? `<div class="agent-sources__meta">${escapeHtml(detail)}</div>` : ''}`;
  }

  function formatBackupJobSource(item) {
    const id = escapeHtml(item.id || item.source_id);
    const name = escapeHtml(item.name || item.title || `Backup job #${id}`);
    const meta = [item.today_status ? `Today: ${escapeHtml(item.today_status)}` : null, item.latest_status ? `Latest: ${escapeHtml(item.latest_status)}` : null, item.description ? escapeHtml(item.description) : null].filter(Boolean).join(' • ');
    return `[#${id}] ${name}${meta ? `<div class="agent-sources__meta">${meta}</div>` : ''}`;
  }

  function formatReportSource(item) {
    const key = escapeHtml(item.key || item.source_id || 'report');
    const title = escapeHtml(item.title || key);
    const meta = [item.source_type ? `Type: ${escapeHtml(item.source_type)}` : null, item.description ? escapeHtml(item.description) : null].filter(Boolean).join('<br />');
    return `[${key}] ${title}${meta ? `<div class="agent-sources__meta">${meta}</div>` : ''}`;
  }

  function formatMailboxSource(item) {
    const upn = escapeHtml(item.user_principal_name || item.title || 'mailbox');
    const name = escapeHtml(item.display_name || item.user_principal_name || 'Mailbox');
    const meta = [item.mailbox_type ? `Type: ${escapeHtml(item.mailbox_type)}` : null, item.storage_used_bytes != null ? `Storage: ${escapeHtml(item.storage_used_bytes)} bytes` : null].filter(Boolean).join(' • ');
    return `[${upn}] ${name}${meta ? `<div class="agent-sources__meta">${meta}</div>` : ''}`;
  }

  function formatBestPracticeSource(item) {
    const id = escapeHtml(item.check_id || item.source_id || 'check');
    const name = escapeHtml(item.check_name || item.title || id);
    const meta = [item.status ? `Status: ${escapeHtml(item.status)}` : null, item.details ? escapeHtml(item.details) : null].filter(Boolean).join('<br />');
    return `[${id}] ${name}${meta ? `<div class="agent-sources__meta">${meta}</div>` : ''}`;
  }

  function formatGenericSource(item) {
    const label = escapeHtml(item.label || item.title || item.name || item.id || item.source_id || 'Result');
    const summary = item.summary || item.description || '';
    return `${label}${summary ? `<div class="agent-sources__meta">${escapeHtml(summary)}</div>` : ''}`;
  }

  function formatFeaturePackTitle(slug) {
    return String(slug || 'feature pack').replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function renderSources(container, sources) {
    if (!container) return;
    container.innerHTML = '';
    if (!sources || typeof sources !== 'object') {
      container.hidden = true;
      return;
    }
    const map = [
      ['Knowledge base', 'knowledge_base', formatKnowledgeBaseSource],
      ['Tickets', 'tickets', formatTicketSource],
      ['Products', 'products', formatProductSource],
      ['Chats', 'chats', formatChatSource],
      ['Orders', 'orders', formatOrderSource],
      ['Assets', 'assets', formatAssetSource],
      ['Packages', 'packages', formatPackageSource],
      ['Companies', 'companies', formatCompanySource],
      ['Staff', 'staff', formatStaffSource],
      ['Issues', 'issues', formatIssueSource],
      ['Service status', 'service_status', formatServiceStatusSource],
      ['Backup summary', 'backup_jobs', formatBackupJobSource],
      ['Reports', 'reports', formatReportSource],
      ['Office 365 mailboxes', 'mailboxes', formatMailboxSource],
      ['Best practices', 'best_practices', formatBestPracticeSource]
    ];
    const groups = map.map(([title, sourceType, formatter]) => {
      const items = sources[sourceType];
      return Array.isArray(items) ? createSourceList(title, sourceType, items, formatter) : null;
    }).filter(Boolean);
    if (sources.feature_packs && typeof sources.feature_packs === 'object') {
      Object.keys(sources.feature_packs).sort().forEach((slug) => {
        const items = sources.feature_packs[slug];
        if (Array.isArray(items)) groups.push(createSourceList(formatFeaturePackTitle(slug), slug, items, formatGenericSource));
      });
    }
    if (!groups.length) {
      container.hidden = true;
      return;
    }
    groups.forEach((group) => container.appendChild(group));
    container.hidden = false;
  }

  function formatStatus(result) {
    if (!result || typeof result !== 'object') return '';
    const parts = [];
    if (result.status) {
      const statusText = String(result.status).toLowerCase();
      if (statusText === 'succeeded') parts.push('Answer generated successfully.');
      else if (statusText === 'skipped') parts.push('The Ollama module is disabled; showing recent context.');
      else parts.push('The agent could not generate a response.');
    }
    if (result.model) parts.push(`Model: ${result.model}`);
    if (result.generated_at) {
      const generatedDate = new Date(result.generated_at);
      if (!Number.isNaN(generatedDate.getTime())) parts.push(`Generated at ${generatedDate.toLocaleString()}`);
    }
    if (result.message) parts.push(result.message);
    return parts.join(' ');
  }

  function formatStage(stage) {
    const data = stage && stage.data && typeof stage.data === 'object' ? stage.data : {};
    const metrics = [];
    Object.keys(data).slice(0, 4).forEach((key) => {
      const value = data[key];
      if (typeof value === 'number' || typeof value === 'string') metrics.push(`${key}: ${value}`);
    });
    return `${stage.name} (${stage.status})${metrics.length ? ` — ${metrics.join(', ')}` : ''}`;
  }

  function renderStages(container, stages) {
    if (!container) return;
    container.innerHTML = '';
    if (!Array.isArray(stages) || stages.length === 0) {
      container.hidden = true;
      return;
    }
    stages.forEach((stage) => {
      const item = document.createElement('div');
      item.className = 'agent-panel__stage';
      item.textContent = formatStage(stage);
      container.appendChild(item);
    });
    container.hidden = false;
  }

  function renderAnswerMeta(container, payload) {
    if (!container) return;
    const confidence = payload.answer_confidence;
    const label = payload.answer_confidence_label;
    const missing = Array.isArray(payload.missing_sources) ? payload.missing_sources : [];
    const parts = [];
    if (confidence != null && !Number.isNaN(Number(confidence))) {
      parts.push(`Confidence: ${Math.round(Number(confidence) * 100)}%${label ? ` (${label})` : ''}`);
    }
    if (missing.length) {
      parts.push(`No strong matches in: ${missing.join(', ')}`);
    }
    if (!parts.length) {
      container.hidden = true;
      container.textContent = '';
      return;
    }
    container.textContent = parts.join(' • ');
    container.hidden = false;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.querySelector('[data-agent-panel]');
    if (!panel) return;

    const form = panel.querySelector('[data-agent-form]');
    const input = panel.querySelector('[data-agent-input]');
    const filterInputs = Array.from(panel.querySelectorAll('[data-agent-filter]'));
    const submitButton = panel.querySelector('[data-agent-submit]');
    const status = panel.querySelector('[data-agent-status]');
    const stages = panel.querySelector('[data-agent-stages]');
    const results = panel.querySelector('[data-agent-results]');
    const answer = panel.querySelector('[data-agent-answer]');
    const answerMeta = panel.querySelector('[data-agent-answer-meta]');
    const answerBody = panel.querySelector('[data-agent-answer-body]');
    const sources = panel.querySelector('[data-agent-sources]');
    const sourcesLists = panel.querySelector('[data-agent-source-lists]');
    const evidence = panel.querySelector('[data-agent-evidence]');
    const evidenceLists = panel.querySelector('[data-agent-evidence-lists]');
    const createTicketButton = panel.querySelector('[data-agent-create-ticket]');
    const saveNameInput = panel.querySelector('[data-agent-save-name]');
    const saveSearchButton = panel.querySelector('[data-agent-save-search]');
    const savedLibrary = panel.querySelector('[data-agent-saved-library]');

    if (!form || !input) return;

    const defaultStatus = 'Enter a question to ask the agent.';
    if (status) status.textContent = defaultStatus;
    let lastQuery = '';
    let lastAnswer = '';

    function selectedSourceFilters() {
      return filterInputs.filter((inputEl) => inputEl.checked).map((inputEl) => inputEl.value);
    }

    function setBusy(isBusy) {
      if (submitButton) submitButton.disabled = isBusy;
      if (input) input.disabled = isBusy;
      filterInputs.forEach((item) => { item.disabled = isBusy; });
      panel.classList.toggle('agent-panel--busy', Boolean(isBusy));
    }

    function resetResults() {
      if (answer) answer.hidden = true;
      if (answerBody) answerBody.textContent = '';
      if (answerMeta) {
        answerMeta.hidden = true;
        answerMeta.textContent = '';
      }
      if (sources) sources.hidden = true;
      if (sourcesLists) sourcesLists.innerHTML = '';
      if (evidence) evidence.hidden = true;
      if (evidenceLists) evidenceLists.innerHTML = '';
      if (stages) {
        stages.hidden = true;
        stages.innerHTML = '';
      }
      if (results) results.hidden = true;
      if (createTicketButton) createTicketButton.hidden = true;
    }

    function openTicketModal() {
      const ticketModal = document.getElementById('create-ticket-modal');
      if (!ticketModal) return;
      const subjectField = ticketModal.querySelector('#modal-ticket-subject');
      if (subjectField && lastQuery) subjectField.value = lastQuery;
      const descriptionField = ticketModal.querySelector('#modal-ticket-description');
      if (descriptionField) {
        let description = '';
        if (lastQuery) description += `Original Question:\n${lastQuery}\n\n`;
        if (lastAnswer) description += `Agent Response:\n${lastAnswer}`;
        if (description) descriptionField.value = description;
      }
      ticketModal.hidden = false;
      ticketModal.setAttribute('aria-hidden', 'false');
      if (subjectField && !subjectField.value) subjectField.focus();
      else if (descriptionField) descriptionField.focus();
    }

    async function fetchSavedSearches() {
      if (!savedLibrary) return;
      try {
        const response = await fetch('/api/agent/saved-searches');
        if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
        const records = await response.json();
        savedLibrary.innerHTML = '';
        if (!Array.isArray(records) || !records.length) {
          savedLibrary.textContent = 'No saved searches yet.';
          return;
        }
        records.forEach((record) => {
          const row = document.createElement('div');
          row.className = 'agent-panel__saved-item';
          const openButton = document.createElement('button');
          openButton.type = 'button';
          openButton.className = 'button button--ghost';
          openButton.textContent = `${record.name}${record.is_shared ? ' (shared)' : ''}`;
          openButton.addEventListener('click', () => {
            input.value = record.query || '';
            filterInputs.forEach((checkbox) => { checkbox.checked = !record.source_filters || record.source_filters.includes(checkbox.value); });
          });
          const deleteButton = document.createElement('button');
          deleteButton.type = 'button';
          deleteButton.className = 'button button--ghost';
          deleteButton.textContent = 'Delete';
          deleteButton.addEventListener('click', async () => {
            await fetch(`/api/agent/saved-searches/${record.id}`, { method: 'DELETE' });
            await fetchSavedSearches();
          });
          row.appendChild(openButton);
          row.appendChild(deleteButton);
          savedLibrary.appendChild(row);
        });
      } catch (error) {
        savedLibrary.textContent = 'Unable to load saved searches.';
      }
    }

    async function saveCurrentSearch() {
      const query = input.value.trim();
      const name = saveNameInput && saveNameInput.value ? saveNameInput.value.trim() : '';
      if (!query || !name) {
        if (status) status.textContent = 'Enter a query and saved-search name first.';
        return;
      }
      try {
        const response = await fetch('/api/agent/saved-searches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            query,
            source_filters: selectedSourceFilters()
          })
        });
        if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
        if (status) status.textContent = 'Search saved.';
        if (saveNameInput) saveNameInput.value = '';
        await fetchSavedSearches();
      } catch (error) {
        if (status) status.textContent = 'Unable to save search.';
      }
    }

    async function handleSubmit(event) {
      event.preventDefault();
      const query = input.value.trim();
      if (!query) {
        if (status) status.textContent = 'Please enter a question for the agent.';
        return;
      }
      const sourceFilters = selectedSourceFilters();
      if (!sourceFilters.length) {
        if (status) status.textContent = 'Select at least one source filter.';
        return;
      }

      setBusy(true);
      resetResults();
      if (status) status.textContent = 'Contacting the agent…';
      lastQuery = query;
      lastAnswer = '';

      try {
        const response = await fetch('/api/agent/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, source_filters: sourceFilters }),
        });

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `Request failed with status ${response.status}`);
        }

        const payload = await response.json();
        if (status) status.textContent = formatStatus(payload) || defaultStatus;
        if (stages) renderStages(stages, payload.stages);

        if (payload && typeof payload === 'object') {
          if (payload.answer) {
            lastAnswer = payload.answer;
            renderSimpleText(answerBody, payload.answer);
            renderAnswerMeta(answerMeta, payload);
            if (answer) answer.hidden = false;
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

          if (payload.evidence) {
            renderSources(evidenceLists, payload.evidence);
            if (evidence && evidenceLists && !evidenceLists.hidden && evidenceLists.children.length > 0) {
              evidence.hidden = false;
            } else if (evidence) {
              evidence.hidden = true;
            }
          }

          if (createTicketButton) {
            const shouldShowButton = payload.has_relevant_sources === false
              || (payload.answer && (
                payload.answer.toLowerCase().includes('create a support ticket')
                || payload.answer.toLowerCase().includes('contact support')
                || payload.answer.toLowerCase().includes("don't have")
              ));
            if (shouldShowButton) createTicketButton.hidden = false;
          }

          if (results) {
            const answerVisible = answer && !answer.hidden;
            const sourcesVisible = sources && !sources.hidden;
            const evidenceVisible = evidence && !evidence.hidden;
            const ticketButtonVisible = createTicketButton && !createTicketButton.hidden;
            results.hidden = !(answerVisible || sourcesVisible || evidenceVisible || ticketButtonVisible);
          }
        }
      } catch (error) {
        if (status) status.textContent = 'Unable to contact the agent. Please try again later.';
        resetResults();
      } finally {
        setBusy(false);
      }
    }

    form.addEventListener('submit', handleSubmit);
    if (createTicketButton) createTicketButton.addEventListener('click', openTicketModal);
    if (saveSearchButton) saveSearchButton.addEventListener('click', saveCurrentSearch);
    fetchSavedSearches();
  });
})();
