import argparse
from pathlib import Path

import yaml
from patcheval.cli import resolve_run_config, _apply_config_to_args


def _write_cfg(tmp_path):
    obj = {
        "agent": "claude-code",
        "model": "claude-opus-4-8",
        "reasoning": "max",
        "auth": {"method": "subscription", "credentials": {"oauth_token": "sk-ant-oat01-x"}},
        "run": {"dataset": "custom.jsonl", "max_workers": 7, "strategy": "smart"},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(obj))
    return p


def _ns(**over):
    base = dict(config=None, dataset=None, outputs_root=None, max_workers=None,
                strategy=None, timeout=None, claude_timeout=None, tool_limits=None,
                max_cost_usd=None)
    base.update(over)
    return argparse.Namespace(**base)


def test_config_values_used(tmp_path):
    p = _write_cfg(tmp_path)
    cfg = resolve_run_config(_ns(config=str(p)))
    assert cfg is not None
    assert cfg.run.max_workers == 7
    assert cfg.run.strategy == "smart"
    assert cfg.run.dataset == "custom.jsonl"


def test_cli_flag_overrides_config(tmp_path):
    p = _write_cfg(tmp_path)
    cfg = resolve_run_config(_ns(config=str(p), max_workers=9))
    assert cfg.run.max_workers == 9          # explicit CLI flag wins
    assert cfg.run.strategy == "smart"       # untouched config value remains


def test_no_config_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)              # no ./config.yaml in this cwd
    assert resolve_run_config(_ns(config=None)) is None


def test_config_mode_coerces_paths(tmp_path):
    # Docker-free: config mode must yield pathlib.Path for dataset/outputs_root
    # so downstream load_dataset().exists() and outputs_root.mkdir() work.
    p = _write_cfg(tmp_path)
    args = _ns(config=str(p))
    cfg = resolve_run_config(args)
    _apply_config_to_args(args, cfg)
    assert isinstance(args.dataset, Path)
    assert isinstance(args.outputs_root, Path)
    assert str(args.dataset) == "custom.jsonl"
    assert args.outputs_root == Path(cfg.run.outputs_root)
