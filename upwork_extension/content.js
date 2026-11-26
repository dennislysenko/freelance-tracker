// Freelance Tracker – Upwork fill: content script for the Upwork work diary.
//
// Shows a small panel with the manual-time entries Freelance Tracker still
// has to add for this contract. "Fill" opens Upwork's Add manual time form
// and fills in start, end and memo. It NEVER clicks Save: the user checks the
// form and saves it. When the user's Save closes the form, the entry is
// reported back to Freelance Tracker as added.

(() => {
  if (window.__ftUpworkFill) return;
  window.__ftUpworkFill = true;

  const DIALOG_TITLE = "Add manual time";
  const MEMO_LIMIT = 140;
  const SAVE_WATCH_MS = 10000;

  const state = {
    href: "",
    contract: "",
    date: "",
    queue: null, // last bridge response
    loading: false,
    message: "",
    messageKind: "", // "", "ok", "err"
    collapsed: false,
    filled: null, // {entry, contract, dialog}
    confirmKey: "", // entry key waiting for "Already added" confirmation
  };

  // --- helpers -------------------------------------------------------------

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();

  async function waitFor(fn, timeout = 3000, step = 100) {
    const end = Date.now() + timeout;
    for (;;) {
      const v = fn();
      if (v) return v;
      if (Date.now() > end) return null;
      await sleep(step);
    }
  }

  function send(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (res) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, reason: "error", error: chrome.runtime.lastError.message });
          } else {
            resolve(res || { ok: false, reason: "error", error: "No response" });
          }
        });
      } catch (e) {
        // Extension reloaded: this old content script is orphaned.
        resolve({ ok: false, reason: "error", error: "Reload this page (the extension was updated)" });
      }
    });
  }

  function fmtMinutes(min) {
    const m = Math.max(0, Math.round(min || 0));
    // Same wording as the dashboard ("2h 10m", "40m").
    const h = Math.floor(m / 60), r = m % 60;
    return h && r ? `${h}h ${r}m` : h ? `${h}h` : `${r}m`;
  }

  function dayDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d));
  }

  // "2026-11-21" -> "Sat, Nov 21, 2026" (the dialog's Date label format)
  function dialogDateLabel(iso) {
    return dayDate(iso).toLocaleDateString("en-US", {
      weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
    });
  }

  // "2026-11-19" -> "Thu Nov 19"
  function shortDay(iso) {
    return dayDate(iso)
      .toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" })
      .replace(",", "");
  }

  function setMessage(text, kind = "") {
    state.message = text;
    state.messageKind = kind;
    render();
  }

  // --- the Upwork form ----------------------------------------------------

  function isAddDialog(el) {
    if (!el || !el.isConnected) return false;
    const h = el.querySelector("h1, h2, h3, .air3-modal-title");
    return !!h && clean(h.textContent) === DIALOG_TITLE;
  }

  function findDialog() {
    return [...document.querySelectorAll('[role="dialog"]')].find(isAddDialog) || null;
  }

  // The element a <label> points at, e.g. "Start time" -> #start-time-selector.
  function fieldFor(dialog, labelText) {
    const label = [...dialog.querySelectorAll("label")].find((l) => clean(l.textContent) === labelText);
    if (!label || !label.htmlFor) return null;
    return dialog.querySelector(`#${CSS.escape(label.htmlFor)}`);
  }

  function dropdownLabel(root) {
    const el = root && root.querySelector(".air3-dropdown-toggle-label");
    return el ? clean(el.textContent) : "";
  }

  // Option text without the screen-reader " selected" suffix.
  function optionText(opt) {
    const span = opt.querySelector(".air3-menu-item-text > span");
    return clean(span ? span.textContent : opt.textContent).replace(/\s+selected$/i, "");
  }

  // A freshly opened form sometimes ignores the first choice while it is still
  // rendering, so try a few times before giving up.
  async function pick(dialog, labelText, value, attempts = 3) {
    for (let i = 1; ; i++) {
      try {
        return await pickOnce(dialog, labelText, value);
      } catch (e) {
        if (i >= attempts || !e.retry) throw e;
        await sleep(300 * i);
      }
    }
  }

  async function pickOnce(dialog, labelText, value) {
    const root = fieldFor(dialog, labelText);
    if (!root) throw new Error(`Couldn't find the ${labelText} field`);
    const toggle = await waitFor(() => {
      const t = root.querySelector('[data-test="dropdown-toggle"], [role="combobox"]');
      return t && t.getAttribute("aria-disabled") !== "true" ? t : null;
    });
    if (!toggle) throw new Error(`The ${labelText} field isn't available`);
    if (toggle.getAttribute("aria-expanded") !== "true") toggle.click();
    const opt = await waitFor(() =>
      [...root.querySelectorAll('[role="option"]')].find((o) => optionText(o) === value)
    );
    if (!opt) {
      if (toggle.getAttribute("aria-expanded") === "true") toggle.click();
      throw Object.assign(new Error(`${value} isn't offered for ${labelText}`), { retry: true });
    }
    opt.scrollIntoView({ block: "nearest" });
    opt.click();
    const ok = await waitFor(() => dropdownLabel(root) === value, 1500);
    if (!ok) {
      if (toggle.getAttribute("aria-expanded") === "true") toggle.click();
      throw Object.assign(new Error(`${labelText} didn't take ${value}. Click Fill again.`), { retry: true });
    }
  }

  function setMemo(dialog, text) {
    const area = dialog.querySelector("#memo-field") || fieldFor(dialog, "Memo");
    if (!area) throw new Error("Couldn't find the Memo field");
    const proto = area instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(area, text);
    area.dispatchEvent(new Event("input", { bubbles: true }));
    area.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function formValues(dialog) {
    const area = dialog.querySelector("#memo-field") || fieldFor(dialog, "Memo");
    return {
      date: dropdownLabel(fieldFor(dialog, "Date")),
      start: dropdownLabel(fieldFor(dialog, "Start time")),
      end: dropdownLabel(fieldFor(dialog, "End time")),
      memo: area ? area.value : "",
    };
  }

  async function openDialog() {
    let dialog = findDialog();
    if (dialog) return dialog;
    const btn = [...document.querySelectorAll("button")].find((b) => clean(b.textContent) === DIALOG_TITLE);
    if (!btn) throw new Error("Couldn't find Upwork's Add manual time button");
    btn.click();
    dialog = await waitFor(findDialog, 5000);
    if (!dialog) throw new Error("The Add manual time form didn't open");
    // Let the form finish rendering its fields.
    await waitFor(() => fieldFor(dialog, "Start time"), 3000);
    return dialog;
  }

  async function fillEntry(entry) {
    const dialog = await openDialog();
    const wantDate = dialogDateLabel(entry.date);
    const gotDate = dropdownLabel(fieldFor(dialog, "Date"));
    if (gotDate !== wantDate) {
      throw new Error(`The form is set to ${gotDate || "another day"}, not ${wantDate}. Pick that day in the work diary first.`);
    }
    await pick(dialog, "Start time", entry.start);
    await pick(dialog, "End time", entry.end);
    setMemo(dialog, String(entry.memo || "").slice(0, MEMO_LIMIT));
    return dialog;
  }

  // --- Save detection -------------------------------------------------------

  function matchesEntry(values, entry) {
    return (
      values.date === dialogDateLabel(entry.date) &&
      values.start === entry.start &&
      values.end === entry.end
    );
  }

  function dialogError(dialog) {
    const el = dialog.querySelector('[role="alert"], .air3-alert-negative, .air3-form-message-error, .text-danger');
    return el ? clean(el.textContent) : "";
  }

  async function watchSave(filled, valuesAtSave) {
    const { entry, contract, dialog } = filled;
    // The modal is position:fixed, so check rendered boxes, not offsetParent.
    const closed = await waitFor(
      () => !isAddDialog(dialog) || dialog.getClientRects().length === 0,
      SAVE_WATCH_MS,
      200
    );
    if (!closed) {
      const err = dialogError(dialog);
      setMessage(err ? `Upwork didn't save it: ${err}` : "Upwork hasn't finished saving; nothing marked yet.", "err");
      return;
    }
    if (state.filled === filled) state.filled = null;
    if (!matchesEntry(valuesAtSave, entry)) {
      setMessage("You changed the times before saving, so it wasn't marked as added. Use Already added if it's in.", "err");
      return;
    }
    const res = await send({ type: "added", contract, key: entry.key });
    if (res.ok) {
      setMessage(`Added ${entry.start}–${entry.end} ✓`, "ok");
    } else {
      setMessage(`Saved on Upwork, but Freelance Tracker didn't record it: ${res.error}`, "err");
    }
    loadQueue();
  }

  document.addEventListener(
    "click",
    (e) => {
      const btn = e.target && e.target.closest && e.target.closest("button");
      if (!btn) return;
      const dialog = btn.closest('[role="dialog"]');
      if (!dialog || !isAddDialog(dialog)) return;
      const filled = state.filled;
      if (!filled || filled.dialog !== dialog) return;
      const text = clean(btn.textContent);
      if (text === "Save" && !btn.disabled) {
        watchSave(filled, formValues(dialog));
      } else if (text === "Cancel" || btn.matches(".air3-modal-close")) {
        state.filled = null;
        setMessage("");
      }
    },
    true
  );

  // --- bridge ---------------------------------------------------------------

  function readUrl() {
    const params = new URLSearchParams(location.search);
    state.contract = (params.get("contractId") || "").trim();
    state.date = (params.get("date") || "").trim();
  }

  async function loadQueue() {
    if (!state.contract) {
      state.queue = null;
      render();
      return;
    }
    state.loading = true;
    render();
    const contract = state.contract;
    const res = await send({ type: "queue", contract });
    if (contract !== state.contract) return; // navigated meanwhile
    state.loading = false;
    state.queue = res;
    render();
  }

  function goToDate(iso) {
    const url = new URL(location.href);
    url.searchParams.set("date", iso);
    location.assign(url.toString());
  }

  async function onFill(entry, button) {
    button.disabled = true;
    setMessage("Filling…");
    try {
      const dialog = await fillEntry(entry);
      state.filled = { entry, contract: state.contract, dialog };
      setMessage("Check the form, then click Save.", "ok");
    } catch (e) {
      setMessage(String((e && e.message) || e), "err");
    }
  }

  async function onAlreadyAdded(entry) {
    if (state.confirmKey !== entry.key) {
      state.confirmKey = entry.key;
      render();
      setTimeout(() => {
        if (state.confirmKey === entry.key) {
          state.confirmKey = "";
          render();
        }
      }, 4000);
      return;
    }
    state.confirmKey = "";
    const res = await send({ type: "added", contract: state.contract, key: entry.key });
    setMessage(res.ok ? `Marked ${entry.start}–${entry.end} as added` : `Couldn't mark it: ${res.error}`, res.ok ? "ok" : "err");
    loadQueue();
  }

  // --- panel ----------------------------------------------------------------

  const host = document.createElement("div");
  host.id = "ft-upwork-fill";
  // Bottom-left: the form's Save button sits bottom-right.
  host.style.cssText = "position:fixed;left:16px;bottom:16px;z-index:2147483646;";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      /* Copied from the Freelance Tracker dashboard (dashboard_panel.py):
         .section, .section-header, .section-title, .section-total, .project-row. */
      /* Lime border and glow so it stands out on Upwork's page. */
      .panel { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif; font-size: 15px; line-height: 1.4;
               color: #c9d1d9; background: #1c1c1e; border: 3px solid #a3e635; border-radius: 16px;
               box-shadow: 0 0 0 4px rgba(163,230,53,0.18), 0 16px 40px rgba(0,0,0,0.45);
               width: 380px; max-height: 75vh; overflow: auto; color-scheme: dark; }
      .panel * { box-sizing: border-box; }
      .head { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px 10px; min-height: 56px;
              cursor: pointer; user-select: none; }
      .head-left { display: flex; align-items: baseline; gap: 10px; }
      .title { font-size: 13px; font-weight: 700; letter-spacing: 1.8px; color: #8b949e; text-transform: uppercase; }
      .total { font-size: 26px; font-weight: 700; color: #3fb950; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .chev { font-size: 13px; color: #8b949e; margin-left: 10px; }
      .body { padding: 0 12px 14px; }
      .panel.collapsed .body { display: none; }
      .summary { font-size: 14px; color: #8b949e; padding: 0 6px 10px; }
      .summary.warn { color: #f0883e; }
      .hint { font-size: 14px; color: #6e7681; font-style: italic; padding: 4px 6px 8px; }
      .sub { font-size: 12px; font-weight: 700; letter-spacing: 1.8px; color: #6e7681; text-transform: uppercase; padding: 4px 6px; }
      .card { border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05); overflow: hidden; padding: 2px 0; }
      .row { padding: 10px 14px; margin: 1px 0; border-top: 1px solid rgba(255,255,255,0.05); }
      .row:first-child { border-top: 0; }
      .time { font-size: 16px; color: #b0b8c1; font-variant-numeric: tabular-nums; display: flex; justify-content: space-between; }
      .time .mins { color: #3fb950; font-weight: 700; }
      .memo { font-size: 14px; color: #6e7681; margin: 3px 0 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .btns { display: flex; align-items: center; gap: 8px; }
      button { font: inherit; font-size: 14px; cursor: pointer; border-radius: 8px; padding: 6px 12px;
               border: 0; background: rgba(255,255,255,0.08); color: #c9d1d9; }
      /* The one next step: solid green, like the dashboard's success button. */
      button.primary { background: rgba(63,185,80,0.92); color: #fff; font-weight: 700; font-size: 16px; padding: 10px 22px; border-radius: 10px; }
      button.primary:hover { background: #3fb950; }
      button.ghost { background: transparent; color: #6e7681; padding: 5px 4px; }
      button.ghost:hover { color: #c9d1d9; }
      button.confirm { background: rgba(248,81,73,0.12); color: #f85149; }
      button:disabled { opacity: .5; cursor: default; }
      .row.next { background: rgba(63,185,80,0.07); box-shadow: inset 3px 0 0 #3fb950; }
      .row.next .time { color: #f0f6fc; }
      .row.later .time, .row.later .time .mins { color: #6e7681; font-weight: 400; }
      .pair { display: flex; gap: 8px; padding: 4px 6px 2px; }
      .pair .code { flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
                    background: rgba(255,255,255,0.04); color: #f0f6fc; font: 14px ui-monospace, Menlo, monospace; }
      .pair .code:focus { outline: none; border-color: rgba(63,185,80,0.6); }
      .days { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; padding: 0 2px; }
      .msg { font-size: 14px; margin-top: 8px; padding: 0 6px; }
      .msg.ok { color: #3fb950; }
      .msg.err { color: #f85149; }
    </style>
    <div class="panel"><div class="head"><span class="head-left"><span class="title">Upwork</span><span class="total"></span></span><span class="chev"></span></div><div class="body"></div></div>
  `;
  const panel = shadow.querySelector(".panel");
  const bodyEl = shadow.querySelector(".body");
  const chevEl = shadow.querySelector(".chev");
  const totalEl = shadow.querySelector(".total");
  shadow.querySelector(".head").addEventListener("click", () => {
    state.collapsed = !state.collapsed;
    chrome.storage.local.set({ panelCollapsed: state.collapsed });
    render();
  });

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else node.setAttribute(k, v);
    });
    children.forEach((c) => c && node.appendChild(c));
    return node;
  }

  function hint(text) {
    return el("div", { class: "hint", text });
  }

  // Pair right here, without the toolbar popup.
  function pairForm(text) {
    const input = el("input", { class: "code", type: "text", placeholder: "Pairing code",
                                autocomplete: "off", spellcheck: "false" });
    // The panel re-renders (focus, reloads); keep what was typed.
    input.value = state.pairDraft || "";
    input.addEventListener("input", () => { state.pairDraft = input.value; });
    const connect = el("button", { class: "primary", text: "Connect" });
    const submit = async () => {
      const code = input.value.trim();
      if (!code) return setMessage("Paste the pairing code first.", "err");
      connect.disabled = true;
      const res = await send({ type: "pair", code });
      connect.disabled = false;
      if (res.ok) {
        state.pairDraft = "";
        setMessage("Paired ✓", "ok");
        loadQueue();
      } else {
        setMessage(res.error || "Couldn't pair", "err");
      }
    };
    connect.addEventListener("click", submit);
    // Keep Upwork's own keyboard shortcuts from seeing the typing.
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") submit();
    });
    return [hint(text), el("div", { class: "pair" }, [input, connect])];
  }

  function renderBody() {
    const q = state.queue;
    if (!state.contract) return [hint("Open a contract's work diary to see entries to add.")];
    if (!q) return [hint(state.loading ? "Loading…" : "")];
    if (!q.ok) {
      if (q.reason === "unpaired") return pairForm("Not paired yet. Paste the pairing code from Freelance Tracker (Settings → Integrations → Upwork, or Export/Invoice → Push to Upwork).");
      if (q.reason === "offline") return [hint("Freelance Tracker isn't running on this Mac.")];
      if (q.reason === "unauthorized") return pairForm("That pairing code doesn't match. Paste the current one from Freelance Tracker (Settings → Integrations → Upwork).");
      return [hint(`Couldn't load entries: ${q.error || "unknown error"}`)];
    }
    if (!q.project) return [hint("This contract isn't linked to a project in Freelance Tracker.")];
    const entries = q.entries || [];
    if (!entries.length) return [hint(`All caught up ✓ Nothing to add for ${q.project}.`)];

    const n = entries.length;
    const out = [
      el("div", {
        class: "summary",
        text: `${n} entr${n === 1 ? "y" : "ies"} to add · locks ${entries[0].locks_label}`,
      }),
    ];

    const today = entries.filter((e) => e.date === state.date);
    if (today.length) {
      out.push(el("div", { class: "sub", text: shortDay(state.date) }));
      const card = el("div", { class: "card" });
      out.push(card);
      today.forEach((entry, i) => {
        // Only the first entry still to add gets the highlighted Fill; the rest wait their turn.
        const isNext = i === 0;
        const fillBtn = el("button", { class: isNext ? "primary" : "ghost", text: "Fill" });
        fillBtn.addEventListener("click", () => onFill(entry, fillBtn));
        const confirming = state.confirmKey === entry.key;
        const addedBtn = el("button", {
          class: confirming ? "confirm" : "ghost",
          text: confirming ? "Confirm: already in Upwork" : "Already added",
          onclick: () => onAlreadyAdded(entry),
        });
        card.appendChild(
          el("div", { class: isNext ? "row next" : "row later" }, [
            el("div", { class: "time" }, [
              el("span", { text: `${entry.start}–${entry.end}` }),
              el("span", { class: "mins", text: fmtMinutes(entry.minutes) }),
            ]),
            el("div", { class: "memo", text: entry.memo || "(no memo)", title: entry.memo || "" }),
            el("div", { class: "btns" }, [fillBtn, addedBtn]),
          ])
        );
      });
    }

    const otherDays = [];
    entries.forEach((e) => {
      if (e.date === state.date) return;
      const d = otherDays.find((x) => x.date === e.date);
      if (d) d.count += 1;
      else otherDays.push({ date: e.date, count: 1 });
    });
    if (otherDays.length) {
      const days = el("div", { class: "days" });
      otherDays.slice(0, 6).forEach((d, i) => {
        // With nothing left on this day, moving to the next day is the one action.
        const isNext = i === 0 && !today.length;
        days.appendChild(
          el("button", {
            class: isNext ? "primary" : "ghost",
            text: `${isNext ? "Next: " : ""}${shortDay(d.date)} (${d.count}) →`,
            onclick: () => goToDate(d.date),
          })
        );
      });
      out.push(days);
    }
    return out;
  }

  function render() {
    panel.classList.toggle("collapsed", state.collapsed);
    chevEl.textContent = state.collapsed ? "▸" : "▾";
    const q = state.queue;
    const waiting = q && q.ok && q.entries && q.entries.length
      ? (q.pending_minutes != null ? q.pending_minutes : q.entries.reduce((s, e) => s + (e.minutes || 0), 0))
      : 0;
    totalEl.textContent = waiting ? fmtMinutes(waiting) : "";
    bodyEl.replaceChildren(...renderBody());
    if (state.message) {
      bodyEl.appendChild(el("div", { class: `msg ${state.messageKind}`, text: state.message }));
    }
  }

  // --- lifecycle -------------------------------------------------------------

  function onUrlChange() {
    state.href = location.href;
    const before = `${state.contract}|${state.date}`;
    readUrl();
    if (`${state.contract}|${state.date}` !== before) {
      state.filled = null;
      state.message = "";
      loadQueue();
    }
  }

  async function start() {
    const { panelCollapsed } = await chrome.storage.local.get("panelCollapsed");
    state.collapsed = !!panelCollapsed;
    (document.body || document.documentElement).appendChild(host);
    onUrlChange();
    render();
    setInterval(() => {
      if (location.href !== state.href) onUrlChange();
      if (!host.isConnected) (document.body || document.documentElement).appendChild(host);
    }, 1000);
    setInterval(() => {
      if (!document.hidden && !state.filled) loadQueue();
    }, 60000);
    window.addEventListener("focus", () => {
      if (!state.filled) loadQueue();
    });
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.pairingCode) loadQueue();
    });
  }

  start();
})();
