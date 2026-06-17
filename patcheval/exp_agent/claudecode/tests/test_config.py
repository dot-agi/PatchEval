import yaml, pytest
from patcheval.config import load_config, ConfigError

def _w(tmp_path, obj):
    p = tmp_path / "config.yaml"; p.write_text(yaml.safe_dump(obj)); return p

def test_load_defaults(tmp_path):
    cfg = load_config(_w(tmp_path, {"agent":"claude-code","model":"claude-opus-4-8","reasoning":"max",
        "auth":{"method":"subscription","credentials":{"oauth_token":"sk-ant-oat01-x"}}}))
    assert cfg.agent == "claude-code" and cfg.model == "claude-opus-4-8"
    assert cfg.reasoning == "max" and cfg.auth.method == "subscription"
    assert cfg.auth.credentials["oauth_token"] == "sk-ant-oat01-x"
    assert cfg.run.dataset == "dataset.jsonl" and cfg.run.max_workers == 4
    assert cfg.run.docker_platform is None and cfg.run.use_harness_skills is True
