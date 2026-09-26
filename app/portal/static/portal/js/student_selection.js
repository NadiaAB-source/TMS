(() => {
  "use strict";
  const form = document.getElementById("student-selection-form");
  if (!form) return;
  const rows = Array.from(form.querySelectorAll(".row-checkbox"));
  const master = document.getElementById("master-checkbox");
  const count = document.getElementById("selected-count");
  const print = document.getElementById("print-selected");
  function refresh() {
    const selected = rows.filter(row => row.checked).length;
    count.textContent = `${selected} selected`;
    print.disabled = !selected;
    master.checked = rows.length > 0 && selected === rows.length;
    master.indeterminate = selected > 0 && selected < rows.length;
  }
  function select(value) { rows.forEach(row => { row.checked = value; }); refresh(); }
  document.getElementById("select-all-shown").addEventListener("click", () => select(true));
  document.getElementById("clear-selection").addEventListener("click", () => select(false));
  master.addEventListener("change", () => select(master.checked));
  rows.forEach(row => row.addEventListener("change", refresh));
  refresh();
})();
