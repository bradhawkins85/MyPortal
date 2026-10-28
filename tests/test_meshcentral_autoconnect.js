const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Runs app/static/meshcentral/myportal-autoconnect.js against a stand-in for
// MeshCentral's page and returns its button and a way to run the next poll.
function setup(search, buttonId) {
  const button = {id: buttonId, disabled: true, offsetParent: null, clicks: 0, click() { this.clicks += 1; }};
  const timers = [];
  const window = {location: {search}, xxcurrentView: -1, setTimeout: callback => timers.push(callback)};
  const document = {readyState: 'complete', getElementById: id => (id === buttonId ? button : null)};
  vm.runInNewContext(fs.readFileSync('app/static/meshcentral/myportal-autoconnect.js', 'utf8'),
    {window, document, URLSearchParams, Date});
  return {button, window, timers, tick: () => timers.shift()()};
}

test('presses Connect once the device view shows and the button is usable', () => {
  const page = setup('?login=x&gotonode=abc&viewmode=12&hide=31&myportalconnect=1', 'connectbutton2');
  assert.equal(page.button.clicks, 0);
  page.window.xxcurrentView = 12;
  page.tick();
  assert.equal(page.button.clicks, 0, 'still disabled while the device is offline');
  page.button.disabled = false;
  page.button.offsetParent = {};
  page.tick();
  assert.equal(page.button.clicks, 1);
  assert.equal(page.timers.length, 0, 'stops after one press');
});

for (const [viewmode, buttonId] of [['9', 'p9Connect'], ['11', 'connectbutton1'], ['13', 'p13Connect']]) {
  test('view ' + viewmode + ' uses ' + buttonId, () => {
    const page = setup('?gotonode=abc&viewmode=' + viewmode + '&myportalconnect=1', buttonId);
    Object.assign(page.button, {disabled: false, offsetParent: {}});
    page.window.xxcurrentView = Number(viewmode);
    page.tick();
    assert.equal(page.button.clicks, 1);
  });
}

test('does nothing on pages MyPortal did not open', () => {
  for (const search of ['?gotonode=abc&viewmode=11', '?viewmode=17&myportalconnect=1', '']) {
    const page = setup(search, 'connectbutton1');
    assert.equal(page.timers.length, 0);
    assert.equal(page.button.clicks, 0);
  }
});
