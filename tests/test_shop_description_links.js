const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function loadHelper(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'app/static/js', file), 'utf8');
  const match = source.match(/function openDescriptionLinksInNewTab\(root\) \{[\s\S]*?\n {2}\}\n/);
  assert.ok(match, `helper missing from ${file}`);
  assert.match(source, /innerHTML = descriptionHtml;\s*openDescriptionLinksInNewTab\(descriptionDiv\);/);
  return new Function(`${match[0]}; return openDescriptionLinksInNewTab;`)();
}

function fakeLink(href) {
  return {target: '', rel: '', getAttribute: (name) => (name === 'href' ? href : null)};
}

for (const file of ['shop.js', 'shop_packages.js']) {
  test(`${file}: external description links open in a new tab`, () => {
    const helper = loadHelper(file);
    const links = {
      web: fakeLink('https://example.com/spec'),
      relative: fakeLink('/shop?category=1'),
      anchor: fakeLink('#specs'),
      mail: fakeLink('mailto:sales@example.com'),
    };
    helper({querySelectorAll: () => Object.values(links)});
    for (const key of ['web', 'relative']) {
      assert.equal(links[key].target, '_blank');
      assert.equal(links[key].rel, 'noopener noreferrer');
    }
    for (const key of ['anchor', 'mail']) {
      assert.equal(links[key].target, '');
    }
  });
}
