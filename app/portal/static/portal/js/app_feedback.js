(function () {
    "use strict";

    const source = document.querySelector(".app-toast-source");
    if (!source) {
        return;
    }

    const messages = Array.from(source.querySelectorAll("[data-toast-message]"))
        .map(function (message) {
            return {
                text: message.textContent.trim(),
                level: message.getAttribute("data-toast-level") || "info",
            };
        })
        .filter(function (message) {
            return message.text.length > 0;
        });

    if (!messages.length) {
        return;
    }

    const anchorKey = "iqarus-feedback-anchor:" + window.location.pathname;

    function getAnchor() {
        let rawAnchor;
        try {
            rawAnchor = window.sessionStorage.getItem(anchorKey);
            window.sessionStorage.removeItem(anchorKey);
        } catch (error) {
            return null;
        }

        if (!rawAnchor) {
            return null;
        }

        try {
            const anchor = JSON.parse(rawAnchor);
            if (
                !Number.isFinite(anchor.left)
                || !Number.isFinite(anchor.top)
            ) {
                return null;
            }
            return anchor;
        } catch (error) {
            return null;
        }
    }

    function toastKind(level) {
        const tags = level.toLowerCase().split(/\s+/);
        if (tags.some(function (tag) { return /error|danger|fail/.test(tag); })) {
            return "error";
        }
        if (tags.some(function (tag) { return /warn/.test(tag); })) {
            return "warning";
        }
        if (tags.some(function (tag) { return /success/.test(tag); })) {
            return "success";
        }
        return "info";
    }

    function toastPosition(anchor, index) {
        const toastWidth = Math.min(340, Math.max(260, window.innerWidth - 32));
        const gutter = 16;
        const verticalGap = 8;
        const defaultLeft = Math.max(gutter, window.innerWidth - toastWidth - gutter);
        const defaultTop = 16;
        const left = anchor
            ? Math.min(Math.max(gutter, anchor.left), window.innerWidth - toastWidth - gutter)
            : defaultLeft;
        const top = anchor
            ? Math.min(Math.max(gutter, anchor.top + index * 62), window.innerHeight - 78)
            : defaultTop + index * 62;

        return { left: left, top: top, gap: verticalGap };
    }

    const region = document.createElement("div");
    region.className = "app-toast-region";
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-relevant", "additions");
    document.body.appendChild(region);

    const anchor = getAnchor();
    messages.forEach(function (message, index) {
        const kind = toastKind(message.level);
        const position = toastPosition(anchor, index);
        const toast = document.createElement("div");

        toast.className = "app-toast app-toast--" + kind;
        toast.setAttribute("role", kind === "error" ? "alert" : "status");
        toast.style.setProperty("--toast-left", position.left + "px");
        toast.style.setProperty("--toast-top", position.top + "px");
        toast.textContent = message.text;
        region.appendChild(toast);

        window.requestAnimationFrame(function () {
            toast.classList.add("is-visible");
        });
        window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.classList.add("is-leaving");
        }, 3000);
        window.setTimeout(function () {
            toast.remove();
            if (!region.childElementCount) {
                region.remove();
            }
        }, 3260);
    });
}());
