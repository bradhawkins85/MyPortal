(function () {
  'use strict';

  const COLUMN_COUNT_REGULAR = 4;
  const COLUMN_COUNT_ADMIN = 9;

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
    
    // Close modal on background click
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.hasAttribute('data-modal-close')) {
        closeModal(modal);
      }
    });
    
    // Close modal on Escape key (using capture to handle once globally)
    const handleEscape = (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        closeModal(modal);
      }
    };
    
    // Store handler reference for cleanup if needed in future
    modal._escapeHandler = handleEscape;
    document.addEventListener('keydown', handleEscape);
  }

  async function requestJson(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || 'Request failed');
    }

    return response.json();
  }

  function formatPrice(price) {
    if (price === null || price === undefined) {
      return '—';
    }
    return `$${parseFloat(price).toFixed(2)}`;
  }

  function formatStock(stock) {
    if (stock === null || stock === undefined) {
      return '—';
    }
    return stock.toString();
  }

  async function loadQuoteDetails(quoteNumber, companyId) {
    const modal = document.getElementById('quote-details-modal');
    const modalTitle = document.getElementById('modal-quote-title');
    const loadingDiv = document.getElementById('modal-quote-loading');
    const contentDiv = document.getElementById('modal-quote-content');
    const errorDiv = document.getElementById('modal-quote-error');
    const itemsContainer = document.getElementById('modal-quote-items');

    if (!modal || !modalTitle || !loadingDiv || !contentDiv || !errorDiv || !itemsContainer) {
      return;
    }

    // Check if user is super admin
    const isSuperAdmin = document.body.dataset.superAdmin === 'true';

    // Reset modal state
    modalTitle.textContent = `Quote ${quoteNumber}`;
    loadingDiv.style.display = 'block';
    contentDiv.style.display = 'none';
    errorDiv.style.display = 'none';
    itemsContainer.innerHTML = '';

    openModal(modal);

    try {
      const data = await requestJson(`/api/quotes/${quoteNumber}?companyId=${companyId}`);
      
      // Populate items table
      if (data.items && data.items.length > 0) {
        data.items.forEach((item) => {
          const row = document.createElement('tr');
          
          let rowHtml = `
            <td data-label="Product">${item.productName || '—'}</td>
            <td data-label="SKU">${item.sku || '—'}</td>
            <td data-label="Quantity">${item.quantity}</td>
            <td data-label="Price">${formatPrice(item.price)}</td>
          `;

          // Add stock columns for super admins
          if (isSuperAdmin) {
            rowHtml += `
              <td data-label="Stock (Total)">${formatStock(item.stock)}</td>
              <td data-label="Stock NSW">${formatStock(item.stockNsw)}</td>
              <td data-label="Stock QLD">${formatStock(item.stockQld)}</td>
              <td data-label="Stock VIC">${formatStock(item.stockVic)}</td>
              <td data-label="Stock SA">${formatStock(item.stockSa)}</td>
            `;
          }

          row.innerHTML = rowHtml;
          itemsContainer.appendChild(row);
        });
      } else {
        const emptyRow = document.createElement('tr');
        const colspan = isSuperAdmin ? COLUMN_COUNT_ADMIN : COLUMN_COUNT_REGULAR;
        emptyRow.innerHTML = `<td colspan="${colspan}" class="table__empty">No items found in this quote.</td>`;
        itemsContainer.appendChild(emptyRow);
      }

      loadingDiv.style.display = 'none';
      contentDiv.style.display = 'block';
    } catch (error) {
      console.error('Failed to load quote details:', error);
      loadingDiv.style.display = 'none';
      errorDiv.style.display = 'block';
    }
  }

  async function deleteQuote(quoteNumber, companyId) {
    if (!confirm(`Are you sure you want to delete quote ${quoteNumber}? This action cannot be undone.`)) {
      return;
    }

    try {
      const response = await fetch(`/api/quotes/${quoteNumber}?companyId=${companyId}`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error('Failed to delete quote');
      }

      // Reload the page to show updated quotes list
      window.location.reload();
    } catch (error) {
      console.error('Failed to delete quote:', error);
      alert('Failed to delete quote. Please try again.');
    }
  }

  async function loadCompanyUsers(companyId) {
    try {
      const response = await fetch(`/api/companies/${companyId}/members`);
      if (!response.ok) {
        throw new Error('Failed to load company users');
      }
      const data = await response.json();
      return data.members || [];
    } catch (error) {
      console.error('Failed to load company users:', error);
      return [];
    }
  }

  async function openAssignModal(quoteNumber, companyId, currentAssignedUserId) {
    const modal = document.getElementById('quote-assign-modal');
    const modalTitle = document.getElementById('modal-assign-title');
    const form = document.getElementById('quote-assign-form');
    const userSelect = document.getElementById('assign-user-select');
    const unassignButton = document.getElementById('unassign-button');
    const errorDiv = document.getElementById('modal-assign-error');

    if (!modal || !modalTitle || !form || !userSelect || !unassignButton || !errorDiv) {
      return;
    }

    // Reset modal state
    modalTitle.textContent = `Assign Quote ${quoteNumber}`;
    errorDiv.style.display = 'none';
    userSelect.innerHTML = '<option value="">Select a user...</option>';

    // Store quote info for submission
    form.dataset.quoteNumber = quoteNumber;
    form.dataset.companyId = companyId;

    // Load company users
    const users = await loadCompanyUsers(companyId);
    users.forEach((user) => {
      const option = document.createElement('option');
      option.value = user.user_id;
      option.textContent = user.email || `User ${user.user_id}`;
      if (user.user_id === parseInt(currentAssignedUserId, 10)) {
        option.selected = true;
      }
      userSelect.appendChild(option);
    });

    openModal(modal);
  }

  async function assignQuote(quoteNumber, companyId, assignedUserId) {
    const errorDiv = document.getElementById('modal-assign-error');
    
    try {
      const response = await requestJson(
        `/api/quotes/${quoteNumber}/assign?companyId=${companyId}`,
        {
          method: 'POST',
          body: JSON.stringify({
            assignedUserId: assignedUserId || null,
          }),
        }
      );

      // Close modal and reload page
      const modal = document.getElementById('quote-assign-modal');
      closeModal(modal);
      window.location.reload();
    } catch (error) {
      console.error('Failed to assign quote:', error);
      if (errorDiv) {
        errorDiv.style.display = 'block';
        errorDiv.textContent = error.message || 'Failed to assign quote. Please try again.';
      }
    }
  }

  function bindQuoteViewButtons() {
    document.querySelectorAll('[data-quote-view]').forEach((button) => {
      button.addEventListener('click', async (event) => {
        const quoteNumber = button.dataset.quoteNumber;
        const companyId = button.dataset.companyId;
        
        if (!quoteNumber || !companyId) {
          return;
        }

        await loadQuoteDetails(quoteNumber, companyId);
      });
    });
  }

  function bindQuoteDeleteButtons() {
    document.querySelectorAll('[data-quote-delete]').forEach((button) => {
      button.addEventListener('click', async (event) => {
        const quoteNumber = button.dataset.quoteNumber;
        const companyId = button.dataset.companyId;
        
        if (!quoteNumber || !companyId) {
          return;
        }

        await deleteQuote(quoteNumber, companyId);
      });
    });
  }

  function bindQuoteAssignButtons() {
    document.querySelectorAll('[data-quote-assign]').forEach((button) => {
      button.addEventListener('click', async (event) => {
        const quoteNumber = button.dataset.quoteNumber;
        const companyId = button.dataset.companyId;
        const assignedUserId = button.dataset.assignedUserId;
        
        if (!quoteNumber || !companyId) {
          return;
        }

        await openAssignModal(quoteNumber, companyId, assignedUserId);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('quote-details-modal');
    const assignModal = document.getElementById('quote-assign-modal');
    const assignForm = document.getElementById('quote-assign-form');
    const unassignButton = document.getElementById('unassign-button');
    
    bindModalDismissal(modal);
    bindModalDismissal(assignModal);
    bindQuoteViewButtons();
    bindQuoteDeleteButtons();
    bindQuoteAssignButtons();

    // Handle assign form submission
    if (assignForm) {
      assignForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const quoteNumber = assignForm.dataset.quoteNumber;
        const companyId = assignForm.dataset.companyId;
        const userSelect = document.getElementById('assign-user-select');
        const assignedUserId = userSelect.value ? parseInt(userSelect.value, 10) : null;
        
        await assignQuote(quoteNumber, companyId, assignedUserId);
      });
    }

    // Handle unassign button
    if (unassignButton) {
      unassignButton.addEventListener('click', async (event) => {
        event.preventDefault();
        const quoteNumber = assignForm.dataset.quoteNumber;
        const companyId = assignForm.dataset.companyId;
        
        await assignQuote(quoteNumber, companyId, null);
      });
    }
  });
})();
