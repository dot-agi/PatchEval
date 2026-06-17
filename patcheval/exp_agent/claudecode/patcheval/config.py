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


REASONING_BY_AGENT = {
    "claude-code": {"low", "medium", "high", "xhigh", "max"},
    "codex": {"low", "medium", "high", "xhigh"},   # gpt-5.5 has no "max"
}
METHODS_BY_AGENT = {
    "claude-code": {"subscription", "api_key", "bedrock", "vertex"},
    "codex": {"subscription", "api_key"},
}
CRED_KEYSETS = {
    ("claude-code", "subscription"): [["oauth_token"]],
    ("claude-code", "api_key"): [["api_key"]],
    ("claude-code", "bedrock"): [["aws_access_key_id","aws_secret_access_key","aws_region"], ["bearer_token","aws_region"]],
    ("claude-code", "vertex"): [["project","region","credentials_json_path"], ["project","region","access_token"]],
    ("codex", "api_key"): [["api_key"]],
    ("codex", "subscription"): [["auth_json"]],
}


def validate(cfg: "AgentRunConfig") -> None:
    if cfg.agent != BACKEND:
        raise ConfigError(f"this dir runs {BACKEND!r}; config has agent {cfg.agent!r}")
    if cfg.reasoning not in REASONING_BY_AGENT[cfg.agent]:
        raise ConfigError(f"reasoning {cfg.reasoning!r} invalid for {cfg.agent} "
                          f"(valid: {sorted(REASONING_BY_AGENT[cfg.agent])})")
    if cfg.auth.method not in METHODS_BY_AGENT[cfg.agent]:
        raise ConfigError(f"auth.method {cfg.auth.method!r} invalid for {cfg.agent} "
                          f"(valid: {sorted(METHODS_BY_AGENT[cfg.agent])})")
    keysets = CRED_KEYSETS[(cfg.agent, cfg.auth.method)]
    creds = cfg.auth.credentials
    if not any(all(k in creds for k in ks) for ks in keysets):
        raise ConfigError("credentials must include one of: " + " OR ".join("+".join(ks) for ks in keysets))
    if (cfg.agent, cfg.auth.method) == ("codex", "subscription"):
        aj = creds.get("auth_json") if isinstance(creds.get("auth_json"), dict) else {}
        toks = aj.get("tokens", {}) if isinstance(aj.get("tokens"), dict) else {}
        if not aj.get("auth_mode") or any(not toks.get(k) for k in ("access_token","refresh_token","account_id")):
            raise ConfigError("codex subscription auth_json must be the full blob: "
                              "auth_mode + tokens.{access_token,refresh_token,account_id}")


def _ex(name: str, value: str) -> str:
    return f"export {name}={_q(str(value))}"   # shlex.quote prevents shell injection/breakage


def build_claude_auth_exports(method, creds, model, reasoning) -> str:
    lines = [_ex("ANTHROPIC_MODEL", model), _ex("CLAUDE_CODE_EFFORT_LEVEL", reasoning)]
    if method == "subscription":
        lines += [_ex("CLAUDE_CODE_OAUTH_TOKEN", creds["oauth_token"]),
                  "unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL"]
    elif method == "api_key":
        lines += [_ex("ANTHROPIC_API_KEY", creds["api_key"]), "unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_BASE_URL"]
    elif method == "bedrock":
        lines += ["export CLAUDE_CODE_USE_BEDROCK=1", _ex("AWS_REGION", creds["aws_region"])]
        if "bearer_token" in creds:
            lines.append(_ex("AWS_BEARER_TOKEN_BEDROCK", creds["bearer_token"]))
        else:
            lines += [_ex("AWS_ACCESS_KEY_ID", creds["aws_access_key_id"]),
                      _ex("AWS_SECRET_ACCESS_KEY", creds["aws_secret_access_key"])]
        lines.append("unset CLAUDE_CODE_OAUTH_TOKEN")
    elif method == "vertex":
        lines += ["export CLAUDE_CODE_USE_VERTEX=1", _ex("CLOUD_ML_REGION", creds["region"]),
                  _ex("ANTHROPIC_VERTEX_PROJECT_ID", creds["project"])]
        lines.append(_ex("GOOGLE_APPLICATION_CREDENTIALS", creds["credentials_json_path"])
                     if "credentials_json_path" in creds else _ex("VERTEX_AUTH_TOKEN", creds["access_token"]))
        lines.append("unset CLAUDE_CODE_OAUTH_TOKEN")
    return "\n".join(lines)


def build_codex_auth(method, creds):
    """Return (bashrc_export_block, auth_json_content_or_None)."""
    if method == "api_key":
        return (_ex("CODEX_API_KEY", creds["api_key"]) + "\nunset OPENAI_API_KEY", None)
    if method == "subscription":
        return ("unset CODEX_API_KEY OPENAI_API_KEY", _json.dumps(creds["auth_json"], indent=2))
    return ("", None)
