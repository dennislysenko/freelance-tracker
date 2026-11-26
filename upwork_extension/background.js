// Service worker: the only place that talks to the Freelance Tracker bridge.
// Content scripts can't reach http://127.0.0.1 from an https page (CORS), so
// they send messages here instead.

const BRIDGE = "http://127.0.0.1:47619";
const NO_ACCESS =
  "The extension isn't allowed to reach Freelance Tracker yet. Click Connect and allow access, " +
  "or turn on its permissions in about:addons → Freelance Tracker – Upwork fill → Permissions.";

async function pairingCode() {
  const { pairingCode } = await chrome.storage.local.get("pairingCode");
  return (pairingCode || "").trim();
}

// Without a (valid) pairing code, tell the app an extension is here, so it
// shows "not paired" instead of an old "connected". Carries no data.
function hello() {
  fetch(BRIDGE + "/hello", { headers: { "X-FT-Hello": "1" }, cache: "no-store" }).catch(() => {});
}

async function call(path, { method = "GET", body } = {}) {
  const code = await pairingCode();
  if (!code) {
    hello();
    return { ok: false, reason: "unpaired", error: "Not paired with Freelance Tracker yet" };
  }
  const headers = { "X-FT-Token": code };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(BRIDGE + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (e) {
    // Without host access (Firefox-based browsers don't grant it to a
    // temporary add-on) the request fails like a dead server; say which.
    const granted = await chrome.permissions
      .contains({ origins: [BRIDGE + "/*"] })
      .catch(() => true);
    if (!granted) {
      return { ok: false, reason: "no_access", error: NO_ACCESS };
    }
    return { ok: false, reason: "offline", error: "Freelance Tracker isn't running" };
  }
  let data = {};
  try {
    data = await res.json();
  } catch (e) {
    data = {};
  }
  if (res.status === 401) {
    hello();
    return { ok: false, reason: "unauthorized", error: "Wrong pairing code" };
  }
  if (!res.ok) {
    return { ok: false, reason: "http", status: res.status, error: data.error || `HTTP ${res.status}` };
  }
  return { ...data, ok: data.ok !== false };
}

async function handle(msg) {
  switch (msg && msg.type) {
    case "ping":
      return call("/ping");
    case "pair": {
      // From the panel on the work diary: save the code, then check it.
      const code = String(msg.code || "").trim();
      if (!code) return { ok: false, reason: "unpaired", error: "Paste the pairing code first." };
      await chrome.storage.local.set({ pairingCode: code });
      return call("/ping");
    }
    case "queue":
      return call(`/upwork/queue?contract=${encodeURIComponent(msg.contract || "")}`);
    case "added":
      return call("/upwork/added", {
        method: "POST",
        body: { contract: String(msg.contract || ""), key: String(msg.key || "") },
      });
    default:
      return { ok: false, reason: "bad_request", error: "Unknown message" };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  handle(msg).then(sendResponse, (e) =>
    sendResponse({ ok: false, reason: "error", error: String((e && e.message) || e) })
  );
  return true; // async response
});
