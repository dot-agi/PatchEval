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
"""Prompt generation for the Codex agent.

Unlike the Claude Code agent (which writes a ``.claude/commands/<strategy>.md``
slash command + ``settings.json``), Codex consumes a plain prompt that is passed
to ``codex exec``. This generator only fills the repair-prompt template; the
runner writes the result to ``codex_prompt.md`` / ``AGENTS.md`` in the container.
"""
from pathlib import Path

from .dataset import CVERecord


class ScriptGenerator:
    """Render the Codex repair prompt from a template."""

    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir

    def generate_cve_fix_command(self, record: CVERecord, strategy: str = "default") -> str:
        """Fill templates/<strategy>.md with this CVE's details.

        Falls back to a built-in prompt if the template file is missing.
        """
        template_path = self.templates_dir / f"{strategy}.md"
        if not template_path.exists():
            return self._default_prompt(record)

        content = template_path.read_text(encoding="utf-8")
        replacements = {
            "{{CVE_ID}}": record.cve_id,
            "{{WORK_DIR}}": record.work_dir,
            "{{PROBLEM_STATEMENT}}": record.problem_statement,
            "{{REPO_NAME}}": Path(record.work_dir).name,
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        return content

    def _default_prompt(self, record: CVERecord) -> str:
        repo = Path(record.work_dir).name
        return f"""You are a senior security engineer fixing a real-world vulnerability.

- cve_id: {record.cve_id}
- work_dir: {record.work_dir} (your working directory and the repo to fix)
- codebase: {repo}

## Vulnerability
{record.problem_statement}

## Task
Analyze the root cause, implement a minimal fix in the source, verify it, then
emit the final patch with EXACTLY this command (nothing else satisfies the
evaluator):

    cd {record.work_dir} && git add -A && git diff --cached > /workspace/final-cve-fix.patch

Only your source fix should appear in the patch (tooling files are git-ignored).
"""
