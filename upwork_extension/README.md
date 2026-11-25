# Freelance Tracker – Upwork fill (browser extension)

Upwork has no API for adding time, so Freelance Tracker can't push hours to an
hourly contract by itself. This extension closes the gap with a few clicks:

1. On a contract's Upwork **Work diary** page, a small **Freelance Tracker**
   panel lists the manual-time entries the tracker still has to add (10-minute
   blocks worked out from your Toggl hours), with the deadline for the week.
2. **Fill** opens Upwork's *Add manual time* form and fills in the start time,
   end time and memo for that entry.
3. You check the form and click **Save** yourself.
4. When the form closes after your Save, the entry is marked as added in
   Freelance Tracker and the panel moves on. Buttons for other days take you to
   that day's diary.

**It fills forms only. It never clicks Save, never submits anything, and never
reads or sends your Upwork login.** If you change the times before saving, the
entry isn't marked automatically; use **Already added** once it's in Upwork.

## Install

### Chrome (and other Chromium browsers)

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and pick this `upwork_extension` folder.
4. Click the extension's icon (pin it from the puzzle-piece menu if needed) and
   paste the **pairing code** from the Freelance Tracker dashboard
   (Export/Invoice → Push to Upwork), then click **Connect**. It should say
   *Connected ✓*.

### Zen, Firefox and other Firefox-based browsers

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…** and pick `manifest.json` in this folder.
3. Open `about:addons` → **Freelance Tracker – Upwork fill** → **Permissions**
   and turn on access to `www.upwork.com` and `127.0.0.1`. A temporary add-on
   doesn't get them at install, and without them it can neither reach the app
   nor show its panel on Upwork.
4. Click the extension's icon (toolbar or Extensions menu), paste the pairing
   code and click **Connect**.

Release builds of Firefox-based browsers only run unsigned add-ons
temporarily: the extension is removed when the browser quits, so load it
again after a restart.

To keep it installed, sign it as an **unlisted** add-on (never published on
addons.mozilla.org): create API credentials at
https://addons.mozilla.org/developers/addon/api/key/, store them in the macOS
Keychain as `amo-jwt-issuer` and `amo-jwt-secret` (see the top of
`sign_upwork_extension.py`), then run `python sign_upwork_extension.py`
(`--bump` for later versions). Install the `.xpi` it writes to `dist/` from
`about:addons` → gear → **Install Add-on From File**.

Freelance Tracker must be running: the extension talks only to it, on
`http://127.0.0.1:47619`, sending the pairing code with every request.

## Files

- `manifest.json` – Manifest V3; access to the work diary page and the local bridge only.
- `background.js` – all requests to the local bridge (`/ping`, `/upwork/queue`, `/upwork/added`).
- `content.js` – the panel and the form filling on the work diary page.
- `popup.html`, `popup.js` – pairing.

If Upwork changes its form, filling fails with a message in the panel; nothing
is ever saved by mistake, and you can still enter the time by hand.
