(function () {
  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getMetaContent(name) {
    const meta = document.querySelector(`meta[name="${name}"]`);
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function getCsrfToken() {
    const metaToken = getMetaContent('csrf-token');
    if (metaToken) {
      return metaToken;
    }
    return getCookie('myportal_session_csrf');
  }

  const searchForm = document.querySelector('[data-knowledge-base-search]');
  const input = searchForm ? searchForm.querySelector('input[name="query"]') : null;
  const resultsSection = document.querySelector('[data-knowledge-base-results]');
  const resultsBody = document.querySelector('[data-knowledge-base-results-body]');
  const ollamaSection = document.querySelector('[data-knowledge-base-ollama]');
  const ollamaBody = document.querySelector('[data-knowledge-base-ollama-body]');
  const ollamaStatus = document.querySelector('[data-knowledge-base-ollama-status]');

  let inFlightController = null;

  function setSectionVisibility(section, hidden) {
    if (!section) {
      return;
    }
    section.hidden = hidden;
  }

  function renderResults(data) {
    if (resultsBody) {
      resultsBody.innerHTML = '';
    }
    if (!resultsSection || !resultsBody) {
      return;
    }
    setSectionVisibility(resultsSection, false);
    const results = Array.isArray(data.results) ? data.results : [];
    if (results.length === 0) {
      resultsBody.innerHTML = '<p class="knowledge-base__empty">No matching articles were found.</p>';
    } else {
      const list = document.createElement('ul');
      list.className = 'knowledge-base__results-list';
      results.forEach((result) => {
        const item = document.createElement('li');
        item.className = 'knowledge-base__results-item';
        const link = document.createElement('a');
        link.className = 'knowledge-base__link';
        link.href = `/knowledge-base/articles/${encodeURIComponent(result.slug)}`;
        link.textContent = result.title || result.slug;
        item.appendChild(link);
        if (result.excerpt) {
          const excerpt = document.createElement('p');
          excerpt.className = 'knowledge-base__summary';
          excerpt.textContent = result.excerpt;
          item.appendChild(excerpt);
        } else if (result.summary) {
          const summary = document.createElement('p');
          summary.className = 'knowledge-base__summary';
          summary.textContent = result.summary;
          item.appendChild(summary);
        }
        list.appendChild(item);
      });
      resultsBody.appendChild(list);
    }

    if (ollamaBody && ollamaSection && ollamaStatus) {
      const status = data.ollama_status || 'skipped';
      const summary = data.ollama_summary;
      const model = data.ollama_model;
      if (summary) {
        setSectionVisibility(ollamaSection, false);
        ollamaStatus.textContent = model ? `${status} • ${model}` : status;
        ollamaBody.innerHTML = '';
        const paragraph = document.createElement('p');
        paragraph.className = 'knowledge-base__ollama-text';
        paragraph.textContent = summary;
        ollamaBody.appendChild(paragraph);
      } else if (status && status !== 'skipped') {
        setSectionVisibility(ollamaSection, false);
        ollamaStatus.textContent = model ? `${status} • ${model}` : status;
        ollamaBody.innerHTML = '<p class="knowledge-base__empty">Ollama did not return a summary for this query.</p>';
      } else {
        setSectionVisibility(ollamaSection, true);
      }
    }
  }

  function renderError(error) {
    if (resultsBody && resultsSection) {
      resultsBody.innerHTML = `<p class="knowledge-base__empty">Search failed: ${error.message}</p>`;
      setSectionVisibility(resultsSection, false);
    }
    if (ollamaSection) {
      setSectionVisibility(ollamaSection, true);
    }
  }

  if (searchForm) {
    searchForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!input) {
        return;
      }
      const query = input.value.trim();
      if (!query) {
        input.focus();
        return;
      }
      searchForm.classList.add('is-loading');
      if (inFlightController) {
        inFlightController.abort();
      }
      inFlightController = new AbortController();
      try {
        const headers = {
          'Content-Type': 'application/json',
        };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        const response = await fetch('/api/knowledge-base/search', {
          method: 'POST',
          headers,
          body: JSON.stringify({ query }),
          signal: inFlightController.signal,
        });
        if (!response.ok) {
          throw new Error(`Search failed with status ${response.status}`);
        }
        const payload = await response.json();
        renderResults(payload);
      } catch (error) {
        if (error.name === 'AbortError') {
          return;
        }
        renderError(error);
      } finally {
        searchForm.classList.remove('is-loading');
        inFlightController = null;
      }
    });
  }

  const feedbackForm = document.querySelector('[data-kb-feedback-form]');
  const feedbackStatus = document.querySelector('[data-kb-feedback-status]');
  if (feedbackForm) {
    feedbackForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const articleSlug = feedbackForm.getAttribute('data-article-slug') || '';
      const selectedRating = feedbackForm.querySelector('input[name="rating"]:checked');
      const feedbackInput = feedbackForm.querySelector('textarea[name="feedback"]');
      const submitButton = feedbackForm.querySelector('button[type="submit"]');
      if (!articleSlug || !selectedRating) {
        return;
      }

      const setStatus = (message, type = '') => {
        if (!feedbackStatus) {
          return;
        }
        feedbackStatus.textContent = message;
        feedbackStatus.classList.remove('is-success', 'is-error');
        if (type) {
          feedbackStatus.classList.add(type);
        }
      };

      submitButton?.setAttribute('disabled', 'disabled');
      setStatus('Submitting feedback…');
      try {
        const headers = {
          'Content-Type': 'application/json',
        };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }
        const response = await fetch(`/api/knowledge-base/articles/${encodeURIComponent(articleSlug)}/feedback`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            rating: selectedRating.value,
            feedback: (feedbackInput?.value || '').trim(),
          }),
        });
        if (!response.ok) {
          let detail = `status ${response.status}`;
          try {
            const errorPayload = await response.json();
            if (errorPayload && typeof errorPayload.detail === 'string' && errorPayload.detail.trim()) {
              detail = errorPayload.detail;
            }
          } catch (_) {
            // ignore parse failure and keep default detail
          }
          throw new Error(detail);
        }
        const payload = await response.json();
        setStatus(`Thanks! Ticket #${payload.ticket_id} was created.`, 'is-success');
        feedbackForm.reset();
      } catch (error) {
        setStatus(`Failed to submit feedback: ${error.message}`, 'is-error');
      } finally {
        submitButton?.removeAttribute('disabled');
      }
    });
  }
})();
