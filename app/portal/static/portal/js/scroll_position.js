(function () {
    "use strict";

    const currentPath = window.location.pathname;
    const scrollStorageKey = "iqarus-scroll-position:" + currentPath;
    const anchorStorageKey = "iqarus-feedback-anchor:" + currentPath;

    try {
        if ("scrollRestoration" in window.history) {
            window.history.scrollRestoration = "manual";
        }
    } catch (error) {
        // Browsers that do not expose history settings still receive the normal restore.
    }

    function readSession(key) {
        try {
            return window.sessionStorage.getItem(key);
        } catch (error) {
            return null;
        }
    }

    function writeSession(key, value) {
        try {
            window.sessionStorage.setItem(key, value);
        } catch (error) {
            // A privacy-restricted browser can still use the application normally.
        }
    }

    function removeSession(key) {
        try {
            window.sessionStorage.removeItem(key);
        } catch (error) {
            // Nothing to clean up when session storage is unavailable.
        }
    }

    function destinationStaysOnPage(destination) {
        let destinationUrl;
        try {
            destinationUrl = new URL(destination, window.location.href);
        } catch (error) {
            return false;
        }

        return (
            destinationUrl.origin === window.location.origin
            && destinationUrl.pathname === currentPath
            && !destinationUrl.hash
        );
    }

    function saveAnchor(target) {
        if (!(target instanceof Element)) {
            return;
        }

        const rect = target.getBoundingClientRect();
        if (!Number.isFinite(rect.left) || !Number.isFinite(rect.bottom)) {
            return;
        }

        writeSession(anchorStorageKey, JSON.stringify({
            left: Math.round(rect.left),
            top: Math.round(rect.bottom + 8),
            width: Math.round(rect.width),
        }));
    }

    function saveScrollPosition(target) {
        writeSession(scrollStorageKey, String(Math.max(0, Math.round(window.scrollY))));
        saveAnchor(target);
    }

    function restoreScrollPosition() {
        const savedPosition = readSession(scrollStorageKey);
        if (savedPosition === null) {
            return;
        }

        removeSession(scrollStorageKey);
        const scrollPosition = Number.parseInt(savedPosition, 10);
        if (!Number.isFinite(scrollPosition)) {
            return;
        }

        const restore = function () {
            window.scrollTo({ top: scrollPosition, left: 0, behavior: "auto" });
        };

        window.requestAnimationFrame(function () {
            window.requestAnimationFrame(restore);
        });
        window.addEventListener("load", function () {
            window.setTimeout(restore, 0);
        }, { once: true });
    }

    document.addEventListener("click", function (event) {
        if (!(event.target instanceof Element)) {
            return;
        }

        const link = event.target.closest("a[href]");
        if (
            !link
            || event.defaultPrevented
            || event.button !== 0
            || event.metaKey
            || event.ctrlKey
            || event.shiftKey
            || event.altKey
            || link.hasAttribute("download")
            || (link.target && link.target !== "_self")
            || !destinationStaysOnPage(link.href)
        ) {
            return;
        }

        saveScrollPosition(link);
    }, true);

    document.addEventListener("change", function (event) {
        if (event.target instanceof Element) {
            // Covers native auto-submit selects as well as controls that trigger a page update.
            saveScrollPosition(event.target);
        }
    }, true);

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }

        const destination = form.action || window.location.href;
        if (!destinationStaysOnPage(destination)) {
            return;
        }

        const submitter = event.submitter instanceof Element
            ? event.submitter
            : (document.activeElement instanceof Element ? document.activeElement : form);
        saveScrollPosition(submitter);
    }, true);

    restoreScrollPosition();
}());
