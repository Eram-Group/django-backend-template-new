/* Notification-actions editor: per-card dirty tracking, fetch save, preview.
 *
 * A static file, not an inline <script>: SECURE_CSP sets script-src to
 * self + nonce, so an inline block would be blocked in production.
 *
 * Progressive: every card links to the standard change form ("Open full
 * form"), so the page still works if this script never loads - it only adds
 * in-place saving and the live preview. */
(function () {
    "use strict";

    var root = document.querySelector(".nc");
    if (!root) {
        return;
    }

    var TXT = {
        saving: root.dataset.textSaving,
        saved: root.dataset.textSaved,
        failed: root.dataset.textFailed,
        invalid: root.dataset.textInvalid,
        unsaved: root.dataset.textUnsaved
    };
    var SAVE_URL = root.dataset.saveUrl;
    var CAN_SAVE = root.dataset.canSave === "1";

    // csrfmiddlewaretoken varies per render; excluding it keeps the snapshot
    // comparison about actual field edits.
    function snapshot(card) {
        var data = new FormData(card);
        var parts = [];
        data.forEach(function (value, key) {
            if (key !== "csrfmiddlewaretoken") {
                parts.push(key + "=" + value);
            }
        });
        return parts.join("&");
    }

    function anyDirty() {
        return Array.prototype.some.call(
            root.querySelectorAll(".nc-card.is-dirty"),
            function () { return true; }
        );
    }

    root.querySelectorAll(".nc-card").forEach(function (card) {
        var save = card.querySelector("[type=submit]");
        var status = card.querySelector(".nc-status");
        var clean = snapshot(card);
        var lang = "en";

        function note(text, kind) {
            if (status) {
                status.textContent = text || "";
                status.className = "nc-status" + (kind ? " is-" + kind : "");
            }
        }

        function clearErrors() {
            card.querySelectorAll(".nc-error.is-visible").forEach(function (el) {
                el.textContent = "";
                el.classList.remove("is-visible");
            });
            card.querySelectorAll(".is-invalid").forEach(function (el) {
                el.classList.remove("is-invalid");
            });
        }

        function showErrors(errors) {
            var prefix = card.dataset.kind + "-";
            Object.keys(errors).forEach(function (key) {
                if (key === "__all__") {
                    note(errors[key].join(" "), "bad");
                    return;
                }
                var slot = card.querySelector("[data-field='" + prefix + key + "']");
                if (slot) {
                    slot.textContent = errors[key].join(" ");
                    slot.classList.add("is-visible");
                }
                var field = card.querySelector("[name='" + prefix + key + "']");
                if (field) {
                    field.classList.add("is-invalid");
                }
            });
        }

        /* --- preview (EN/AR sourced from the shadow fields) --------------- */
        function renderPreview() {
            var title = card.querySelector("[name$='-title_" + lang + "']");
            var body = card.querySelector("[name$='-body_" + lang + "']");
            var titleSlot = card.querySelector("[data-preview=title]");
            var bodySlot = card.querySelector("[data-preview=body]");
            if (titleSlot && title) {
                titleSlot.textContent = title.value;
            }
            if (bodySlot && body) {
                bodySlot.textContent = body.value;
            }
        }

        card.querySelectorAll(".nc-lang").forEach(function (button) {
            button.addEventListener("click", function () {
                lang = button.dataset.lang;
                card.querySelectorAll(".nc-lang").forEach(function (other) {
                    other.classList.toggle("is-on", other === button);
                });
                renderPreview();
            });
        });

        /* --- dirty tracking ----------------------------------------------- */
        function syncDirty() {
            var dirty = snapshot(card) !== clean;
            card.classList.toggle("is-dirty", dirty);
            if (save) {
                save.disabled = !dirty;
            }
            note(dirty ? TXT.unsaved : "");
        }

        card.addEventListener("input", function () {
            renderPreview();
            syncDirty();
        });
        card.addEventListener("change", syncDirty);

        /* --- save --------------------------------------------------------- */
        card.addEventListener("submit", function (event) {
            event.preventDefault();
            if (!CAN_SAVE) {
                return;
            }
            clearErrors();
            note(TXT.saving);
            if (save) {
                save.disabled = true;
            }
            fetch(SAVE_URL, {
                method: "POST",
                body: new FormData(card),
                headers: { "X-Requested-With": "XMLHttpRequest" }
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        return { ok: response.ok, data: data };
                    });
                })
                .then(function (result) {
                    if (result.ok && result.data.ok) {
                        clean = snapshot(card);
                        card.classList.remove("is-dirty");
                        note(TXT.saved, "good");
                        return;
                    }
                    note(""); // clear "Saving…" before deciding what to show
                    showErrors(result.data.errors || {});
                    if (status && !status.textContent) {
                        note(TXT.invalid, "bad");
                    }
                    if (save) {
                        save.disabled = false;
                    }
                })
                .catch(function () {
                    note(TXT.failed, "bad");
                    if (save) {
                        save.disabled = false;
                    }
                });
        });

        renderPreview();
    });

    window.addEventListener("beforeunload", function (event) {
        if (anyDirty()) {
            event.preventDefault();
        }
    });
})();
