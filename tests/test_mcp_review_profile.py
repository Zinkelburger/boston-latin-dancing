"""The scheduled review's MCP profile exposes only the question-and-answer
tools: an unattended agent must not be able to hand-edit an event, attach an
unchecked link or publish."""

import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tool_names(monkeypatch, profile):
    if profile:
        monkeypatch.setenv("BLD_MCP_PROFILE", profile)
    else:
        monkeypatch.delenv("BLD_MCP_PROFILE", raising=False)
    spec = importlib.util.spec_from_file_location(f"bld_mcp_{profile}", ROOT / "mcp-server" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {t.name for t in asyncio.run(module.mcp.list_tools())}


def test_review_profile_is_only_the_review_tools(monkeypatch):
    assert _tool_names(monkeypatch, "review") == {
        "review_next", "review_answer", "review_skip", "review_link_check", "event_get"}


def test_full_profile_keeps_every_tool(monkeypatch):
    names = _tool_names(monkeypatch, None)
    assert {"event_edit", "event_publish", "review_answer"} <= names


def test_the_scheduled_review_uses_the_review_profile():
    import json
    config = json.loads((ROOT / "automation" / "claude-mcp.json").read_text())
    assert config["mcpServers"]["boston-latin-dance"]["env"]["BLD_MCP_PROFILE"] == "review"
