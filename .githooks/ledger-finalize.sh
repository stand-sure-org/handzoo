#!/usr/bin/env bash
#
# Post-commit hook: Finalize pending ledger entry and handle auto-sealing.
#
# Worktree-safe: reads ledger from .git/agent-ledger/ (shared common dir).

set -e

LEDGER_CLI="${HOME}/.claude/scripts/claude-ledger"
GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || git rev-parse --git-dir)"
GIT_DIR="$(git rev-parse --git-dir)"

LEDGER_FILE="${GIT_COMMON_DIR}/agent-ledger/chains.json"

# Check if ledger CLI exists
if [ ! -x "$LEDGER_CLI" ]; then
    exit 0
fi

# Ledger must exist
if [ ! -f "$LEDGER_FILE" ]; then
    exit 0
fi

# Resolve session ID from pre-commit env file
if [ -f "${GIT_DIR}/ledger-session-id.env" ]; then
    AGENT_SESSION_ID=$(grep -m1 '^AGENT_SESSION_ID=' "${GIT_DIR}/ledger-session-id.env" | cut -d= -f2-)
fi

# Fallback to active session on branch
if [ -z "$AGENT_SESSION_ID" ]; then
    BRANCH=$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD)
    AGENT_SESSION_ID=$("$LEDGER_CLI" list --unsealed --branch "$BRANCH" --quiet 2>/dev/null | head -1 || echo "")
fi

if [ -z "$AGENT_SESSION_ID" ]; then
    exit 0
fi

COMMIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
if [ -z "$COMMIT_SHA" ]; then
    exit 0
fi

# Detect GPG signature - intent to cap/seal
COMMIT_SIG_STATUS=$(git show -s --format='%G?' HEAD 2>/dev/null || echo "N")
COMMIT_MSG=$(git show -s --format='%s' HEAD 2>/dev/null || echo "")

# Finalize: patch the most-recent pending entry with commit_sha + diff_stat.
if [[ ! "$COMMIT_MSG" =~ ^Seal\ agent\ work: ]]; then
    # Compute diff stats
    SHORTSTAT=$(git diff HEAD~1..HEAD --shortstat 2>/dev/null || echo "")
    INSERTIONS=$(echo "$SHORTSTAT" | sed -n 's/.*[[:space:]]\([0-9][0-9]*\) insertion.*/\1/p')
    DELETIONS=$(echo  "$SHORTSTAT" | sed -n 's/.*[[:space:]]\([0-9][0-9]*\) deletion.*/\1/p')
    INSERTIONS="${INSERTIONS:-0}"
    DELETIONS="${DELETIONS:-0}"

    LEDGER_FILE="$LEDGER_FILE" \
    CHAIN_SESSION_ID="$AGENT_SESSION_ID" \
    CHAIN_COMMIT_SHA="$COMMIT_SHA" \
    LEDGER_INSERTIONS="$INSERTIONS" \
    LEDGER_DELETIONS="$DELETIONS" \
    python3 - <<'PYEOF'
import json, os, sys, hashlib

def compute_entry_hash(entry, previous_hash):
    content = (
        previous_hash +
        entry.get("commit_sha", "pending") +
        entry.get("instruction_hash", "") +
        ",".join(sorted(entry.get("files", []))) +
        entry.get("timestamp", "")
    )
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()

try:
    lf          = os.environ['LEDGER_FILE']
    session_id  = os.environ['CHAIN_SESSION_ID']
    commit_sha  = os.environ['CHAIN_COMMIT_SHA']
    insertions  = int(os.environ.get('LEDGER_INSERTIONS', '0'))
    deletions   = int(os.environ.get('LEDGER_DELETIONS',  '0'))

    with open(lf) as f:
        ledger = json.load(f)

    chain = ledger.get('chains', {}).get(session_id, {})
    entries = chain.get('entries', [])

    updated = False
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.get('commit_sha') == 'pending':
            entry['commit_sha'] = commit_sha
            entry['diff_stat']  = {'insertions': insertions, 'deletions': deletions}
            
            if i > 0:
                previous_hash = entries[i-1]['entry_hash']
            else:
                previous_hash = chain['started_at']
            
            entry['entry_hash'] = compute_entry_hash(entry, previous_hash)
            chain['chain_hash'] = entry['entry_hash']
            updated = True
            break

    if updated:
        with open(lf, 'w') as f:
            json.dump(ledger, f, indent=2)
            f.write('\n')
    sys.exit(0)
except Exception as e:
    print(f'[ledger-finalize] updater failed: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
fi

# Auto-Seal if commit is signed (human intent)
if [[ "$COMMIT_SIG_STATUS" != "N" ]] && [[ ! "$COMMIT_MSG" =~ ^Seal\ agent\ work: ]]; then
    echo "[ledger-finalize] Detected signed commit. Auto-sealing session: $AGENT_SESSION_ID"
    "$LEDGER_CLI" seal --session "$AGENT_SESSION_ID" --force || true
fi

rm -f "${GIT_DIR}/ledger-session-id.env"
exit 0
