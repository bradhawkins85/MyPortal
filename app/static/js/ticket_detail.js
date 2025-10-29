(function () {
  function getLineHeightPx(element) {
    const computed = window.getComputedStyle(element);
    const lineHeight = parseFloat(computed.lineHeight);
    if (!Number.isNaN(lineHeight)) {
      return lineHeight;
    }

    const fontSize = parseFloat(computed.fontSize);
    if (!Number.isNaN(fontSize)) {
      return fontSize * 1.5;
    }

    return 24;
  }

  function initialiseTimelineMessage(wrapper, toggle) {
    const content = wrapper.querySelector('[data-timeline-message-content]');
    if (!content) {
      return;
    }

    const lineHeight = getLineHeightPx(content);
    const maxCollapsedHeight = lineHeight * 5;
    const currentScrollHeight = content.scrollHeight;

    if (currentScrollHeight <= maxCollapsedHeight + 1) {
      return;
    }

    const moreLabel = toggle.dataset.moreLabel || 'Show more';
    const lessLabel = toggle.dataset.lessLabel || 'Show less';

    function collapse() {
      const collapsedHeight = `${maxCollapsedHeight}px`;
      wrapper.style.maxHeight = collapsedHeight;
      content.style.maxHeight = collapsedHeight;
      wrapper.classList.add('timeline__message--collapsed');
      content.classList.add('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = moreLabel;
    }

    function expand() {
      wrapper.style.maxHeight = '';
      content.style.maxHeight = '';
      wrapper.classList.remove('timeline__message--collapsed');
      content.classList.remove('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.textContent = lessLabel;
    }

    collapse();
    toggle.hidden = false;

    toggle.addEventListener('click', () => {
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
      if (isExpanded) {
        collapse();
      } else {
        expand();
      }
    });
  }

  function ready() {
    const messageWrappers = document.querySelectorAll('[data-timeline-message]');

    messageWrappers.forEach((wrapper) => {
      const toggle = wrapper.parentElement?.querySelector('[data-timeline-message-toggle]');
      if (!(toggle instanceof HTMLButtonElement)) {
        return;
      }

      initialiseTimelineMessage(wrapper, toggle);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
