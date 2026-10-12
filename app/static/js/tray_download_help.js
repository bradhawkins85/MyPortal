(function () {
  'use strict';

  const tutorial = document.querySelector('[data-tray-download-help]');
  if (!tutorial) return;

  const controls = tutorial.querySelector('[data-download-browser-controls]');
  const selector = tutorial.querySelector('#tray-download-browser');
  const status = tutorial.querySelector('[data-download-browser-status]');
  const guides = tutorial.querySelectorAll('[data-download-browser]');
  if (!controls || !selector || !status) return;

  function detectBrowser() {
    const agent = navigator.userAgent || '';
    const brands = navigator.userAgentData && navigator.userAgentData.brands;
    const hasBrand = (name) => Array.isArray(brands) && brands.some((item) => item.brand === name);

    // Edge also advertises Chrome/Chromium, so check it first.
    if (hasBrand('Microsoft Edge') || /Edg(?:e|A|iOS)?\//i.test(agent)) return 'edge';
    if (/OPR\/|Opera|SamsungBrowser|Vivaldi|YaBrowser|DuckDuckGo|Brave|UCBrowser|Firefox|FxiOS/i.test(agent)) return 'all';
    if (hasBrand('Google Chrome') || /Chrome\/|CriOS\//i.test(agent)) return 'chrome';
    return 'all';
  }

  function showGuide(browser, detected) {
    guides.forEach((guide) => {
      guide.hidden = browser !== 'all' && guide.dataset.downloadBrowser !== browser;
    });
    selector.value = browser;
    if (browser === 'all') {
      status.textContent = detected
        ? 'Choose your browser below, or follow either guide if you are not sure.'
        : 'Showing instructions for both browsers.';
    } else {
      const name = browser === 'edge' ? 'Microsoft Edge' : 'Google Chrome';
      status.textContent = detected
        ? 'Browser detected: ' + name + '. You can change the instructions below.'
        : 'Showing instructions for ' + name + '.';
    }
  }

  showGuide(detectBrowser(), true);
  controls.hidden = false;
  selector.addEventListener('change', () => showGuide(selector.value, false));
})();
