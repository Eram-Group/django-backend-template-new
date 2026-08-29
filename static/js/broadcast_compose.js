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
    var USERS_URL = root.dataset.usersUrl;
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
    var AUDIENCE_FIELDS = ["target", "language", "require_device", "joined_after", "joined_before"];

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
        form.querySelectorAll("input[name=recipients]").forEach(function (picked) {
            data.append("recipients", picked.value);
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

    /* --- date range picker ------------------------------------------------ */
    // One popover calendar per date field, sharing the pair so the span
    // between "after" and "before" is shaded in both. Values are ISO
    // (YYYY-MM-DD) in plain text inputs - typing works, the calendar just
    // fills them and fires "change" so the reach estimate refreshes.
    (function () {
        var lang = document.documentElement.lang || "en";
        var start = form.querySelector("[name=joined_after]");
        var end = form.querySelector("[name=joined_before]");
        if (!start || !end) {
            return;
        }
        var PICK_TXT = {
            today: root.dataset.textToday,
            clear: root.dataset.textClear,
            prev: root.dataset.textPrevMonth,
            next: root.dataset.textNextMonth
        };
        var monthName = new Intl.DateTimeFormat(lang, { month: "long", year: "numeric" });
        var dayName = new Intl.DateTimeFormat(lang, { weekday: "short" });
        var firstDay = 1; // Monday unless the locale says otherwise
        try {
            var info = new Intl.Locale(lang);
            var week = info.getWeekInfo ? info.getWeekInfo() : info.weekInfo;
            if (week && week.firstDay) {
                firstDay = week.firstDay % 7; // Intl: 7 = Sunday -> 0
            }
        } catch (error) { /* older engines: Monday */ }

        function iso(date) {
            var m = String(date.getMonth() + 1);
            var d = String(date.getDate());
            return date.getFullYear() + "-" + (m.length < 2 ? "0" + m : m) + "-" + (d.length < 2 ? "0" + d : d);
        }

        function parse(value) {
            var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
            if (!match) {
                return null;
            }
            var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
            return iso(date) === value.trim() ? date : null;
        }

        var open = null; // the picker currently shown

        function Picker(input, role) {
            var host = input.closest(".bc-date-field");
            var button = host.querySelector(".bc-date-btn");
            var box = document.createElement("div");
            box.className = "bc-cal";
            box.hidden = true;
            host.appendChild(box);
            var view = parse(input.value) || new Date();
            view = new Date(view.getFullYear(), view.getMonth(), 1);

            function pick(date) {
                input.value = date ? iso(date) : "";
                input.dispatchEvent(new Event("change", { bubbles: true }));
                close();
                input.focus();
            }

            function el(tag, className, text) {
                var node = document.createElement(tag);
                node.className = className;
                if (text !== undefined) {
                    node.textContent = text;
                }
                return node;
            }

            function render() {
                box.textContent = "";
                var selected = parse(input.value);
                var from = parse(start.value);
                var to = parse(end.value);
                var todayIso = iso(new Date());

                var head = el("div", "bc-cal-head");
                var prev = el("button", "bc-cal-nav", "\u2039");
                prev.type = "button";
                prev.setAttribute("aria-label", PICK_TXT.prev);
                var title = el("div", "bc-cal-title", monthName.format(view));
                var next = el("button", "bc-cal-nav", "\u203a");
                next.type = "button";
                next.setAttribute("aria-label", PICK_TXT.next);
                prev.addEventListener("click", function () { shift(-1); });
                next.addEventListener("click", function () { shift(1); });
                head.appendChild(prev);
                head.appendChild(title);
                head.appendChild(next);
                box.appendChild(head);

                var grid = el("div", "bc-cal-grid");
                grid.setAttribute("role", "grid");
                var probe = new Date(2024, 0, 7 + firstDay); // a Sunday + offset
                for (var i = 0; i < 7; i += 1) {
                    grid.appendChild(el("div", "bc-cal-dow", dayName.format(probe)));
                    probe.setDate(probe.getDate() + 1);
                }
                var first = new Date(view.getFullYear(), view.getMonth(), 1);
                var lead = (first.getDay() - firstDay + 7) % 7;
                var cursor = new Date(first);
                cursor.setDate(cursor.getDate() - lead);
                for (var cell = 0; cell < 42; cell += 1) {
                    var date = new Date(cursor);
                    var value = iso(date);
                    var day = el("button", "bc-cal-day", String(date.getDate()));
                    day.type = "button";
                    day.dataset.date = value;
                    if (date.getMonth() !== view.getMonth()) {
                        day.classList.add("is-out");
                    }
                    if (value === todayIso) {
                        day.classList.add("is-today");
                    }
                    if (selected && value === iso(selected)) {
                        day.classList.add("is-selected");
                    }
                    if (from && to && date > from && date < to) {
                        day.classList.add("is-range");
                    }
                    if (from && value === iso(from)) {
                        day.classList.add("is-start");
                    }
                    if (to && value === iso(to)) {
                        day.classList.add("is-end");
                    }
                    day.addEventListener("click", function (event) {
                        pick(parse(event.currentTarget.dataset.date));
                    });
                    grid.appendChild(day);
                    cursor.setDate(cursor.getDate() + 1);
                }
                box.appendChild(grid);

                var foot = el("div", "bc-cal-foot");
                var today = el("button", "bc-cal-link", PICK_TXT.today);
                today.type = "button";
                today.addEventListener("click", function () { pick(new Date()); });
                var clear = el("button", "bc-cal-link", PICK_TXT.clear);
                clear.type = "button";
                clear.addEventListener("click", function () { pick(null); });
                foot.appendChild(today);
                foot.appendChild(clear);
                box.appendChild(foot);
            }

            function shift(months) {
                view = new Date(view.getFullYear(), view.getMonth() + months, 1);
                render();
            }

            function show() {
                if (open && open !== api) {
                    open.close();
                }
                var current = parse(input.value);
                if (current) {
                    view = new Date(current.getFullYear(), current.getMonth(), 1);
                }
                render();
                box.hidden = false;
                host.classList.add("is-open");
                open = api;
            }

            function close() {
                box.hidden = true;
                host.classList.remove("is-open");
                if (open === api) {
                    open = null;
                }
            }

            var api = { show: show, close: close, render: render, host: host };

            button.addEventListener("click", function () {
                if (box.hidden) {
                    show();
                    input.focus();
                } else {
                    close();
                }
            });
            input.addEventListener("focus", show);
            input.addEventListener("input", function () {
                if (!box.hidden) {
                    render();
                }
            });
            input.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    close();
                } else if (event.key === "Enter" && !box.hidden) {
                    event.preventDefault();
                    close();
                }
            });
            return api;
        }

        var pickers = [Picker(start, "start"), Picker(end, "end")];
        // Re-shade the partner when either end changes.
        [start, end].forEach(function (input) {
            input.addEventListener("change", function () {
                pickers.forEach(function (picker) { picker.render(); });
            });
        });
        document.addEventListener("mousedown", function (event) {
            if (open && !open.host.contains(event.target)) {
                open.close();
            }
        });
        document.addEventListener("focusin", function (event) {
            if (open && !open.host.contains(event.target)) {
                open.close();
            }
        });
    })();
    runEstimate();

    /* --- audience target: filters vs specific users ----------------------- */
    (function () {
        var targets = form.querySelectorAll("[name=target]");
        var panes = form.querySelectorAll("[data-target-pane]");
        var query = $("bc-user-query");
        var results = $("bc-user-results");
        var picked = $("bc-user-picked");
        if (!targets.length || !query || !results || !picked) {
            return;
        }
        var searchTimer = null;
        var lastQuery = "";

        function currentTarget() {
            var on = form.querySelector("[name=target]:checked");
            return on ? on.value : "filters";
        }

        function syncPanes() {
            var target = currentTarget();
            panes.forEach(function (pane) {
                pane.hidden = pane.dataset.targetPane !== target;
            });
            syncEmpty();
        }

        function syncEmpty() {
            picked.classList.toggle("is-empty", !picked.querySelector(".bc-user-chip"));
        }

        function pickedIds() {
            return Array.prototype.map.call(
                picked.querySelectorAll(".bc-user-chip"),
                function (chip) { return chip.dataset.id; }
            );
        }

        function addUser(user) {
            if (pickedIds().indexOf(user.id) !== -1) {
                return;
            }
            var chip = document.createElement("span");
            chip.className = "bc-user-chip";
            chip.dataset.id = user.id;
            var hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = "recipients";
            hidden.value = user.id;
            var name = document.createElement("span");
            name.className = "bc-user-chip-name";
            name.textContent = user.name || user.email;
            var mail = document.createElement("span");
            mail.className = "bc-user-chip-mail";
            mail.textContent = user.email;
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "bc-user-chip-x";
            remove.textContent = "\u00d7";
            chip.appendChild(hidden);
            chip.appendChild(name);
            chip.appendChild(mail);
            chip.appendChild(remove);
            picked.appendChild(chip);
            syncEmpty();
            scheduleEstimate();
        }

        picked.addEventListener("click", function (event) {
            var button = event.target.closest(".bc-user-chip-x");
            if (button) {
                button.closest(".bc-user-chip").remove();
                syncEmpty();
                scheduleEstimate();
            }
        });

        function closeResults() {
            results.hidden = true;
            results.textContent = "";
        }

        function renderResults(users) {
            results.textContent = "";
            var chosen = pickedIds();
            if (!users.length) {
                var none = document.createElement("div");
                none.className = "bc-user-none";
                none.textContent = root.dataset.textNoUsers;
                results.appendChild(none);
            }
            users.forEach(function (user) {
                var row = document.createElement("button");
                row.type = "button";
                row.className = "bc-user-row" + (chosen.indexOf(user.id) !== -1 ? " is-picked" : "");
                row.setAttribute("role", "option");
                var name = document.createElement("span");
                name.className = "bc-user-row-name";
                name.textContent = user.name || user.email;
                var meta = document.createElement("span");
                meta.className = "bc-user-row-meta";
                meta.textContent = [user.email, user.phone].filter(Boolean).join(" \u00b7 ");
                row.appendChild(name);
                row.appendChild(meta);
                row.addEventListener("mousedown", function (event) {
                    event.preventDefault(); // keep the search box focused
                    addUser(user);
                    row.classList.add("is-picked");
                });
                results.appendChild(row);
            });
            results.hidden = false;
        }

        function search() {
            var text = query.value.trim();
            if (text === lastQuery) {
                return;
            }
            lastQuery = text;
            if (!text) {
                closeResults();
                return;
            }
            fetch(USERS_URL + "?q=" + encodeURIComponent(text), {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            })
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    if (query.value.trim() === text) {
                        renderResults(data.users || []);
                    }
                })
                .catch(closeResults);
        }

        query.addEventListener("input", function () {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(search, 250);
        });
        query.addEventListener("focus", function () {
            if (query.value.trim()) {
                lastQuery = "";
                search();
            }
        });
        query.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                closeResults();
            } else if (event.key === "Enter") {
                event.preventDefault(); // never submit the broadcast from the search box
                var first = results.querySelector(".bc-user-row:not(.is-picked)");
                if (first) {
                    first.dispatchEvent(new MouseEvent("mousedown"));
                }
            }
        });
        document.addEventListener("mousedown", function (event) {
            if (!event.target.closest(".bc-user-search")) {
                closeResults();
            }
        });

        targets.forEach(function (radio) {
            radio.addEventListener("change", function () {
                syncPanes();
                scheduleEstimate();
            });
        });
        syncPanes();
    })();

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
