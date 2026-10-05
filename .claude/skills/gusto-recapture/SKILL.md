---
name: gusto-recapture
description: Re-capture the request shapes of Gusto's internal (undocumented) GraphQL API from the Gusto web app and update gusto_client.py when the Toggl-to-Gusto sync breaks. Use when the Gusto sync fails with a GraphQL error ("Cannot query field", "Unknown argument", a changed variable type, addHours/shiftsForDay changed), a 403 GRAPHQL_IDENTITY_FAILURE, or "Gusto's page loaded but never made an authenticated request"; or when the user says "re-capture the gusto request", "Gusto changed its API", or "/gusto-recapture".
---

# Re-capture Gusto's GraphQL request shapes

`gusto_client.py` sends its own minimal GraphQL documents (`SHIFTS_QUERY`, `ADD_HOURS_MUTATION`)
to the same gateway the Gusto web app uses. Gusto does not require persisted or allow-listed
queries, so when Gusto changes its schema the fix is: find what Gusto's own client sends now,
update our documents, verify with read-only queries. Discovery is **passive**: scan the JS
bundles and run read queries. Never submit a shift to see what happens.

## Hard rules
- **Headed Chrome only, never headless.** Gusto is behind a Cloudflare bot check. Never try to get past it: no UA spoofing, stealth plugins, or cookie extraction. If a challenge shows, stop and hand over to the user.
- **Use the app's own Chrome.** `gusto_client._ensure_chrome()` starts a detached Chrome on `PROFILE_DIR` (`~/Library/Application Support/TogglMenuBar/gusto-browser`) with DevTools on `CDP_URL` (`http://127.0.0.1:9333`); attach with `connect_over_cdp` (or `open_session`). **Never close that browser**; leaving the `with` block only detaches, and the user may take over the window. For exploration you may instead use the Playwright MCP browser, where the user logs in themselves.
- **Never type a password or approve a passkey.** Gusto login is email + passkey (Touch ID) and the user does it. Clicking the remembered account is fine.
- **Never write request headers, cookies, or HAR files to disk** (they carry the session), and never print header values, only header **names**. Response bodies and extracted mutation text may go under the gitignored `.playwright-mcp/` dir.
- **No test writes.** Do not call `addHours`, `editShift`, or `deleteShift` to probe. The only allowed writes are real Toggl hours the user approves after seeing the dry-run output. The first `run` after any change to `ADD_HOURS_MUTATION` needs explicit user approval.

## Known facts (as of the last capture)
- App: `https://app.gusto.com/<slug>/time_tracking`; slug is `gusto_company_slug` in `~/Library/Application Support/TogglMenuBar/preferences.json`. Contractor "My hours" page: `/<slug>/time_tracking/review/<week-start>/<week-end>`.
- GraphQL: `https://graphql.app.gusto.com/?operationName=<Op>`. REST `https://app.gusto.com/api/time_tracking/trackers` returns the tracker list (contractor has one); see `GustoSession.tracker_id`.
- Page load: `ReviewTrackerTimesheet`, which reads `tracker(id).groupedShiftsWithTotals(payPeriod:{startDate,endDate})` (mirrored by our `SHIFTS_QUERY`).
- Save a manual shift: `TimesheetAddShift` → `addHours(input:{trackerId, clockInTimestamp, clockOutTimestamp, noteText, timezone, ...})`. Response `shiftsForDay` is a **single object, not a list** (`add_shift` handles both). Also in the bundle: `TimesheetEditShift` (`editShift`) and `TimesheetDeleteShift` (`deleteShift`). We never use either.
- Auth: the gateway needs `x-csrf-token` and `x-role-id` (plus `apollographql-client-name`/`-version`) on top of cookies. Without `x-role-id` it returns 403 `GRAPHQL_IDENTITY_FAILURE`. `open_session` copies `_FORWARDED_HEADERS` from the first POST Gusto's own client sends to `GRAPHQL_URL`, keeps them in memory, and issues `fetch(..., {credentials:'include'})` from inside the page.
- The mutation text was in a `ReviewTrackerTimesheetRoot-*.js` bundle on a CloudFront host (e.g. `d3bnlkto289wdc.cloudfront.net/vite-dev/assets/*.js`).

## Procedure
Run everything from the repo root with the venv active: `source venv/bin/activate`.

**1. Open a session (user logs in if needed).** If the session expired, run `python gusto_sync.py login` and let the user approve the passkey. Then attach with a heredoc script (no script file needed). `open_session` already navigates to time_tracking and captures the headers:

```python
python - <<'EOF'
import json, pathlib
import gusto_client
from preferences import load_preferences
slug = load_preferences()["gusto_company_slug"]
out = pathlib.Path(".playwright-mcp/gusto-recapture"); out.mkdir(parents=True, exist_ok=True)
with gusto_client.open_session(slug) as s:
    print("captured header names:", sorted(s._headers))   # names only, never values
    page = s._page
    # step 2 and step 3 go here
EOF
```
If `open_session` fails with "never made an authenticated request", the header shape changed. List the names Gusto now sends on its GraphQL POSTs and compare them against `_FORWARDED_HEADERS`:
```python
names = set()
page.on("request", lambda r: r.method == "POST" and r.url.startswith(gusto_client.GRAPHQL_URL) and names.update(r.headers.keys()))
page.reload(wait_until="domcontentloaded"); page.wait_for_timeout(5000)
print(sorted(names))   # NAMES only
```

**2. Scan the loaded bundles for shift mutations.** This is passive: it only re-downloads the page's own scripts.
```python
hits = page.evaluate("""async () => {
  const urls = new Set([...performance.getEntriesByType('resource').map(e => e.name),
                        ...[...document.scripts].map(s => s.src)]
                       .filter(u => u && /\\.js(\\?|$)/.test(u)));
  const hits = [];
  for (const url of urls) {
    let text; try { text = await (await fetch(url)).text(); } catch (e) { continue; }
    const ops = new Set(); const re = /mutation\\s+(\\w*(?:Shift|Timesheet|Clock|Hours)\\w*)/g; let m;
    while ((m = re.exec(text))) ops.add(m[1]);
    if (ops.size) hits.push({ url, ops: [...ops] });
  }
  return hits;
}""")
print(json.dumps(hits, indent=2))
```
If nothing matches, the page may not have loaded the timesheet yet. Navigate to the review page (`/<slug>/time_tracking/review/<mon>/<sun>`), wait, and scan again. Use the same approach with `query\s+(\w*Timesheet\w*)` to find the current `ReviewTrackerTimesheet`.

**3. Slice out the full document** (brace-matched) from the bundle that contains it, and save it:
```python
doc = page.evaluate("""async ({ url, name }) => {
  const text = await (await fetch(url)).text();
  const start = text.indexOf(name); if (start < 0) return null;
  let i = text.indexOf('{', start), depth = 0;
  for (; i < text.length; i++) { if (text[i] === '{') depth++; else if (text[i] === '}' && --depth === 0) break; }
  return text.slice(start, i + 1).replace(/\\\\n/g, '\\n');
}""", {"url": BUNDLE_URL, "name": "mutation TimesheetAddShift"})
(out / "TimesheetAddShift.graphql").write_text(doc or "NOT FOUND")
```
If the document spreads fragments (`...SomeFields`), slice each `fragment SomeFields on` the same way. Do the same for `query ReviewTrackerTimesheet`.

**4. Diff against our documents.** Compare variable names and types (e.g. `ISO8601DateTime!`, `ID!`), the `input:` fields of `addHours`, newly required fields, and the response shape (`shiftsForDay`, `userErrorPayload.userErrors`) against `ADD_HOURS_MUTATION`. Compare `groupedShiftsWithTotals` args and shift fields (`clockInDate`, `durationInMinutes`, `reviewerClockIn/OutTimestamp`, `notes { text }`) against `SHIFTS_QUERY`. Edit `gusto_client.py` minimally: the documents, `_FORWARDED_HEADERS`, and the parsing in `list_shifts`/`add_shift` if the response shape moved. Keep our own operation names (`FreelanceTracker*`).

**5. Verify reads with a probe** (inside the same `with` block, after editing):
```python
PROBE = """query FreelanceTrackerProbe($trackerId: ID!, $startDate: Date!, $endDate: Date!) {
  tracker(id: $trackerId) { id employmentType
    groupedShiftsWithTotals(payPeriod: {startDate: $startDate, endDate: $endDate}) {
      date shifts { id durationInMinutes } } } }"""
from datetime import date
start, end = date(2026, 9, 28), date(2026, 10, 4)
tid = s.tracker_id()
data = s.graphql("FreelanceTrackerProbe", PROBE,
                 {"trackerId": tid, "startDate": start.isoformat(), "endDate": end.isoformat()})
(out / "probe.json").write_text(json.dumps(data, indent=2))
print(s.list_shifts(tid, start, end)[:3])   # exercises the edited SHIFTS_QUERY
```
Use a recent week. A schema mismatch shows up as `GustoError("<op>: <message>")`.

**6. Tests and dry run.**
```bash
source venv/bin/activate && python -m pytest -q
python gusto_sync.py dry-run --start 2026-09-28 --end 2026-10-04
```
`dry-run` prints the shifts it would push and writes nothing to Gusto or the local state.

**7. First real push (only with approval).** Show the user the dry-run lines. If `ADD_HOURS_MUTATION` changed, ask before any `python gusto_sync.py run`, and suggest a one-day `--start/--end` window so the first push is a single real entry they can check in Gusto.

## What to report
- The error that triggered this, and its cause (schema field, variable type, response shape, or header).
- Old vs new request shape: changed `addHours` input fields and variable types, response fields, query args, and header **names**. Point to the saved `.playwright-mcp/gusto-recapture/*.graphql` files.
- The `gusto_client.py` diff summary, the pytest result, and the probe and dry-run output (what it would push).
- Whether a real push still needs the user's approval, and that the Gusto Chrome window was left open.
