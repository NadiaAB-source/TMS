(function () {
    "use strict";

    const serviceBranch = document.getElementById("id_service_branch");
    const serviceBranchOtherField = document.getElementById(
        "service-branch-other-field"
    );
    const serviceBranchOther = document.getElementById(
        "id_service_branch_other"
    );

    function updateServiceBranchOther() {
        if (!serviceBranch || !serviceBranchOtherField || !serviceBranchOther) {
            return;
        }
        const isOther = serviceBranch.value === "OTHER";
        serviceBranchOtherField.hidden = !isOther;
        serviceBranchOther.required = isOther;
        if (!isOther) {
            serviceBranchOther.value = "";
        }
    }

    if (serviceBranch) {
        serviceBranch.addEventListener("change", updateServiceBranchOther);
        updateServiceBranchOther();
    }

    const list = document.getElementById("id_instructors");
    const search = document.getElementById("instructor-search");
    const count = document.getElementById("selected-instructor-count");
    const empty = document.getElementById("instructor-empty");

    if (!list || !search || !count || !empty) {
        return;
    }

    const options = Array.from(
        list.querySelectorAll('input[type="checkbox"]')
    ).map((checkbox) => {
        const label = checkbox.closest("label");
        return {
            checkbox,
            row: label ? label.parentElement : null,
            name: label ? label.textContent.trim().toLocaleLowerCase() : "",
        };
    });

    function updateSelectedState() {
        let selected = 0;

        options.forEach(({ checkbox, row }) => {
            if (checkbox.checked) {
                selected += 1;
            }
            if (row) {
                row.classList.toggle("is-selected", checkbox.checked);
            }
        });

        count.textContent = String(selected);
    }

    function filterOptions() {
        const query = search.value.trim().toLocaleLowerCase();
        let visible = 0;

        options.forEach(({ name, row }) => {
            const matches = !query || name.includes(query);
            if (row) {
                row.hidden = !matches;
            }
            if (matches) {
                visible += 1;
            }
        });

        empty.hidden = visible !== 0;
    }

    list.addEventListener("change", updateSelectedState);
    search.addEventListener("input", filterOptions);

    updateSelectedState();
    filterOptions();
}());

