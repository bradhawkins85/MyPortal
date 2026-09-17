document.addEventListener('DOMContentLoaded', function () {
  var element = document.querySelector('[data-spam-purge-config]');
  if (!element) return;

  var config = {};
  try {
    config = JSON.parse(element.getAttribute('data-spam-purge-config') || '{}');
  } catch (error) {
    console.error('Failed to parse spam purge config.', error);
  }

  var focusButton = document.querySelector('[data-focus-search]');
  if (focusButton) {
    focusButton.addEventListener('click', function () {
      var sender = document.getElementById('sender');
      var searchSection = document.getElementById('new-spam-search');
      if (sender) sender.focus();
      if (searchSection) searchSection.scrollIntoView({ behavior: 'smooth' });
    });
  }

  if (config.refresh) {
    window.setTimeout(function () {
      window.location.reload();
    }, 10000);
  }
});
