(function () {
  function getCellValue(row, index) {
    const cell = row.children[index];
    return cell ? cell.getAttribute('data-value') || cell.textContent.trim() : '';
  }

  function parseValue(value, type) {
    if (type === 'number') {
      const parsed = parseFloat(value);
      return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    }
    if (type === 'date') {
      const timestamp = Date.parse(value);
      return Number.isNaN(timestamp) ? Number.NEGATIVE_INFINITY : timestamp;
    }
    return value.toLowerCase();
  }

  function sortTable(table, columnIndex, type) {
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const current = table.getAttribute('data-sort-index') === String(columnIndex)
      ? table.getAttribute('data-sort-order')
      : null;
    const ascending = current !== 'asc';

    rows.sort((a, b) => {
      const valueA = parseValue(getCellValue(a, columnIndex), type);
      const valueB = parseValue(getCellValue(b, columnIndex), type);
      if (valueA < valueB) {
        return ascending ? -1 : 1;
      }
      if (valueA > valueB) {
        return ascending ? 1 : -1;
      }
      return 0;
    });

    const fragment = document.createDocumentFragment();
    rows.forEach((row) => fragment.appendChild(row));
    tbody.appendChild(fragment);
    table.setAttribute('data-sort-index', String(columnIndex));
    table.setAttribute('data-sort-order', ascending ? 'asc' : 'desc');
  }

  function attachSorting(table) {
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach((header, index) => {
      header.addEventListener('click', () => {
        sortTable(table, index, header.getAttribute('data-sort') || 'string');
      });
    });
  }

  function attachFilters() {
    document.querySelectorAll('[data-table-filter]').forEach((input) => {
      const tableId = input.getAttribute('data-table-filter');
      const table = document.getElementById(tableId);
      if (!table) {
        return;
      }
      input.addEventListener('input', () => {
        const term = input.value.trim().toLowerCase();
        table.querySelectorAll('tbody tr').forEach((row) => {
          const text = row.textContent || '';
          row.style.display = !term || text.toLowerCase().includes(term) ? '' : 'none';
        });
      });
    });
  }

  function convertUtcElements() {
    document.querySelectorAll('[data-utc]').forEach((element) => {
      const iso = element.getAttribute('data-utc');
      if (!iso) {
        return;
      }
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      const formatted = date.toLocaleString();
      element.textContent = formatted;
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('table[data-table]').forEach((table) => {
      attachSorting(table);
    });
    attachFilters();
    convertUtcElements();
  });
})();
