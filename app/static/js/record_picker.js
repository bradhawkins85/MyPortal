document.querySelectorAll('[data-record-picker]').forEach((picker) => {
  const search = picker.querySelector('[data-record-picker-search]');
  const select = picker.querySelector('[data-record-picker-options]');
  if (!search || !select) return;

  const options = Array.from(select.options).slice(1);
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase();
    options.forEach((option) => {
      option.hidden = Boolean(query) && !option.text.toLocaleLowerCase().includes(query);
    });
    if (select.selectedOptions[0]?.hidden) select.value = '';
  });
});
