(() => {
  document.querySelectorAll('.stock-issue-form').forEach(form => {
    const body = form.querySelector('[data-issue-lines]');
    const updateTotals = () => {
      const units = new Map();
      [...body.rows].forEach(row => {
        const select = row.querySelector('select');
        const unit = select.selectedOptions[0]?.dataset.unit || '';
        row.querySelector('.issue-line-unit').textContent = unit || '—';
        const quantity = Number(row.querySelector('input').value);
        if (select.value && Number.isInteger(quantity) && quantity > 0) units.set(unit, (units.get(unit) || 0) + quantity);
      });
      form.querySelector('[data-issue-total]').textContent = [...units].map(([u,q]) => `${q} ${u}`).join(' · ') || 'Choose items and quantities';
    };
    form.addEventListener('change', updateTotals);
    form.addEventListener('input', updateTotals);
    form.querySelector('[data-add-issue-line]')?.addEventListener('click', () => {
      if (body.rows.length >= 100) return;
      const row = body.rows[0].cloneNode(true);
      row.querySelector('select').value = '';
      row.querySelector('input').value = '1';
      body.appendChild(row);
      updateTotals();
      row.querySelector('select').focus();
    });
    body.addEventListener('click', event => {
      const remove = event.target.closest('[data-remove-issue-line]');
      if (!remove) return;
      const row = remove.closest('tr');
      if (body.rows.length > 1) row.remove();
      else { row.querySelector('select').value = ''; row.querySelector('input').value = '1'; }
      updateTotals();
    });
    updateTotals();
  });
  document.querySelector('[data-edit-stock-items]')?.addEventListener('click', () => {
    const panel = document.querySelector('.stock-warehouse');
    panel.classList.toggle('is-choosing-item');
    panel.querySelector('.stock-item-action a')?.focus({preventScroll: true});
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); });
  });
})();
