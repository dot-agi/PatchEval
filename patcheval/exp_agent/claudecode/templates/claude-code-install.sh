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

#!/bin/bash

set -e  

echo "=== Claude Code Environment Setup Script ==="

if id "claude_user" &>/dev/null; then
    echo "🗑️  Removing existing claude_user..."
    userdel -r claude_user 2>/dev/null || true
fi


echo "👤 Creating claude_user..."
adduser --disabled-password --gecos '' claude_user >/dev/null


if ! command -v node &> /dev/null; then
    echo "📦 Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
    apt-get install -y nodejs >/dev/null 2>&1
    echo "✅ Node.js $(node --version) installed"
else
    echo "✅ Node.js $(node --version) already installed"
fi


# The defending-code harness skills shell out to `python3 .claude/skills/_lib/checkpoint.py`
# and use git; make sure both exist (best-effort, never abort the install on failure).
if ! command -v python3 &> /dev/null || ! command -v git &> /dev/null; then
    echo "📦 Ensuring python3 + git are installed (needed by the harness skills)..."
    apt-get install -y python3 git >/dev/null 2>&1 \
        || { apt-get update >/dev/null 2>&1 && apt-get install -y python3 git >/dev/null 2>&1; } \
        || true
fi
command -v python3 &> /dev/null && echo "✅ python3 $(python3 --version 2>&1 | awk '{print $2}')" || echo "⚠️  python3 not available"
command -v git &> /dev/null && echo "✅ git $(git --version 2>&1 | awk '{print $3}')" || echo "⚠️  git not available"


echo "🔧 Setting workspace permissions..."
chown -R claude_user:claude_user /workspace 2>/dev/null || true


echo "⚙️  Installing Claude Code..."
su - claude_user << 'USEREOF'

npm config set prefix ~/.npm-global >/dev/null 2>&1


mkdir -p ~/.npm-global/bin

npm install -g @anthropic-ai/claude-code >/dev/null 2>&1


NPM_PREFIX=$(npm config get prefix)


cat > ~/.bashrc << 'BASHEOF'

export PATH="$HOME/.npm-global/bin:$PATH"
export PATH=$PATH:/usr/local/go/bin

# Claude Code auth + model + reasoning effort, rendered from the run config
# (see patcheval/config.py: build_claude_auth_exports).
{{AUTH_EXPORTS}}

# Useful aliases
alias ll='ls -la'
alias la='ls -la'
BASHEOF


source ~/.bashrc


if [ -L ~/.npm-global/bin/claude ]; then
    target=$(readlink ~/.npm-global/bin/claude)
    if [ -x "$target" ]; then
        echo "✅ Claude Code installation verified"
    else
        chmod +x "$target"
        echo "✅ Claude Code permissions fixed"
    fi
else
    echo "⚠️  Claude symlink not found, checking alternatives..."
fi


if ~/.npm-global/bin/claude --version >/dev/null 2>&1; then
    echo "✅ Claude Code ready to use"
elif command -v claude &> /dev/null; then
    echo "✅ Claude Code found in PATH"
else
    echo "❌ Claude Code installation may have issues"
fi
USEREOF

echo "📁 Setting up Claude commands directory..."
su - claude_user << 'CMDEOF'
mkdir -p /workspace/markdown-it/.claude/commands 2>/dev/null || true
CMDEOF

echo "🔍 Final verification..."
su - claude_user << 'VERIFYEOF'

source ~/.bashrc


if command -v claude &> /dev/null; then
    echo "✅ Claude Code ready: $(claude --version 2>/dev/null || echo 'version check failed')"
elif ~/.npm-global/bin/claude --version >/dev/null 2>&1; then
    echo "✅ Claude Code available via direct path"
else
    echo "❌ Claude Code not accessible"
    echo "💡 Use full path: ~/.npm-global/bin/claude"
fi

echo "🔧 Environment: Node $(node --version), NPM $(npm --version)"
if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
    echo "🔑 Auth: Claude subscription OAuth token (CLAUDE_CODE_OAUTH_TOKEN set)"
else
    echo "⚠️  Auth: CLAUDE_CODE_OAUTH_TOKEN is empty"
fi
echo "🧠 Model: ${ANTHROPIC_MODEL:-default}, effort: ${CLAUDE_CODE_EFFORT_LEVEL:-default}"
VERIFYEOF

echo ""
echo "🎉 Setup Complete!"
echo ""
echo "Usage:"
echo "  su - claude_user"
echo "  cd /workspace/your-project"
echo "  claude /your-command"
echo ""
echo "💡 If 'claude' not found, use: ~/.npm-global/bin/claude"