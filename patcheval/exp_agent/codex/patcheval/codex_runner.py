# Copyright (c) 2025 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Runner that drives the OpenAI Codex CLI inside a CVE container.

This is the Codex analog of claudecode's ``claude_runner_enhanced.py``. It
installs the Codex CLI inside the per-CVE Docker container, authenticates it
(OpenAI API key OR a ChatGPT-plan subscription), runs ``codex exec`` to repair
the vulnerability, and captures ``/workspace/final-cve-fix.patch`` via
``git diff`` -- the exact same output contract used by every PatchEval agent.

It deliberately mirrors the public surface of ``ClaudeRunnerEnhanced`` so the
shared ``single_runner``/``batch_runner`` can drive it with only an import
swap: ``setup_environment(...)``, ``execute_cve_repair(...)``, ``_extract_patch``,
``get_container_logs``, ``set_success_and_finalize_log``, ``cleanup``,
``get_detailed_process_log``, ``save_process_log`` and the ``execution_stopped``
attribute.
"""
import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .dataset import CVERecord
from .script_generator import ScriptGenerator


class CostController:
    """Lightweight token/cost bookkeeping (best-effort, from Codex JSONL usage)."""

    def __init__(self, max_cost_usd: float = 10.0):
        self.max_cost = max_cost_usd
        self.input_tokens = 0
        self.output_tokens = 0

    def add_usage(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)

    def get_cost_summary(self) -> Dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


class CodexRunner:
    """Install + drive the Codex CLI in a container to repair one CVE."""

    def __init__(self, container_id: str, work_dir: str,
                 auth_mode: str = "auto",
                 tool_limits: Optional[Dict[str, int]] = None,
                 max_total_tool_calls: Optional[int] = None,
                 max_cost_usd: float = 10.0,
                 enable_detailed_logging: bool = True,
                 allow_git_diff_fallback: bool = False,
                 settings_file: Optional[str] = None,
                 cfg=None):
        self.container_id = container_id
        self.work_dir = work_dir
        self.auth_mode = auth_mode
        self.tool_limits = tool_limits or {}
        self.max_total_tool_calls = max_total_tool_calls
        self.allow_git_diff_fallback = allow_git_diff_fallback
        self.settings_file = settings_file
        self.enable_detailed_logging = enable_detailed_logging
        self.cost_controller = CostController(max_cost_usd)
        self.logger = logging.getLogger(__name__)

        # Config-driven run: model / reasoning effort / auth mode come from the
        # AgentRunConfig (patcheval.config). When no cfg is supplied we keep the
        # legacy env-based behavior (CODEX_MODEL / CODEX_REASONING_EFFORT set by
        # run_infer.sh), mirroring how the Claude runner reads ANTHROPIC_MODEL /
        # CLAUDE_CODE_EFFORT_LEVEL. Unset => let Codex use its configured default.
        self.cfg = cfg
        if cfg is not None:
            self.model = cfg.model
            self.effort = cfg.reasoning
            self.auth_mode = {"api_key": "api-key", "subscription": "subscription"}[cfg.auth.method]
        else:
            self.model = os.getenv("CODEX_MODEL") or None
            self.effort = os.getenv("CODEX_REASONING_EFFORT") or None
        # CODEX_HOME inside the container (for the non-root agent user).
        self.codex_home = os.getenv("CONTAINER_CODEX_HOME", "/home/claude_user/.codex")

        self.process_log = []
        self.start_time = None
        self.end_time = None
        self.execution_stopped = False
        self.stop_reason = ""
        self.cve_id = None
        self.temp_log_file = None
        self._last_output = ""

    # ------------------------------------------------------------------ setup
    def setup_environment(self, record: CVERecord, strategy: str,
                          api_key: str, api_provider: str, port: str) -> bool:
        """Install Codex in the container, inject auth, write the prompt, and
        baseline-commit so harness/tooling files stay out of the final patch."""
        try:
            self.cve_id = record.cve_id

            install_script = self._render_install_script()
            self._write_file_to_container("/tmp/install_codex.sh", install_script)
            self._exec_in_container("chmod", "+x /tmp/install_codex.sh")
            self._log_process_step("codex_install", "install Codex CLI")
            try:
                status = self._exec_in_container_with_output(
                    "bash",
                    "-c 'bash /tmp/install_codex.sh >/tmp/install.log 2>&1 "
                    "&& echo INSTALL_SUCCESS || echo INSTALL_FAILED'",
                ).strip()
            except Exception as e:
                self.logger.error(f"Codex install error: {e}")
                self._log_process_step("codex_install_error", str(e)[:200])
                return False
            # Fail fast: the install script hard-gates on `command -v codex`, so a
            # missing INSTALL_SUCCESS means Codex is not runnable -> abort setup.
            if "INSTALL_SUCCESS" not in status:
                log = self._safe_cat("/tmp/install.log")
                self.logger.error(f"Codex CLI install failed; install.log tail:\n{log[-800:]}")
                self._log_process_step("codex_install_failed", log[-500:])
                return False
            self.logger.info("Codex CLI install success")
            self._log_process_step("codex_install_success", "Codex CLI installed")

            # Subscription auth: seed the host's ~/.codex/auth.json into the
            # container's CODEX_HOME (Codex refreshes it in place at runtime).
            if self._resolved_auth_mode() == "subscription":
                self._seed_subscription_auth()

            # Install the Codex Security plugin ($codex-security:* skills) by seeding
            # the host-installed plugin into the container's CODEX_HOME.
            self._install_security_plugin()

            # Keep harness / tooling artifacts out of the captured patch.
            self._append_gitignore()
            self._git_baseline_commit()

            # Write the repair prompt (consumed by `codex exec`) and AGENTS.md.
            script_gen = ScriptGenerator(Path("templates"))
            prompt = script_gen.generate_cve_fix_command(record, strategy)
            self._write_file_to_container(f"{self.work_dir}/AGENTS.md", prompt)
            self._write_file_to_container("/workspace/codex_prompt.md", prompt)
            self._log_process_step("prompt_generation", "wrote codex_prompt.md + AGENTS.md")

            # Install the defending-code harness so Codex can follow the same
            # vuln-scan -> triage -> patch process as the Claude Code agent. The
            # config-driven run can opt out via run.use_harness_skills=False.
            if self.cfg is None or self.cfg.run.use_harness_skills:
                self._install_harness()

            # Commit the tooling (AGENTS.md, .claude/, harness) so the agent's
            # final `git diff` contains only the real source fix.
            self._git_commit_tooling()

            self._exec_in_container("chown", f"-R claude_user:claude_user {self.work_dir}")
            return True
        except Exception as e:
            self.logger.error(f"setup_environment failed: {e}")
            return False

    def _render_install_script(self) -> str:
        """Read templates/codex-install.sh and fill the auth/model placeholders."""
        with open("templates/codex-install.sh", "r", encoding="utf-8") as f:
            script = f.read()
        auth_exports = self._build_auth_exports()
        script = script.replace("{{AUTH_EXPORTS}}", auth_exports)
        script = script.replace("{{CODEX_HOME}}", self.codex_home)
        return script

    def _build_auth_exports(self) -> str:
        """Bashrc export block for the resolved auth mode.

        api-key:       export CODEX_API_KEY (consumed by `codex exec`).
        subscription:  rely on the seeded $CODEX_HOME/auth.json (no token env).
        """
        # Config-driven: derive the export block from the AgentRunConfig. This does
        # NOT mutate os.environ -- the block is written into the container .bashrc.
        if getattr(self, "cfg", None) is not None:
            from .config import build_codex_auth
            exports, _ = build_codex_auth(self.cfg.auth.method, self.cfg.auth.credentials)
            return exports
        mode = self._resolved_auth_mode()
        lines = [f"export CODEX_HOME='{self.codex_home}'"]
        if mode == "api-key":
            key = os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
            if not key:
                self.logger.warning(
                    "auth=api-key but no CODEX_API_KEY/OPENAI_API_KEY in env"
                )
            # CODEX_API_KEY is honored by `codex exec`; unset OPENAI_API_KEY so it
            # cannot silently shadow the intended credential.
            lines.append(f"export CODEX_API_KEY='{key}'")
            lines.append("unset OPENAI_API_KEY")
        else:  # subscription
            # Tokens live in $CODEX_HOME/auth.json (seeded separately). Make sure a
            # stray API key doesn't override the subscription session.
            lines.append("unset CODEX_API_KEY OPENAI_API_KEY")
        return "\n".join(lines)

    def _resolved_auth_mode(self) -> str:
        """Resolve 'auto' -> 'api-key' if a key is present, else 'subscription'."""
        if self.auth_mode in ("api-key", "subscription"):
            return self.auth_mode
        if os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY"):
            return "api-key"
        return "subscription"

    def _seed_subscription_auth(self) -> None:
        """Seed the container's CODEX_HOME/auth.json with the subscription tokens.

        Config-driven: write the auth_json blob from the AgentRunConfig straight
        into the container. Otherwise copy the host's ~/.codex/auth.json.
        """
        if getattr(self, "cfg", None) is not None and self.cfg.auth.credentials.get("auth_json"):
            import json
            self._write_file_to_container(
                f"{self.codex_home}/auth.json",
                json.dumps(self.cfg.auth.credentials["auth_json"], indent=2),
            )
            self._exec_in_container("chown", f"-R claude_user:claude_user {self.codex_home}")
            self._log_process_step("codex_auth_seeded_cfg", f"{self.codex_home}/auth.json")
            return
        cfg_path = (self.cfg.auth.credentials.get("auth_json_path")
                    if getattr(self, "cfg", None) is not None else None)
        host_auth = os.path.expanduser(
            cfg_path or os.getenv("HOST_CODEX_AUTH", "~/.codex/auth.json")
        )
        if not os.path.exists(host_auth):
            self.logger.warning(
                f"subscription auth requested but {host_auth} not found; run "
                "`codex login` on the host or set HOST_CODEX_AUTH"
            )
            self._log_process_step("codex_auth_missing", host_auth)
            return
        try:
            self._exec_in_container("mkdir", f"-p {self.codex_home}")
            cp = subprocess.run(
                f"docker cp {host_auth} {self.container_id}:{self.codex_home}/auth.json",
                shell=True, capture_output=True, text=True,
            )
            if cp.returncode != 0:
                self.logger.warning(f"failed to seed auth.json: {cp.stderr.strip()}")
                self._log_process_step("codex_auth_error", cp.stderr.strip()[:200])
                return
            self._exec_in_container("chown", f"-R claude_user:claude_user {self.codex_home}")
            self._log_process_step("codex_auth_seeded", f"{self.codex_home}/auth.json")
        except Exception as e:
            self.logger.warning(f"seed auth error: {e}")

    def _install_harness(self) -> None:
        """Copy the defending-code-reference-harness skills into the workspace so
        Codex can follow the same process as the Claude Code agent.

        NOTE: Codex is not a Claude skill host; the repair prompt drives the
        vuln-scan -> triage -> patch workflow as prose and references these files.
        Whether Codex auto-loads them as native skills is a runtime detail to
        verify when the Codex backend is first run live.
        """
        skills_dir = os.getenv(
            "DEFENDING_CODE_SKILLS_DIR",
            "third_party/defending-code-reference-harness/.claude/skills",
        )
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            self.logger.info(
                f"defending-code skills not found at {skills_path}; Codex will do a "
                "direct repair from the prompt."
            )
            self._log_process_step("harness_skills_missing", str(skills_path))
            return
        try:
            dest = f"{self.work_dir}/.claude/skills"
            self._exec_in_container("mkdir", f"-p {dest}")
            cp = subprocess.run(
                f"docker cp {skills_path}/. {self.container_id}:{dest}/",
                shell=True, capture_output=True, text=True,
            )
            if cp.returncode != 0:
                self.logger.warning(f"failed to copy harness skills: {cp.stderr.strip()}")
                return
            self._log_process_step("harness_skills_installed", dest)
        except Exception as e:
            self.logger.warning(f"harness install error: {e}")

    def _install_security_plugin(self) -> None:
        """Seed the host-installed Codex Security plugin into the container's CODEX_HOME.

        Codex's `openai-curated` marketplace is *reserved* (it cannot be re-added
        from a local path, so `codex plugin add` does not work in a fresh
        container). Instead we copy the already-installed plugin cache + the
        reserved-marketplace snapshot (`$CODEX_HOME/.tmp/plugins`) + a minimal
        config entry from the host; Codex then lists the plugin installed+enabled
        and loads its `$codex-security:*` skills. The host must have it installed
        once: `codex plugin add codex-security@openai-curated`.
        Controlled by USE_CODEX_SECURITY_PLUGIN (default on).
        """
        if os.getenv("USE_CODEX_SECURITY_PLUGIN", "1").lower() not in ("1", "true", "yes"):
            return
        host_codex = os.path.expanduser(os.getenv("HOST_CODEX_DIR", "~/.codex"))
        cache = os.path.join(host_codex, "plugins/cache/openai-curated/codex-security")
        mkt = os.path.join(host_codex, ".tmp/plugins")
        if not os.path.isdir(cache) or not os.path.isdir(mkt):
            self.logger.warning(
                "Codex Security plugin not found on host; install it once with "
                "`codex plugin add codex-security@openai-curated`. Codex will do a "
                "direct repair without the plugin for now."
            )
            self._log_process_step("codex_plugin_missing", cache)
            return
        try:
            import tempfile
            # 1) plugin cache
            self._exec_in_container("mkdir", f"-p {self.codex_home}/plugins/cache/openai-curated")
            subprocess.run(
                f"docker cp {shlex.quote(cache)} "
                f"{self.container_id}:{self.codex_home}/plugins/cache/openai-curated/codex-security",
                shell=True, capture_output=True, text=True,
            )
            # 2) reserved-marketplace snapshot, trimmed of .git via a staging dir
            stage = tempfile.mkdtemp(prefix="codex-mkt-")
            subprocess.run(
                f"rsync -a --exclude .git {shlex.quote(mkt)}/ {shlex.quote(stage)}/",
                shell=True, capture_output=True, text=True,
            )
            self._exec_in_container("mkdir", f"-p {self.codex_home}/.tmp")
            subprocess.run(
                f"docker cp {shlex.quote(stage)} {self.container_id}:{self.codex_home}/.tmp/plugins",
                shell=True, capture_output=True, text=True,
            )
            subprocess.run(f"rm -rf {shlex.quote(stage)}", shell=True)
            # 3) enable the plugin in the container config
            self._write_file_to_container(
                f"{self.codex_home}/config.toml",
                '[plugins."codex-security@openai-curated"]\nenabled = true\n',
            )
            # 4) ownership
            self._exec_in_container("chown", f"-R claude_user:claude_user {self.codex_home}")
            self._log_process_step("codex_plugin_seeded", "codex-security@openai-curated")
            self.logger.info("Seeded Codex Security plugin into container")
        except Exception as e:
            self.logger.warning(f"plugin seed error: {e}")

    def _safe_cat(self, path: str) -> str:
        try:
            return self._exec_in_container("cat", path)
        except Exception:
            return ""

    # -------------------------------------------------------------- execution
    def execute_cve_repair(self, strategy: str = "default",
                           timeout: int = 1800) -> Tuple[bool, str, str]:
        """Run `codex exec` to completion and capture the patch."""
        self.start_time = time.time()
        cmd = self._build_codex_command()
        self._log_process_step("command_build", cmd)

        env_prefix = ""
        if self.enable_detailed_logging:
            env_prefix = "RUST_LOG=info "
        switch_user_cmd = (
            f"su - claude_user -c 'cd {self.work_dir} && . ~/.bashrc 2>/dev/null; "
            f"{env_prefix}{cmd}'"
        )
        try:
            result = self._exec_with_timeout(switch_user_cmd, timeout)
            self._last_output = result
            success = True
        except subprocess.TimeoutExpired:
            self._last_output = f"Codex execution timeout after {timeout}s"
            success = False
        except Exception as e:
            self._last_output = str(e)
            self._log_process_step("command_error", str(e)[:200])
            success = False

        self.end_time = time.time()
        self._parse_codex_usage(self._last_output)

        # Deterministically capture Codex's working-tree edits as the final patch,
        # so capture does not depend on the model running the git command itself.
        self._capture_patch_via_git()

        if success:
            success = self._check_repair_success(self._last_output)
        patch_content = self._extract_patch()
        self._log_process_step(
            "repair_result", f"fix {'success' if success and patch_content else 'fail'}"
        )
        return bool(patch_content), self._last_output, patch_content

    def _build_codex_command(self) -> str:
        """Build the `codex exec` invocation.

        The container is already the isolation boundary, so we bypass Codex's
        own sandbox/approvals (mirrors the Claude runner's
        --dangerously-skip-permissions / bypassPermissions).
        """
        parts = [
            "codex exec", "-",  # prompt read from stdin (see redirect below)
            f"-C {shlex.quote(self.work_dir)}",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]
        if self.model:
            parts += ["-m", shlex.quote(self.model)]
        if self.effort:
            parts += ["-c", f"model_reasoning_effort={shlex.quote(self.effort)}"]
        if self.enable_detailed_logging:
            parts.append("--json")
        parts += ["-o", "/workspace/codex-last-message.txt"]
        # Feed the prompt via stdin to avoid argv-length limits / nested-quoting.
        parts.append("< /workspace/codex_prompt.md")
        return " ".join(parts)

    def _parse_codex_usage(self, output: str) -> None:
        """Best-effort token accounting from Codex `--json` turn.completed events."""
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "turn.completed":
                usage = evt.get("usage", {}) or {}
                self.cost_controller.add_usage(
                    usage.get("input_tokens", 0), usage.get("output_tokens", 0)
                )

    def _check_repair_success(self, output: str) -> bool:
        indicators = [
            "final-cve-fix.patch", "Task completed", "CVE repair", "patch generated",
        ]
        if any(i in output for i in indicators):
            return True
        return self._check_patch_file_exists()

    # ----------------------------------------------------------- patch output
    def _capture_patch_via_git(self) -> None:
        """Generate /workspace/final-cve-fix.patch from Codex's working-tree edits.

        Runs `git add -A && git diff --cached` in the repo so patch capture is
        deterministic and does not depend on the model running the command. The
        baseline + tooling commits + .gitignore ensure only the source fix appears.
        """
        try:
            self._exec_in_container(
                "bash",
                f"-c 'cd {shlex.quote(self.work_dir)} && git add -A && "
                "git diff --cached > /workspace/final-cve-fix.patch 2>/dev/null'",
            )
            self._log_process_step("patch_capture", "git diff --cached -> final-cve-fix.patch")
        except Exception as e:
            self.logger.warning(f"git patch capture failed: {e}")

    def _check_patch_file_exists(self) -> bool:
        for loc in ["/workspace/final-cve-fix.patch", f"{self.work_dir}/final-cve-fix.patch"]:
            try:
                self._exec_in_container("test", f"-f {loc}")
                return True
            except Exception:
                continue
        return False

    def _extract_patch(self) -> str:
        for loc in ["/workspace/final-cve-fix.patch", f"{self.work_dir}/final-cve-fix.patch"]:
            try:
                content = self._exec_in_container("cat", loc)
                if content.strip():
                    self.logger.info(f"found patch: {loc}")
                    return content
            except Exception:
                continue
        if self.allow_git_diff_fallback:
            try:
                git_patch = self._exec_in_container(
                    "bash", f"-c 'cd {self.work_dir} && git diff HEAD'"
                )
                if git_patch.strip():
                    return git_patch
            except Exception as e:
                self.logger.warning(f"git diff fallback failed: {e}")
        return ""

    # ----------------------------------------------------------- git plumbing
    def _append_gitignore(self) -> None:
        patterns = (
            ".claude/\\nAGENTS.md\\nPATCHES/\\n.patch-state/\\n.triage-state/\\n"
            "VULN-FINDINGS.json\\nTRIAGE.json\\nPATCHES.json\\n*.md\\n"
            "node_modules/\\n__pycache__/\\n"
        )
        cmd = f'echo -e "\\n\\n{patterns}\\n" >> {self.work_dir}/.gitignore'
        try:
            self._exec_in_container("bash", f"-c '{cmd}'")
        except Exception as e:
            self.logger.warning(f"gitignore append failed: {e}")

    def _git_baseline_commit(self) -> None:
        try:
            self._exec_in_container("git", f"config --global --add safe.directory {self.work_dir}")
            self._exec_in_container(
                "bash",
                f"-c 'cd {self.work_dir} && git config user.email cve@example.com "
                "&& git config user.name \"CVE Repair\" "
                "&& git add -A && git commit --no-verify -q -m baseline || true'",
            )
        except Exception as e:
            self.logger.info(f"baseline commit note: {e}")

    def _git_commit_tooling(self) -> None:
        try:
            self._exec_in_container(
                "bash",
                f"-c 'cd {self.work_dir} && git add -f .gitignore .claude AGENTS.md 2>/dev/null; "
                "git commit --no-verify -q -m \"codex tooling (excluded from patch)\" || true'",
            )
        except Exception as e:
            self.logger.info(f"tooling commit note: {e}")

    # ------------------------------------------------------- docker exec utils
    def _exec_in_container(self, command: str, args: str = "") -> str:
        full = f"docker exec {self.container_id} {command} {args}"
        r = subprocess.run(full, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"command failed: {r.stderr}")
        return r.stdout

    def _exec_in_container_with_output(self, command: str, args: str) -> str:
        full = f"docker exec -i {self.container_id} {command} {args}"
        r = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=3600)
        return r.stdout + "\n--- STDERR ---\n" + r.stderr

    def _exec_with_timeout(self, command: str, timeout_seconds: int) -> str:
        full = f"docker exec {self.container_id} {command}"
        r = subprocess.run(full, shell=True, capture_output=True, text=True,
                           timeout=timeout_seconds)
        return (r.stdout or "") + (("\n--- STDERR ---\n" + r.stderr) if r.stderr else "")

    def _write_file_to_container(self, file_path: str, content: str) -> None:
        self._exec_in_container("mkdir", f"-p {str(Path(file_path).parent)}")
        cmd = f"docker exec -i {self.container_id} tee {file_path}"
        subprocess.run(cmd, shell=True, input=content, text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------- logging API
    def _log_process_step(self, step_type: str, message: Any) -> None:
        ts = time.time()
        self.process_log.append({
            "timestamp": ts,
            "step_type": step_type,
            "message": message,
            "elapsed": ts - (self.start_time or ts),
        })
        if self.enable_detailed_logging:
            self.logger.info(f"[{step_type.upper()}] {message}")

    def get_container_logs(self) -> str:
        try:
            r = subprocess.run(f"docker logs {self.container_id}", shell=True,
                               capture_output=True, text=True)
            return r.stdout + r.stderr
        except Exception:
            return ""

    def get_detailed_process_log(self) -> Dict[str, Any]:
        return {
            "process_steps": self.process_log,
            "total_duration": (self.end_time - self.start_time)
            if (self.start_time and self.end_time) else 0,
            "auth_mode": self._resolved_auth_mode(),
            "model": self.model,
            "effort": self.effort,
            "cost_summary": self.cost_controller.get_cost_summary(),
        }

    def save_process_log(self, output_file: str) -> None:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(self.get_detailed_process_log(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"save_process_log failed: {e}")

    def set_success_and_finalize_log(self, success: bool, patch_content: str = "",
                                     container_logs: str = "") -> None:
        # The shared single_runner writes the canonical per-CVE log; nothing extra
        # to finalize here beyond recording the outcome.
        self._log_process_step("finalize", "success" if success else "fail")

    def cleanup(self) -> None:
        try:
            self._exec_in_container("rm", "-f /tmp/install_codex.sh")
        except Exception:
            pass
