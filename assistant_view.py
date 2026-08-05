"""Assistant view rendered inside the WebKit dashboard popover.

Mirrors settings_view.py: this module owns the HTML/CSS/JS for the assistant
surface, and dashboard_panel.py wires the bridge messages.

Bridge message protocol:

    router:assistant
    router:dashboard
    assistant_ask:<base64 utterance>
    assistant_apply:<proposal_id>:<comma-separated row indices>
    assistant_clear

The utterance is base64-encoded because the bridge splits raw action strings
on ':' and spaces (see ActionMessageHandler): a note like
"note: sprint planning, 2:30 standup" would otherwise shred the parser.
"""

from __future__ import annotations

from datetime import timedelta


def _esc(text):
    """Escape HTML entities. Mirrors the helper in dashboard_panel.py."""
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def generate_assistant_css():
    return """
    body[data-view="assistant"] .dashboard-root,
    body[data-view="assistant"] .settings-root,
    body[data-view="assistant"] .footer { display: none; }
    body[data-view="dashboard"] .assistant-root,
    body[data-view="settings"] .assistant-root { display: none; }
    body[data-view="assistant"] {
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: rgba(255,255,255,0.24) transparent;
    }
    body[data-view="assistant"]::-webkit-scrollbar { width: 8px; }
    body[data-view="assistant"]::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,0.22);
        border-radius: 4px;
    }

    .assistant-root { width: 100%; box-sizing: border-box; }
    .assistant-root * { box-sizing: border-box; }

    .assistant-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        position: sticky;
        top: 0;
        background: #1c1c1e;
        z-index: 5;
    }
    .assistant-title { flex: 1; font-size: 13px; font-weight: 700; color: #c9d1d9; }

    .assistant-body { padding: 12px; }

    .assistant-turn { margin-bottom: 12px; }
    .assistant-turn.user {
        text-align: right;
    }
    .assistant-bubble {
        display: inline-block;
        max-width: 88%;
        text-align: left;
        padding: 7px 10px;
        border-radius: 10px;
        font-size: 12px;
        line-height: 1.45;
        background: rgba(255,255,255,0.05);
        color: #c9d1d9;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .assistant-turn.user .assistant-bubble {
        background: rgba(88,166,255,0.14);
        color: #cfe4ff;
    }
    .assistant-bubble.error {
        background: rgba(248,81,73,0.12);
        color: #ff9b95;
    }

    .assistant-empty {
        font-size: 11px;
        color: #8b949e;
        line-height: 1.6;
        padding: 6px 2px 0;
    }
    .assistant-empty code {
        color: #c9d1d9;
        background: rgba(255,255,255,0.06);
        padding: 1px 4px;
        border-radius: 4px;
    }

    /* --- proposal card --- */
    .prop-card {
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 10px;
        overflow: hidden;
        margin-top: 2px;
    }
    .prop-head {
        font-size: 11px;
        font-weight: 600;
        color: #8b949e;
        padding: 7px 10px;
        background: rgba(255,255,255,0.03);
        border-bottom: 1px solid rgba(255,255,255,0.07);
    }
    .prop-row {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
    }
    .prop-row:last-of-type { border-bottom: 0; }
    .prop-row input[type="checkbox"] {
        margin-top: 2px;
        accent-color: #58a6ff;
        flex: none;
    }
    .prop-when { font-size: 12px; color: #c9d1d9; }
    .prop-meta { font-size: 11px; color: #8b949e; margin-top: 1px; }
    .prop-warn { font-size: 11px; color: #d29922; margin-top: 2px; }
    .prop-row.collide { background: rgba(210,153,34,0.06); }

    .prop-actions {
        display: flex;
        gap: 6px;
        justify-content: flex-end;
        padding: 8px 10px;
        background: rgba(255,255,255,0.02);
        border-top: 1px solid rgba(255,255,255,0.07);
    }
    .prop-applied {
        font-size: 11px;
        color: #3fb950;
        padding: 8px 10px;
    }

    /* --- composer --- */
    .assistant-composer {
        display: flex;
        gap: 6px;
        padding: 10px 12px;
        border-top: 1px solid rgba(255,255,255,0.08);
        position: sticky;
        bottom: 0;
        background: #1c1c1e;
    }
    .assistant-input {
        flex: 1;
        min-width: 0;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px;
        color: #c9d1d9;
        font: inherit;
        font-size: 12px;
        padding: 7px 9px;
        -webkit-appearance: none;
    }
    .assistant-input:focus {
        outline: none;
        border-color: rgba(88,166,255,0.5);
    }
    .assistant-input::placeholder { color: #6e7681; }

    .assistant-pending {
        font-size: 11px;
        color: #8b949e;
        padding: 2px 2px 10px;
    }

    /* --- dashboard entry point --- */
    .assistant-launcher {
        display: flex;
        margin-bottom: 10px;
    }
    .assistant-launcher-input {
        flex: 1;
        min-width: 0;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 8px;
        color: #c9d1d9;
        font: inherit;
        font-size: 12px;
        padding: 7px 9px;
        -webkit-appearance: none;
    }
    .assistant-launcher-input:focus {
        outline: none;
        border-color: rgba(88,166,255,0.5);
    }
    .assistant-launcher-input::placeholder { color: #6e7681; }
    """


def render_launcher(enabled):
    """The always-present one-line input at the top of the dashboard.

    Hidden entirely when no OpenAI key is configured — that absence is the
    feature's off switch, so no separate preference is needed.
    """
    if not enabled:
        return ""
    return (
        '<div class="assistant-launcher">'
        '<input class="assistant-launcher-input" id="assistantLauncher" type="text" '
        'placeholder="Log time or ask…" autocomplete="off" spellcheck="false" '
        'onkeydown="assistantLauncherKey(event)">'
        '</div>'
    )


def _render_proposal(proposal):
    rows = []
    checked_default = set(proposal.default_selection())
    for index, item in enumerate(proposal.entries):
        collides = index in proposal.collisions
        start = item["start"]
        end = start + timedelta(seconds=item["duration_seconds"])
        when = (
            f'{start.strftime("%a %b %-d")} &middot; '
            f'{start.strftime("%-I:%M%p").lower()}–{end.strftime("%-I:%M%p").lower()}'
        )
        hours = item["duration_seconds"] / 3600
        meta = f'{_esc(item["project_name"])} &middot; {hours:.2f}h'
        if item["description"]:
            meta += f' &middot; {_esc(item["description"])}'
        checked = " checked" if index in checked_default else ""
        disabled = " disabled" if proposal.applied else ""
        warn = (
            '<div class="prop-warn">Overlaps time already logged</div>'
            if collides
            else ""
        )
        rows.append(
            f'<div class="prop-row{" collide" if collides else ""}">'
            f'<input type="checkbox" data-prop="{proposal.id}" value="{index}"{checked}{disabled}>'
            f'<div><div class="prop-when">{when}</div>'
            f'<div class="prop-meta">{meta}</div>{warn}</div></div>'
        )

    count = len(proposal.entries)
    head = f'Proposed {count} {"entry" if count == 1 else "entries"}'
    if proposal.collisions:
        head += f' &middot; {len(proposal.collisions)} overlap existing time'

    if proposal.applied:
        actions = (
            f'<div class="prop-applied">&#10003; Logged {proposal.applied_count} '
            f'{"entry" if proposal.applied_count == 1 else "entries"} to Toggl</div>'
        )
    else:
        actions = (
            '<div class="prop-actions">'
            f'<button class="settings-btn" type="button" '
            f'onclick="assistantDismiss(\'{proposal.id}\')">Dismiss</button>'
            f'<button class="settings-btn primary" type="button" '
            f'onclick="assistantApply(\'{proposal.id}\')">Apply</button>'
            '</div>'
        )

    return (
        f'<div class="prop-card" data-prop-card="{proposal.id}">'
        f'<div class="prop-head">{head}</div>'
        f'{"".join(rows)}{actions}</div>'
    )


def _render_turn(turn):
    if turn.role == "user":
        return (
            '<div class="assistant-turn user">'
            f'<div class="assistant-bubble">{_esc(turn.text)}</div></div>'
        )

    parts = []
    if turn.error:
        parts.append(
            f'<div class="assistant-bubble error">{_esc(turn.error)}</div>'
        )
    elif turn.text:
        parts.append(f'<div class="assistant-bubble">{_esc(turn.text)}</div>')
    if turn.note and turn.proposal is not None:
        parts.append(f'<div class="assistant-bubble">{_esc(turn.note)}</div>')
    if turn.proposal is not None:
        parts.append(_render_proposal(turn.proposal))
    return '<div class="assistant-turn">' + "".join(parts) + "</div>"


EMPTY_STATE = """
<div class="assistant-empty">
    Type what you worked on and confirm before anything is written.<br><br>
    <code>1 hour of Acme retainer at 9am the past 2 days, no note</code><br>
    <code>90 minutes on TriShot friday afternoon</code><br>
    <code>how many hours on Acme this month?</code>
</div>
"""


def generate_assistant_html(session, pending=False):
    """Render the assistant view from session state."""
    turns = "".join(_render_turn(t) for t in session.turns) if session.turns else EMPTY_STATE
    pending_html = (
        '<div class="assistant-pending">Thinking…</div>' if pending else ""
    )
    disabled = " disabled" if pending else ""
    return f"""
    <div class="assistant-root">
        <div class="assistant-header">
            <button class="settings-back" aria-label="Back to dashboard"
                    onclick="assistantGoBack()">&#8592;</button>
            <div class="assistant-title">Assistant</div>
            <div class="settings-actions">
                <button class="settings-btn" type="button"
                        onclick="assistantClear()">Clear</button>
            </div>
        </div>
        <div class="assistant-body" id="assistantBody">
            {turns}
            {pending_html}
        </div>
        <div class="assistant-composer">
            <input class="assistant-input" id="assistantInput" type="text"
                   placeholder="Log time or ask…" autocomplete="off"
                   spellcheck="false" onkeydown="assistantKey(event)"{disabled}>
            <button class="settings-btn primary" type="button"
                    onclick="assistantSend()"{disabled}>Send</button>
        </div>
    </div>
    """


def generate_assistant_js():
    # Single braces: embedded via f-string substitution by the caller.
    return """
    function assistantEncode(text) {
        // btoa() throws on non-Latin1; encodeURIComponent first so any
        // character survives the trip across the string-based bridge.
        return btoa(unescape(encodeURIComponent(text)));
    }

    function assistantSubmit(text) {
        text = (text || '').trim();
        if (!text) return;
        postAction('assistant_ask:' + assistantEncode(text));
    }

    function assistantSend() {
        var el = document.getElementById('assistantInput');
        if (!el || el.disabled) return;
        var text = el.value;
        el.value = '';
        assistantSubmit(text);
    }

    function assistantKey(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            assistantSend();
        }
    }

    // The dashboard launcher routes to the full view on submit, so a long
    // conversation never resizes the dashboard.
    function assistantLauncherKey(event) {
        if (event.key !== 'Enter' || event.shiftKey) return;
        event.preventDefault();
        var el = document.getElementById('assistantLauncher');
        if (!el) return;
        var text = el.value;
        el.value = '';
        if (!text.trim()) {
            document.body.setAttribute('data-view', 'assistant');
            postAction('router:assistant');
            return;
        }
        document.body.setAttribute('data-view', 'assistant');
        assistantSubmit(text);
    }

    function assistantGoBack() {
        document.body.setAttribute('data-view', 'dashboard');
        postAction('router:dashboard');
    }

    function assistantClear() {
        postAction('assistant_clear');
    }

    function assistantSelected(proposalId) {
        var boxes = document.querySelectorAll('input[data-prop="' + proposalId + '"]');
        var picked = [];
        for (var i = 0; i < boxes.length; i++) {
            if (boxes[i].checked) picked.push(boxes[i].value);
        }
        return picked;
    }

    function assistantApply(proposalId) {
        var picked = assistantSelected(proposalId);
        if (!picked.length) return;
        postAction('assistant_apply:' + proposalId + ':' + picked.join(','));
    }

    function assistantDismiss(proposalId) {
        var card = document.querySelector('[data-prop-card="' + proposalId + '"]');
        if (card) card.parentNode.removeChild(card);
    }

    function assistantScrollToEnd() {
        var body = document.getElementById('assistantBody');
        if (body) window.scrollTo(0, document.body.scrollHeight);
    }

    function assistantFocus() {
        var el = document.getElementById('assistantInput');
        if (el && !el.disabled) el.focus();
    }
    """
