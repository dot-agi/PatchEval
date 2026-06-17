#!/bin/bash
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
#
# Install the OpenAI Codex CLI inside a CVE container and configure auth.
# The auth-exports and codex-home placeholders below are filled by codex_runner.py
# (do not reference the literal placeholder tokens in comments -- they get replaced).
set -e

echo "=== Codex Environment Setup ==="

if id "claude_user" &>/dev/null; then
    echo "Removing existing claude_user..."
    userdel -r claude_user 2>/dev/null || true
fi
echo "Creating agent user (claude_user)..."
adduser --disabled-password --gecos '' claude_user >/dev/null

# Node.js (the Codex CLI ships as an npm package wrapping a standalone binary).
if ! command -v node &> /dev/null; then
    echo "Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
    apt-get install -y nodejs >/dev/null 2>&1
    echo "Node.js $(node --version) installed"
else
    echo "Node.js $(node --version) already present"
fi

# git + python3 are needed for the repair workflow (git diff) and the harness.
if ! command -v python3 &> /dev/null || ! command -v git &> /dev/null; then
    echo "Ensuring python3 + git..."
    apt-get install -y python3 git >/dev/null 2>&1 \
        || { apt-get update >/dev/null 2>&1 && apt-get install -y python3 git >/dev/null 2>&1; } \
        || true
fi

chown -R claude_user:claude_user /workspace 2>/dev/null || true

echo "Installing Codex CLI as claude_user..."
su - claude_user << 'USEREOF'
set -e
export CODEX_NON_INTERACTIVE=1          # skip installer prompts

npm config set prefix ~/.npm-global >/dev/null 2>&1
mkdir -p ~/.npm-global/bin ~/.local/bin
npm install -g @openai/codex >/dev/null 2>&1 || echo "WARN: npm install @openai/codex failed"

# CODEX_HOME must already exist if set (per Codex docs).
mkdir -p "{{CODEX_HOME}}"

cat > ~/.bashrc << 'BASHEOF'
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
export PATH=$PATH:/usr/local/go/bin
export CODEX_NON_INTERACTIVE=1

# --- Codex auth / config (filled by codex_runner) ---
{{AUTH_EXPORTS}}

alias ll='ls -la'
BASHEOF

source ~/.bashrc

if command -v codex &> /dev/null; then
    echo "Codex ready: $(codex --version 2>/dev/null || echo 'version check failed')"
else
    echo "WARN: codex not on PATH; try ~/.npm-global/bin/codex or ~/.local/bin/codex"
fi
USEREOF

echo "Final verification..."
su - claude_user << 'VERIFYEOF'
source ~/.bashrc
command -v codex &> /dev/null && echo "codex: $(codex --version 2>/dev/null)" || echo "codex NOT found"
if [ -n "$CODEX_API_KEY" ]; then
    echo "Auth: OpenAI API key (CODEX_API_KEY set)"
elif [ -f "$CODEX_HOME/auth.json" ]; then
    echo "Auth: ChatGPT subscription (seeded auth.json present)"
else
    echo "WARN: no Codex credential detected (CODEX_API_KEY unset and no auth.json)"
fi
echo "Env: Node $(node --version), CODEX_HOME=$CODEX_HOME"
VERIFYEOF

# Hard gate: fail the install (non-zero exit) if Codex is not actually runnable,
# so the runner does not proceed to `codex exec` with a missing binary.
su - claude_user -c 'source ~/.bashrc 2>/dev/null; command -v codex >/dev/null 2>&1' \
    || { echo "FATAL: Codex CLI not installed / not on PATH"; exit 1; }

echo "Codex setup complete."
