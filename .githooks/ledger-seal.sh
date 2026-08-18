#!/usr/bin/env bash
#
# Seal the current agent chain.
# Usage: ./ledger-seal.sh [--session <id>] [--message <text>]

set -e

LEDGER_CLI="${HOME}/.claude/scripts/claude-ledger"

# Resolve session ID if not provided
SESSION_ID=""
MESSAGE=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --session) SESSION_ID="$2"; shift ;;
        --message) MESSAGE="$2"; shift ;;
    esac
    shift
done

if [ -z "$SESSION_ID" ]; then
    # Pick up active session on branch
    BRANCH=$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD)
    SESSION_ID=$("$LEDGER_CLI" list --unsealed --branch "$BRANCH" --quiet 2>/dev/null | head -1 || echo "")
fi

if [ -z "$SESSION_ID" ]; then
    echo "No unsealed session found to seal."
    exit 0
fi

echo "Sealing session: $SESSION_ID"
"$LEDGER_CLI" seal --session "$SESSION_ID" ${MESSAGE:+--message "$MESSAGE"}
