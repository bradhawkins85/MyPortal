(function () {
  function debounce(fn, delay) {
    let timerId;
    return function (...args) {
      clearTimeout(timerId);
      timerId = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    const input = document.querySelector('form[action="/cart/update"] input[name="_csrf"]');
    return input ? input.value : null;
  }

  function autoSaveQuantity(input) {
    const name = input.getAttribute('name');
    if (!name || !name.startsWith('quantity_')) return;
    if (!input.validity.valid) return;

    const csrf = getCsrfToken();
    const formData = new FormData();
    if (csrf) formData.append('_csrf', csrf);
    formData.append(name, input.value);

    fetch('/cart/update', {
      method: 'POST',
      body: formData,
      redirect: 'manual',
    })
      .then((response) => {
        if (response.type === 'opaqueredirect' || response.ok) {
          window.location.href = window.location.pathname + '?_=' + Date.now();
        } else {
          window.location.reload();
        }
      })
      .catch(() => {
        window.location.reload();
      });
  }

  function parseJson(elementId) {
    const element = document.getElementById(elementId);
    if (!element) {
      return [];
    }
    try {
      return JSON.parse(element.textContent || '[]');
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return [];
    }
  }

  function bindStockLimitInputs(container) {
    container.querySelectorAll('[data-stock-limit]').forEach((input) => {
      if (!(input instanceof HTMLInputElement)) {
        return;
      }

      const limitAttr = Number(input.getAttribute('data-stock-limit'));
      const currentValue = Number(input.value);
      const fallbackLimit = Number.isFinite(currentValue) ? currentValue : 0;
      const limit = Number.isFinite(limitAttr) ? limitAttr : fallbackLimit;
      const effectiveLimit = limit > 0 ? limit : fallbackLimit;
      const minAttr = Number(input.getAttribute('min'));
      const min = Number.isFinite(minAttr) ? minAttr : 0;
      let previous = input.value;

      input.addEventListener('focus', () => {
        previous = input.value;
        input.setCustomValidity('');
      });

      input.addEventListener('input', () => {
        const value = Number(input.value);
        if (!Number.isFinite(value)) {
          return;
        }

        if (value > effectiveLimit) {
          input.value = previous || String(effectiveLimit || '');
          input.setCustomValidity('Cannot exceed available stock.');
          input.reportValidity();
          return;
        }

        if (value < min) {
          input.setCustomValidity(min > 0 ? 'Quantity must be at least 1.' : 'Quantity cannot be negative.');
          input.reportValidity();
          input.value = previous || String(Math.max(min, 0));
          return;
        }

        previous = input.value;
        input.setCustomValidity('');
      });

      input.addEventListener('blur', () => {
        if (!input.value) {
          input.value = previous || String(Math.max(min, 0));
        }
      });

      input.addEventListener('invalid', (event) => {
        event.preventDefault();
        if (Number(input.value) > effectiveLimit) {
          input.setCustomValidity('Cannot exceed available stock.');
        } else {
          input.setCustomValidity('Enter a valid quantity.');
        }
        input.reportValidity();
      });

      const debouncedSave = debounce(() => autoSaveQuantity(input), 600);
      input.addEventListener('change', debouncedSave);
    });
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.classList.add('is-visible');
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove('is-visible');
    modal.hidden = true;
  }

  function bindModalDismissal(modal) {
    if (!modal) {
      return;
    }
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.hasAttribute('data-modal-close')) {
        closeModal(modal);
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        closeModal(modal);
      }
    });
  }

  function appendDetail(container, label, value) {
    if (!container || value === undefined || value === null || value === '') {
      return;
    }
    const paragraph = document.createElement('p');
    paragraph.className = 'modal__text';
    const strong = document.createElement('strong');
    strong.textContent = `${label}: `;
    paragraph.append(strong);
    paragraph.append(document.createTextNode(String(value)));
    container.append(paragraph);
  }

  const RICH_TEXT_ALLOWED_TAGS = new Set([
    'a',
    'b',
    'blockquote',
    'br',
    'code',
    'div',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'hr',
    'i',
    'img',
    'iframe',
    'li',
    'ol',
    'p',
    'pre',
    'span',
    'strong',
    'sub',
    'sup',
    'table',
    'tbody',
    'td',
    'th',
    'thead',
    'tr',
    'u',
    'ul',
  ]);
  const RICH_TEXT_VOID_TAGS = new Set(['br', 'hr', 'img']);
  const RICH_TEXT_ALLOWED_ATTRIBUTES = {
    a: new Set(['href', 'title', 'target', 'rel']),
    img: new Set(['src', 'alt', 'title', 'width', 'height', 'loading', 'decoding']),
    iframe: new Set(['src', 'title', 'width', 'height', 'loading', 'allow', 'allowfullscreen', 'referrerpolicy']),
    span: new Set(['data-mention']),
    table: new Set(['role']),
  };
  const RICH_TEXT_ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:', 'data:']);

  function decodeHtmlEntities(value) {
    return String(value || '')
      .replace(/&#(\d+);/g, (_match, codepoint) => String.fromCodePoint(Number(codepoint)))
      .replace(/&#x([0-9a-f]+);/gi, (_match, codepoint) => String.fromCodePoint(parseInt(codepoint, 16)))
      .replace(/&nbsp;/g, '\u00a0')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, '&');
  }

  function isSafeRichTextUrl(value) {
    const url = String(value || '').trim();
    if (!url) {
      return false;
    }
    try {
      return RICH_TEXT_ALLOWED_PROTOCOLS.has(new URL(url, window.location.origin).protocol);
    } catch (_error) {
      return false;
    }
  }

  function applyRichTextAttribute(element, tagName, attributeName, attributeValue) {
    const allowedAttributes = RICH_TEXT_ALLOWED_ATTRIBUTES[tagName];
    if (!allowedAttributes || !allowedAttributes.has(attributeName)) {
      return;
    }

    const value = decodeHtmlEntities(attributeValue || '');
    if ((attributeName === 'href' || attributeName === 'src') && !isSafeRichTextUrl(value)) {
      return;
    }
    if ((attributeName === 'width' || attributeName === 'height') && !/^\d{1,4}$/.test(value)) {
      return;
    }
    if (attributeName === 'allowfullscreen' && value && !['allowfullscreen', 'true'].includes(value)) {
      return;
    }
    if (attributeName === 'target' && value !== '_blank') {
      return;
    }
    if (attributeName === 'loading' && !['lazy', 'eager'].includes(value)) {
      return;
    }
    if (attributeName === 'decoding' && !['async', 'sync', 'auto'].includes(value)) {
      return;
    }
    element.setAttribute(attributeName, value);
    if (tagName === 'a' && attributeName === 'target' && value === '_blank') {
      element.setAttribute('rel', 'noopener noreferrer');
    }
  }

  function appendRichTextHtml(container, html) {
    const rootStack = [container];
    const tagPattern = /<\/?[a-zA-Z][^>]*>/g;
    let cursor = 0;
    let match;

    while ((match = tagPattern.exec(html)) !== null) {
      const text = html.slice(cursor, match.index);
      if (text) {
        rootStack[rootStack.length - 1].append(document.createTextNode(decodeHtmlEntities(text)));
      }

      const token = match[0];
      const closingMatch = token.match(/^<\/\s*([a-zA-Z0-9]+)\s*>$/);
      if (closingMatch) {
        const closingTag = closingMatch[1].toLowerCase();
        for (let index = rootStack.length - 1; index > 0; index -= 1) {
          if (rootStack[index].tagName && rootStack[index].tagName.toLowerCase() === closingTag) {
            rootStack.length = index;
            break;
          }
        }
        cursor = tagPattern.lastIndex;
        continue;
      }

      const openingMatch = token.match(/^<\s*([a-zA-Z0-9]+)([^>]*)>$/);
      if (!openingMatch) {
        cursor = tagPattern.lastIndex;
        continue;
      }

      const tagName = openingMatch[1].toLowerCase();
      if (!RICH_TEXT_ALLOWED_TAGS.has(tagName)) {
        cursor = tagPattern.lastIndex;
        continue;
      }

      const element = document.createElement(tagName);
      const attributes = openingMatch[2] || '';
      attributes.replace(/([a-zA-Z][\w:-]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+))/g, (_attr, rawName, _rawValue, doubleQuoted, singleQuoted, unquoted) => {
        applyRichTextAttribute(element, tagName, rawName.toLowerCase(), doubleQuoted || singleQuoted || unquoted || '');
        return '';
      });

      rootStack[rootStack.length - 1].append(element);
      if (!RICH_TEXT_VOID_TAGS.has(tagName) && !/\/\s*>$/.test(token)) {
        rootStack.push(element);
      }
      cursor = tagPattern.lastIndex;
    }

    const remainingText = html.slice(cursor);
    if (remainingText) {
      rootStack[rootStack.length - 1].append(document.createTextNode(decodeHtmlEntities(remainingText)));
    }
  }

  function renderProductDetails(container, product) {
    if (!container) {
      return;
    }
    container.replaceChildren();

    if (!product) {
      const empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = 'Product details are unavailable.';
      container.append(empty);
      return;
    }

    if (product.image_url) {
      const image = document.createElement('img');
      image.src = product.image_url;
      image.alt = `${product.name || 'Product'} image`;
      image.className = 'modal__image';
      container.append(image);
    }

    appendDetail(container, 'SKU', product.sku);
    appendDetail(container, 'Vendor SKU', product.vendor_sku);
    appendDetail(container, 'Unit price', `$${product.unit_price}`);
    appendDetail(container, 'Quantity in cart', product.quantity);
    appendDetail(container, 'Line total', `$${product.line_total}`);

    const descriptionHtml = typeof product.description_html === 'string' ? product.description_html.trim() : '';
    const descriptionText = typeof product.description === 'string' ? product.description.trim() : '';
    if (descriptionHtml || descriptionText) {
      const descriptionTitle = document.createElement('h3');
      descriptionTitle.className = 'modal__subtitle';
      descriptionTitle.textContent = 'Description';
      container.append(descriptionTitle);

      const descriptionDiv = document.createElement('div');
      descriptionDiv.className = 'modal__description rich-text-viewer';
      if (descriptionHtml) {
        appendRichTextHtml(descriptionDiv, descriptionHtml);
      } else {
        descriptionDiv.textContent = descriptionText;
      }
      container.append(descriptionDiv);
    }
  }

  function handleFormSubmitAndReload() {
    // Find all forms that modify cart state
    const forms = document.querySelectorAll(
      'form[action="/cart/update"], form[action="/cart/remove"], form[action="/cart/add"], form[action="/cart/place-order"]'
    );

    forms.forEach((form) => {
      form.addEventListener('submit', (event) => {
        event.preventDefault();

        // Submit the form using fetch to intercept the redirect
        const formData = new FormData(form);
        
        // Get the action URL - check if the submit button has a formaction attribute
        let action = form.getAttribute('action');
        const submitter = event.submitter;
        if (submitter && submitter.hasAttribute('formaction')) {
          action = submitter.getAttribute('formaction');
        }

        fetch(action, {
          method: 'POST',
          body: formData,
          redirect: 'manual', // Don't follow redirects automatically
        })
          .then((response) => {
            // The server returns a 303 redirect, which fetch sees as an opaque redirect response
            // We want to follow the redirect manually and reload the page
            const redirectStatuses = [301, 302, 303];
            if (response.type === 'opaqueredirect' || redirectStatuses.includes(response.status)) {
              // Force a full page reload to the current cart page with cache bust
              window.location.href = window.location.pathname + '?_=' + Date.now();
            } else if (response.redirected) {
              // If fetch followed a redirect automatically (shouldn't happen with redirect: 'manual')
              window.location.href = response.url;
            } else {
              // Fallback: just reload the current page
              window.location.reload();
            }
          })
          .catch(() => {
            // On error, try to reload the page anyway
            window.location.reload();
          });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    bindStockLimitInputs(container);
    handleFormSubmitAndReload();

    const modal = document.getElementById('cart-product-details-modal');
    const modalTitle = document.getElementById('cart-product-details-title');
    const modalBody = document.getElementById('cart-product-details-body');
    if (!modal || !modalBody) {
      return;
    }

    // Bind modal dismissal
    bindModalDismissal(modal);

    const itemsData = parseJson('cart-items-data');
    if (!Array.isArray(itemsData) || itemsData.length === 0) {
      return;
    }

    document.querySelectorAll('[data-cart-product-modal]').forEach((button) => {
      button.addEventListener('click', (event) => {
        const productId = Number(button.getAttribute('data-cart-product-modal'));
        const product = itemsData.find((item) => item.product_id === productId);
        
        if (product) {
          modalTitle.textContent = product.name || 'Product details';
          renderProductDetails(modalBody, product);
          openModal(modal);
        }
      });
    });

    // Handle save quote modal
    const saveQuoteButton = document.getElementById('save-quote-button');
    const saveQuoteModal = document.getElementById('save-quote-modal');
    if (saveQuoteButton && saveQuoteModal) {
      bindModalDismissal(saveQuoteModal);
      
      saveQuoteButton.addEventListener('click', () => {
        openModal(saveQuoteModal);
      });
    }
  });
})();
