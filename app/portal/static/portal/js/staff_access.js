(() => {
  'use strict';
  const page = document.querySelector('.staff-access-page');
  if (!page) return;
  page.querySelector('[data-edit-staff]')?.addEventListener('click', event => {
    const editing = page.classList.toggle('is-editing');
    event.currentTarget.setAttribute('aria-pressed', String(editing));
    const url = new URL(window.location.href);
    if (editing) url.searchParams.set('edit', 'yes'); else url.searchParams.delete('edit');
    history.replaceState(null, '', url);
    const filter = document.getElementById('staff-filter-form');
    let mode = filter.querySelector('[name="edit"]');
    if (editing && !mode) { mode = document.createElement('input'); mode.type = 'hidden'; mode.name = 'edit'; filter.append(mode); }
    if (mode) { mode.value = 'yes'; mode.disabled = !editing; }
  });
  page.querySelector('[data-add-staff]')?.addEventListener('click', () => {
    const panel = document.getElementById('staff-add-panel');
    panel.open = true;
    panel.scrollIntoView({block: 'nearest'});
    panel.querySelector('input:not([type="hidden"])')?.focus({preventScroll: true});
  });
  page.querySelectorAll('.staff-position-picker').forEach((picker) => {
    const label = picker.querySelector('[data-position-label]');
    const boxes = [...picker.querySelectorAll('input[type="checkbox"]')];
    const update = () => {
      const names = boxes.filter((box) => box.checked).map((box) => box.parentElement.textContent.trim());
      label.textContent = names.length === 1 ? names[0] : names.length ? `${names.length} positions` : 'Choose positions';
      picker.querySelector('summary').title = names.join(', ');
    };
    boxes.forEach((box) => box.addEventListener('change', update));
    picker.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { picker.open = false; picker.querySelector('summary').focus(); }
    });
    picker.addEventListener('toggle', () => {
      if (!picker.open) return;
      const panel = picker.querySelector('.staff-position-options');
      const rect = picker.getBoundingClientRect();
      panel.style.position = 'fixed';
      panel.style.width = Math.max(205, rect.width) + 'px';
      panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 268)) + 'px';
      panel.style.top = (window.innerHeight - rect.bottom >= 205 ? rect.bottom + 2 : Math.max(8, rect.top - 193)) + 'px';
    });
    update();
  });
  page.querySelectorAll('input[type="email"]').forEach((input) => {
    input.addEventListener('change', () => { input.value = input.value.trim().toLowerCase(); });
  });
  document.addEventListener('click', (event) => {
    page.querySelectorAll('.staff-position-picker[open]').forEach((picker) => {
      if (!picker.contains(event.target)) picker.open = false;
    });
  });
})();
