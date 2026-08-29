/* Notification-actions editor: per-card dirty tracking, one fetch save.
 *
 * A static file, not an inline <script>: SECURE_CSP sets script-src to
 * self + nonce, so an inline block would be blocked in production.
 *
 * The message composer is a token editor: each variable is an atomic pill
 * inside a contenteditable, inserted by typing "{" (menu) or clicking the
 * palette; the real form fields (.nc-src) stay the transport - the editor
 * hydrates from them and serialises back to plain "{amount}" format strings
 * on every input, so the save endpoint and validation are untouched. Both
 * languages sit side by side - editor and preview per language, nothing to
 * switch - and the preview renders each variable's example value. */
(function () {
    "use strict";

    var root = document.querySelector(".nc");
    if (!root) {
        return;
    }

    var SAVE_URL = root.dataset.saveUrl;
    var CAN_SAVE = root.dataset.canSave === "1";

    var TXT = {
        saving: root.dataset.textSaving,
        saved: root.dataset.textSaved,
        failed: root.dataset.textFailed,
        invalid: root.dataset.textInvalid,
        unsaved: root.dataset.textUnsaved,
        unknown: root.dataset.textUnknown,
        emptyTitle: root.dataset.textEmptyTitle,
        emptyBody: root.dataset.textEmptyBody,
        dirtyOne: root.dataset.textDirtyOne,
        dirtyMany: root.dataset.textDirtyMany
    };
    // Mirrors string.Formatter: `{name}`, `{name!r}`, `{name:.2f}`; the part
    // before `!`/`:` is the field name (`{}` / `{0}` count as unknown too).
    var PLACEHOLDER = /\{([^{}]*)\}/g;

    var ZWSP = "\u200b";

    function fieldName(text) {
        return text.split(/[!:]/)[0];
    }

    function makePill(key, known) {
        var pill = document.createElement("span");
        pill.className = "nc-tok" + (known[key] ? "" : " is-unknown");
        pill.contentEditable = "false";
        pill.draggable = false;
        pill.dataset.key = key;
        pill.textContent = key;
        return pill;
    }

    // Text with "{placeholders}" -> DOM fragment of text nodes, <br>s and
    // pills. A zero-width space follows every pill so the caret always has a
    // text position after it (Chrome cannot place the caret after a trailing
    // contenteditable=false node; Firefox would step inside it).
    function tokenize(text, known) {
        var frag = document.createDocumentFragment();
        var last = 0;
        var match;
        function pushText(chunk) {
            var lines = chunk.split("\n");
            lines.forEach(function (line, index) {
                if (index > 0) {
                    frag.appendChild(document.createElement("br"));
                }
                if (line) {
                    frag.appendChild(document.createTextNode(line));
                }
            });
        }
        PLACEHOLDER.lastIndex = 0;
        while ((match = PLACEHOLDER.exec(text)) !== null) {
            pushText(text.slice(last, match.index));
            frag.appendChild(makePill(fieldName(match[1]), known));
            frag.appendChild(document.createTextNode(ZWSP));
            last = match.index + match[0].length;
        }
        pushText(text.slice(last));
        return frag;
    }

    // Editor DOM -> the stored format string. Browsers differ on how Enter
    // splits lines (<br>, <div>, <div><br></div>); every block boundary and
    // <br> becomes "\n", ZWSPs are dropped, a single trailing newline (the
    // placeholder <br> Chrome leaves at the end) is trimmed.
    function serialize(editor) {
        var out = "";
        function walk(node, top) {
            Array.prototype.forEach.call(node.childNodes, function (child, index) {
                if (child.nodeType === 3) {
                    out += child.nodeValue.split(ZWSP).join("");
                    return;
                }
                if (child.nodeType !== 1) {
                    return;
                }
                if (child.classList.contains("nc-tok")) {
                    out += "{" + child.dataset.key + "}";
                    return;
                }
                if (child.tagName === "BR") {
                    out += "\n";
                    return;
                }
                var block = getComputedStyle(child).display !== "inline";
                if (block && !(top && index === 0)) {
                    out += "\n";
                }
                walk(child, false);
            });
        }
        walk(editor, true);
        out = out.replace(/\n$/, "");
        if (editor.dataset.single === "1") {
            out = out.replace(/\n+/g, " ");
        }
        return out;
    }

    function caretRange(editor) {
        var selection = window.getSelection();
        if (selection.rangeCount) {
            var range = selection.getRangeAt(0);
            if (editor.contains(range.commonAncestorContainer)) {
                return range;
            }
        }
        var end = document.createRange();
        end.selectNodeContents(editor);
        end.collapse(false);
        return end;
    }

    function placeCaretAfter(node) {
        var range = document.createRange();
        range.setStartAfter(node);
        range.collapse(true);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }

    // Insert a fragment at the caret (replacing any selection) and leave the
    // caret after it. Range.insertNode is logical, so RTL panes need nothing
    // special.
    function insertAtCaret(editor, frag) {
        var range = caretRange(editor);
        range.deleteContents();
        var tail = frag.lastChild;
        range.insertNode(frag);
        if (tail) {
            placeCaretAfter(tail);
        }
        editor.focus();
        editor.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function pillBeside(range, before) {
        var node = range.startContainer;
        var offset = range.startOffset;
        if (node.nodeType === 3) {
            var text = node.nodeValue;
            if (before ? text.slice(0, offset).split(ZWSP).join("") : text.slice(offset).split(ZWSP).join("")) {
                return null; // real characters between the caret and any pill
            }
            node = before ? node.previousSibling : node.nextSibling;
        } else {
            node = before ? node.childNodes[offset - 1] : node.childNodes[offset];
        }
        return node && node.nodeType === 1 && node.classList.contains("nc-tok") ? node : null;
    }

    var form = document.getElementById("nc-form");
    var bar = root.querySelector(".nc-bar");
    var barStatus = root.querySelector(".nc-bar-status");
    var saveButton = root.querySelector(".nc-bar [type=submit]");
    var cards = [];  // {card, isDirty, markSaved, showErrors, clearErrors, note}

    // Each card's fields, as one comparable string (the card's own "kind"
    // input is excluded: it is what the page toggles to select the card).
    function fieldsOf(card) {
        return Array.prototype.filter.call(
            card.querySelectorAll("input[name], textarea[name]"),
            function (field) { return field.name !== "kind"; }
        );
    }

    function snapshot(card) {
        return fieldsOf(card).map(function (field) {
            if (field.type === "checkbox" || field.type === "radio") {
                return field.checked ? field.name + "=" + field.value : "";
            }
            return field.name + "=" + field.value;
        }).join("&");
    }

    function dirtyCards() {
        return cards.filter(function (entry) { return entry.isDirty(); });
    }

    function barNote(text, kind) {
        if (barStatus) {
            barStatus.textContent = text || "";
            barStatus.className = "nc-bar-status" + (kind ? " is-" + kind : "");
        }
    }

    // The sticky bar appears as soon as anything changed and names how much.
    function syncBar() {
        var dirty = dirtyCards();
        if (!bar) {
            return;
        }
        bar.hidden = dirty.length === 0 && !bar.dataset.hold;
        if (saveButton) {
            saveButton.disabled = dirty.length === 0;
        }
        if (dirty.length) {
            barNote(dirty.length === 1
                ? TXT.dirtyOne
                : TXT.dirtyMany.replace("%(count)s", String(dirty.length)));
        }
    }

    root.querySelectorAll(".nc-card").forEach(function (card) {
        var status = card.querySelector(".nc-status");
        var kindInput = card.querySelector("input[name=kind]");
        var clean;

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
                var editor = editorOf(prefix + key);
                if (editor) {
                    editor.classList.add("is-invalid");
                }
            });
        }

        /* --- variables --------------------------------------------------- */
        var samples = {};
        var known = {};
        card.querySelectorAll(".nc-palette .nc-var").forEach(function (chip) {
            samples[chip.dataset.key] = chip.dataset.sample;
            known[chip.dataset.key] = true;
        });

        function sourceOf(editor) {
            return card.querySelector("[name='" + editor.dataset.for + "']");
        }

        function editorOf(name) {
            return card.querySelector(".nc-editor[data-for='" + name + "']");
        }

        /* --- token editors ------------------------------------------------ */
        var editors = Array.prototype.slice.call(card.querySelectorAll(".nc-editor"));
        var focused = null;   // last editor the operator was in (palette target)
        var composing = false;

        function hydrate(editor) {
            editor.textContent = "";
            editor.appendChild(tokenize(sourceOf(editor).value, known));
        }

        function sync(editor) {
            var source = sourceOf(editor);
            var value = serialize(editor);
            var error = editor.parentNode.querySelector(".nc-error");
            var unknown = [];
            editor.querySelectorAll(".nc-tok.is-unknown").forEach(function (pill) {
                if (unknown.indexOf(pill.dataset.key) === -1) {
                    unknown.push(pill.dataset.key);
                }
            });
            source.value = value;
            editor.classList.toggle("is-empty", value === "");
            editor.classList.toggle("is-invalid", unknown.length > 0);
            if (error) {
                error.textContent = unknown.length
                    ? TXT.unknown + " " + unknown.map(function (key) {
                        return "{" + key + "}";
                    }).join(", ")
                    : "";
                error.classList.toggle("is-visible", unknown.length > 0);
            }
        }

        function activeEditor() {
            return focused || card.querySelector(".nc-editor[data-single='0']");
        }

        function insertVariable(editor, key) {
            var frag = document.createDocumentFragment();
            frag.appendChild(makePill(key, known));
            frag.appendChild(document.createTextNode(ZWSP));
            insertAtCaret(editor, frag);
        }

        /* --- "{" menu ----------------------------------------------------- */
        var menu = null;
        var menuIndex = 0;
        var menuFilter = "";

        function closeMenu() {
            if (menu) {
                menu.remove();
                menu = null;
            }
        }

        function menuItems() {
            return Object.keys(samples).filter(function (key) {
                return key.indexOf(menuFilter) === 0;
            });
        }

        function renderMenu(editor) {
            var keys = menuItems();
            if (!keys.length) {
                closeMenu();
                return;
            }
            if (!menu) {
                menu = document.createElement("div");
                menu.className = "nc-menu";
                menu.setAttribute("role", "listbox");
                editor.parentNode.appendChild(menu);
            }
            menuIndex = Math.min(menuIndex, keys.length - 1);
            menu.textContent = "";
            keys.forEach(function (key, index) {
                var item = document.createElement("div");
                item.className = "nc-menu-item" + (index === menuIndex ? " is-on" : "");
                item.setAttribute("role", "option");
                var pill = document.createElement("span");
                pill.className = "nc-tok";
                pill.textContent = key;
                var sample = document.createElement("span");
                sample.className = "nc-var-sample";
                sample.textContent = samples[key];
                item.appendChild(pill);
                item.appendChild(sample);
                // mousedown would blur the editor before click fires.
                item.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    pick(editor, key);
                });
                menu.appendChild(item);
            });
            // Under the caret when it has a box; an empty line has none, then
            // under the editor itself.
            var rect = caretRange(editor).getBoundingClientRect();
            var host = editor.parentNode.getBoundingClientRect();
            var box = editor.getBoundingClientRect();
            if (rect.width || rect.height) {
                menu.style.top = (rect.bottom - host.top + 4) + "px";
                menu.style.left = Math.min(rect.left - host.left, host.width - 220) + "px";
            } else {
                menu.style.top = (box.bottom - host.top + 4) + "px";
                menu.style.left = (box.left - host.left) + "px";
            }
        }

        function openMenu(editor) {
            menuIndex = 0;
            menuFilter = "";
            renderMenu(editor);
        }

        function pick(editor, key) {
            closeMenu();
            insertVariable(editor, key);
        }

        editors.forEach(function (editor) {
            hydrate(editor);
            sync(editor);

            editor.addEventListener("focus", function () {
                focused = editor;
            });
            editor.addEventListener("blur", closeMenu);
            editor.addEventListener("compositionstart", function () { composing = true; });
            editor.addEventListener("compositionend", function () {
                composing = false;
                sync(editor);
            });

            editor.addEventListener("keydown", function (event) {
                if (event.isComposing || event.keyCode === 229) {
                    return;
                }
                if (menu) {
                    var keys = menuItems();
                    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                        event.preventDefault();
                        menuIndex = (menuIndex + (event.key === "ArrowDown" ? 1 : keys.length - 1)) % keys.length;
                        renderMenu(editor);
                        return;
                    }
                    if (event.key === "Enter" || event.key === "Tab") {
                        event.preventDefault();
                        pick(editor, keys[menuIndex]);
                        return;
                    }
                    if (event.key === "Escape") {
                        event.preventDefault();
                        closeMenu();
                        return;
                    }
                    if (event.key === "Backspace") {
                        if (!menuFilter) {
                            closeMenu();
                            return;
                        }
                        event.preventDefault();
                        menuFilter = menuFilter.slice(0, -1);
                        renderMenu(editor);
                        return;
                    }
                    if (/^[a-z_]$/i.test(event.key)) {
                        event.preventDefault();
                        menuFilter += event.key.toLowerCase();
                        renderMenu(editor);
                        return;
                    }
                    closeMenu();
                }
                if (event.key === "{") {
                    event.preventDefault();
                    openMenu(editor);
                    return;
                }
                if (event.key === "}") {
                    event.preventDefault();
                    return;
                }
                if (event.key === "Enter" && editor.dataset.single === "1") {
                    event.preventDefault();
                    return;
                }
                // Pills delete as a unit: Firefox would otherwise step inside.
                if (event.key === "Backspace" || event.key === "Delete") {
                    var range = caretRange(editor);
                    if (!range.collapsed) {
                        return;
                    }
                    var pill = pillBeside(range, event.key === "Backspace");
                    if (pill) {
                        event.preventDefault();
                        var anchor = pill.previousSibling;
                        var after = pill.nextSibling;
                        if (after && after.nodeType === 3 && after.nodeValue === ZWSP) {
                            after.remove();
                        }
                        pill.remove();
                        if (anchor) {
                            placeCaretAfter(anchor);
                        }
                        editor.dispatchEvent(new Event("input", { bubbles: true }));
                    }
                }
            });

            // Pasted text is re-tokenised, so "{amount}" arrives as a pill.
            editor.addEventListener("paste", function (event) {
                event.preventDefault();
                var text = (event.clipboardData || window.clipboardData).getData("text/plain");
                if (editor.dataset.single === "1") {
                    text = text.replace(/\s*\n+\s*/g, " ");
                }
                insertAtCaret(editor, tokenize(text, known));
            });

            editor.addEventListener("input", function () {
                if (!composing) {
                    sync(editor);
                }
            });
        });

        card.querySelectorAll(".nc-palette .nc-var").forEach(function (chip) {
            chip.addEventListener("mousedown", function (event) {
                event.preventDefault(); // keep the editor's caret where it is
            });
            chip.addEventListener("click", function () {
                var editor = activeEditor();
                if (editor) {
                    insertVariable(editor, chip.dataset.key);
                }
            });
        });

        /* --- previews (one per language, under its own editor) ------------ */
        // Placeholders render as their example value, marked so they stay
        // recognisable as variables; unknown ones stay literal.
        function fillPreview(slot, text, empty) {
            slot.textContent = "";
            if (!text.trim()) {
                slot.textContent = empty;
                slot.classList.add("is-empty");
                return;
            }
            slot.classList.remove("is-empty");
            var last = 0;
            var match;
            PLACEHOLDER.lastIndex = 0;
            while ((match = PLACEHOLDER.exec(text)) !== null) {
                var name = fieldName(match[1]);
                if (!known[name]) {
                    continue;
                }
                slot.appendChild(document.createTextNode(text.slice(last, match.index)));
                var mark = document.createElement("mark");
                mark.className = "nc-sample";
                mark.textContent = samples[name];
                slot.appendChild(mark);
                last = match.index + match[0].length;
            }
            slot.appendChild(document.createTextNode(text.slice(last)));
        }

        function renderPreviews() {
            card.querySelectorAll(".nc-preview").forEach(function (box) {
                var lang = box.dataset.lang;
                fillPreview(
                    box.querySelector("[data-preview=title]"),
                    card.querySelector("[name$='-title_" + lang + "']").value,
                    TXT.emptyTitle
                );
                fillPreview(
                    box.querySelector("[data-preview=body]"),
                    card.querySelector("[name$='-body_" + lang + "']").value,
                    TXT.emptyBody
                );
            });
        }

        /* --- dirty tracking ----------------------------------------------- */
        // A kind with no row yet counts as changed until its first save
        // creates it - the bar shows it and Save writes it.
        function isDirty() {
            return card.dataset.new === "1" || snapshot(card) !== clean;
        }

        function syncDirty() {
            var dirty = isDirty();
            card.classList.toggle("is-dirty", dirty);
            // Only a dirty card's "kind" travels with the save request.
            kindInput.disabled = !dirty;
            note(dirty ? TXT.unsaved : "");
            syncBar();
        }

        card.addEventListener("input", function () {
            renderPreviews();
            syncDirty();
        });
        card.addEventListener("change", syncDirty);

        cards.push({
            card: card,
            isDirty: isDirty,
            note: note,
            clearErrors: clearErrors,
            showErrors: showErrors,
            markSaved: function () {
                clean = snapshot(card);
                delete card.dataset.new;
                card.classList.remove("is-dirty");
                kindInput.disabled = true;
                note(TXT.saved, "good");
            }
        });

        // The clean snapshot is taken AFTER hydrate+serialize so a byte-stable
        // round-trip never shows a card as dirty on load.
        clean = snapshot(card);
        syncDirty();
        renderPreviews();
    });

    /* --- one save for every edited card ----------------------------------- */
    if (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var dirty = dirtyCards();
            if (!CAN_SAVE || !dirty.length) {
                return;
            }
            cards.forEach(function (entry) { entry.clearErrors(); });
            bar.dataset.hold = "1";
            barNote(TXT.saving);
            saveButton.disabled = true;
            dirty.forEach(function (entry) { entry.note(TXT.saving); });
            // Disabled "kind" inputs of clean cards drop out of the FormData;
            // their fields still travel but the server ignores un-listed kinds.
            fetch(SAVE_URL, {
                method: "POST",
                body: new FormData(form),
                headers: { "X-Requested-With": "XMLHttpRequest" },
                credentials: "same-origin"
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        return { ok: response.ok, data: data };
                    });
                })
                .then(function (result) {
                    if (result.ok && result.data.ok) {
                        dirty.forEach(function (entry) { entry.markSaved(); });
                        barNote(TXT.saved, "good");
                        window.setTimeout(function () {
                            delete bar.dataset.hold;
                            syncBar();
                        }, 1800);
                        return;
                    }
                    var errors = result.data.errors || {};
                    var first = null;
                    dirty.forEach(function (entry) {
                        var own = errors[entry.card.dataset.kind];
                        entry.note("");
                        if (own) {
                            entry.showErrors(own);
                            first = first || entry.card;
                        }
                    });
                    barNote(errors.__all__ ? errors.__all__.join(" ") : TXT.invalid, "bad");
                    if (first) {
                        first.scrollIntoView({ behavior: "smooth", block: "center" });
                    }
                    delete bar.dataset.hold;
                    saveButton.disabled = false;
                })
                .catch(function (error) {
                    if (window.console) {
                        window.console.error("notification config save failed", error);
                    }
                    dirty.forEach(function (entry) { entry.note(""); });
                    barNote(TXT.failed, "bad");
                    delete bar.dataset.hold;
                    saveButton.disabled = false;
                });
        });
    }

    window.addEventListener("beforeunload", function (event) {
        if (dirtyCards().length) {
            event.preventDefault();
        }
    });
})();
