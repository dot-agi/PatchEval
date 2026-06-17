"""Config-driven CodexRunner tests (Task D1).

These exercise the cfg path of ``CodexRunner`` without touching Docker: model /
effort / auth come from an ``AgentRunConfig`` instead of the environment, the
auth exports are derived from the config (no os.environ mutation), the harness
install is gated on ``run.use_harness_skills``, and subscription auth is seeded
from the in-config ``auth_json`` blob.
"""
import json
import shlex
from dataclasses import dataclass
from unittest.mock import MagicMock

from patcheval.config import AgentRunConfig, AuthConfig, RunConfig
from patcheval.codex_runner import CodexRunner


@dataclass
class _FakeRecord:
    cve_id: str = "CVE-2025-0001"
    work_dir: str = "/workspace/repo"
    problem_statement: str = "An example vulnerability to repair."


def _api_key_cfg(api_key="sk-test123", **run_kw):
    return AgentRunConfig(
        agent="codex", model="gpt-5.5", reasoning="xhigh",
        auth=AuthConfig(method="api_key", credentials={"api_key": api_key}),
        run=RunConfig(**run_kw),
    )


def _subscription_cfg(auth_json, **run_kw):
    return AgentRunConfig(
        agent="codex", model="gpt-5.5", reasoning="high",
        auth=AuthConfig(method="subscription", credentials={"auth_json": auth_json}),
        run=RunConfig(**run_kw),
    )


def _stub_container_io(runner, install_output="INSTALL_SUCCESS"):
    """Neutralize every method that would touch Docker / the host filesystem."""
    runner._render_install_script = lambda *a, **k: "# install script"
    runner._write_file_to_container = lambda *a, **k: None
    runner._exec_in_container = lambda *a, **k: ""
    runner._exec_in_container_with_output = lambda *a, **k: install_output
    runner._install_security_plugin = lambda *a, **k: None
    runner._append_gitignore = lambda *a, **k: None
    runner._git_baseline_commit = lambda *a, **k: None
    runner._git_commit_tooling = lambda *a, **k: None
    runner._safe_cat = lambda *a, **k: ""


def test_cfg_drives_model_effort_and_auth():
    cfg = _api_key_cfg(api_key="sk-test123")
    runner = CodexRunner("cid", "/workspace/repo", cfg=cfg)

    # model + effort come from cfg, not the environment.
    assert runner.model == "gpt-5.5"
    assert runner.effort == "xhigh"
    # cfg auth.method "api_key" maps to the runner's "api-key" resolved mode.
    assert runner._resolved_auth_mode() == "api-key"

    exports = runner._build_auth_exports()
    # shlex-quoted CODEX_API_KEY export + OPENAI_API_KEY unset, derived from cfg.
    assert "export CODEX_API_KEY=" in exports
    assert f"export CODEX_API_KEY={shlex.quote('sk-test123')}" in exports
    assert "unset OPENAI_API_KEY" in exports


def test_harness_skipped_when_use_harness_skills_false():
    cfg = _api_key_cfg(use_harness_skills=False)
    runner = CodexRunner("cid", "/workspace/repo", cfg=cfg)
    _stub_container_io(runner)
    runner._install_harness = MagicMock()

    ok = runner.setup_environment(_FakeRecord(), "default", "", "openai", "")

    assert ok is True
    runner._install_harness.assert_not_called()


def test_harness_installed_when_use_harness_skills_true():
    cfg = _api_key_cfg(use_harness_skills=True)
    runner = CodexRunner("cid", "/workspace/repo", cfg=cfg)
    _stub_container_io(runner)
    runner._install_harness = MagicMock()

    ok = runner.setup_environment(_FakeRecord(), "default", "", "openai", "")

    assert ok is True
    runner._install_harness.assert_called_once()


def test_seed_subscription_auth_writes_from_cfg():
    auth_json = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "a", "refresh_token": "r",
            "id_token": "i", "account_id": "x",
        },
    }
    cfg = _subscription_cfg(auth_json)
    runner = CodexRunner("cid", "/workspace/repo", cfg=cfg)

    captured = {}

    def _capture_write(path, content):
        captured["path"] = path
        captured["content"] = content

    runner._write_file_to_container = _capture_write
    runner._exec_in_container = lambda *a, **k: ""  # chown no-op

    runner._seed_subscription_auth()

    assert captured["path"] == f"{runner.codex_home}/auth.json"
    assert json.loads(captured["content"]) == auth_json
