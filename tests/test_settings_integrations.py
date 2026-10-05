"""Tests for the Integrations grid and preferences view-state restoration.

The popover is transient: switching to a browser to fetch a credential
dismisses it. Reopening used to drop the user on the first tab, so they had to
click Integrations and scroll to find the field again. These cover the grid
layout and the state that makes reopening land where they left off.
"""

import unittest

from settings_view import generate_settings_html, generate_settings_js


CONFIGURED = {
    "TOGGL_API_TOKEN": "tok",
    "TOGGL_WORKSPACE_ID": "1",
    "STRIPE_API_KEY": "sk_live_x",
    "GOOGLE_CALENDAR_ICS_URL": "https://example.com/a.ics",
    "OPENAI_API_KEY": "sk-abc",
}


class TestIntegrationsGrid(unittest.TestCase):
    def test_every_integration_has_a_cell(self):
        html = generate_settings_html({}, CONFIGURED)
        for key in ("toggl", "openai", "stripe", "calendar", "mapping"):
            self.assertIn(f'data-intg-cell="{key}"', html)

    def test_every_cell_has_a_matching_detail_pane(self):
        html = generate_settings_html({}, CONFIGURED)
        for key in ("toggl", "openai", "stripe", "calendar", "mapping"):
            self.assertIn(f'data-intg-detail="{key}"', html)

    def test_configured_integration_reads_active(self):
        html = generate_settings_html({}, CONFIGURED)
        self.assertIn("Active", html)
        # Toggl needs both token and workspace before it counts as configured.
        partial = generate_settings_html({}, {"TOGGL_API_TOKEN": "tok"})
        self.assertIn("Configure integration", partial)

    def test_unconfigured_integration_prompts_to_configure(self):
        html = generate_settings_html({}, {})
        self.assertIn("Configure integration", html)

    def test_credential_fields_still_render_inside_details(self):
        html = generate_settings_html({}, CONFIGURED)
        for field_id in (
            "set_toggl_token",
            "set_toggl_workspace",
            "set_stripe_key",
            "set_calendar_ics_url",
            "set_openai_key",
        ):
            self.assertIn(field_id, html)

    def test_openai_detail_carries_privacy_and_scoping_guidance(self):
        html = generate_settings_html({}, CONFIGURED)
        self.assertIn("Privacy:", html)
        self.assertIn("/v1/responses", html)
        self.assertIn("budget", html)

    def test_lone_cell_group_spans_full_width(self):
        html = generate_settings_html({}, CONFIGURED)
        self.assertIn('class="intg-grid single"', html)


class TestViewStateRestoration(unittest.TestCase):
    def test_defaults_to_first_tab_and_the_grid(self):
        html = generate_settings_html({}, CONFIGURED)
        self.assertIn('class="settings-tab active" data-tab="caching"', html)
        self.assertIn('data-intg=""', html)

    def test_restores_the_active_tab(self):
        html = generate_settings_html({}, CONFIGURED, active_tab="integrations")
        self.assertIn('class="settings-tab active" data-tab="integrations"', html)
        self.assertIn('class="settings-panel active" data-panel="integrations"', html)

    def test_restores_the_open_integration(self):
        html = generate_settings_html(
            {}, CONFIGURED, active_tab="integrations", active_integration="openai"
        )
        self.assertIn('data-intg="openai"', html)
        self.assertIn('class="intg-detail active" data-intg-detail="openai"', html)

    def test_other_details_stay_closed(self):
        html = generate_settings_html(
            {}, CONFIGURED, active_tab="integrations", active_integration="openai"
        )
        self.assertIn('class="intg-detail" data-intg-detail="stripe"', html)

    def test_unknown_tab_falls_back_instead_of_rendering_nothing(self):
        html = generate_settings_html({}, CONFIGURED, active_tab="bogus")
        self.assertIn('class="settings-tab active" data-tab="caching"', html)

    def test_unknown_integration_falls_back_to_the_grid(self):
        html = generate_settings_html(
            {}, CONFIGURED, active_tab="integrations", active_integration="bogus"
        )
        self.assertNotIn("intg-detail active", html)


if __name__ == "__main__":
    unittest.main()


class TestAgentsMcpCell(unittest.TestCase):
    def test_mcp_cell_and_detail_render(self):
        html = generate_settings_html({"mcp_enabled": False}, CONFIGURED)
        self.assertIn('data-intg-cell="mcp"', html)
        self.assertIn('data-intg-detail="mcp"', html)
        self.assertIn('id="set_mcp_enabled"', html)
        self.assertIn('id="set_mcp_anonymize"', html)
        self.assertIn("claude mcp add --scope user --transport stdio freelance-tracker", html)
        self.assertIn("[mcp_servers.freelance-tracker]", html)
        self.assertIn("settingsTestMcp()", html)
        self.assertIn("settings:test_mcp", generate_settings_js())

    def test_mcp_cell_reads_active_only_when_enabled(self):
        off = generate_settings_html({"mcp_enabled": False}, CONFIGURED)
        on = generate_settings_html({"mcp_enabled": True}, CONFIGURED)
        self.assertNotIn('id="set_mcp_enabled" checked', off)
        self.assertIn('id="set_mcp_enabled" checked', on)

    def test_form_collects_mcp_flags(self):
        js = generate_settings_js()
        self.assertIn("mcp_enabled: settingsReadBool('set_mcp_enabled')", js)
        self.assertIn("mcp_anonymize: settingsReadBool('set_mcp_anonymize')", js)
        self.assertIn("reply.type === 'mcp_test'", js)


class TestMcpSettingsSave(unittest.TestCase):
    def test_handler_coerces_flags_to_bool(self):
        from unittest import mock
        import settings_handler as H
        from preferences import DEFAULT_PREFERENCES
        saved = {}
        with mock.patch.object(H, "load_preferences", lambda: dict(DEFAULT_PREFERENCES)), \
             mock.patch.object(H, "save_preferences", lambda p: saved.update(p)):
            result = H.apply_settings_save({"mcp_enabled": 1, "mcp_anonymize": ""})
        self.assertTrue(result["ok"], result)
        self.assertIs(saved["mcp_enabled"], True)
        self.assertIs(saved["mcp_anonymize"], False)
