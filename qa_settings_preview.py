"""Dump the dashboard HTML (settings view active) to /tmp for QA preview.

Produces the exact HTML the popover renders, sized to match the popover
(420 x 620 — PANEL_WIDTH x PANEL_MAX_HEIGHT from DashboardPanelController).
Open /tmp/freelance_settings_preview.html in a browser resized to that
window size, or drive via Playwright with viewport {width: 420, height: 620}.

    source venv/bin/activate && python qa_settings_preview.py

Stub-patches webkit.messageHandlers so bridge-only actions (router:*,
settings:*, postAction) don't throw in a browser context.
"""

from __future__ import annotations

from pathlib import Path

from dashboard_panel import DashboardPanelController

OUT_PATH = Path("/tmp/freelance_settings_preview.html")

# Browser-mode shim: Playwright and stock browsers don't have
# window.webkit.messageHandlers. Without a stub, every postAction(...) call
# would throw and break interactive tab switching / row add buttons. We
# inject a no-op shim at the top of <head> so the UI is fully interactive
# in a browser, then the existing JS still round-trips correctly.
BROWSER_SHIM = """
<script>
(function() {
    if (window.webkit && window.webkit.messageHandlers) return;
    window.webkit = {
        messageHandlers: {
            action: {
                postMessage: function(msg) {
                    console.log('[postAction]', msg);
                    // Fake a Stripe customers reply so the Integrations tab has
                    // something to render during QA.
                    if (typeof msg === 'string' && msg.indexOf('settings:refresh_stripe') === 0) {
                        window.setTimeout(function() {
                            if (window.__settingsAck) {
                                window.__settingsAck({
                                    type: 'stripe_customers',
                                    ok: true,
                                    customers: [
                                        { id: 'cus_preview1', display_name: 'Preview Customer One' },
                                        { id: 'cus_preview2', display_name: 'Preview Customer Two' }
                                    ]
                                });
                            }
                        }, 50);
                    }
                    if (typeof msg === 'string' && msg.indexOf('settings:save') === 0) {
                        window.setTimeout(function() {
                            if (window.__settingsAck) {
                                window.__settingsAck({ ok: true, errors: [] });
                            }
                        }, 50);
                    }
                }
            }
        }
    };
})();
</script>
"""


def main():
    panel = DashboardPanelController()
    panel.set_view("settings")
    daily, weekly, monthly = panel._placeholder_data()
    html = panel._generate_html(daily, weekly, monthly)

    # Inject shim right after the opening <head>.
    needle = "<head>"
    idx = html.find(needle)
    if idx < 0:
        raise SystemExit("Could not locate <head> in rendered HTML")
    insertion = idx + len(needle)
    html_with_shim = html[:insertion] + BROWSER_SHIM + html[insertion:]

    OUT_PATH.write_text(html_with_shim, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(html_with_shim)} bytes)")
    print(f"Popover size: 420 x 620")
    print(f"Open: open -a 'Google Chrome' --args --app=file://{OUT_PATH}"
          f" --window-size=420,620")


if __name__ == "__main__":
    main()
