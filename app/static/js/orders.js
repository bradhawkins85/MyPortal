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

  async function loadOrderDetails(orderNumber, companyId) {
    const modal = document.getElementById('order-details-modal');
    const modalTitle = document.getElementById('modal-order-title');
    const loadingDiv = document.getElementById('modal-order-loading');
    const contentDiv = document.getElementById('modal-order-content');
    const errorDiv = document.getElementById('modal-order-error');
    const itemsContainer = document.getElementById('modal-order-items');

    if (!modal || !modalTitle || !loadingDiv || !contentDiv || !errorDiv || !itemsContainer) {
      return;
    }

    // Check if user is super admin
    const isSuperAdmin = document.body.dataset.superAdmin === 'true';

    // Reset modal state
    modalTitle.textContent = `Order ${orderNumber}`;
    loadingDiv.style.display = 'block';
    contentDiv.style.display = 'none';
    errorDiv.style.display = 'none';
    itemsContainer.innerHTML = '';

    openModal(modal);

    try {
      const data = await requestJson(`/api/orders/${orderNumber}?companyId=${companyId}`);
      
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
        emptyRow.innerHTML = `<td colspan="${colspan}" class="table__empty">No items found in this order.</td>`;
        itemsContainer.appendChild(emptyRow);
      }

      loadingDiv.style.display = 'none';
      contentDiv.style.display = 'block';
    } catch (error) {
      console.error('Failed to load order details:', error);
      loadingDiv.style.display = 'none';
      errorDiv.style.display = 'block';
    }
  }

  function bindOrderViewButtons() {
    document.querySelectorAll('[data-order-view]').forEach((button) => {
      button.addEventListener('click', async (event) => {
        const orderNumber = button.dataset.orderNumber;
        const companyId = button.dataset.companyId;
        
        if (!orderNumber || !companyId) {
          return;
        }

        await loadOrderDetails(orderNumber, companyId);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('order-details-modal');
    bindModalDismissal(modal);
    bindOrderViewButtons();
  });
})();
