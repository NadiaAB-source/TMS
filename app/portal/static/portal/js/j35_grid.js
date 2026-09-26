/* The J35 sheet saves through CSRF-protected requests and never reloads the page. */
(() => {
  "use strict";
  const sheet = document.querySelector(".j35-sheet");
  if (!sheet || sheet.dataset.editable !== "true") return;
  const table = sheet.querySelector("tbody");
  const dialog = sheet.querySelector("dialog");
  const form = dialog.querySelector("form");
  const feedback = sheet.querySelector(".j35-sheet-feedback");
  const campOptions = [...sheet.querySelectorAll("#j35-camp-options option")];
  let busy = false;
  let sourceCell = null;
  let feedbackTimer;

  const campFor = (name) => campOptions.find(option => option.value.trim().toLowerCase() === name.trim().toLowerCase());
  const flash = (message, error, anchor) => {
    clearTimeout(feedbackTimer);
    feedback.textContent = message;
    feedback.classList.toggle("is-error", error);
    feedback.hidden = false;
    const rect = anchor?.getBoundingClientRect();
    const left = rect ? Math.max(12, Math.min(rect.left, window.innerWidth - feedback.offsetWidth - 12)) : 20;
    const top = rect ? Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - feedback.offsetHeight - 12)) : 90;
    feedback.style.left = `${left}px`;
    feedback.style.top = `${top}px`;
    feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 3000);
  };

  const cellData = cell => {
    const row = cell.closest("tr");
    const day = cell.closest("td").dataset.date;
    const activity = cell.querySelector(".j35-cell-text").value.trim();
    const camp = campFor(activity);
    const data = {
      allocation: cell.dataset.allocation || "",
      revision: cell.dataset.revision || "0",
      instructor: row.dataset.instructor,
      activity,
      camp: camp?.dataset.camp || cell.dataset.camp || "",
      kind: cell.dataset.kind || (camp ? "course" : "other"),
      course: cell.dataset.course || sheet.querySelector("[data-default-course]").value,
      start_date: cell.dataset.startDate || day,
      end_date: cell.dataset.endDate || day,
      status: cell.querySelector(".j35-cell-status").value,
      color: cell.dataset.color || "#ffffff",
      notes: cell.dataset.notes || "",
      override_reason: cell.dataset.overrideReason || "",
      session: cell.dataset.session || "",
    };
    if (cell.dataset.originalActivity && cell.dataset.originalActivity !== activity) {
      data.camp = camp?.dataset.camp || "";
      data.session = "";
    }
    return data;
  };

  const recordOriginals = () => {
    table.querySelectorAll(".j35-sheet-cell").forEach(cell => {
      const input = cell.querySelector(".j35-cell-text");
      if (input) cell.dataset.originalActivity = input.value.trim();
    });
  };
  recordOriginals();

  const showEditor = (cell, error = "") => {
    sourceCell = cell;
    const data = cellData(cell);
    for (const [key, value] of Object.entries(data)) {
      if (form.elements.namedItem(key)) form.elements.namedItem(key).value = value;
    }
    // A historic/inactive course may not be in the new-courses choices.
    if (data.course && !form.elements.course.value) {
      const option = new Option("Current course", data.course);
      form.elements.course.add(option);
      form.elements.course.value = data.course;
    }
    dialog.querySelector(".j35-editor-person").textContent = cell.closest("tr").dataset.instructorName;
    const alert = dialog.querySelector(".j35-editor-error");
    alert.textContent = error;
    alert.hidden = !error;
    form.querySelector("[data-course-field]").hidden = data.kind !== "course";
    form.querySelector("[data-clear-cell]").hidden = !data.allocation || data.status !== "draft";
    dialog.showModal();
    form.elements.activity.focus({preventScroll: true});
  };

  const save = async (data, anchor, fromDialog = false) => {
    if (busy) return false;
    if (!data.activity) {
      flash("Enter a camp or task first.", true, anchor);
      return false;
    }
    busy = true;
    sheet.setAttribute("aria-busy", "true");
    const buttons = [...form.querySelectorAll("button")];
    buttons.forEach(button => { button.disabled = true; });
    const scrollArea = sheet.querySelector(".j35-sheet-scroll");
    const scroll = {x: scrollArea.scrollLeft, y: scrollArea.scrollTop, pageX: window.scrollX, pageY: window.scrollY};
    try {
      const body = new URLSearchParams({...data, j35_start: sheet.dataset.start});
      body.set("csrfmiddlewaretoken", sheet.querySelector("[name=csrfmiddlewaretoken]").value);
      const response = await fetch(sheet.dataset.saveUrl, {
        method: "POST", body, credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
      });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error("The session may have expired. Refresh the page and sign in again."); }
      if (!response.ok || !result.ok) throw new Error(result.error || "The assignment could not be saved.");
      const rect = anchor?.getBoundingClientRect();
      // Merge only the allocation just saved. Replacing the whole sheet would
      // discard text the planner has typed into another cell before pressing Enter.
      const fragment = document.createElement("template");
      fragment.innerHTML = `<table><tbody>${result.rows_html}</tbody></table>`;
      const rowSelector = `tr[data-instructor="${result.instructor_id}"]`;
      const oldRow = table.querySelector(rowSelector);
      const newRow = fragment.content.querySelector(rowSelector);
      if (!oldRow || !newRow) throw new Error("The instructor list changed. Refresh J35 to show the saved assignment.");
      const savedSelector = `.j35-sheet-cell[data-allocation="${result.allocation_id}"]`;
      [...oldRow.querySelectorAll("td[data-date]")].forEach(oldDay => {
        const newDay = newRow.querySelector(`td[data-date="${oldDay.dataset.date}"]`);
        if (!newDay || (!oldDay.querySelector(savedSelector) && !newDay.querySelector(savedSelector))) return;
        const dirty = [...oldDay.querySelectorAll(".j35-sheet-cell")].filter(cell => {
          const input = cell.querySelector(".j35-cell-text");
          return input && cell.dataset.allocation !== String(result.allocation_id)
            && input.value.trim() !== cell.dataset.originalActivity;
        }).map(cell => ({id: cell.dataset.allocation || "", value: cell.querySelector(".j35-cell-text").value, data: {...cell.dataset}}));
        oldDay.innerHTML = newDay.innerHTML;
        oldDay.querySelectorAll(".j35-sheet-cell").forEach(cell => {
          const input = cell.querySelector(".j35-cell-text");
          if (input) cell.dataset.originalActivity = input.value.trim();
        });
        dirty.forEach(edit => {
          const remaining = [...oldDay.querySelectorAll(".j35-sheet-cell")].find(cell => (cell.dataset.allocation || "") === edit.id);
          if (remaining) {
            remaining.querySelector(".j35-cell-text").value = edit.value;
            Object.assign(remaining.dataset, edit.data);
          }
        });
      });
      if (result.row_height) sheet.querySelector(".j35-sheet-table").style.setProperty("--j35-row-height", `${result.row_height}px`);
      table.querySelectorAll(savedSelector).forEach(cell => {
        cell.dataset.originalActivity = cell.querySelector(".j35-cell-text").value.trim();
      });
      scrollArea.scrollLeft = scroll.x;
      scrollArea.scrollTop = scroll.y;
      window.scrollTo(scroll.pageX, scroll.pageY);
      if (fromDialog) dialog.close();
      flash(result.message, false, rect ? {getBoundingClientRect: () => rect} : null);
      return true;
    } catch (error) {
      if (fromDialog) {
        const alert = dialog.querySelector(".j35-editor-error");
        alert.textContent = error.message;
        alert.hidden = false;
      } else {
        flash(error.message, true, anchor);
        // Keep the typed value available so a camp/course/conflict can be fixed.
        if (anchor?.isConnected && !dialog.open) showEditor(anchor, error.message);
      }
      return false;
    } finally {
      busy = false;
      sheet.removeAttribute("aria-busy");
      buttons.forEach(button => { button.disabled = false; });
    }
  };

  table.addEventListener("keydown", async event => {
    if (!event.target.matches(".j35-cell-text") || event.key !== "Enter") return;
    event.preventDefault();
    const cell = event.target.closest(".j35-sheet-cell");
    const instructor = cell.closest("tr").dataset.instructor;
    const day = cell.closest("td").dataset.date;
    if (await save(cellData(cell), cell)) {
      const next = table.querySelector(`tr[data-instructor="${instructor}"] td[data-date="${day}"] .j35-cell-text`);
      next?.focus({preventScroll: true});
    }
  });
  table.addEventListener("change", event => {
    if (!event.target.matches(".j35-cell-status")) return;
    const cell = event.target.closest(".j35-sheet-cell");
    if (busy) {
      event.target.value = cell.dataset.status || "draft";
      flash("Please wait for the current assignment to finish saving.", false, cell);
      return;
    }
    save(cellData(cell), cell);
  });
  table.addEventListener("click", event => {
    const details = event.target.closest("[data-cell-details]");
    if (details && !busy) showEditor(details.closest(".j35-sheet-cell"));
  });
  form.querySelectorAll('[data-j35-color]').forEach(button => button.addEventListener('click', () => {
    form.elements.color.value = button.dataset.j35Color;
    form.querySelectorAll('[data-j35-color]').forEach(swatch => swatch.setAttribute('aria-pressed', String(swatch === button)));
  }));
  form.addEventListener("change", event => {
    if (event.target.name === "kind") form.querySelector("[data-course-field]").hidden = event.target.value !== "course";
    if (["activity", "course", "start_date", "end_date"].includes(event.target.name)) form.elements.session.value = "";
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form));
    const camp = campFor(data.activity);
    data.camp = camp?.dataset.camp || "";
    // Legacy labels can contain the course title, with camp stored separately.
    if (!camp && sourceCell && sourceCell.dataset.originalActivity === data.activity.trim()) data.camp = sourceCell.dataset.camp || "";
    save(data, sourceCell, true);
  });
  dialog.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-clear-cell]").addEventListener("click", () => {
    if (sourceCell) save({...cellData(sourceCell), action: "clear"}, sourceCell, true);
  });
})();
