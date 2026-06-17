"""Docker-free tests for the config-driven ClaudeRunnerEnhanced setup (Task B2).

These exercise `setup_environment` and `_build_claude_command` with a config
(`AgentRunConfig`) supplied, stubbing every container/IO call so nothing touches
Docker. They verify that the in-container install script is rendered from the
config auth (not the host-env OAuth flow), that the harness-skills install is
gated on `run.use_harness_skills`, and that the `--model` flag comes from cfg.
"""
import types
from pathlib import Path

import pytest

from patcheval.config import AgentRunConfig, AuthConfig, RunConfig
from patcheval.claude_runner_enhanced import ClaudeRunnerEnhanced

# Relative template paths (templates/claude-code-install.sh, templates/default.md)
# are resolved against cwd inside setup_environment; pin cwd to the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_cfg(use_harness_skills: bool) -> AgentRunConfig:
    return AgentRunConfig(
        agent="claude-code",
        model="claude-opus-4-8",
        reasoning="high",
        auth=AuthConfig(method="api_key", credentials={"api_key": "sk-ant-xyz"}),
        run=RunConfig(use_harness_skills=use_harness_skills),
    )


def _make_record():
    return types.SimpleNamespace(
        cve_id="CVE-2025-0001",
        problem_statement="A test vulnerability in the parser.",
        work_dir="/workspace/repo",
    )


def _runner_with_stubs(cfg, monkeypatch):
    """Build a runner and stub every container/IO + harness call to no-ops.

    Returns (runner, writes, harness_calls) where `writes` accumulates every
    (path, content) passed to _write_file_to_container and `harness_calls`
    records each _install_harness_skills invocation.
    """
    runner = ClaudeRunnerEnhanced("cid", "/workspace/repo", cfg=cfg)

    writes = []
    harness_calls = []

    # Instance-level stubs (shadow the bound methods); they are called as
    # self._x(...), so the lambdas take no implicit self.
    monkeypatch.setattr(
        runner, "_write_file_to_container",
        lambda path, content: writes.append((path, content)),
    )
    # Container exec helpers return empty output so the git/baseline branches
    # take their "nothing to do" paths without raising.
    monkeypatch.setattr(runner, "_exec_in_container", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_exec_in_container_with_output", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_install_harness_skills", lambda: harness_calls.append(1))

    return runner, writes, harness_calls


def _install_script(writes):
    matches = [content for (path, content) in writes if path == "/tmp/install_claude.sh"]
    assert matches, "install script was never written to /tmp/install_claude.sh"
    return matches[0]


def test_setup_renders_api_key_auth_and_gates_harness_off(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    cfg = _make_cfg(use_harness_skills=False)
    runner, writes, harness_calls = _runner_with_stubs(cfg, monkeypatch)
    record = _make_record()

    ok = runner.setup_environment(record, "default", None, "anthropic", "")
    assert ok is True

    install = _install_script(writes)
    # Auth came from cfg (api_key), not the host-env subscription flow.
    assert "export ANTHROPIC_API_KEY=" in install
    assert "sk-ant-xyz" in install
    # The placeholder was substituted away.
    assert "{{AUTH_EXPORTS}}" not in install
    # No OAuth token is exported/set in api_key mode. (build_claude_auth_exports
    # deliberately emits `unset CLAUDE_CODE_OAUTH_TOKEN ...`, so we assert the
    # absence of the *export*, which is the meaningful "no OAuth token" check.)
    assert "export CLAUDE_CODE_OAUTH_TOKEN" not in install
    # Harness skills gated off via run.use_harness_skills=False.
    assert harness_calls == []


def test_setup_installs_harness_when_enabled(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    cfg = _make_cfg(use_harness_skills=True)
    runner, writes, harness_calls = _runner_with_stubs(cfg, monkeypatch)
    record = _make_record()

    ok = runner.setup_environment(record, "default", None, "anthropic", "")
    assert ok is True

    install = _install_script(writes)
    assert "export ANTHROPIC_API_KEY=" in install
    assert "{{AUTH_EXPORTS}}" not in install
    # Harness skills installed because run.use_harness_skills=True.
    assert harness_calls == [1]


def test_build_command_uses_cfg_model(monkeypatch):
    # Ensure no host env leaks in to make this a real cfg-driven assertion.
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    cfg = _make_cfg(use_harness_skills=False)
    runner = ClaudeRunnerEnhanced("cid", "/workspace/repo", cfg=cfg)

    cmd = runner._build_claude_command("default")
    assert "--model claude-opus-4-8" in cmd
