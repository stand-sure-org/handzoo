#!/usr/bin/env bash
#
# Pre-commit hook: Add ledger entry before commit
#
# Worktree-safe: ledger lives in .git/agent-ledger/ (shared across all worktrees
# via --git-common-dir). The ledger is NOT committed to the working tree.

set -e

LEDGER_CLI="${HOME}/.claude/scripts/claude-ledger"

# --git-common-dir is the shared .git root regardless of which worktree we're in.
GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || git rev-parse --git-dir)"
GIT_DIR="$(git rev-parse --git-dir)"

LEDGER_FILE="${GIT_COMMON_DIR}/agent-ledger/chains.json"

# Check if ledger CLI exists
if [ ! -x "$LEDGER_CLI" ]; then
    exit 0
fi

# Get current branch
BRANCH=$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD)
BRANCH_SLUG=$(echo "$BRANCH" | tr '/' '-' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | cut -c1-40)

# Resolve session ID
if [ -n "$GEMINI_SESSION_ID" ]; then
    SESSION_ID="$GEMINI_SESSION_ID"
    AGENT_TYPE="gemini-cli"
    CONVERSATION_ID="$GEMINI_SESSION_ID"
elif [ -n "$CLAUDE_SESSION_ID" ]; then
    SESSION_ID="$CLAUDE_SESSION_ID"
    AGENT_TYPE="${CLAUDE_AGENT_TYPE:-claude-code}"
    CONVERSATION_ID="${CLAUDE_CONVERSATION_ID:-$SESSION_ID}"
elif [ -f "$LEDGER_FILE" ]; then
    # Pick up an existing unsealed chain on this branch, if any
    ACTIVE_SESSION=$("$LEDGER_CLI" list --unsealed --quiet 2>/dev/null | grep "$BRANCH_SLUG" | head -1 || echo "")
    if [ -z "$ACTIVE_SESSION" ]; then
        # Just pick the very first unsealed one as a last resort
        ACTIVE_SESSION=$("$LEDGER_CLI" list --unsealed --quiet 2>/dev/null | head -1 || echo "")
    fi

    if [ -n "$ACTIVE_SESSION" ]; then
        SESSION_ID="$ACTIVE_SESSION"
        # Try to detect agent type from ledger
        AGENT_TYPE=$(SID="$SESSION_ID" LEDGER_FILE="$LEDGER_FILE" python3 -c "import json, os; print(json.load(open(os.environ['LEDGER_FILE']))['chains'].get(os.environ['SID'], {}).get('agent', 'unknown'))" 2>/dev/null || echo "unknown")
        CONVERSATION_ID="$SESSION_ID"
    else
        SESSION_ID="session-$(date +%s)-${BRANCH_SLUG}"
        AGENT_TYPE="unknown"
        CONVERSATION_ID="$SESSION_ID"
    fi
else
    SESSION_ID="session-$(date +%s)-${BRANCH_SLUG}"
    AGENT_TYPE="unknown"
    CONVERSATION_ID="$SESSION_ID"
fi

# Initialize chain if needed
if [ ! -f "$LEDGER_FILE" ] || ! "$LEDGER_CLI" verify --session "$SESSION_ID" >/dev/null 2>&1; then
    "$LEDGER_CLI" init \
        --session "$SESSION_ID" \
        --branch "$BRANCH" \
        --agent "${AGENT_TYPE:-agent}" \
        --worktree "$(pwd)" \
        2>/dev/null || true
fi

PARENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM \
    | grep -v "^\.claude/ledger/" \
    || true)

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

# Idempotency guard
HAS_PENDING=$(LEDGER_FILE="$LEDGER_FILE" \
              CHAIN_SESSION_ID="$SESSION_ID" \
              CHAIN_STAGED_FILES="$STAGED_FILES" \
python3 - <<'PYEOF'
import json, os, sys

try:
    lf = os.environ['LEDGER_FILE']
    if not os.path.exists(lf):
        print('no')
        sys.exit(0)

    with open(lf) as f:
        ledger = json.load(f)

    chain = ledger.get('chains', {}).get(os.environ['CHAIN_SESSION_ID'], {})
    staged = set(l for l in os.environ['CHAIN_STAGED_FILES'].splitlines() if l)

    for entry in chain.get('entries', []):
        if entry.get('commit_sha') == 'pending' and set(entry.get('files', [])) == staged:
            print('yes')
            sys.exit(0)

    print('no')
except Exception:
    print('no')
PYEOF
)

if [ "$HAS_PENDING" = "yes" ]; then
    exit 0
fi

COMMIT_MSG_FILE="${GIT_DIR}/COMMIT_EDITMSG"
if [ -f "$COMMIT_MSG_FILE" ]; then
    INSTRUCTION=$(head -1 "$COMMIT_MSG_FILE" | sed 's/^[[:space:]]*//' | cut -c1-200)
else
    INSTRUCTION="Automated commit"
fi

INSTRUCTION_HASH="sha256:$(printf '%s' "$INSTRUCTION" \
    | (sha256sum 2>/dev/null || shasum -a 256) \
    | cut -d' ' -f1)"

STAGED_FILES_CSV=$(echo "$STAGED_FILES" | tr '\n' ',' | sed 's/,$//')

"$LEDGER_CLI" add \
    --session "$SESSION_ID" \
    --parent "${PARENT_SHA:-HEAD}" \
    --instruction "$INSTRUCTION" \
    --instruction-hash "$INSTRUCTION_HASH" \
    --conversation-id "$CONVERSATION_ID" \
    --files "$STAGED_FILES_CSV" \
    2>/dev/null || true

echo "AGENT_SESSION_ID=$SESSION_ID" > "${GIT_DIR}/ledger-session-id.env"

exit 0
