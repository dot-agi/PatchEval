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
"""Command-line interface for the Codex CVE benchmark agent."""
import argparse
import logging
import os
import sys
from pathlib import Path

from .batch_runner import run_batch_cves
from .single_runner import run_single_cve
from .docker_utils import cleanup_containers_by_prefix
from .dataset import load_dataset


def get_available_strategies() -> list[str]:
    templates_dir = Path(__file__).parent.parent / "templates"
    if not templates_dir.exists():
        return ["default"]
    strategies = [t.stem for t in templates_dir.glob("*.md")]
    return strategies or ["default"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex agent for PatchEval",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    def add_common(p):
        p.add_argument("--dataset", type=Path, default="./dataset.jsonl")
        p.add_argument("--outputs-root", type=Path, default="./outputs")
        p.add_argument("--timeout", type=str, default="45m")
        p.add_argument("--agent-timeout", type=str, default="30m")
        p.add_argument("--strategy", choices=get_available_strategies(), default="default")
        # Agent backend + auth. Reserved for future backends; codex is the default.
        p.add_argument("--agent", choices=["codex"], default="codex")
        p.add_argument("--auth", choices=["api-key", "subscription", "auto"], default="auto")
        p.add_argument("--api-provider", choices=["openai"], default="openai")
        p.add_argument("--tool-limits", type=str, help="(tool1:limit1,... or total:500)")
        p.add_argument("--max-cost-usd", type=float, default=10.0)
        p.add_argument("--enable-detailed-logging", action="store_true", default=True)
        p.add_argument("--save-process-logs", action="store_true")
        p.add_argument("--allow-git-diff-fallback", action="store_true")
        p.add_argument("--settings", type=str)
        p.add_argument("--port", type=str)

    batch_parser = subparsers.add_parser("batch")
    add_common(batch_parser)
    batch_parser.add_argument("--max-workers", type=int, default=1)
    batch_parser.add_argument("--limit", type=int)
    batch_parser.add_argument("--resume", action="store_true")
    batch_parser.add_argument("--keep-containers", action="store_true")

    single_parser = subparsers.add_parser("single")
    add_common(single_parser)
    single_parser.add_argument("--cve-id", type=str, required=True)
    single_parser.add_argument("--keep-container", action="store_true")

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def parse_tool_limits(tool_limits_str):
    if not tool_limits_str:
        return {}, None
    if tool_limits_str.strip().lower().startswith("total:"):
        return {}, int(tool_limits_str.split(":", 1)[1].strip())
    limits = {}
    for pair in tool_limits_str.split(","):
        tool, limit = pair.strip().split(":")
        limits[tool.strip()] = int(limit.strip())
    return limits, None


def parse_timeout(timeout_str: str) -> int:
    if timeout_str.endswith("s"):
        return int(timeout_str[:-1])
    if timeout_str.endswith("m"):
        return int(timeout_str[:-1]) * 60
    if timeout_str.endswith("h"):
        return int(timeout_str[:-1]) * 3600
    return int(timeout_str)


def setup_logging(level=logging.INFO):
    logging.basicConfig(level=level,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")


def resolve_auth_mode(auth_mode: str) -> str:
    """Validate Codex credentials and resolve 'auto' to a concrete mode."""
    has_key = bool(os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY"))
    host_auth = os.path.exists(os.path.expanduser(os.getenv("HOST_CODEX_AUTH", "~/.codex/auth.json")))
    if auth_mode == "api-key":
        if not has_key:
            raise RuntimeError("auth=api-key but no CODEX_API_KEY/OPENAI_API_KEY set")
        return "api-key"
    if auth_mode == "subscription":
        if not host_auth:
            raise RuntimeError("auth=subscription but ~/.codex/auth.json not found (run `codex login` on host)")
        return "subscription"
    # auto
    if has_key:
        return "api-key"
    if host_auth:
        return "subscription"
    raise RuntimeError(
        "No Codex credentials found. Set CODEX_API_KEY/OPENAI_API_KEY (api-key) "
        "or run `codex login` on the host so ~/.codex/auth.json exists (subscription)."
    )


def handle_batch_command(args) -> int:
    try:
        auth_mode = resolve_auth_mode(args.auth)
    except RuntimeError as e:
        logging.error(str(e))
        return 1
    logging.info(f"Codex backend | auth={auth_mode}")
    try:
        tool_limits_dict, max_total_calls = parse_tool_limits(getattr(args, "tool_limits", None))
        summary = run_batch_cves(
            dataset_path=args.dataset,
            outputs_root=args.outputs_root,
            max_workers=args.max_workers,
            timeout_seconds=parse_timeout(args.timeout),
            claude_timeout_seconds=parse_timeout(args.agent_timeout),
            strategy=args.strategy,
            api_provider=args.api_provider,
            auth_mode=auth_mode,
            resume=args.resume,
            limit=args.limit,
            keep_containers=args.keep_containers,
            tool_limits=tool_limits_dict,
            max_total_tool_calls=max_total_calls,
            max_cost_usd=getattr(args, "max_cost_usd", 10.0),
            enable_detailed_logging=getattr(args, "enable_detailed_logging", True),
            save_process_logs=getattr(args, "save_process_logs", False),
            allow_git_diff_fallback=getattr(args, "allow_git_diff_fallback", False),
            settings_file=getattr(args, "settings", None),
            port=args.port,
        )
        print(f"\nBatch complete: {summary.get('successful', 0)}/{summary.get('total_processed', 0)} "
              f"({summary.get('success_rate', 0):.0%})")
        return 0 if summary.get("successful", 0) > 0 else 1
    except Exception as e:
        logging.error(f"Batch processing failed: {e}")
        return 1


def handle_single_command(args) -> int:
    try:
        auth_mode = resolve_auth_mode(args.auth)
    except RuntimeError as e:
        logging.error(str(e))
        return 1
    records = load_dataset(args.dataset)
    record = next((r for r in records if args.cve_id in (r.cve_id, r.problem_id)), None)
    if not record:
        logging.error(f"CVE not found: {args.cve_id}")
        return 1
    try:
        tool_limits_dict, max_total_calls = parse_tool_limits(getattr(args, "tool_limits", None))
        result = run_single_cve(
            record=record,
            outputs_root=args.outputs_root,
            timeout_seconds=parse_timeout(args.timeout),
            claude_timeout_seconds=parse_timeout(args.agent_timeout),
            strategy=args.strategy,
            api_provider=args.api_provider,
            auth_mode=auth_mode,
            keep_container=args.keep_container,
            tool_limits=tool_limits_dict,
            max_total_tool_calls=max_total_calls,
            max_cost_usd=getattr(args, "max_cost_usd", 10.0),
            enable_detailed_logging=getattr(args, "enable_detailed_logging", True),
            save_process_logs=getattr(args, "save_process_logs", False),
            allow_git_diff_fallback=getattr(args, "allow_git_diff_fallback", False),
            settings_file=getattr(args, "settings", None),
            port=args.port,
        )
        status = "success" if result["is_success"] else f"fail ({result.get('stage','?')})"
        print(f"\nCVE {args.cve_id}: {status}")
        return 0 if result["is_success"] else 1
    except Exception as e:
        logging.error(f"Single CVE processing failed: {e}")
        return 1


def handle_cleanup_command(args) -> int:
    if args.all:
        cleanup_containers_by_prefix("bench.")
        print("All benchmark containers cleaned up")
    else:
        print("Use --all to clean up all benchmark containers")
    return 0


def main() -> int:
    args = parse_args()
    if not args.command:
        print("Error: specify a command (batch | single | cleanup). Use --help.")
        return 1
    setup_logging()
    try:
        if args.command == "batch":
            return handle_batch_command(args)
        if args.command == "single":
            return handle_single_command(args)
        if args.command == "cleanup":
            return handle_cleanup_command(args)
        print(f"Unknown command: {args.command}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
