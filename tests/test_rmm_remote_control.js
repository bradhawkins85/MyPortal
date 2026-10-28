const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(responses, {blockPopups = false} = {}) {
  class Element {
    constructor() { this.hidden = true; this.attributes = {}; this.classList = {toggle() {}}; }
    closest() { return this; }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
  }
  const message = new Element();
  const link = new Element();
  const box = new Element();
  box.querySelector = selector => selector.includes('message') ? message : link;
  const button = new Element();
  button.dataset = {rmmRemote: 'meshcentral', rmmRemoteAsset: '10', rmmRemoteName: 'PC'};
  const clicks = [];
  const opened = [];
  const requests = [];
  const timers = [];
  // The tab opened once the session is ready: about:blank, then sent on.
  const tab = {closed: false, opener: {}, document: {title: '', body: {textContent: ''}}, location: {},
    close() { this.closed = true; }};
  tab.location.replace = url => { tab.location.href = url; };
  const window = {location: {}, open: (...args) => { opened.push(args); return blockPopups ? null : tab; }};
  const document = {
    addEventListener: (name, callback) => { if (name === 'click') clicks.push(callback); },
    querySelector: selector => selector === '[data-rmm-remote-status]' ? box : null,
    querySelectorAll: selector => selector === '[data-rmm-remote]' ? [button] : [],
  };
  vm.runInNewContext(fs.readFileSync('app/static/js/rmm_remote_control.js', 'utf8'), {
    window, document, Element,
    fetch: async (url, options) => {
      requests.push({url, options});
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return {ok: true, json: async () => ({session: next})};
    },
    setTimeout: callback => { timers.push(callback); return timers.length; },
    clearTimeout() {},
  });
  return {message, link, button, opened, window, requests, tab,
    click: async () => { for (const click of clicks) await click({target: button, preventDefault() {}}); },
    poll: async () => { timers.shift()(); await new Promise(resolve => setImmediate(resolve)); },
  };
}
const activating = {id: 1, label: 'MeshCentral', status: 'activating', run_status: 'running'};
const ready = {label: 'MeshCentral', status: 'ready', launch_url: 'https://mesh.example.com/?login=test'};

test('shows progress on the page, then opens MeshCentral in a new tab once ready', async () => {
  const app = setup([activating, ready]);
  await app.click();
  assert.equal(app.opened.length, 0);
  assert.equal(app.message.textContent, 'Switching on MeshCentral on PC: the script is running…');
  await app.poll();
  assert.deepEqual(app.opened, [['', '_blank']]);
  assert.equal(app.tab.opener, null);
  assert.equal(app.tab.location.href, ready.launch_url);
  assert.equal(app.link.href, ready.launch_url);
  assert.equal(app.link.hidden, false);
  assert.match(app.message.textContent, /opening.*new tab/);
  assert.equal(app.button.disabled, false);
});

test('offers a link on the page when the browser blocks the new tab', async () => {
  const app = setup([activating, ready], {blockPopups: true});
  await app.click();
  await app.poll();
  assert.deepEqual(app.opened, [['', '_blank']]);
  assert.equal(app.link.href, ready.launch_url);
  assert.equal(app.link.hidden, false);
  assert.match(app.message.textContent, /ready.*use the link/i);
});

for (const failure of [{...activating, status: 'failed', error: 'Installation failed'}, new Error('Network failed')]) {
  test('failed activation opens no tab: ' + (failure.error || failure.message), async () => {
    const app = setup([activating, failure]);
    await app.click();
    await app.poll();
    assert.equal(app.opened.length, 0);
    assert.equal(app.link.hidden, true);
    assert.equal(app.button.disabled, false);
    assert.match(app.message.textContent, /failed/);
  });
}

test('immediately ready session opens its actual URL', async () => {
  const app = setup([ready]);
  await app.click();
  assert.equal(app.tab.location.href, ready.launch_url);
});

test('invalid launch URL never reaches a tab', async () => {
  const app = setup([{...ready, launch_url: 'javascript:alert(1)'}]);
  await app.click();
  assert.equal(app.opened.length, 0);
  assert.equal(app.link.hidden, true);
  assert.match(app.message.textContent, /cannot open/);
});

test('MeshCentral registry option sends its mode', async () => {
  const app = setup([{...ready, mode: 'registry', mode_label: 'Registry'}]);
  app.button.dataset.rmmRemoteMode = 'registry';
  await app.click();
  assert.deepEqual(JSON.parse(app.requests[0].options.body), {provider: 'meshcentral', mode: 'registry'});
  assert.match(app.message.textContent, /MeshCentral registry/);
});

test('RustDesk continues to launch through its protocol handler', async () => {
  const app = setup([{label: 'RustDesk', status: 'ready', launch_url: 'rustdesk://connection/new/123'}]);
  app.button.dataset.rmmRemote = 'rustdesk';
  await app.click();
  assert.equal(app.opened.length, 0);
  assert.equal(app.window.location.href, 'rustdesk://connection/new/123');
});


test('RustDesk prompt option sends its mode and launches without a password', async () => {
  const app = setup([{label: 'RustDesk', mode: 'remote_prompt', mode_label: 'Remote desktop (Prompt)',
    status: 'ready', launch_url: 'rustdesk://connection/new/123'}]);
  app.button.dataset.rmmRemote = 'rustdesk';
  app.button.dataset.rmmRemoteMode = 'remote_prompt';
  await app.click();
  assert.deepEqual(JSON.parse(app.requests[0].options.body), {provider: 'rustdesk', mode: 'remote_prompt'});
  assert.equal(app.window.location.href, 'rustdesk://connection/new/123');
  assert.match(app.message.textContent, /remote desktop \(prompt\)/);
});
