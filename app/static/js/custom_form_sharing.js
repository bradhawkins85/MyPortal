(function () {
  'use strict';

  document.querySelectorAll('[data-cf-print]').forEach((button) => {
    button.addEventListener('click', () => window.print());
  });

  document.querySelectorAll('[data-cf-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const target = document.getElementById(button.getAttribute('data-cf-copy'));
      if (!target) return;
      const original = button.textContent;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(target.value);
        } else {
          target.select();
          document.execCommand('copy');
        }
        button.textContent = 'Copied';
      } catch (err) {
        target.select();
        button.textContent = 'Press Ctrl+C to copy';
      }
      setTimeout(() => { button.textContent = original; }, 2000);
    });
  });
})();
