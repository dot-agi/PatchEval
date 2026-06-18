import yaml
from dataclasses import dataclass, field
from pathlib import Path
from shlex import quote as _q
from typing import Optional

# This module is Claude-Code-specific. The codex/ dir has its own config.py with
# the Codex auth methods + renderer; the two intentionally do not share code.
BACKEND = "claude-code"


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


# --- claude-code validation -------------------------------------------------
VALID_REASONING = {"low", "medium", "high", "xhigh", "max"}
VALID_METHODS = {"subscription", "api_key", "bedrock", "vertex"}
CRED_KEYSETS = {
    "subscription": [["oauth_token"]],
    "api_key": [["api_key"]],
    "bedrock": [["aws_access_key_id", "aws_secret_access_key", "aws_region"], ["bearer_token", "aws_region"]],
    "vertex": [["project", "region", "credentials_json_path"], ["project", "region", "access_token"]],
}


def validate(cfg: "AgentRunConfig") -> None:
    if cfg.agent != BACKEND:
        raise ConfigError(f"this dir runs {BACKEND!r}; config has agent {cfg.agent!r}")
    if cfg.reasoning not in VALID_REASONING:
        raise ConfigError(f"reasoning {cfg.reasoning!r} invalid for claude-code "
                          f"(valid: {sorted(VALID_REASONING)})")
    if cfg.auth.method not in VALID_METHODS:
        raise ConfigError(f"auth.method {cfg.auth.method!r} invalid for claude-code "
                          f"(valid: {sorted(VALID_METHODS)})")
    creds = cfg.auth.credentials
    keysets = CRED_KEYSETS[cfg.auth.method]
    if not any(all(k in creds for k in ks) for ks in keysets):
        raise ConfigError("credentials must include one of: " + " OR ".join("+".join(ks) for ks in keysets))


def _ex(name: str, value: str) -> str:
    return f"export {name}={_q(str(value))}"   # shlex.quote prevents shell injection/breakage


def build_claude_auth_exports(method, creds, model, reasoning) -> str:
    lines = [_ex("ANTHROPIC_MODEL", model), _ex("CLAUDE_CODE_EFFORT_LEVEL", reasoning),
             "unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX"]
    if method == "subscription":
        lines.append(_ex("CLAUDE_CODE_OAUTH_TOKEN", creds["oauth_token"]))
    elif method == "api_key":
        lines.append(_ex("ANTHROPIC_API_KEY", creds["api_key"]))
    elif method == "bedrock":
        lines += ["export CLAUDE_CODE_USE_BEDROCK=1", _ex("AWS_REGION", creds["aws_region"])]
        if "bearer_token" in creds:
            lines.append(_ex("AWS_BEARER_TOKEN_BEDROCK", creds["bearer_token"]))
        else:
            lines += [_ex("AWS_ACCESS_KEY_ID", creds["aws_access_key_id"]),
                      _ex("AWS_SECRET_ACCESS_KEY", creds["aws_secret_access_key"])]
    elif method == "vertex":
        lines += ["export CLAUDE_CODE_USE_VERTEX=1", _ex("CLOUD_ML_REGION", creds["region"]),
                  _ex("ANTHROPIC_VERTEX_PROJECT_ID", creds["project"])]
        lines.append(_ex("GOOGLE_APPLICATION_CREDENTIALS", creds["credentials_json_path"])
                     if "credentials_json_path" in creds else _ex("VERTEX_AUTH_TOKEN", creds["access_token"]))
    return "\n".join(lines)
