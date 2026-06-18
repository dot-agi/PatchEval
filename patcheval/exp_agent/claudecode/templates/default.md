---
allowed-tools: Bash, Read, Glob, Grep, Edit, MultiEdit, Write, Task, Skill, LS, TodoWrite
description: Fix a real-world CVE using the defending-code reference harness skills
---

You are a senior security engineer. Fix the vulnerability in the current
repository and emit a single patch file.

Task information:
- cve_id: {{CVE_ID}}
- work_dir: {{WORK_DIR}}   (your current working directory and the repo to fix)
- codebase: {{REPO_NAME}}
- problem_statement:
<begin_of_problem_statement>
{{PROBLEM_STATEMENT}}
<end_of_problem_statement>

## Step 1 — Use the defending-code reference harness skills

This workspace has the Anthropic **defending-code-reference-harness** skills
installed under `.claude/skills/`. USE them rather than free-handing the whole
task. Drive this static, read/write-only workflow (safe to run here):

1. `/vuln-scan .` — static security scan of this repo. Scope it to the
   vulnerable area named in the problem statement when possible (e.g.
   `--focus <area>`) so unrelated findings don't dominate. Writes
   `VULN-FINDINGS.json`.
2. `/triage VULN-FINDINGS.json --repo . --auto` — verify, dedupe, and rank.
   Writes `TRIAGE.json`. (Preferred; if it stalls, skip to step 3 using
   `VULN-FINDINGS.json`.)
3. `/patch TRIAGE.json --repo . --top 1` (or `/patch VULN-FINDINGS.json
   --repo .`) — generate a candidate fix for the CVE finding. This writes an
   **inert** diff to `PATCHES/bug_00/patch.diff`; by design it does NOT modify
   the repo.

Do NOT use the autonomous `vuln-pipeline` / `bin/vp-sandboxed` (C/C++ + ASAN +
gVisor) — it does not apply to this task.

## Step 2 — Apply the fix and emit the final patch (REQUIRED)

The `/patch` skill leaves the repo unmodified, so you must now make the fix land:

1. Read the generated `PATCHES/bug_*/patch.diff` and apply it to the working
   tree: `git apply --3way PATCHES/bug_*/patch.diff` (try `-p1`/`-p0` if the
   path prefix differs).
2. If it does not apply cleanly (context drift), implement the SAME fix directly
   with Edit/Write on the repo source, guided by `patch.diff` and the finding's
   rationale. Make the minimal change that fixes the root cause — no refactoring.
3. Verify: re-read the changed code; if the repo ships a PoC or tests for this
   CVE, run them to confirm the vulnerability is fixed and nothing else breaks.
4. Emit the final patch with EXACTLY this contract (nothing else satisfies the
   evaluator):

```bash
cd {{WORK_DIR}} && git add -A && git diff --cached > /workspace/final-cve-fix.patch
```

The harness artifacts (`.claude/`, `PATCHES/`, `VULN-FINDINGS.json`,
`TRIAGE.json`) are git-ignored, so they will NOT appear in the patch — only your
source fix will.

## Fallback

If any harness skill is missing or errors out, fall back to a direct repair:
analyze the CVE from the problem statement, fix the root cause in the source with
Edit/Write, verify, and still emit `/workspace/final-cve-fix.patch` via the
command above. **Producing a correct, minimal patch is the priority.**

Your final reply must confirm that `/workspace/final-cve-fix.patch` exists and
contains the fix.
