/* Broadcast composer: live preview, live reach estimate, confirm on submit.
 *
 * A static file, not an inline <script>: SECURE_CSP sets script-src to
 * self + nonce, so an inline block would be blocked in production.
 *
 * Progressive: the form is a plain Django admin form and posts perfectly well
 * without any of this. Everything here is additive — the chips and switches
 * wrap real radio/checkbox inputs, so the page still works if the script
 * never loads. */
(function () {
    "use strict";

    var root = document.querySelector(".bc");
    var form = document.getElementById("bc-form");
    if (!root || !form) {
        return;
    }

    var $ = function (id) { return document.getElementById(id); };

    var TXT = {
        recipients: root.dataset.textRecipients,
        devices: root.dataset.textDevices,
        calculating: root.dataset.textCalculating,
        failed: root.dataset.textFailed,
        empty: root.dataset.textEmpty,
        confirm: root.dataset.textConfirm,
        emptyTitle: root.dataset.emptyTitle,
        emptyBody: root.dataset.emptyBody
    };
    var ESTIMATE_URL = root.dataset.estimateUrl;
    var LIMITS = {
        title: parseInt(root.dataset.titleLimit, 10),
        message: parseInt(root.dataset.messageLimit, 10)
    };

    var title = form.querySelector("[name=title]");
    var message = form.querySelector("[name=message]");
    var lastEstimate = null;
    var estimateTimer = null;
    var confirmed = false;

    function fmt(value) {
        return Number(value).toLocaleString();
    }

    function note(text, kind) {
        var el = $("bc-note");
        el.textContent = text || "";
        el.className = "bc-status" + (kind ? " is-" + kind : "");
    }

    /* --- preview + counters ---------------------------------------------- */
    function renderPreview() {
        $("bc-preview-title").textContent = title.value.trim() || TXT.emptyTitle;
        $("bc-preview-body").textContent = message.value.trim() || TXT.emptyBody;
    }

    // The limits are advisory: no platform rejects a longer string, they just
    // truncate at their own width, so going over colours the counter and
    // nothing else.
    function countIn(field, counter, limit) {
        function sync() {
            counter.textContent = field.value.length + "/" + limit;
            counter.classList.toggle("is-over", field.value.length > limit);
            renderPreview();
        }
        field.addEventListener("input", sync);
        sync();
    }

    countIn(title, $("bc-title-count"), LIMITS.title);
    countIn(message, $("bc-message-count"), LIMITS.message);

    /* --- chips and switches ---------------------------------------------- */
    // The chip is a <label> around a real radio, so a click on any part of it
    // checks the input; this only mirrors that state onto the wrapper. Radios
    // fire `change` on the newly-checked one alone, hence the full re-sync.
    var languageInputs = form.querySelectorAll(".bc-chip input");

    function syncChips() {
        languageInputs.forEach(function (input) {
            input.closest(".bc-chip").classList.toggle("is-on", input.checked);
        });
    }

    languageInputs.forEach(function (input) {
        input.addEventListener("change", function () {
            syncChips();
            scheduleEstimate();
        });
    });
    syncChips();

    var smsToggle = form.querySelector("[data-channel=sms] input");
    if (smsToggle) {
        smsToggle.addEventListener("change", function () {
            $("bc-sms-warning").classList.toggle("is-visible", smsToggle.checked);
        });
    }

    /* --- reach ------------------------------------------------------------ */
    // Only the audience half is posted: the endpoint validates those fields
    // alone, so a number appears before the message is written.
    var AUDIENCE_FIELDS = ["language", "require_device", "joined_after", "joined_before"];

    function audiencePayload() {
        var data = new FormData();
        var token = form.querySelector("[name=csrfmiddlewaretoken]");
        if (token) {
            data.append("csrfmiddlewaretoken", token.value);
        }
        AUDIENCE_FIELDS.forEach(function (name) {
            // `:checked` matters for the language radio group: querySelector
            // would otherwise return the first radio regardless of choice.
            var checkable = form.querySelector("[name=" + name + "]:checked");
            if (checkable) {
                data.append(name, checkable.type === "checkbox" ? "on" : checkable.value);
                return;
            }
            var field = form.querySelector("[name=" + name + "]");
            if (field && field.type !== "checkbox" && field.type !== "radio" && field.value) {
                data.append(name, field.value);
            }
        });
        return data;
    }

    function runEstimate() {
        $("bc-reach-devices").textContent = TXT.calculating;
        fetch(ESTIMATE_URL, {
            method: "POST",
            body: audiencePayload(),
            headers: { "X-Requested-With": "XMLHttpRequest" }
        })
            .then(function (response) {
                return response.json();
            })
            .then(function (data) {
                var reach = $("bc-reach");
                reach.classList.remove("is-stale");
                if (!data.ok) {
                    // Usually the reversed-date case; the field error already
                    // says so on submit, so just stop claiming a number.
                    reach.classList.remove("is-empty");
                    $("bc-reach-count").textContent = "—";
                    $("bc-reach-devices").textContent = TXT.failed;
                    lastEstimate = null;
                    return;
                }
                lastEstimate = data;
                reach.classList.toggle("is-empty", data.recipients === 0);
                $("bc-reach-count").textContent =
                    TXT.recipients.replace(":n", fmt(data.recipients));
                $("bc-reach-devices").textContent =
                    TXT.devices.replace(":n", fmt(data.devices));
            })
            .catch(function () {
                $("bc-reach").classList.remove("is-stale");
                $("bc-reach-devices").textContent = TXT.failed;
                lastEstimate = null;
            });
    }

    function scheduleEstimate() {
        window.clearTimeout(estimateTimer);
        $("bc-reach").classList.add("is-stale");
        estimateTimer = window.setTimeout(runEstimate, 350);
    }

    ["joined_after", "joined_before", "require_device"].forEach(function (name) {
        var field = form.querySelector("[name=" + name + "]");
        if (field) {
            field.addEventListener("change", scheduleEstimate);
        }
    });
    runEstimate();

    /* --- confirm ---------------------------------------------------------- */
    var modal = $("bc-modal");

    form.addEventListener("submit", function (event) {
        if (confirmed || !modal) {
            return;
        }
        event.preventDefault();
        if (!form.reportValidity()) {
            return;
        }
        // An empty audience is a mistake worth catching here, not after the
        // draft exists: the estimate runs the same query the dispatcher pages.
        if (lastEstimate && lastEstimate.recipients === 0) {
            note(TXT.empty, "bad");
            return;
        }
        note("");
        $("bc-modal-text").textContent = lastEstimate
            ? TXT.confirm
                .replace(":recipients", fmt(lastEstimate.recipients))
                .replace(":devices", fmt(lastEstimate.devices))
            : TXT.failed;
        modal.classList.add("is-open");
    });

    var cancel = $("bc-cancel");
    if (cancel) {
        cancel.addEventListener("click", function () {
            modal.classList.remove("is-open");
        });
    }

    var confirm = $("bc-confirm");
    if (confirm) {
        confirm.addEventListener("click", function () {
            confirmed = true;
            confirm.disabled = true;
            modal.classList.remove("is-open");
            // requestSubmit keeps the clicked button's name in the POST, which
            // the admin needs to tell _save from _addanother.
            form.requestSubmit($("bc-save"));
        });
    }

    renderPreview();
})();
