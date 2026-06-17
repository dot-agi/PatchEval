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
"""Run a single CVE through the Codex agent inside its container."""
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .codex_runner import CodexRunner
from .dataset import CVERecord
from .docker_utils import pull_image_with_retry, run_work_container_no_mount, stop_container
from .patch import get_patch_stats, validate_patch, write_patch_file


def _resolve_codex_credentials(auth_mode: str) -> str:
    """Validate Codex credentials for the resolved auth mode; return a token-ish
    string (only used as a non-empty 'creds present' sentinel — the runner injects
    the real credential into the container)."""
    if auth_mode in ("auto", "api-key"):
        key = os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY")
        if key:
            return key
        if auth_mode == "api-key":
            raise RuntimeError("auth=api-key but no CODEX_API_KEY/OPENAI_API_KEY set")
    # subscription (or auto with no key): need a host auth.json to seed.
    host_auth = os.path.expanduser(os.getenv("HOST_CODEX_AUTH", "~/.codex/auth.json"))
    if os.path.exists(host_auth):
        return f"subscription:{host_auth}"
    raise RuntimeError(
        "No Codex credentials: set CODEX_API_KEY/OPENAI_API_KEY (api-key) or run "
        "`codex login` on the host so ~/.codex/auth.json exists (subscription)"
    )


def run_single_cve(record: CVERecord,
                   outputs_root: Path,
                   semaphore: Optional[threading.Semaphore] = None,
                   timeout_seconds: int = 2700,
                   claude_timeout_seconds: int = 1800,  # generic agent timeout (kept for cross-module compat)
                   strategy: str = "default",
                   api_provider: str = "openai",
                   auth_mode: str = "auto",
                   keep_container: bool = False,
                   tool_limits: Optional[Dict[str, int]] = None,
                   max_total_tool_calls: Optional[int] = None,
                   max_cost_usd: float = 10.0,
                   enable_detailed_logging: bool = True,
                   save_process_logs: bool = False,
                   allow_git_diff_fallback: bool = False,
                   settings_file: Optional[str] = None,
                   port: str = "8082",
                   cfg=None) -> Dict[str, Any]:

    if semaphore is None:
        semaphore = threading.Semaphore(1)

    problem_id = record.problem_id
    start_time = time.time()
    logger = logging.getLogger(__name__)

    result = {
        "problem_id": problem_id,
        "cve_id": record.cve_id,
        "is_success": False,
        "agent_duration": 0.0,
        "total_duration": 0.0,
        "container_id": "",
        "patch_stats": {},
        "error_message": "",
        "stage": "initialization",
        "strategy": strategy,
        "api_provider": api_provider,
        "auth_mode": auth_mode,
    }

    container_id = ""
    agent = None

    try:
        if cfg is not None:
            # Config-driven: credentials (and model/effort) come from cfg via the
            # runner; skip the env/host credential resolution entirely.
            api_key = ""
        else:
            result["stage"] = "api_check"
            api_key = _resolve_codex_credentials(auth_mode)

        result["stage"] = "docker_setup"
        pull_image_with_retry(record.image_name, semaphore)

        modelname = os.getenv("MY_MODEL", "codex")
        result["stage"] = "work_container"
        container_id = run_work_container_no_mount(record.image_name, problem_id, semaphore, modelname)
        result["container_id"] = container_id

        agent = CodexRunner(
            container_id,
            record.work_dir,
            auth_mode=auth_mode,
            tool_limits=tool_limits,
            max_total_tool_calls=max_total_tool_calls,
            max_cost_usd=max_cost_usd,
            enable_detailed_logging=enable_detailed_logging,
            allow_git_diff_fallback=allow_git_diff_fallback,
            settings_file=settings_file,
            cfg=cfg,
        )

        result["stage"] = "environment_setup"
        if not agent.setup_environment(record, strategy, api_key, api_provider, port):
            raise RuntimeError("Codex environment setup failed (see container /tmp/install.log)")

        result["stage"] = "agent_execution"
        agent_start = time.time()
        success, output_log, patch_content = agent.execute_cve_repair(strategy, claude_timeout_seconds)
        result["agent_duration"] = time.time() - agent_start

        if not patch_content:
            patch_content = agent._extract_patch()

        result["stage"] = "patch_processing"
        if (not patch_content or not patch_content.strip()) and allow_git_diff_fallback:
            try:
                import subprocess
                git_diff = subprocess.run(
                    f"docker exec {container_id} bash -c 'cd {record.work_dir} && git diff'",
                    shell=True, capture_output=True, text=True,
                ).stdout
                if git_diff.strip():
                    patch_content = git_diff
                    result["patch_source"] = "git_diff_fallback"
            except Exception:
                pass

        validate_patch(patch_content, relaxed=True)
        patch_stats = get_patch_stats(patch_content)
        result["patch_stats"] = patch_stats
        logger.info(f"patch stats: {patch_stats}")

        result["stage"] = "output_writing"
        outputs_root.mkdir(parents=True, exist_ok=True)
        (outputs_root / "patches").mkdir(exist_ok=True)
        (outputs_root / "agent_logs").mkdir(exist_ok=True)

        write_patch_file(patch_content, outputs_root / "patches" / f"{problem_id}.patch")
        log_file_path = outputs_root / "agent_logs" / f"{problem_id}.log"

        container_logs = agent.get_container_logs()
        agent.set_success_and_finalize_log(True, patch_content, container_logs)

        full_log = {
            "problem_id": problem_id,
            "cve_id": record.cve_id,
            "strategy": strategy,
            "api_provider": api_provider,
            "auth_mode": auth_mode,
            "duration": result["agent_duration"],
            "patch_stats": patch_stats,
            "agent_output": output_log,
            "container_logs": container_logs,
        }
        if enable_detailed_logging:
            full_log["detailed_process"] = agent.get_detailed_process_log()

        import json
        log_file_path.write_text(json.dumps(full_log, indent=2, ensure_ascii=False))

        if save_process_logs:
            process_log_path = outputs_root / "process_logs" / f"{problem_id}_process.json"
            process_log_path.parent.mkdir(exist_ok=True)
            agent.save_process_log(str(process_log_path))

        result["is_success"] = bool(patch_content and patch_content.strip())
        if result.get("patch_source") == "git_diff_fallback":
            result["is_success"] = False
            result["is_partial_success"] = True

        agent.cleanup()
        result["stage"] = "completed"
        result["total_duration"] = time.time() - start_time

    except Exception as e:
        result["error_message"] = str(e)
        result["is_success"] = False
        result["total_duration"] = time.time() - start_time
        logger.error(f"{result['stage']}: {e}")
        try:
            if container_id and agent is not None:
                container_logs = agent.get_container_logs()
                agent.set_success_and_finalize_log(False, "", container_logs)
                outputs_root.mkdir(parents=True, exist_ok=True)
                (outputs_root / "agent_logs").mkdir(exist_ok=True)
                failed_log = {
                    "problem_id": problem_id, "cve_id": record.cve_id, "strategy": strategy,
                    "api_provider": api_provider, "stage": result["stage"], "error": str(e),
                    "container_logs": container_logs,
                }
                import json
                (outputs_root / "agent_logs" / f"{problem_id}_failed.log").write_text(
                    json.dumps(failed_log, indent=2, ensure_ascii=False)
                )
        except Exception as log_e:
            logger.warning(f"failed-log write error: {log_e}")

    finally:
        # Stop by the actual container id returned at creation. (The name is
        # bench.{problem_id}.{modelname}.work, so a fixed bench.{problem_id}.work
        # string would not match and would leak containers.)
        if container_id and not keep_container:
            try:
                force_stop = bool(agent and getattr(agent, "execution_stopped", False))
                stop_container(container_id, force=force_stop)
            except Exception:
                pass

    return result
