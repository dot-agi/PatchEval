import shlex
from patcheval.config import build_claude_auth_exports, build_codex_auth
def test_api_key_quoted():
    s = build_claude_auth_exports("api_key", {"api_key":"sk-ant"}, "claude-opus-4-8", "high")
    assert f"export ANTHROPIC_API_KEY={shlex.quote('sk-ant')}" in s and "unset CLAUDE_CODE_OAUTH_TOKEN" in s
def test_injection_safe():
    s = build_claude_auth_exports("api_key", {"api_key":"a'b; rm -rf /"}, "claude-opus-4-8", "high")
    line = [l for l in s.splitlines() if l.startswith("export ANTHROPIC_API_KEY=")][0]
    assert shlex.split(line) == ["export", "ANTHROPIC_API_KEY=a'b; rm -rf /"]
def test_codex_api_key():
    exports, aj = build_codex_auth("api_key", {"api_key":"sk-oai"})
    assert f"export CODEX_API_KEY={shlex.quote('sk-oai')}" in exports and aj is None
