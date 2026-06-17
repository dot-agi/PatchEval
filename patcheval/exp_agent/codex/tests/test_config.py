import yaml, pytest
from patcheval.config import load_config, ConfigError
def _w(tmp_path, obj):
    p = tmp_path/"config.yaml"; p.write_text(yaml.safe_dump(obj)); return p
def test_codex_api_key_ok(tmp_path):
    cfg = load_config(_w(tmp_path, {"agent":"codex","model":"gpt-5.5","reasoning":"xhigh",
        "auth":{"method":"api_key","credentials":{"api_key":"sk-oai"}}}))
    assert cfg.agent == "codex" and cfg.auth.method == "api_key"
def test_codex_rejects_max(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_w(tmp_path, {"agent":"codex","model":"gpt-5.5","reasoning":"max",
            "auth":{"method":"api_key","credentials":{"api_key":"k"}}}))
def test_codex_rejects_claude_agent(tmp_path):
    with pytest.raises(ConfigError):     # this dir is BACKEND=codex
        load_config(_w(tmp_path, {"agent":"claude-code","model":"claude-opus-4-8","reasoning":"max",
            "auth":{"method":"subscription","credentials":{"oauth_token":"t"}}}))
def test_codex_subscription_full_blob(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_w(tmp_path, {"agent":"codex","model":"gpt-5.5","reasoning":"high",
            "auth":{"method":"subscription","credentials":{"auth_json":{"tokens":{"access_token":"a"}}}}}))
    load_config(_w(tmp_path, {"agent":"codex","model":"gpt-5.5","reasoning":"high",
        "auth":{"method":"subscription","credentials":{"auth_json":{"auth_mode":"chatgpt",
            "tokens":{"access_token":"a","refresh_token":"r","id_token":"i","account_id":"x"}}}}}))
