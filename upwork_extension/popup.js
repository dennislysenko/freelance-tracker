const codeInput = document.getElementById("code");
const connectBtn = document.getElementById("connect");
const statusEl = document.getElementById("status");

function show(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

async function ping() {
  const res = await chrome.runtime.sendMessage({ type: "ping" });
  if (res && res.ok) {
    show("Connected ✓", "ok");
  } else if (res && res.reason === "unpaired") {
    show("");
  } else {
    show((res && res.error) || "Couldn't reach Freelance Tracker", "err");
  }
}

// Firefox-based browsers (Zen) may not grant host access at install. Ask here,
// synchronously inside the click so the browser counts it as a user gesture;
// where access is already granted this resolves at once with no prompt.
const ORIGINS = ["http://127.0.0.1:47619/*", "https://www.upwork.com/*"];

function requestAccess() {
  try {
    return Promise.resolve(chrome.permissions.request({ origins: ORIGINS })).catch(() => false);
  } catch (e) {
    return Promise.resolve(false);
  }
}

connectBtn.addEventListener("click", async () => {
  const access = requestAccess();
  const code = codeInput.value.trim();
  if (!code) {
    show("Paste the pairing code first.", "err");
    return;
  }
  // Start saving before anything awaits: the access prompt can close the
  // popup in Firefox-based browsers, and reopening it then finds the code.
  const saved = chrome.storage.local.set({ pairingCode: code });
  connectBtn.disabled = true;
  show("Connecting…");
  await saved;
  if (!(await access)) {
    show("Allow the extension to access Upwork and Freelance Tracker, then click Connect again.", "err");
    connectBtn.disabled = false;
    return;
  }
  await ping();
  connectBtn.disabled = false;
});

codeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") connectBtn.click();
});

(async () => {
  const { pairingCode } = await chrome.storage.local.get("pairingCode");
  if (pairingCode) {
    codeInput.value = pairingCode;
    show("Checking…");
    await ping();
  }
})();
