// Office 365 group cleanup: member review modal, selection and scan refresh.
document.addEventListener('DOMContentLoaded', function () {
  var root = document.querySelector('[data-group-cleanup]');
  if (!root) return;

  if (root.getAttribute('data-gcl-refresh') === 'true') {
    window.setTimeout(function () { window.location.reload(); }, 5000);
  }

  var openModal = null;
  var activeTrigger = null;

  function focusable(modal) {
    return modal.querySelectorAll('button:not([disabled]), input:not([disabled]), [href]');
  }

  function onKeydown(event) {
    if (!openModal) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    } else if (event.key === 'Tab') {
      var items = focusable(openModal);
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  function open(trigger) {
    var modal = document.getElementById(trigger.getAttribute('data-gcl-open'));
    if (!modal) return;
    if (openModal) close();
    openModal = modal;
    activeTrigger = trigger;
    modal.hidden = false;
    modal.style.display = '';
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.addEventListener('keydown', onKeydown);
    var focusId = trigger.getAttribute('data-gcl-focus');
    var target = (focusId && document.getElementById(focusId)) || modal.querySelector('[data-modal-close]');
    if (target) target.focus();
  }

  function close() {
    if (!openModal) return;
    openModal.classList.remove('is-visible');
    openModal.hidden = true;
    openModal.setAttribute('aria-hidden', 'true');
    openModal = null;
    document.removeEventListener('keydown', onKeydown);
    if (activeTrigger && typeof activeTrigger.focus === 'function') activeTrigger.focus();
    activeTrigger = null;
  }

  document.querySelectorAll('[data-gcl-open]').forEach(function (trigger) {
    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      open(trigger);
    });
  });

  document.querySelectorAll('.gcl-modal').forEach(function (modal) {
    modal.querySelectorAll('[data-modal-close]').forEach(function (button) {
      button.addEventListener('click', close);
    });
    modal.addEventListener('click', function (event) {
      if (event.target === modal) close();
    });

    var form = modal.querySelector('[data-gcl-form]');
    var boxes = Array.prototype.slice.call(modal.querySelectorAll('[data-gcl-member]'));
    var selectAll = modal.querySelector('[data-gcl-select-all]');
    var count = modal.querySelector('[data-gcl-count]');
    var submit = modal.querySelector('[data-gcl-submit]');
    var error = modal.querySelector('[data-gcl-error]');
    if (!form || !boxes.length) return;

    function update() {
      var selected = boxes.filter(function (box) { return box.checked; }).length;
      if (count) count.textContent = String(selected);
      if (submit) submit.disabled = selected === 0;
      if (selectAll) {
        selectAll.checked = selected === boxes.length;
        selectAll.indeterminate = selected > 0 && selected < boxes.length;
      }
      if (error && selected > 0) error.hidden = true;
    }

    boxes.forEach(function (box) { box.addEventListener('change', update); });
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        boxes.forEach(function (box) { box.checked = selectAll.checked; });
        update();
      });
    }
    form.addEventListener('submit', function (event) {
      var selected = boxes.filter(function (box) { return box.checked; }).length;
      if (selected === 0) {
        event.preventDefault();
        if (error) error.hidden = false;
        return;
      }
      if (submit) {
        submit.disabled = true;
        submit.textContent = 'Removing…';
      }
    });
    update();
  });
});
