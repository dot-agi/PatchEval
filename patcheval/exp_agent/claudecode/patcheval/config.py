import yaml
from dataclasses import dataclass, field
from pathlib import Path
from shlex import quote as _q
from typing import Optional
import json as _json

BACKEND = "claude-code"   # the codex/ copy of this file sets this to "codex"


class ConfigError(ValueError):
    """Malformed or incomplete run config."""


@dataclass
class AuthConfig:
    method: str
    credentials: dict


@dataclass
class RunConfig:
    dataset: str = "dataset.jsonl"
    outputs_root: str = "./outputs/run"
    strategy: str = "default"
    max_workers: int = 4
    timeout: str = "45m"
    agent_timeout: str = "30m"
    tool_limits: Optional[str] = "total:200"
    max_cost_usd: float = 1000.0
    docker_platform: Optional[str] = None
    use_harness_skills: bool = True


@dataclass
class AgentRunConfig:
    agent: str
    model: str
    reasoning: str
    auth: AuthConfig
    run: RunConfig = field(default_factory=RunConfig)


def load_config(path) -> "AgentRunConfig":
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {p}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping")
    for k in ("agent", "model", "reasoning", "auth"):
        if k not in data:
            raise ConfigError(f"Missing required key: {k!r}")
    auth = data["auth"] or {}
    if "method" not in auth or "credentials" not in auth:
        raise ConfigError("auth must contain 'method' and 'credentials'")
    run_raw = data.get("run") or {}
    known = RunConfig().__dict__.keys()
    if set(run_raw) - set(known):
        raise ConfigError(f"Unknown run keys: {sorted(set(run_raw) - set(known))}")
    cfg = AgentRunConfig(
        agent=data["agent"], model=data["model"], reasoning=str(data["reasoning"]),
        auth=AuthConfig(auth["method"], auth["credentials"] or {}),
        run=RunConfig(**{k: run_raw[k] for k in run_raw if k in known}),
    )
    validate(cfg)
    return cfg


def validate(cfg: "AgentRunConfig") -> None:   # full implementation lands in Task A2
    return None
