# Running Codex on PatchEval

This is the **OpenAI Codex** agent for PatchEval — the Codex analog of the
[`claudecode`](../claudecode/) experiment. For each CVE it boots the Dockerized
vulnerable repo, installs the Codex CLI **inside** the container, runs
`codex exec` to repair the vulnerability, and captures
`/workspace/final-cve-fix.patch` via `git diff`. Evaluation is shared and
agent-agnostic (`../../evaluation/run_evaluation.py`).

It supports **both** auth methods, selectable at run time:

| `--auth` | Credential | How it reaches the container |
|---|---|---|
| `api-key` | `CODEX_API_KEY` / `OPENAI_API_KEY` | exported into the agent user's `~/.bashrc` (consumed by `codex exec`) |
| `subscription` | ChatGPT-plan login (`~/.codex/auth.json`) | the host's `auth.json` is `docker cp`-seeded into the container's `$CODEX_HOME` (Codex refreshes it in place) |
| `auto` (default) | whichever is present | API key if set, else the host subscription |

> The `--agent` flag exists for parity with a future multi-backend setup; `codex`
> is the only backend in this folder.

## Architecture (mirrors ClaudeCode)

```
codex exec  (in container, model + reasoning effort)  --auth-->  OpenAI / ChatGPT
   |
   +-- repair prompt: templates/default.md  (vuln-scan -> triage -> patch -> emit patch)
```

`patcheval/codex_runner.py` is the agent file (analog of ClaudeCode's
`claude_runner_enhanced.py`); the rest of `patcheval/` is the shared
agent-agnostic orchestration (dataset, docker, patch, batch/single runners).

## Setup

```bash
cd patcheval/exp_agent/codex

# 1. python env with the docker SDK + tqdm (uv)
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

# 2. credentials — pick ONE:
#    (a) API key:
echo 'CODEX_API_KEY=sk-...' > .env.local        # git-ignored
#    (b) subscription: log in once on the host; the runner seeds ~/.codex/auth.json
codex login

# 3. install the Codex Security plugin ON THE HOST (once). The runner seeds the
#    installed plugin (cache + reserved-marketplace snapshot + config) into each
#    container's CODEX_HOME, since `openai-curated` is a reserved marketplace that
#    can't be `plugin add`-ed inside a fresh container.
codex plugin add codex-security@openai-curated
```

The repair prompt (`templates/default.md`) then drives the plugin skills
`$codex-security:security-scan` → `$codex-security:fix-finding`, falling back to a
direct repair if the plugin isn't available. Disable seeding with
`USE_CODEX_SECURITY_PLUGIN=0`.

## Run

```bash
# pull a subset image (Apple Silicon: amd64 under emulation)
DOCKER_DEFAULT_PLATFORM=linux/amd64 .venv/bin/python ../../../scripts/download_images.py \
  --images-file ../../../scripts/images_subset.txt --limit 1

# generate patches (Codex). AUTH_MODE=auto picks api-key or subscription.
DATASET=dataset_subset.jsonl OUTDIR=./outputs/codex_smoke MAX_WORKERS=1 \
  CODEX_MODEL=gpt-5.5 CODEX_REASONING_EFFORT=xhigh \
  bash shells/run_infer.sh

# evaluate (apply patch + PoC/unit tests). DATASET must match generation.
DATASET=dataset_subset.jsonl bash shells/run_eval.sh codex_smoke
```

Generation output: `outputs/<prefix>/{summary.json,patches/,agent_logs/}`.
Evaluation output: `evaluation_output/<prefix>/`.

## Notes / status

- **Not yet run live.** This scaffold is functionally complete and statically
  validated (compile, install-script rendering, command construction); the first
  live Codex run will confirm model id, auth, and the skill-loading detail below.
- **Model / effort:** set via `CODEX_MODEL` + `CODEX_REASONING_EFFORT` (passed as
  `-m` / `-c model_reasoning_effort=`). Defaults assume your host's Codex access.
- **Security plugin:** the repair uses the Codex Security plugin's
  `$codex-security:security-scan` / `fix-finding` skills (seeded from the host —
  see Setup step 3). Verify the plugin actually loads on the first live run
  (`codex plugin list` inside the container should show it `installed, enabled`).
- **Unenforced flags:** `--tool-limits` / `--max-cost-usd` are accepted for
  interface parity with ClaudeCode but are **not enforced** for Codex (Codex has
  no equivalent tool-call cap; it manages its own budget). Per-CVE work is bounded
  by `--agent-timeout` (the subprocess wall-clock timeout).
- Apple-Silicon runs the amd64 CVE images under emulation (slower, correct).
