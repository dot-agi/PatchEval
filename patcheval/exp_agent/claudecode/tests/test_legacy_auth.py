"""Legacy (no-config) auth-selection test for ClaudeRunnerEnhanced.

When no AgentRunConfig is supplied, setup_environment must pick the in-container
auth method from whichever host-env credential is present. Here ONLY
ANTHROPIC_API_KEY is set (CLAUDE_CODE_OAUTH_TOKEN is deleted), so the rendered
install script must export the API key and must NOT export a real OAuth token --
the unset-all line clears it instead. All Docker/git IO is stubbed (mirrors the
stubbing style in tests/test_claude_setup.py).
"""
import os
import types
from pathlib import Path

from patcheval.claude_runner_enhanced import ClaudeRunnerEnhanced

# templates/claude-code-install.sh is resolved against cwd inside setup_environment;
# pin cwd to the project root so the relative template path exists.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_record():
    return types.SimpleNamespace(
        cve_id="CVE-2025-0002",
        problem_statement="A legacy-path vulnerability in the parser.",
        work_dir="/workspace/repo",
    )


def _install_script(writes):
    matches = [content for (path, content) in writes if path == "/tmp/install_claude.sh"]
    assert matches, "install script was never written to /tmp/install_claude.sh"
    return matches[0]


def test_legacy_api_key_only(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    # Host env: ONLY the API key is present; the OAuth token is explicitly absent.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-legacy")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    # No cfg -> legacy auth-selection path.
    runner = ClaudeRunnerEnhanced("cid", "/workspace/repo")

    writes = []
    monkeypatch.setattr(
        runner, "_write_file_to_container",
        lambda path, content: writes.append((path, content)),
    )
    monkeypatch.setattr(runner, "_exec_in_container", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_exec_in_container_with_output", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_install_harness_skills", lambda: None)

    record = _make_record()
    ok = runner.setup_environment(
        record, "default", os.getenv("ANTHROPIC_API_KEY"), "anthropic", ""
    )
    assert ok is True

    install = _install_script(writes)
    # Legacy path selected api_key from the host env.
    assert "export ANTHROPIC_API_KEY=" in install
    assert "sk-ant-legacy" in install
    # No real OAuth token is exported; it is unset by the unset-all line instead.
    assert "export CLAUDE_CODE_OAUTH_TOKEN=" not in install
    # Placeholder was substituted away.
    assert "{{AUTH_EXPORTS}}" not in install
