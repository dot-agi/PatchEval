import yaml, pytest
from patcheval.config import load_config, ConfigError
def _w(tmp_path, obj):
    p = tmp_path/"config.yaml"; p.write_text(yaml.safe_dump(obj)); return p

def test_rejects_wrong_agent_for_dir(tmp_path):     # claudecode dir: BACKEND=claude-code
    with pytest.raises(ConfigError):
        load_config(_w(tmp_path, {"agent":"codex","model":"gpt-5.5","reasoning":"high",
            "auth":{"method":"api_key","credentials":{"api_key":"k"}}}))
def test_rejects_incomplete_creds(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_w(tmp_path, {"agent":"claude-code","model":"claude-opus-4-8","reasoning":"max",
            "auth":{"method":"subscription","credentials":{}}}))
def test_bedrock_two_forms(tmp_path):
    for creds in ({"bearer_token":"t","aws_region":"r"},
                  {"aws_access_key_id":"a","aws_secret_access_key":"s","aws_region":"r"}):
        load_config(_w(tmp_path, {"agent":"claude-code","model":"claude-opus-4-8","reasoning":"high",
            "auth":{"method":"bedrock","credentials":creds}}))
