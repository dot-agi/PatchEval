import shlex
from patcheval.config import build_claude_auth_exports, build_codex_auth


def _unset_line(s):
    # build_claude_auth_exports emits exactly one `unset ...` line that wipes
    # every other auth/provider var before exporting the selected method's vars.
    lines = [l for l in s.splitlines() if l.startswith("unset ")]
    assert lines, "expected an unset-all line"
    return lines[0]


def test_api_key_quoted():
    s = build_claude_auth_exports("api_key", {"api_key":"sk-ant"}, "claude-opus-4-8", "high")
    assert f"export ANTHROPIC_API_KEY={shlex.quote('sk-ant')}" in s
    # The unset-all line clears every other auth var, including the OAuth token,
    # so no stale subscription token can leak into an api_key run.
    unset = _unset_line(s)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in unset
    # ADDED: api_key output must unset ANTHROPIC_AUTH_TOKEN (in the unset line).
    assert "ANTHROPIC_AUTH_TOKEN" in unset
    # No competing OAuth token is exported in api_key mode.
    assert "export CLAUDE_CODE_OAUTH_TOKEN" not in s


def test_injection_safe():
    s = build_claude_auth_exports("api_key", {"api_key":"a'b; rm -rf /"}, "claude-opus-4-8", "high")
    line = [l for l in s.splitlines() if l.startswith("export ANTHROPIC_API_KEY=")][0]
    assert shlex.split(line) == ["export", "ANTHROPIC_API_KEY=a'b; rm -rf /"]


def test_bedrock_unsets_oauth():
    s = build_claude_auth_exports(
        "bedrock",
        {"aws_access_key_id": "AKIA", "aws_secret_access_key": "secret", "aws_region": "us-east-1"},
        "claude-opus-4-8", "high",
    )
    assert "export CLAUDE_CODE_USE_BEDROCK=1" in s
    # ADDED: bedrock output unsets the OAuth token (via the unset-all line).
    assert "CLAUDE_CODE_OAUTH_TOKEN" in _unset_line(s)
    assert "export CLAUDE_CODE_OAUTH_TOKEN" not in s


def test_vertex_sets_use_vertex_and_unsets_bedrock():
    s = build_claude_auth_exports(
        "vertex",
        {"project": "proj", "region": "us-central1", "access_token": "tok"},
        "claude-opus-4-8", "high",
    )
    # ADDED: vertex sets CLAUDE_CODE_USE_VERTEX=1 ...
    assert "export CLAUDE_CODE_USE_VERTEX=1" in s
    # ... and the unset-all line does not leave CLAUDE_CODE_USE_BEDROCK set.
    assert "CLAUDE_CODE_USE_BEDROCK" in _unset_line(s)
    assert "export CLAUDE_CODE_USE_BEDROCK=1" not in s


def test_codex_api_key():
    exports, aj = build_codex_auth("api_key", {"api_key":"sk-oai"})
    assert f"export CODEX_API_KEY={shlex.quote('sk-oai')}" in exports and aj is None
