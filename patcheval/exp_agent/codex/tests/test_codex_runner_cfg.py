"""Config-driven CodexRunner tests.

Exercise the cfg path of ``CodexRunner`` without touching Docker: model / effort /
auth come from an ``AgentRunConfig`` instead of the environment, the auth exports
are derived from the config (no os.environ mutation), and subscription auth is
seeded from the in-config ``auth_json`` blob. (Codex does NOT use the
defending-code harness — that is a Claude Code tool.)
"""
import json
import shlex

from patcheval.config import AgentRunConfig, AuthConfig, RunConfig
from patcheval.codex_runner import CodexRunner


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


def test_cfg_drives_model_effort_and_auth():
    cfg = _api_key_cfg(api_key="sk-test123")
    runner = CodexRunner("cid", "/workspace/repo", cfg=cfg)
    # model + effort come from cfg, not the environment.
    assert runner.model == "gpt-5.5"
    assert runner.effort == "xhigh"
    # cfg auth.method "api_key" maps to the runner's "api-key" resolved mode.
    assert runner._resolved_auth_mode() == "api-key"
    exports = runner._build_auth_exports()
    assert f"export CODEX_API_KEY={shlex.quote('sk-test123')}" in exports
    assert "unset OPENAI_API_KEY" in exports


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
