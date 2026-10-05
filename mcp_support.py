"""Helpers shared by the MCP server and the settings UI that enables it.

Kept free of MCP SDK imports so the settings view can render registration
snippets and detect existing registrations without the `mcp` package.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SERVER_NAME = "freelance-tracker"
REPO_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = REPO_DIR / "mcp_server.py"


def python_executable() -> str:
    """The venv interpreter the LaunchAgent uses, falling back to the current one."""
    venv_python = REPO_DIR / "venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def claude_code_command() -> str:
    """Shell command that registers the server with Claude Code (user scope)."""
    return (
        f"claude mcp add --scope user --transport stdio {SERVER_NAME} -- "
        f"{python_executable()} {SERVER_SCRIPT}"
    )


def codex_config_block() -> str:
    """TOML block for ~/.codex/config.toml."""
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f'command = "{python_executable()}"\n'
        f'args = ["{SERVER_SCRIPT}"]\n'
    )


def detect_registrations(home: Path | None = None) -> dict:
    """Best-effort, read-only check of where the server is already registered.

    Returns {"claude_code": bool, "codex": bool}. Never raises: a malformed
    config file just reads as "not registered".
    """
    home = home or Path.home()
    result = {"claude_code": False, "codex": False}

    claude_json = home / ".claude.json"
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
        result["claude_code"] = _claude_json_has_server(data)
    except (OSError, ValueError):
        pass

    codex_toml = home / ".codex" / "config.toml"
    try:
        text = codex_toml.read_text(encoding="utf-8")
        pattern = rf"^\s*\[mcp_servers\.{re.escape(SERVER_NAME)}\]"
        result["codex"] = re.search(pattern, text, re.MULTILINE) is not None
    except OSError:
        pass

    return result


def _claude_json_has_server(data) -> bool:
    """User-scope servers live at top-level `mcpServers`; project-scope ones
    under `projects.<path>.mcpServers`. Either counts as registered."""
    if not isinstance(data, dict):
        return False
    if SERVER_NAME in (data.get("mcpServers") or {}):
        return True
    for proj in (data.get("projects") or {}).values():
        if isinstance(proj, dict) and SERVER_NAME in (proj.get("mcpServers") or {}):
            return True
    return False


def probe_server(timeout_seconds: float = 20.0) -> dict:
    """Spawn the server and run initialize + tools/list + get_data_freshness.

    Used by the settings "Test server" button. Imports the MCP SDK lazily so
    this module stays importable without it. Returns a JSON-friendly dict.
    """
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(
            command=python_executable(), args=[str(SERVER_SCRIPT)], cwd=str(REPO_DIR),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                fresh = await session.call_tool("get_data_freshness", {})
                data = getattr(fresh, "structuredContent", None) or {}
                if not data:
                    for block in fresh.content or []:
                        text = getattr(block, "text", None)
                        if text:
                            try:
                                data = json.loads(text)
                            except ValueError:
                                data = {}
                            break
                if isinstance(data, dict) and "result" in data and len(data) == 1:
                    data = data["result"]
                return {
                    "ok": True,
                    "tools": len(tools.tools),
                    "enabled": bool(data.get("mcp_enabled", False)) if isinstance(data, dict) else None,
                    "data_as_of": data.get("this_month_as_of") if isinstance(data, dict) else None,
                }

    async def with_timeout():
        with anyio.fail_after(timeout_seconds):
            return await run()

    try:
        return anyio.run(with_timeout)
    except TimeoutError:
        return {"ok": False, "error": f"Server did not respond within {timeout_seconds:.0f}s"}
    except Exception as exc:  # surfaced verbatim in the settings pane
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
