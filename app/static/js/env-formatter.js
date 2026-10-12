/* Pure browser-only .env analysis. Never send or persist pasted values. */
(function (root) {
  'use strict';

  function closingQuote(value, quote, start) {
    for (let i = start; i < value.length; i += 1) {
      if (value[i] !== quote) continue;
      let escapes = 0;
      for (let j = i - 1; j >= 0 && value[j] === '\\'; j -= 1) escapes += 1;
      if (escapes % 2 === 0) return i;
    }
    return -1;
  }

  function analyseEnvironment(input, catalog) {
    const schema = new Map(catalog.map(item => [item.key, item]));
    const lines = input.split('\n');
    const blocks = [];
    const issues = [];
    const seen = new Map();
    let pending = [];
    let interpolation = false;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (/^\s*(?:#.*)?$/.test(line)) {
        pending.push(line);
        continue;
      }
      const match = line.replace(/\r$/, '').match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
      const lineNumber = i + 1;
      const raw = [line];
      let valid = Boolean(match);
      if (match && /^["']/.test(match[2])) {
        const quote = match[2][0];
        let value = match[2];
        let close = closingQuote(value, quote, 1);
        while (close === -1 && i + 1 < lines.length) {
          i += 1;
          raw.push(lines[i]);
          value += '\n' + lines[i];
          close = closingQuote(value, quote, 1);
        }
        valid = close !== -1 && /^\s*(?:#.*)?$/.test(value.slice(close + 1));
      }
      if (!valid) issues.push(`Line ${lineNumber}: unrecognized syntax or unterminated quoted value; preserved in Stale content.`);
      const key = valid ? match[1] : null;
      if (key) {
        const count = (seen.get(key) || 0) + 1;
        seen.set(key, count);
        if (count === 2) issues.push(`${key}: duplicate assignments preserved in their original relative order.`);
        if (raw.join('\n').includes('${')) interpolation = true;
      }
      blocks.push({key, raw: [...pending, ...raw].join('\n'), meta: schema.get(key)});
      pending = [];
    }
    if (pending.length) blocks.push({key: null, raw: pending.join('\n')});
    const missing = catalog.filter(item => item.key === item.aliases[0] && !item.aliases.some(key => seen.has(key)));
    const extra = [...seen.keys()].filter(key => !schema.has(key));
    const groups = new Map();
    const stale = [];
    for (const block of blocks) {
      if (!block.meta) { stale.push(block.raw); continue; }
      const id = `${block.meta.active ? 'active' : 'disabled'}:${block.meta.group}`;
      if (!groups.has(id)) groups.set(id, {label: block.meta.group, active: block.meta.active, blocks: []});
      groups.get(id).blocks.push(block.raw);
    }
    const output = [];
    for (const active of [true, false]) {
      output.push(active ? '# === Active configuration ===' : '# === Disabled modules and feature packs ===');
      const selected = [...groups.values()].filter(group => group.active === active)
        .sort((a, b) => a.label.localeCompare(b.label));
      for (const group of selected) {
        output.push(`# --- ${group.label} ---`, ...group.blocks, '');
      }
    }
    output.push('# === Stale content — retained for admin review ===', ...stale);
    if (interpolation) issues.push('Variable interpolation detected (${…}). Reordering can change dotenv expansion. Review references before replacing the server file.');
    return {formatted: output.join('\n') + '\n', missing, extra, issues, count: seen.size};
  }

  // Export the pure analyser for Node regression tests; browsers only initialise the UI.
  if (typeof module !== 'undefined' && module.exports) module.exports = {analyseEnvironment};
  if (typeof document === 'undefined') return;
  const form = document.getElementById('env-checker');
  if (!form) return;
  const catalog = JSON.parse(document.getElementById('env-catalog').textContent);
  const input = document.getElementById('env-input');
  const output = document.getElementById('env-output');
  const results = document.getElementById('env-results');
  const status = document.getElementById('env-status');
  const copy = document.getElementById('env-copy');
  const download = document.getElementById('env-download');

  function clearResults() {
    output.value = '';
    results.hidden = true;
    copy.disabled = true;
    download.disabled = true;
    status.textContent = '';
  }
  input.addEventListener('input', clearResults);
  document.getElementById('env-clear').addEventListener('click', () => {
    input.value = '';
    clearResults();
    input.focus();
  });
  function showList(id, items, empty) {
    const list = document.getElementById(id);
    list.replaceChildren();
    for (const text of (items.length ? items : [empty])) {
      const item = document.createElement('li');
      item.textContent = text;
      list.appendChild(item);
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!input.value.trim()) {
      status.textContent = 'Paste your .env contents first.';
      input.focus();
      return;
    }
    const result = analyseEnvironment(input.value, catalog);
    output.value = result.formatted;
    showList('env-missing', result.missing.map(item => `${item.key} — ${item.group}${item.active ? '' : ' (disabled)'}`), 'No missing variables.');
    showList('env-extra', result.extra, 'No extra variables.');
    showList('env-issues', result.issues, 'No syntax, duplicate, or interpolation warnings detected.');
    status.textContent = `Checked ${result.count} variables. ${result.missing.length} missing from the available schema; ${result.extra.length} extra; ${result.issues.length} warnings.`;
    results.hidden = false;
    copy.disabled = false;
    download.disabled = false;
  });
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(output.value);
      status.textContent = 'Formatted .env copied to clipboard.';
    } catch (_) {
      output.focus();
      output.select();
      status.textContent = 'Clipboard access unavailable. The output is selected; copy it manually.';
    }
  });
  download.addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob([output.value], {type: 'application/octet-stream'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'formatted.env';
    link.click();
    status.textContent = 'Downloaded as formatted.env. Review it before renaming it to .env on the server.';
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  // Clear secrets when navigating away, including before the page enters the back/forward cache.
  root.addEventListener('pagehide', () => { input.value = ''; clearResults(); });
})(typeof window !== 'undefined' ? window : globalThis);
