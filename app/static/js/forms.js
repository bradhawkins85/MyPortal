(function () {
  function parseJson(elementId) {
    const element = document.getElementById(elementId);
    if (!element) {
      return [];
    }
    try {
      return JSON.parse(element.textContent || '[]');
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return [];
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('forms-data');
    const iframe = document.getElementById('form-frame');
    const buttons = Array.from(document.querySelectorAll('[data-form-switch]'));

    if (buttons.length === 0 || !iframe) {
      return;
    }

    buttons.forEach((button, index) => {
      button.addEventListener('click', () => {
        buttons.forEach((btn) => btn.classList.remove('is-active'));
        button.classList.add('is-active');
        const url = button.getAttribute('data-form-url') || '';
        if (url) {
          iframe.src = url;
        } else if (data[index] && data[index].iframe_url) {
          iframe.src = data[index].iframe_url;
        }
      });
    });
  });
})();
