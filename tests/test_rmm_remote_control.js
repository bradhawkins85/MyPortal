const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(responses, {blockPopups = false} = {}) {
  class Element {
    constructor() { this.hidden = true; this.attributes = {}; this.listeners = {}; this.classList = {toggle() {}}; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    closest() { return this; }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
  }
  const message = new Element();
  const link = new Element();
  const dismiss = new Element();
  const box = new Element();
  box.querySelector = selector => selector.includes('message') ? message : selector.includes('dismiss') ? dismiss : link;
  const button = new Element();
  button.dataset = {rmmRemote: 'meshcentral', rmmRemoteAsset: '10', rmmRemoteName: 'PC'};
  const clicks = [];
  const opened = [];
  const requests = [];
  const timers = [];
  // A tab reserved on the click: about:blank until MyPortal sends it to MeshCentral.
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
  return {message, link, button, box, dismiss, opened, window, requests, tab,
    click: async () => { for (const click of clicks) await click({target: button, preventDefault() {}}); },
    poll: async () => { timers.shift()(); await new Promise(resolve => setImmediate(resolve)); },
  };
}
const openUrl = 'https://mesh.example.com/?login=test&filter=PC';
const activating = {id: 1, label: 'MeshCentral', status: 'activating', run_status: 'running', open_url: openUrl};
const ready = {label: 'MeshCentral', status: 'ready', launch_url: 'https://mesh.example.com/?login=test'};

test('opens MeshCentral on the click while the script runs, with progress on the page', async () => {
  const app = setup([activating, ready]);
  await app.click();
  assert.deepEqual(app.opened, [['', '_blank']]);
  assert.equal(app.tab.opener, null);
  assert.equal(app.tab.location.href, openUrl);
  assert.equal(app.message.textContent,
    'Switching on MeshCentral on PC: the script is running… MeshCentral is open in a new tab.');
  await app.poll();
  // The open tab is left alone; the link goes straight to the session.
  assert.equal(app.opened.length, 1);
  assert.equal(app.tab.location.href, openUrl);
  assert.equal(app.tab.closed, false);
  assert.equal(app.link.href, ready.launch_url);
  assert.equal(app.link.hidden, false);
  assert.match(app.message.textContent, /ready on PC.*go straight to it/);
  assert.equal(app.button.disabled, false);
});

test('offers MeshCentral as a link on the page when the browser blocks the tab', async () => {
  const app = setup([activating, ready], {blockPopups: true});
  await app.click();
  assert.equal(app.link.href, openUrl);
  assert.equal(app.link.hidden, false);
  await app.poll();
  assert.deepEqual(app.opened, [['', '_blank']]);
  assert.equal(app.link.href, ready.launch_url);
  assert.match(app.message.textContent, /ready.*use the link/i);
});

for (const failure of [{...activating, status: 'failed', error: 'Installation failed'}, new Error('Network failed')]) {
  test('failed activation leaves MeshCentral open: ' + (failure.error || failure.message), async () => {
    const app = setup([activating, failure]);
    await app.click();
    await app.poll();
    assert.equal(app.opened.length, 1);
    assert.equal(app.tab.closed, false);
    assert.equal(app.button.disabled, false);
    assert.match(app.message.textContent, /failed/);
  });
}

test('a failed start closes the reserved tab', async () => {
  const app = setup([new Error('MeshCentral is not configured')]);
  await app.click();
  assert.equal(app.tab.closed, true);
  assert.equal(app.tab.location.href, undefined);
  assert.equal(app.link.hidden, true);
  assert.match(app.message.textContent, /not configured/);
});

test('immediately ready session sends the reserved tab to its actual URL', async () => {
  const app = setup([ready]);
  await app.click();
  assert.equal(app.tab.location.href, ready.launch_url);
});

test('invalid launch URL never reaches a tab', async () => {
  const app = setup([{...ready, launch_url: 'javascript:alert(1)'}]);
  await app.click();
  assert.equal(app.tab.closed, true);
  assert.equal(app.tab.location.href, undefined);
  assert.equal(app.link.hidden, true);
  assert.match(app.message.textContent, /cannot open/);
});

test('invalid open URL never reaches a tab', async () => {
  const app = setup([{...activating, open_url: 'javascript:alert(1)'}]);
  await app.click();
  assert.equal(app.tab.closed, true);
  assert.equal(app.tab.location.href, undefined);
  assert.equal(app.link.hidden, true);
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

test('RustDesk menu offers only the sessions the company allows', () => {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.classList = {add() {}}; }
    setAttribute(key, value) { this.attributes[key] = value; }
    append(...items) { this.children.push(...items.filter(item => typeof item === 'object')); }
    prepend(...items) { this.children.unshift(...items.filter(item => typeof item === 'object')); }
    querySelector(selector) { return selector === 'summary' ? this.children.find(child => child.tag === 'summary') : null; }
  }
  const window = {};
  const document = {createElement: tag => new Node(tag), addEventListener() {}, querySelector: () => null, querySelectorAll: () => []};
  vm.runInNewContext(fs.readFileSync('app/static/js/rmm_remote_control.js', 'utf8'), {window, document, Element: Node});
  const modesOf = providers => {
    const wrapper = window.MyPortalRmm.assetActions(7, 'PC', providers);
    const panel = wrapper.children[0].children.find(child => child.tag === 'div');
    return panel.children.map(item => item.attributes['data-rmm-remote-mode']);
  };
  const restricted = [{provider: 'rustdesk', label: 'RustDesk', modes: [
    {mode: 'remote_prompt', label: 'Remote desktop (Prompt)'},
    {mode: 'file_transfer', label: 'File transfer'},
    {mode: 'terminal', label: 'Terminal'},
  ]}];
  assert.deepEqual(modesOf(restricted), ['remote_prompt', 'file_transfer', 'terminal']);
  // Older payloads without modes keep every session.
  assert.deepEqual(modesOf([{provider: 'rustdesk', label: 'RustDesk'}]),
    ['remote', 'remote_prompt', 'file_transfer', 'terminal']);
});

test('the status box can be dismissed and stays closed while the session finishes', async () => {
  const app = setup([{...activating, label: 'RustDesk', open_url: ''},
    {label: 'RustDesk', status: 'ready', launch_url: 'rustdesk://connection/new/123'}]);
  app.button.dataset.rmmRemote = 'rustdesk';
  await app.click();
  assert.equal(app.box.hidden, false);
  app.dismiss.listeners.click();
  assert.equal(app.box.hidden, true);
  await app.poll();
  assert.equal(app.window.location.href, 'rustdesk://connection/new/123');
  assert.equal(app.box.hidden, true);
  // Starting another session shows the box again.
  await app.click();
  assert.equal(app.box.hidden, false);
});
