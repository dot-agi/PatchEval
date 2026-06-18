import yaml
from dataclasses import dataclass, field
from pathlib import Path
from shlex import quote as _q
from typing import Optional

# This module is codex-specific. The claudecode/ dir has its own config.py with
# the Claude-Code auth methods + renderer; the two intentionally do not share code.
BACKEND = "codex"


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


# --- codex validation -------------------------------------------------------
VALID_REASONING = {"low", "medium", "high", "xhigh"}   # gpt-5.5 has no "max"
VALID_METHODS = {"api_key", "subscription"}
CRED_KEYSETS = {
    "api_key": [["api_key"]],
    "subscription": [["auth_json"], ["auth_json_path"]],
}


def validate(cfg: "AgentRunConfig") -> None:
    if cfg.agent != BACKEND:
        raise ConfigError(f"this dir runs {BACKEND!r}; config has agent {cfg.agent!r}")
    if cfg.reasoning not in VALID_REASONING:
        raise ConfigError(f"reasoning {cfg.reasoning!r} invalid for codex "
                          f"(valid: {sorted(VALID_REASONING)})")
    if cfg.auth.method not in VALID_METHODS:
        raise ConfigError(f"auth.method {cfg.auth.method!r} invalid for codex "
                          f"(valid: {sorted(VALID_METHODS)})")
    creds = cfg.auth.credentials
    keysets = CRED_KEYSETS[cfg.auth.method]
    if not any(all(k in creds for k in ks) for ks in keysets):
        raise ConfigError("credentials must include one of: " + " OR ".join("+".join(ks) for ks in keysets))
    if cfg.auth.method == "subscription" and "auth_json" in creds:
        aj = creds.get("auth_json") if isinstance(creds.get("auth_json"), dict) else {}
        toks = aj.get("tokens", {}) if isinstance(aj.get("tokens"), dict) else {}
        if not aj.get("auth_mode") or any(not toks.get(k) for k in ("access_token", "refresh_token", "account_id")):
            raise ConfigError("codex subscription auth_json must be the full blob: "
                              "auth_mode + tokens.{access_token,refresh_token,account_id}")


def _ex(name: str, value: str) -> str:
    return f"export {name}={_q(str(value))}"   # shlex.quote prevents shell injection/breakage


def build_codex_auth(method, creds):
    """Return (bashrc_export_block, auth_json_content_or_None) for the in-container .bashrc."""
    if method == "api_key":
        return (_ex("CODEX_API_KEY", creds["api_key"]) + "\nunset OPENAI_API_KEY", None)
    if method == "subscription":
        # Tokens are seeded into the container's auth.json separately (the runner's
        # _seed_subscription_auth, from inline auth_json OR auth_json_path), so the
        # export block must NOT require an inline blob — just clear stray API keys.
        return ("unset CODEX_API_KEY OPENAI_API_KEY", None)
    return ("", None)
