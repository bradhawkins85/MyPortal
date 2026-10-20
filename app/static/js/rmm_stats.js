(function () {
  'use strict';

  // Machine stats charts: show the time and values at the point under the
  // pointer (or the point picked with the arrow keys).

  const SNAP = 8; // viewBox units: how far another series' point may sit from the hovered time

  function formatDate(value) {
    const date = new Date(/Z|[+-]\d\d:?\d\d$/.test(value) ? value : value + 'Z');
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function nearest(points, x) {
    let best = null;
    points.forEach((point) => {
      if (!best || Math.abs(point[0] - x) < Math.abs(best[0] - x)) {
        best = point;
      }
    });
    return best;
  }

  function setUp(canvas) {
    let data;
    try {
      data = JSON.parse(canvas.getAttribute('data-rmm-stats-chart'));
    } catch (error) {
      return;
    }
    const series = data.series || [];
    const all = series.flat();
    if (!all.length) {
      return;
    }
    const svg = canvas.querySelector('svg');
    const height = svg.viewBox.baseVal.height;
    const cursor = canvas.querySelector('[data-rmm-stats-cursor]');
    const markers = Array.from(cursor.children);
    const tooltip = canvas.querySelector('[data-rmm-stats-tooltip]');
    const time = tooltip.querySelector('time');
    const keys = Array.from(tooltip.querySelectorAll('[data-name]'));
    const anchors = Array.from(new Set(all.map((point) => point[0]))).sort((a, b) => a - b);
    let current = -1;

    function show(x) {
      const anchor = nearest(all, x);
      current = anchors.indexOf(anchor[0]);
      time.dateTime = anchor[2];
      time.textContent = formatDate(anchor[2]);
      series.forEach((points, index) => {
        const point = nearest(points, anchor[0]);
        const near = point && Math.abs(point[0] - anchor[0]) <= SNAP;
        keys[index].hidden = !near;
        markers[index].hidden = !near;
        if (near) {
          keys[index].textContent = keys[index].dataset.name + ': ' + point[3];
          markers[index].style.left = (point[0] / data.width) * 100 + '%';
          markers[index].style.top = (point[1] / height) * 100 + '%';
        }
      });
      const left = (anchor[0] / data.width) * 100;
      cursor.style.setProperty('--x', left + '%');
      cursor.hidden = false;
      tooltip.hidden = false;
      tooltip.classList.toggle('is-flipped', left > 50);
      tooltip.style.left = left + '%';
    }

    function hide() {
      cursor.hidden = true;
      tooltip.hidden = true;
    }

    canvas.addEventListener('pointermove', (event) => {
      const box = svg.getBoundingClientRect();
      if (box.width) {
        show(((event.clientX - box.left) / box.width) * data.width);
      }
    });
    canvas.addEventListener('pointerleave', hide);

    canvas.tabIndex = 0;
    canvas.addEventListener('focus', () => show(anchors[anchors.length - 1]));
    canvas.addEventListener('blur', hide);
    canvas.addEventListener('keydown', (event) => {
      const step = { ArrowLeft: -1, ArrowRight: 1, Home: -anchors.length, End: anchors.length }[event.key];
      if (step === undefined) {
        if (event.key === 'Escape') {
          hide();
        }
        return;
      }
      event.preventDefault();
      show(anchors[Math.max(0, Math.min(anchors.length - 1, current + step))]);
    });
  }

  document.querySelectorAll('[data-rmm-stats-chart]').forEach(setUp);
})();
